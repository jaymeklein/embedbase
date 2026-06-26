"""Celery ingestion tasks: parse → chunk → embed → store, plus BM25 indexing."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from api.constants import REDIS_URL as _REDIS_URL_DEFAULT
from api.models.redis import CorpusConfig
from api.services.redis.redis import get_corpus
from worker.celery_app import celery_app
from worker.config import get_config
from worker.db import (
    SessionLocal,
    collection_tags,
    collections,
    document_tags,
    documents,
    job_records,
    tags,
    workspace_tags,
)

if TYPE_CHECKING:
    from api.models.chunk import Chunk

logger = structlog.get_logger()

# task_time_limit in celery_app.py is 600s; allow a margin before reclaiming.
STALE_PROCESSING_SECONDS = 660

# Lazily-built singletons. Tests override these module globals (or pass deps
# directly to ``_run_ingestion``) to avoid real Redis / Chroma / model loads.
_embedder_singleton: Any = None
_vector_store_singleton: Any = None
_redis_singleton: Any = None


def _embedder() -> Any:
    global _embedder_singleton
    if _embedder_singleton is None:
        from api.adapters.embeddings import get_embedding_adapter

        _embedder_singleton = get_embedding_adapter(get_config().embedding)
    return _embedder_singleton


def _vector_store() -> Any:
    global _vector_store_singleton
    if _vector_store_singleton is None:
        from api.adapters.vector_store import get_vector_store

        dims = _embedder().dimensions
        _vector_store_singleton = get_vector_store(get_config().vector_store, dims)
    return _vector_store_singleton


def _redis() -> Any:
    global _redis_singleton
    if _redis_singleton is None:
        import redis

        url = os.environ.get("REDIS_URL", _REDIS_URL_DEFAULT)
        _redis_singleton = redis.Redis.from_url(url, decode_responses=True)
    return _redis_singleton


def reload_adapters() -> None:
    """Rebuild the embedder + vector-store singletons from the current config.

    Called by the config hot-reload listener after ``get_config.cache_clear()`` so
    a live config change takes effect without restarting the worker. Building here
    (rather than nulling the singletons for lazy rebuild) surfaces a bad config
    immediately, so the listener can ack an error and the API can roll back.
    """
    global _embedder_singleton, _vector_store_singleton
    from api.adapters.embeddings import get_embedding_adapter
    from api.adapters.vector_store import get_vector_store

    config = get_config()
    embedder = get_embedding_adapter(config.embedding)
    _embedder_singleton = embedder
    _vector_store_singleton = get_vector_store(config.vector_store, embedder.dimensions)


# ---------------------------------------------------------------------------
# Job-record helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _set_job_status(session: Any, job_id: str, status: str, **fields: Any) -> None:
    session.execute(
        update(job_records)
        .where(job_records.c.job_id == job_id)
        .values(status=status, updated_at=_now(), **fields)
    )


# ---------------------------------------------------------------------------
# Effective tags (D6 search bridge)
# ---------------------------------------------------------------------------


def _effective_document_tags(session: Any, collection_id: str, document_id: str) -> list[str]:
    """Return the union of a document's workspace, collection, and document tags.

    A document's *effective* tags inherit downward: tagging a workspace or a
    collection makes that tag apply to every document beneath it. The sorted
    name list is folded into each chunk's metadata so D3 tag filtering works.

    Args:
        session: Synchronous SQLAlchemy session.
        collection_id: Collection the document belongs to.
        document_id: Document whose effective tags to resolve.

    Returns:
        Distinct tag names, sorted, across all three inheritance levels.
    """
    ws_row = session.execute(
        select(collections.c.workspace_id).where(collections.c.id == collection_id)
    ).fetchone()
    workspace_id = ws_row[0] if ws_row else None
    specs = [
        (workspace_tags, "workspace_id", workspace_id),
        (collection_tags, "collection_id", collection_id),
        (document_tags, "document_id", document_id),
    ]
    names: set[str] = set()
    for join, col, entity_id in specs:
        if entity_id is None:
            continue
        rows = session.execute(
            select(tags.c.name)
            .select_from(join.join(tags, tags.c.id == join.c.tag_id))
            .where(join.c[col] == entity_id)
        ).fetchall()
        names.update(row[0] for row in rows)
    return sorted(names)


def _apply_effective_tags(
    session_factory: Any, collection_id: str, document_id: str, chunks: list[Chunk]
) -> None:
    """Fold the document's effective tags into each chunk's metadata in place."""
    with session_factory() as session:
        effective = _effective_document_tags(session, collection_id, document_id)
    for chunk in chunks:
        chunk.metadata.tags = effective


# ---------------------------------------------------------------------------
# AI auto-tagging at ingestion (D6 follow-up)
# ---------------------------------------------------------------------------


def _normalize_tag(name: str) -> str:
    """Lowercase, trim, and collapse whitespace — matches the API's tag rule."""
    return " ".join(name.strip().lower().split())


