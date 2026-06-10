"""pgvector vector-store adapter (Delivery 3).

The ``VectorStoreAdapter`` Protocol is synchronous but is called from both the
Celery worker (no running event loop) and the API search path (inside a running
loop). asyncpg is async-only, so each adapter instance owns a dedicated
background thread running a persistent event loop; the synchronous methods
submit coroutines to it. asyncpg connection pools are loop-bound, so binding the
pool to that one loop keeps every query on a consistent loop.

Schema: a single shared ``chunks`` table. Each collection gets a lazily-built
*partial* HNSW index (``WHERE collection_id = ...``) once it crosses
``index_min_rows`` rows, created with ``CREATE INDEX CONCURRENTLY`` so the build
never locks the table.

# verified against asyncpg==0.29 + pgvector==0.2.5 via Context7 (/magicstack/asyncpg)
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar

from api.models.chunk import Chunk
from api.models.document import DocumentSummary
from api.models.search import SearchResult

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class _AsyncRunner:
    """Owns a background thread running a persistent asyncio event loop.

    Lets synchronous adapter methods drive asyncpg coroutines regardless of
    whether the calling thread already runs an event loop (the API does, the
    worker does not).
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Run ``coro`` on the background loop and block until it returns.

        Args:
            coro: The coroutine to execute.

        Returns:
            The coroutine's return value.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


_UPSERT_SQL = (
    "INSERT INTO chunks (id, collection_id, text, metadata, embedding) "
    "VALUES ($1, $2, $3, $4::jsonb, $5) "
    "ON CONFLICT (id) DO UPDATE SET "
    "text = EXCLUDED.text, metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding"
)

_SEARCH_SQL = (
    "SELECT id, text, metadata, 1 - (embedding <=> $1) AS score "
    "FROM chunks WHERE collection_id = $2 "
    "ORDER BY embedding <=> $1 LIMIT $3"
)

_LIST_SQL = (
    "SELECT metadata->>'document_id' AS document_id, "
    "metadata->>'filename' AS filename, metadata->>'parser' AS parser, "
    "count(*) AS chunk_count FROM chunks "
    "WHERE collection_id = $1 AND metadata->>'document_id' IS NOT NULL "
    "GROUP BY 1, 2, 3"
)


