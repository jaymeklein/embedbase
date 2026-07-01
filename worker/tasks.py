"""Celery ingestion tasks: parse → chunk → embed → store.

Lexical/BM25 is the STORED ``text_tsv`` generated column on ``chunks`` (Phase 3),
maintained automatically by the vector-store upsert — no Redis corpus write path.
"""

from __future__ import annotations

import inspect
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from api.constants import REDIS_URL as _REDIS_URL_DEFAULT
from api.services import realtime
from api.sql_compat import dialect_insert
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

# A "processing" job is alive while it keeps a fresh heartbeat, refreshed on every
# progress event. This is what makes a long-but-advancing ingestion safe: the work
# is chunked (each chunk is fast and individually bounded by the embed adapter's
# per-request timeout), so a job that keeps making progress is healthy no matter how
# long it runs — it never trips a wall-clock total-time limit. Only a job that STOPS
# progressing lets its heartbeat expire and gets reclaimed. The TTL must exceed the
# largest gap between progress events (an opaque docling convert is the worst case).
HEARTBEAT_TTL = int(os.environ.get("INGESTION_HEARTBEAT_TTL", "600"))  # 10 min

# Global progress topic for the Ingestion Queue tab — fed alongside the
# per-collection ``ingestion:{collection_id}`` topic the Documents view uses.
_QUEUE_TOPIC = "ingestion-queue"


def _heartbeat_key(job_id: str) -> str:
    return f"ingest:hb:{job_id}"


def _beat(redis_client: Any, job_id: str) -> None:
    """Refresh a job's progress heartbeat; best-effort (never breaks ingestion)."""
    try:
        redis_client.set(_heartbeat_key(job_id), "1", ex=HEARTBEAT_TTL)
    except Exception:  # pragma: no cover - heartbeat is best-effort
        logger.debug("heartbeat failed", job_id=job_id)


def _job_alive(redis_client: Any, job_id: str) -> bool:
    """True while a job's heartbeat is fresh. On a redis error, assume alive — a
    transient blip must not trigger reclaiming (and double-processing) a live job."""
    try:
        return bool(redis_client.exists(_heartbeat_key(job_id)))
    except Exception:  # pragma: no cover
        return True


def _clear_beat(redis_client: Any, job_id: str) -> None:
    """Drop a job's heartbeat on task exit; best-effort (never breaks ingestion)."""
    try:
        redis_client.delete(_heartbeat_key(job_id))
    except Exception:  # pragma: no cover - best-effort
        logger.debug("heartbeat clear failed", job_id=job_id)

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
        dialect_insert(session.bind, tags)
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
                dialect_insert(session.bind, document_tags)
                .values(document_id=document_id, tag_id=tag_id)
                .on_conflict_do_nothing()
            )
            applied.append(name)
        session.commit()
    logger.info("auto-tagged document", document_id=document_id, tags=applied)


# ---------------------------------------------------------------------------
# Pipeline core (dependency-injected so it is unit-testable without infra)
# ---------------------------------------------------------------------------


def _parse_with_progress(parser: Any, file_path: str, document_id: str, emit: Any) -> list[Chunk]:
    """Call ``parser.parse``, threading a per-page progress callback when supported.

    Only parsers that declare an ``on_progress`` keyword (currently the PyMuPDF PDF
    parser) receive page callbacks; every other parser is called exactly as before.
    Docling is opaque (a single ``convert()``), so it reports only the ``parsing``
    start the caller already emitted.
    """
    if "on_progress" in inspect.signature(parser.parse).parameters:
        return parser.parse(
            file_path,
            document_id,
            on_progress=lambda current, total: emit("parsing", current, total),
        )
    return parser.parse(file_path, document_id)