def _get_or_create_tag(session: Any, workspace_id: str, name: str) -> str:
    """Return the id of the workspace tag named ``name``, creating it if absent."""
    session.execute(
        sqlite_insert(tags)
        .values(
            id=f"tag_{uuid4().hex[:12]}",
            workspace_id=workspace_id,
            name=name,
            color=None,
            created_at=_now(),
        )
        .on_conflict_do_nothing()
    )
    row = session.execute(
        select(tags.c.id).where(tags.c.workspace_id == workspace_id, tags.c.name == name)
    ).fetchone()
    return str(row[0])


def _auto_tag_document(
    session_factory: Any,
    collection_id: str,
    document_id: str,
    chunks: list[Chunk],
    config: Any,
) -> None:
    """Auto-apply high-confidence AI tags to a freshly ingested document.

    Runs the configured suggester over the document text and assigns every
    suggestion scoring at least ``tagging.suggester.min_confidence``, creating
    workspace tags by name as needed. Best-effort: any suggester/LLM failure is
    logged and never fails ingestion. Called before effective-tag folding so the
    new tags also reach chunk metadata (and thus tag-filtered search).
    """
    tagging = config.tagging
    if not getattr(tagging, "auto_tag_on_ingest", False):
        return
    text = "\n".join(c.text for c in chunks).strip()
    if not text:
        return
    # Don't re-suggest tags the document already has (own or inherited).
    with session_factory() as session:
        existing = _effective_document_tags(session, collection_id, document_id)
    try:
        from api.adapters.tagging import get_tag_suggester

        suggestions = get_tag_suggester(tagging).suggest(text, existing)
    except Exception as exc:
        logger.warning("auto-tag failed", document_id=document_id, error=str(exc))
        return

    keep = [s for s in suggestions if s.confidence >= tagging.suggester.min_confidence]
    if not keep:
        return
    with session_factory() as session:
        ws_row = session.execute(
            select(collections.c.workspace_id).where(collections.c.id == collection_id)
        ).fetchone()
        if not ws_row:
            return
        applied: list[str] = []
        for suggestion in keep:
            name = _normalize_tag(suggestion.name)
            if not name:
                continue
            tag_id = _get_or_create_tag(session, ws_row[0], name)
            session.execute(
                sqlite_insert(document_tags)
                .values(document_id=document_id, tag_id=tag_id)
                .on_conflict_do_nothing()
            )
            applied.append(name)
        session.commit()
    logger.info("auto-tagged document", document_id=document_id, tags=applied)


# ---------------------------------------------------------------------------
# BM25 write path
# ---------------------------------------------------------------------------


def _update_bm25_index(redis_client: Any, collection_id: str, chunks: list[Chunk]) -> None:
    """Append ``[chunk_id, document_id, text]`` triples to the collection's BM25 corpus.

    Keying by chunk_id (not document_id) means each chunk gets its own BM25
    score — a multi-chunk document no longer silently clobbers earlier scores.
    document_id is retained as entry[1] so _delete_from_bm25_index can prune
    all chunks for a document without a separate index.

    The corpus is stored as JSON (never pickle — untrusted-deserialization risk)
    under ``bm25:{collection_id}:corpus`` with no expiry — it mirrors the
    permanent vector store and is only ever rewritten by ingestion/deletion, so a
    TTL would silently break BM25 while the vectors live on. A monotonically
    increasing ``:version`` key lets the search side invalidate its local cache.
    """
    if not chunks:
        return
    corpus_key = f"bm25:{collection_id}:corpus"
    version_key = f"bm25:{collection_id}:version"

    raw = redis_client.get(corpus_key)
    corpus: list[list[str]] = json.loads(raw) if raw else []
    corpus.extend([chunk.id, chunk.metadata.document_id, chunk.text] for chunk in chunks)

    redis_client.set(corpus_key, json.dumps(corpus))
    redis_client.incr(version_key)


