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
from api.models.search import SearchFilters, SearchResult

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

# {filter} is an optional AND-chain of metadata predicates (see _metadata_filter_sql),
# folded into the WHERE so restrictive tag/filename filters cut rows during the index
# scan instead of after it (Phase 4 pre-filter pushdown) — correct top-k, fewer rows.
_SEARCH_SQL = (
    "SELECT id, text, metadata, 1 - (embedding <=> $1) AS score "
    "FROM chunks WHERE collection_id = $2{filter} "
    "ORDER BY embedding <=> $1 LIMIT $3"
)

# Real BM25 via ParadeDB pg_search: the ``text ||| $2`` match operator tokenizes
# the raw query and matches chunks containing any of its terms (OR, recall-first),
# scored by ``pdb.score(id)`` = Okapi BM25. Scoped to the vector candidate set
# ($3) — the search service only ever re-ranks candidates, so scoring just those
# keeps this tiny. ``collection_id`` is in the index (filter pushdown).
# ponytail: ``|||`` (match-any-term) over ``@@@`` (query-string) so raw user
# queries with punctuation don't hit the query-string parser.
_BM25_SQL = (
    "SELECT id, pdb.score(id) AS score "
    "FROM chunks WHERE collection_id = $1 AND id = ANY($3::text[]) "
    "AND text ||| $2"
)

_LIST_SQL = (
    "SELECT metadata->>'document_id' AS document_id, "
    "metadata->>'filename' AS filename, metadata->>'parser' AS parser, "
    "count(*) AS chunk_count FROM chunks "
    "WHERE collection_id = $1 AND metadata->>'document_id' IS NOT NULL "
    "GROUP BY 1, 2, 3"
)