class PgvectorAdapter:
    """Vector store backed by PostgreSQL with the pgvector extension."""

    def __init__(self, host: str, port: int, database: str, user: str,
                 password: str, dimensions: int, index_min_rows: int = 100) -> None:
        self._conn_kwargs: dict[str, Any] = {
            "host": host, "port": port, "user": user,
            "password": password, "database": database,
        }
        self._dimensions = dimensions
        self._index_min_rows = index_min_rows
        self._pool: Any = None
        self._runner = _AsyncRunner()
        self._lock = asyncio.Lock()

    # -- connection / schema bootstrap --------------------------------------

    async def _bootstrap_schema(self) -> None:
        """Enable the vector extension and create the shared ``chunks`` table."""
        import asyncpg

        conn = await asyncpg.connect(**self._conn_kwargs)
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                "id text PRIMARY KEY, collection_id text NOT NULL, text text NOT NULL, "
                "metadata jsonb NOT NULL DEFAULT '{}'::jsonb, "
                f"embedding vector({self._dimensions}) NOT NULL)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_collection_id_idx ON chunks (collection_id)"
            )
        finally:
            await conn.close()

    @staticmethod
    async def _register_vector(conn: Any) -> None:
        """Pool ``init`` callback registering the pgvector type codec."""
        from pgvector.asyncpg import register_vector

        await register_vector(conn)

    async def _get_pool(self) -> Any:
        """Return the connection pool, bootstrapping the schema on first use.

        Guarded by an async lock so concurrent first-use callers (the search
        fan-out runs many coroutines on the shared runner loop) build exactly
        one pool rather than racing and leaking a duplicate.
        """
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                import asyncpg

                await self._bootstrap_schema()
                self._pool = await asyncpg.create_pool(
                    **self._conn_kwargs, init=self._register_vector, min_size=1, max_size=4
                )
        return self._pool

    # -- upsert -------------------------------------------------------------

    async def _upsert(self, collection_id: str, chunks: list[Chunk],
                      vectors: list[list[float]]) -> None:
        pool = await self._get_pool()
        rows = [
            (c.id, collection_id, c.text, json.dumps(c.metadata.model_dump()), v)
            for c, v in zip(chunks, vectors, strict=True)
        ]
        async with pool.acquire() as conn:
            await conn.executemany(_UPSERT_SQL, rows)
        await self._ensure_hnsw_index(collection_id)

    def upsert(self, collection_id: str, chunks: list[Chunk],
               vectors: list[list[float]]) -> None:
        """Insert or update chunk embeddings for a collection.

        Args:
            collection_id: Target collection namespace.
            chunks: Chunks to store; ``chunk.id`` is the conflict key.
            vectors: Embedding per chunk, aligned by index with ``chunks``.
        """
        if not chunks:
            return
        self._runner.run(self._upsert(collection_id, chunks, vectors))

    async def _ensure_hnsw_index(self, collection_id: str) -> None:
        """Build the per-collection partial HNSW index once it has enough rows.

        Runs ``CREATE INDEX CONCURRENTLY`` as a single autocommit statement
        (concurrent builds cannot run inside a transaction block). The index
        name is double-quoted and the predicate literal is escaped so an
        unusual collection id cannot break the DDL. Failures are logged, not
        raised — search still works via sequential scan without the index.

        Args:
            collection_id: Collection whose index to (maybe) build.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE collection_id = $1", collection_id
            )
            if count < self._index_min_rows:
                return
            name = f"chunks_embedding_hnsw_{collection_id[:8]}"
            literal = collection_id.replace("'", "''")
            sql = (
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" '
                "ON chunks USING hnsw (embedding vector_cosine_ops) "
                f"WHERE collection_id = '{literal}'"
            )
            try:
                await conn.execute(sql)
            except Exception as exc:  # pragma: no cover - infra-dependent
                logger.warning("hnsw index build skipped for %s: %s", collection_id, exc)

    # -- search -------------------------------------------------------------

    async def _search(self, collection_id: str, vector: list[float],
                      top_k: int) -> list[SearchResult]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SEARCH_SQL, vector, collection_id, top_k)
        return [
            SearchResult(
                chunk_id=row["id"], text=row["text"], score=float(row["score"]),
                rank=rank, metadata=self._as_dict(row["metadata"]),
            )
            for rank, row in enumerate(rows)
        ]

    def search(self, collection_id: str, vector: list[float], top_k: int,
               filters: dict | None = None) -> list[SearchResult]:
        """Return the ``top_k`` most cosine-similar chunks in a collection.

        Args:
            collection_id: Collection to search.
            vector: Query embedding.
            top_k: Maximum results to return.
            filters: Accepted for Protocol compatibility; metadata filtering is
                applied by the search service after ranking (mirrors Chroma).

        Returns:
            Ranked results with ``score`` = ``1 - cosine_distance``.
        """
        return self._runner.run(self._search(collection_id, vector, top_k))

    # -- delete -------------------------------------------------------------

    async def _delete_document(self, collection_id: str, document_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM chunks WHERE collection_id = $1 "
                "AND metadata->>'document_id' = $2",
                collection_id, document_id,
            )

    def delete_document(self, collection_id: str, document_id: str) -> None:
        """Delete every chunk belonging to a document in a collection.

        Args:
            collection_id: Collection to prune.
            document_id: Document whose chunks to remove.
        """
        self._runner.run(self._delete_document(collection_id, document_id))

    async def _delete_collection(self, collection_id: str) -> None:
        pool = await self._get_pool()
        name = f"chunks_embedding_hnsw_{collection_id[:8]}"
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM chunks WHERE collection_id = $1", collection_id)
            await conn.execute(f'DROP INDEX IF EXISTS "{name}"')

    def delete_collection(self, collection_id: str) -> None:
        """Delete all chunks and the HNSW index for a collection.

        Args:
            collection_id: Collection to drop.
        """
        self._runner.run(self._delete_collection(collection_id))

    # -- list ---------------------------------------------------------------

    async def _list_documents(self, collection_id: str) -> list[DocumentSummary]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_LIST_SQL, collection_id)
        now = datetime.now(UTC)
        return [
            DocumentSummary(
                document_id=row["document_id"], filename=row["filename"] or "",
                file_type=row["parser"] or "", chunk_count=row["chunk_count"],
                created_at=now, updated_at=now,
            )
            for row in rows
        ]

    def list_documents(self, collection_id: str) -> list[DocumentSummary]:
        """Aggregate stored chunks into per-document summaries.

        Timestamps are synthetic; the authoritative listing lives in SQLite.

        Args:
            collection_id: Collection to summarise.

        Returns:
            One ``DocumentSummary`` per distinct ``document_id``.
        """
        return self._runner.run(self._list_documents(collection_id))

    @staticmethod
    def _as_dict(value: Any) -> dict:
        """Decode an asyncpg jsonb column (returned as ``str``) into a dict."""
        if isinstance(value, str):
            return json.loads(value)
        return value or {}