def _delete_from_bm25_index(redis_client: Any, corpus_config: CorpusConfig, document_id: str) -> None:
    """Remove all corpus entries for ``document_id`` from the BM25 index.

    Reads the JSON corpus from ``bm25:{collection_id}:corpus``, filters out
    all ``[document_id, text]`` pairs, rewrites the corpus, and bumps the
    version key so the search side invalidates its local cache.
    No-op when the corpus key is absent or the document has no entries.
    """
    
    corpus = get_corpus(redis_client, corpus_config)
    pruned = [entry for entry in corpus.data if entry[1] != document_id]
    if len(pruned) == len(corpus.data):
        return
    redis_client.set(corpus_config.corpus_key, json.dumps(pruned))
    redis_client.incr(corpus_config.version_key)


def _reindex_document_bm25(
    redis_client: Any, vector_store: Any, collection_id: str, document_id: str
) -> int:
    """Rebuild one document's BM25 corpus entries from the vector store.

    Pulls the document's stored chunks (text already lives in the vector store),
    replaces any existing corpus entries for that document, and bumps the version.
    No re-parsing or re-embedding — recovers BM25 even when the source file is gone.

    Returns the number of chunks indexed.
    """
    triples: list[tuple[str, str, str]] = vector_store.iter_document_chunks(
        collection_id, document_id
    )
    cfg = CorpusConfig(collection_id)
    kept = [e for e in get_corpus(redis_client, cfg).data if e[1] != document_id]
    kept.extend(triples)
    redis_client.set(cfg.corpus_key, json.dumps(kept))
    redis_client.incr(cfg.version_key)
    return len(triples)


def _reindex_collection_bm25(
    redis_client: Any, vector_store: Any, session_factory: Any, collection_id: str
) -> int:
    """Rebuild a collection's entire BM25 corpus from the vector store in one write.

    Reads every active document's chunks and replaces the corpus wholesale, so
    indexing many documents at once cannot race on the read-modify-write. Returns
    the total number of chunks indexed.
    """
    with session_factory() as session:
        doc_ids = [
            row[0] for row in session.execute(
                select(documents.c.id).where(
                    documents.c.collection_id == collection_id,
                    documents.c.status.is_(None),
                )
            ).fetchall()
        ]
    entries: list[tuple[str, str, str]] = []
    for doc_id in doc_ids:
        entries.extend(vector_store.iter_document_chunks(collection_id, doc_id))
    cfg = CorpusConfig(collection_id)
    redis_client.set(cfg.corpus_key, json.dumps(entries))
    redis_client.incr(cfg.version_key)
    return len(entries)


# ---------------------------------------------------------------------------
# Pipeline core (dependency-injected so it is unit-testable without infra)
# ---------------------------------------------------------------------------


def _run_ingestion(
    job_id: str,
    file_path: str,
    collection_id: str,
    document_id: str,
    file_type: str,
    *,
    session_factory: Any = None,
    embedder: Any = None,
    vector_store: Any = None,
    redis_client: Any = None,
    config: Any = None,
) -> int:
    """Run the full ingestion pipeline. Returns the number of chunks stored."""
    from api.adapters.parsers import get_parser

    session_factory = session_factory or SessionLocal
    embedder = embedder or _embedder()
    vector_store = vector_store or _vector_store()
    redis_client = redis_client or _redis()
    config = config or get_config()

    # --- Idempotency guard ---------------------------------------------------
    with session_factory() as session:
        row = session.execute(
            select(
                job_records.c.status,
                job_records.c.processing_started_at,
            ).where(job_records.c.job_id == job_id)
        ).fetchone()
        if row is None:
            logger.warning("ingest: unknown job", job_id=job_id)
            return 0
        if row.status == "done":
            logger.info("ingest: already done", job_id=job_id)
            return 0
        if row.status == "processing":
            started = row.processing_started_at
            now = datetime.now(UTC).replace(tzinfo=None)
            elapsed = (now - started).total_seconds() if started else 0
            if started is None or elapsed < STALE_PROCESSING_SECONDS:
                logger.info("ingest: already handling", job_id=job_id)
                return 0
            logger.warning("ingest: reclaiming stale job", job_id=job_id, elapsed_s=int(elapsed))
        _set_job_status(
            session,
            job_id,
            "processing",
            processing_started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.commit()

    # --- Parse → chunk -------------------------------------------------------
    parser = get_parser(file_type, config.chunking, parsers=config.parsers)
    chunks = parser.parse(file_path, document_id)

    # --- Embed (batched) → upsert -------------------------------------------
    if chunks:
        _auto_tag_document(session_factory, collection_id, document_id, chunks, config)
        _apply_effective_tags(session_factory, collection_id, document_id, chunks)
        batch_size = config.embedding.batch_size
        texts = [c.text for c in chunks]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(embedder.embed_batch(texts[start : start + batch_size]))
        vector_store.upsert(collection_id, chunks, vectors)
        _update_bm25_index(redis_client, collection_id, chunks)

    # --- Mark done -----------------------------------------------------------
    chunk_count = len(chunks)
    with session_factory() as session:
        session.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(chunk_count=chunk_count, updated_at=_now())
        )
        _set_job_status(session, job_id, "done", chunk_count=chunk_count)
        session.commit()

    logger.info("ingest complete", job_id=job_id, document_id=document_id, chunks=chunk_count)
    return chunk_count