def _metadata_filter_sql(
    filters: SearchFilters | None, next_param: int
) -> tuple[str, list[Any]]:
    """Build an AND-chained WHERE fragment + params for metadata pre-filtering.

    Placeholders start at ``$<next_param>`` so callers append the returned params
    after their fixed args. Mirrors ``api.services.search._matches``: language and
    filename are exact-match; ``tags`` requires every requested tag present (jsonb
    array containment ``@>``). Returns ``("", [])`` when there is nothing to filter.
    """
    if filters is None:
        return "", []
    conditions: list[str] = []
    params: list[Any] = []
    idx = next_param
    if filters.language:
        conditions.append(f"metadata->>'language' = ${idx}")
        params.append(filters.language)
        idx += 1
    if filters.filename:
        conditions.append(f"metadata->>'filename' = ${idx}")
        params.append(filters.filename)
        idx += 1
    if filters.tags:
        conditions.append(f"metadata->'tags' @> ${idx}::jsonb")
        params.append(json.dumps(filters.tags))
        idx += 1
    if not conditions:
        return "", []
    return " AND " + " AND ".join(conditions), params


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
        """Enable the vector + pg_search extensions and create the ``chunks`` table."""
        import asyncpg

        conn = await asyncpg.connect(**self._conn_kwargs)
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # ParadeDB pg_search: real Okapi BM25 inside Postgres (@@@ / paradedb.score).
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                "id text PRIMARY KEY, collection_id text NOT NULL, text text NOT NULL, "
                "metadata jsonb NOT NULL DEFAULT '{}'::jsonb, "
                f"embedding vector({self._dimensions}) NOT NULL)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_collection_id_idx ON chunks (collection_id)"
            )
            # Drop the native-FTS stand-in if a prior build created it (idempotent).
            await conn.execute("DROP INDEX IF EXISTS chunks_text_tsv_idx")
            await conn.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS text_tsv")
            # Lexical/BM25 (Phase 3): a pg_search BM25 index over text, maintained
            # incrementally on every upsert — no corpus, no rebuild. collection_id
            # is included so the search's WHERE filter pushes into the index scan.
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_bm25_idx ON chunks "
                "USING bm25 (id, text, collection_id) WITH (key_field = 'id')"
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

    # -- liveness -----------------------------------------------------------

    def ping(self) -> bool:
        """Return True if PostgreSQL answers a trivial query."""
        try:
            self._runner.run(self._ping())
            return True
        except Exception:
            return False

    async def _ping(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")

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

    async def _search(self, collection_id: str, vector: list[float], top_k: int,
                      filters: SearchFilters | None = None) -> list[SearchResult]:
        # $1 vector, $2 collection_id, $3 top_k; filter params (if any) start at $4.
        frag, extra = _metadata_filter_sql(filters, 4)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _SEARCH_SQL.format(filter=frag), vector, collection_id, top_k, *extra
            )
        return [
            SearchResult(
                chunk_id=row["id"], text=row["text"], score=float(row["score"]),
                rank=rank, metadata=self._as_dict(row["metadata"]),
            )
            for rank, row in enumerate(rows)
        ]

    def search(self, collection_id: str, vector: list[float], top_k: int,
               filters: SearchFilters | None = None) -> list[SearchResult]:
        """Return the ``top_k`` most cosine-similar chunks in a collection.

        Args:
            collection_id: Collection to search.
            vector: Query embedding.
            top_k: Maximum results to return.
            filters: Optional metadata pre-filter (language/filename/tags) folded
                into the SQL ``WHERE`` (Phase 4 pushdown) so the DB returns only
                matching rows — fewer scanned, correct top-k under restrictive
                filters. The search service still re-applies them portably.

        Returns:
            Ranked results with ``score`` = ``1 - cosine_distance``.
        """
        return self._runner.run(self._search(collection_id, vector, top_k, filters))

    # -- lexical / BM25 -----------------------------------------------------

    async def _bm25_scores(
        self, collection_id: str, query: str, chunk_ids: list[str]
    ) -> dict[str, float]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_BM25_SQL, collection_id, query, chunk_ids)
        return {row["id"]: float(row["score"]) for row in rows}

    def bm25_scores(
        self, collection_id: str, query: str, chunk_ids: list[str]
    ) -> dict[str, float]:
        """Return BM25 relevance scores for the given chunks against ``query``.

        Real Okapi BM25 via ParadeDB ``pg_search`` (``|||`` match + ``pdb.score``)
        replaces the old Redis BM25 corpus. Scoped to ``chunk_ids`` (the vector
        candidate set) since the search service only re-ranks candidates.

        Args:
            collection_id: Collection to score within.
            query: Raw user query (parsed by ``websearch_to_tsquery``).
            chunk_ids: Candidate chunk ids to score.

        Returns:
            ``chunk_id -> score`` for the candidates whose text matches the query;
            non-matching candidates are simply absent (treated as 0 downstream).
        """
        if not chunk_ids:
            return {}
        return self._runner.run(self._bm25_scores(collection_id, query, chunk_ids))

    async def _iter_document_chunks(
        self, collection_id: str, document_id: str
    ) -> list[tuple[str, str, str]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, text FROM chunks "
                "WHERE collection_id = $1 AND metadata->>'document_id' = $2",
                collection_id, document_id,
            )
        return [(row["id"], document_id, row["text"] or "") for row in rows]

    def iter_document_chunks(
        self, collection_id: str, document_id: str
    ) -> list[tuple[str, str, str]]:
        """Return ``(chunk_id, document_id, text)`` triples for a document's chunks."""
        return self._runner.run(self._iter_document_chunks(collection_id, document_id))

    async def _collection_texts(self, collection_id: str) -> list[str]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT text FROM chunks WHERE collection_id = $1", collection_id
            )
        return [row["text"] or "" for row in rows]

    def collection_texts(self, collection_id: str) -> list[str]:
        """Return the stored text of every chunk in a collection (tag suggestion)."""
        return self._runner.run(self._collection_texts(collection_id))

    # -- tag sync -----------------------------------------------------------

    async def _set_document_tags(
        self, collection_id: str, document_id: str, tags: list[str]
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chunks SET metadata = jsonb_set(metadata, '{tags}', $3::jsonb) "
                "WHERE collection_id = $1 AND metadata->>'document_id' = $2",
                collection_id, document_id, json.dumps(tags),
            )

    def set_document_tags(
        self, collection_id: str, document_id: str, tags: list[str]
    ) -> None:
        """Rewrite the ``tags`` metadata key on every chunk of a document.

        Args:
            collection_id: Collection holding the document's chunks.
            document_id: Document whose chunk tags to replace.
            tags: New effective tag list to store.
        """
        self._runner.run(self._set_document_tags(collection_id, document_id, tags))

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