def _chunk_label(chunk: Chunk) -> str:
    """Short human label for a chunk in the Ingestion Queue's live list."""
    md = chunk.metadata
    if md.heading_path:
        return md.heading_path[:80]
    if md.page_number is not None:
        return f"p.{md.page_number}"
    if md.symbol_name:
        return md.symbol_name[:80]
    return " ".join(chunk.text.split())[:80]


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

    # Identity for the queue view (filename + collection name), resolved once.
    with session_factory() as _s:
        filename = _s.execute(
            select(job_records.c.filename).where(job_records.c.job_id == job_id)
        ).scalar() or document_id
        collection_name = _s.execute(
            select(collections.c.name).where(collections.c.id == collection_id)
        ).scalar() or collection_id

    def emit(
        phase: str,
        current: int | None = None,
        total: int | None = None,
        status: str = "processing",
        chunks: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        """Best-effort publish of a progress event; never breaks ingestion.

        Fans out to two topics: ``ingestion:{collection_id}`` (the per-collection
        Documents view) and the global ``ingestion-queue`` (the Ingestion Queue tab).
        ``chunks`` carries the labels of the chunks a batch just embedded, for the
        queue's live per-chunk list; ``error`` the failure reason on a ``failed`` event.
        """
        _beat(redis_client, job_id)  # progress = alive; resets the stale timer
        try:
            pct = round(100 * current / total) if current is not None and total else None
            payload: dict[str, Any] = {
                "document_id": document_id,
                "collection_id": collection_id,
                "collection_name": collection_name,
                "filename": filename,
                "phase": phase,
                "current": current,
                "total": total,
                "pct": pct,
                "status": status,
            }
            if chunks:
                payload["chunks"] = chunks
            if error:
                payload["error"] = error
            realtime.publish(
                redis_client, f"ingestion:{collection_id}", payload, snapshot_key=document_id
            )
            realtime.publish(redis_client, _QUEUE_TOPIC, payload, snapshot_key=document_id)
        except Exception:  # pragma: no cover - progress is best-effort
            logger.debug("progress emit failed", document_id=document_id, phase=phase)

    # --- Idempotency guard ---------------------------------------------------
    with session_factory() as session:
        row = session.execute(
            select(job_records.c.status).where(job_records.c.job_id == job_id)
        ).fetchone()
        if row is None:
            logger.warning("ingest: unknown job", job_id=job_id)
            return 0
        if row.status == "done":
            logger.info("ingest: already done", job_id=job_id)
            return 0
        if row.status == "processing":
            # Still beating → another worker is actively progressing it; back off.
            # No heartbeat → it stopped (crashed/lost), so reclaim it regardless of
            # how long it ran — long-but-progressing jobs keep their heartbeat alive.
            if _job_alive(redis_client, job_id):
                logger.info("ingest: already handling", job_id=job_id)
                return 0
            logger.warning("ingest: reclaiming job with no heartbeat", job_id=job_id)
        _set_job_status(
            session,
            job_id,
            "processing",
            processing_started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.commit()
    _beat(redis_client, job_id)  # first heartbeat, before any slow work begins

    # The finally drops this execution's heartbeat on ANY exit (done, failed, or a
    # worker restart killing the task) so the next delivery reclaims it cleanly
    # instead of seeing a stale "alive" beat and backing off.
    try:
        # --- Parse → chunk ---------------------------------------------------
        try:
            emit("parsing")
            parser = get_parser(file_type, config.chunking, parsers=config.parsers)
            chunks = _parse_with_progress(parser, file_path, document_id, emit)

            # --- Embed (batched) → upsert, resumable -------------------------
            if chunks:
                # Resume: skip chunks already stored for this document. Chunk ids are
                # deterministic (document_id + index), so re-parsing yields the same ids
                # and anything already in the store was embedded by a prior (interrupted)
                # run. If the embedding model changed since, the stored vectors are stale
                # → re-embed all (the per-batch upsert overwrites them by id).
                with session_factory() as _s:
                    doc_model = _s.execute(
                        select(documents.c.embedding_model).where(documents.c.id == document_id)
                    ).scalar()
                model_changed = doc_model is not None and doc_model != config.embedding.model
                existing_ids: set[str] = (
                    set()
                    if model_changed
                    else {cid for cid, _, _ in vector_store.iter_document_chunks(collection_id, document_id)}
                )

                _apply_effective_tags(session_factory, collection_id, document_id, chunks)
                if not existing_ids:  # fresh run only — auto-tagging is doc-level/expensive
                    _auto_tag_document(session_factory, collection_id, document_id, chunks, config)

                pending = [c for c in chunks if c.id not in existing_ids]
                total = len(chunks)
                done = total - len(pending)  # already persisted by a prior run
                if done:
                    logger.info("ingest: resuming", job_id=job_id, done=done, total=total)
                batch_size = config.embedding.batch_size
                emit("embedding", current=done, total=total)  # show the resume point at once
                for start in range(0, len(pending), batch_size):
                    batch = pending[start : start + batch_size]
                    vectors = embedder.embed_batch([c.text for c in batch])
                    vector_store.upsert(collection_id, batch, vectors)  # persist now → resumable
                    done += len(batch)
                    # Reveal the chunks this batch just embedded, as they occur.
                    emit(
                        "embedding",
                        current=done,
                        total=total,
                        chunks=[{"index": c.metadata.chunk_index, "label": _chunk_label(c)} for c in batch],
                    )
                emit("storing", current=total, total=total)
                # BM25/lexical is now the STORED tsvector column on `chunks`
                # (Phase 3) — populated automatically by the upsert above. No
                # separate corpus write.
        except Exception as exc:
            emit("failed", status="failed", error=str(exc)[:300])
            raise

        # --- Mark done -------------------------------------------------------
        chunk_count = len(chunks)
        with session_factory() as session:
            session.execute(
                update(documents)
                .where(documents.c.id == document_id)
                .values(
                    chunk_count=chunk_count,
                    embedding_model=config.embedding.model,
                    updated_at=_now(),
                )
            )
            _set_job_status(session, job_id, "done", chunk_count=chunk_count)
            session.commit()

        emit("done", current=chunk_count, total=chunk_count, status="done")
        logger.info("ingest complete", job_id=job_id, document_id=document_id, chunks=chunk_count)
        return chunk_count
    finally:
        _clear_beat(redis_client, job_id)


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
    """Remove vectors and the document row for a deleted document.

    Deleting the chunks drops their FTS ``text_tsv`` too (same rows), so there is
    no separate lexical index to prune.
    """
    try:
        _vector_store().delete_document(collection_id, document_id)
        with SessionLocal() as db:
            db.execute(sa_delete(documents).where(documents.c.id == document_id))
            db.commit()
            collection_empty = (
                db.execute(
                    select(documents.c.id)
                    .where(documents.c.collection_id == collection_id)
                    .limit(1)
                ).first()
                is None
            )
        # Last document gone: drop the vector collection so the next ingestion
        # recreates it at the current embedding dimension (model may have changed).
        if collection_empty:
            _vector_store().delete_collection(collection_id)
            logger.info("collection emptied; dropped vector collection", collection_id=collection_id)
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
    """No-op: lexical/BM25 is the STORED tsvector on ``chunks`` (Phase 3).

    The generated column keeps FTS current on every upsert, so there is nothing
    to rebuild. Kept as a task so the manual /index endpoint stays a valid
    (instantly-satisfied) call.
    """
    logger.info("bm25 index no-op (FTS auto-maintained)", document_id=document_id)


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def index_collection(self, collection_id: str) -> None:
    """No-op: lexical/BM25 is the STORED tsvector on ``chunks`` (Phase 3).

    See :func:`index_document` — the generated column makes a rebuild unnecessary.
    """
    logger.info("bm25 collection index no-op (FTS auto-maintained)", collection_id=collection_id)