def _mark_failed(job_id: str, error: str) -> None:
    try:
        with SessionLocal() as session:
            _set_job_status(session, job_id, "failed", error=error[:2000])
            session.commit()
    except Exception:  # pragma: no cover - failure-path best effort
        logger.error("could not record job failure", job_id=job_id)


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def ingest_document(
    self,
    job_id: str,
    file_path: str,
    collection_id: str,
    document_id: str,
    file_type: str,
):
    """Parse → chunk → embed → store a single document."""
    # SoftTimeLimitExceeded MUST be the first except — it subclasses Exception.
    try:
        return _run_ingestion(job_id, file_path, collection_id, document_id, file_type)
    except SoftTimeLimitExceeded:
        logger.warning("task exceeded time limit", job_id=job_id)
        _mark_failed(job_id, "Ingestion exceeded time limit")
        raise  # plain raise — never self.retry()
    except Exception as exc:
        logger.error("ingest task failed", job_id=job_id, error=str(exc))
        _mark_failed(job_id, str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def sync_document_tags(self, document_id: str, collection_id: str) -> None:
    """Refresh a document's effective tags on its stored chunks (search bridge).

    Recomputes the document's effective tags from the DB and writes them onto
    every stored chunk so D3 tag-filtered search reflects the latest assignment.

    Consistency model (CAP): this is the asynchronous, availability-favoring leg
    of the search bridge. Tag assignment/rename/merge/delete return to the client
    before this sync runs, so tag-filtered search is *eventually* consistent — it
    may briefly return stale results and reconverges once the worker applies the
    write. The authoritative tag state always lives in SQLite; the vector store
    only carries a denormalized copy for filtering.

    Pure command (CQS): mutates the vector store and returns nothing; the synced
    tags are surfaced via the log line and the query helper
    :func:`_effective_document_tags`, which tests call directly.
    """
    try:
        with SessionLocal() as session:
            effective = _effective_document_tags(session, collection_id, document_id)
        _vector_store().set_document_tags(collection_id, document_id, effective)
        logger.info("synced document tags", document_id=document_id, tags=effective)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.error("sync tags task failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def delete_document(self, document_id: str, collection_id: str) -> None:
    """Remove vectors, BM25 corpus entries, and the document row for a deleted document."""
    redis_client = _redis()
    try:
        _vector_store().delete_document(collection_id, document_id)
        _delete_from_bm25_index(redis_client, CorpusConfig(collection_id), document_id)
        with SessionLocal() as db:
            db.execute(sa_delete(documents).where(documents.c.id == document_id))
            db.commit()
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.error("delete task failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def index_document(self, document_id: str, collection_id: str) -> None:
    """Rebuild one document's BM25 corpus entries from the vector store."""
    try:
        n = _reindex_document_bm25(_redis(), _vector_store(), collection_id, document_id)
        logger.info("bm25 index complete", document_id=document_id, chunks=n)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.error("bm25 index failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def index_collection(self, collection_id: str) -> None:
    """Rebuild a whole collection's BM25 corpus from the vector store."""
    try:
        n = _reindex_collection_bm25(_redis(), _vector_store(), SessionLocal, collection_id)
        logger.info("bm25 collection index complete", collection_id=collection_id, chunks=n)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.error("bm25 collection index failed", collection_id=collection_id, error=str(exc))
        raise self.retry(exc=exc) from exc
