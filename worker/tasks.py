"""Celery ingestion tasks: parse → chunk → embed → store.

Lexical/BM25 is the STORED ``text_tsv`` generated column on ``chunks`` (Phase 3),
maintained automatically by the vector-store upsert — no Redis corpus write path.
"""

from __future__ import annotations

import inspect
import os
import re
import time
from collections import deque
from datetime import UTC, datetime, timedelta
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


def _claim_job(session_factory: Any, redis_client: Any, job_id: str) -> bool:
    """Atomically claim a job for processing; return False if it should be skipped.

    Skips an unknown job, one already ``done``, or one still ``processing`` with a
    fresh heartbeat (another worker is actively progressing it). A ``processing``
    job with no heartbeat has stopped (crashed/lost), so it is reclaimed regardless
    of how long it ran — long-but-advancing jobs keep their heartbeat alive.
    """
    with session_factory() as session:
        row = session.execute(
            select(job_records.c.status).where(job_records.c.job_id == job_id)
        ).fetchone()
        if row is None:
            logger.warning("ingest: unknown job", job_id=job_id)
            return False
        if row.status == "done":
            logger.info("ingest: already done", job_id=job_id)
            return False
        if row.status == "processing":
            if _job_alive(redis_client, job_id):
                logger.info("ingest: already handling", job_id=job_id)
                return False
            logger.warning("ingest: reclaiming job with no heartbeat", job_id=job_id)
        _set_job_status(
            session,
            job_id,
            "processing",
            processing_started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.commit()
    return True


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

    The page callback is *coalesced* before it reaches ``emit``: a large PDF fires
    ``on_progress`` once per page, so a 1400-page doc would otherwise push ~1400
    frames onto the queue socket during parsing alone. We forward at most one update
    per whole percent (plus always the final page), so the progress bar still
    advances smoothly with ~100 frames instead of one-per-page.
    """
    if "on_progress" not in inspect.signature(parser.parse).parameters:
        return parser.parse(file_path, document_id)

    last = 0

    def on_progress(current: int, total: int) -> None:
        nonlocal last
        step = max(1, total // 100)
        if current >= total or current - last >= step:
            last = current
            emit("parsing", current, total)

    return parser.parse(file_path, document_id, on_progress=on_progress)


def _chunk_label(chunk: Chunk) -> str:
    """Short human label for a chunk in the Ingestion Queue's live list."""
    md = chunk.metadata
    if md.heading_path:
        return md.heading_path[:80]
    if md.page_number is not None:
        return f"p.{md.page_number}"
    return " ".join(chunk.text.split())[:80]


class _RpmLimiter:
    """Sliding-window limiter keeping embedded texts-per-minute under a cap.

    Single-process and monotonic-clock: the worker runs ``--concurrency=1`` so one
    instance paces all its embedding. ``throttle(n, rpm)`` blocks until adding ``n``
    texts keeps the trailing-60s total at or under ``rpm`` (``rpm <= 0`` disables it).
    This lets a bulk re-embed run continuously just under the provider's quota instead
    of bursting into a 429 and stalling until the next retry sweep.
    """

    def __init__(self) -> None:
        self._events: deque[tuple[float, int]] = deque()  # (monotonic_ts, count)

    def _used(self, now: float) -> int:
        while self._events and now - self._events[0][0] >= 60.0:
            self._events.popleft()
        return sum(count for _, count in self._events)

    def throttle(self, n: int, rpm: int) -> None:
        if rpm <= 0 or n <= 0:
            return
        while True:
            now = time.monotonic()
            used = self._used(now)
            # Let the batch through once the window has room, or when it is empty (a
            # batch larger than the whole per-minute budget must not deadlock).
            if used + n <= rpm or not self._events:
                break
            wait = 60.0 - (now - self._events[0][0])
            time.sleep(max(0.05, min(wait, 5.0)))
        self._events.append((time.monotonic(), n))


# Module-level so the cap is honoured across successive ingest tasks in this worker.
_rpm_limiter = _RpmLimiter()


def _embed_and_store(
    chunks: list[Chunk],
    collection_id: str,
    document_id: str,
    *,
    session_factory: Any,
    embedder: Any,
    vector_store: Any,
    config: Any,
    emit: Any,
) -> None:
    """Resume-aware embed + upsert of a document's chunks, emitting progress.

    Skips chunks already stored for this document (deterministic ids make a
    re-parse yield the same ids, so anything present was embedded by a prior,
    interrupted run) — unless the embedding model changed, in which case the
    stored vectors are stale and all chunks are re-embedded (the per-batch upsert
    overwrites them by id). Auto-tagging runs once, on a fresh (non-resumed) run.
    """
    # Resume set: chunks already embedded with the CURRENT model. Only the rest are
    # (re-)embedded, so an interrupted run continues where it left off and a model
    # change re-embeds just the chunks still on the old model — each retry makes real
    # progress instead of restarting from chunk 0.
    model = config.embedding.model
    existing_ids: set[str] = vector_store.document_chunk_ids_at_model(
        collection_id, document_id, model
    )

    _apply_effective_tags(session_factory, collection_id, document_id, chunks)
    if not existing_ids:  # fresh run only — auto-tagging is doc-level/expensive
        _auto_tag_document(session_factory, collection_id, document_id, chunks, config)

    pending = [c for c in chunks if c.id not in existing_ids]
    total = len(chunks)
    done = total - len(pending)  # already embedded with the current model by a prior run
    if done:
        logger.info("ingest: resuming", document_id=document_id, done=done, total=total)
    batch_size = config.embedding.batch_size
    emit("embedding", current=done, total=total)  # show the resume point at once
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        _rpm_limiter.throttle(len(batch), config.embedding.max_rpm)  # stay under provider quota
        vectors = embedder.embed_batch([c.text for c in batch])
        vector_store.upsert(collection_id, batch, vectors, model=model)  # persist now → resumable
        done += len(batch)
        # Reveal the chunks this batch just embedded, as they occur.
        emit(
            "embedding",
            current=done,
            total=total,
            chunks=[{"index": c.metadata.chunk_index, "label": _chunk_label(c)} for c in batch],
        )
    emit("storing", current=total, total=total)


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
    storage: Any = None,
) -> int:
    """Run the full ingestion pipeline. Returns the number of chunks stored.

    ``file_path`` is a storage key (see api.services.storage); the document's
    bytes are fetched to a local temp file for parsing and released afterwards.
    For the default ``local`` backend that temp file IS the on-disk original and
    cleanup is a no-op, so behavior is unchanged.
    """
    from api.adapters.parsers import get_parser

    session_factory = session_factory or SessionLocal
    embedder = embedder or _embedder()
    vector_store = vector_store or _vector_store()
    redis_client = redis_client or _redis()
    config = config or get_config()

    # Identity for the queue view (filename + collection name) + the backend that
    # holds this document's bytes, resolved once. NULL storage_backend = legacy/local.
    with session_factory() as _s:
        filename = _s.execute(
            select(job_records.c.filename).where(job_records.c.job_id == job_id)
        ).scalar() or document_id
        collection_name = _s.execute(
            select(collections.c.name).where(collections.c.id == collection_id)
        ).scalar() or collection_id
        backend_name = _s.execute(
            select(documents.c.storage_backend).where(documents.c.id == document_id)
        ).scalar() or "local"
    if storage is None:
        from api.services.storage import get_storage

        storage = get_storage(config.storage, backend_name)

    def emit(
        phase: str,
        current: int | None = None,
        total: int | None = None,
        status: str = "processing",
        chunks: list[dict[str, Any]] | None = None,
        error: str | None = None,
        retry_at: str | None = None,
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
            if retry_at:
                payload["retry_at"] = retry_at
            realtime.publish(
                redis_client, f"ingestion:{collection_id}", payload, snapshot_key=document_id
            )
            realtime.publish(redis_client, _QUEUE_TOPIC, payload, snapshot_key=document_id)
        except Exception:  # pragma: no cover - progress is best-effort
            logger.debug("progress emit failed", document_id=document_id, phase=phase)

    # --- Idempotency guard ---------------------------------------------------
    if not _claim_job(session_factory, redis_client, job_id):
        return 0
    _beat(redis_client, job_id)  # first heartbeat, before any slow work begins

    # The finally drops this execution's heartbeat on ANY exit (done, failed, or a
    # worker restart killing the task) so the next delivery reclaims it cleanly
    # instead of seeing a stale "alive" beat and backing off. It also releases the
    # fetched temp file (a no-op for the local backend, which hands back the real one).
    local_file = None
    chunks: list[Chunk] = []  # bound before parsing so the except below can read len(chunks)
    try:
        # --- Parse → chunk ---------------------------------------------------
        try:
            # Pull the document's bytes from its backend to a local path to parse.
            local_file = storage.fetch_to_temp(file_path)
            emit("parsing")
            parser = get_parser(file_type, config.chunking, parsers=config.parsers)
            chunks = _parse_with_progress(parser, str(local_file), document_id, emit)

            # --- Embed (batched) → upsert, resumable -------------------------
            # BM25/lexical is the STORED tsvector column on `chunks` (Phase 3),
            # populated automatically by the upsert inside — no corpus write.
            if chunks:
                _embed_and_store(
                    chunks, collection_id, document_id,
                    session_factory=session_factory, embedder=embedder,
                    vector_store=vector_store, config=config, emit=emit,
                )
        except Exception as exc:
            # Progress reporting must never mask the original failure: the DB read + emit below
            # do I/O, and if one raised it would REPLACE ``exc`` on the way out, so the outer
            # ingest_document handler would misclassify a rate limit as a hard failure and lose
            # the infinite-resume path. Report best-effort, then always re-raise the original.
            try:
                if _is_rate_limit(exc):
                    # Report where it paused (chunks already at the current model) so the
                    # queue shows real progress (e.g. 128/1436), not 0. retry_at is when this
                    # task is scheduled to resume (matches the countdown ingest_document below
                    # passes to self.retry), so the queue can render a live countdown.
                    done = len(
                        vector_store.document_chunk_ids_at_model(
                            collection_id, document_id, config.embedding.model
                        )
                    )
                    retry_at = (
                        datetime.now(UTC) + timedelta(seconds=_retry_delay_seconds(exc))
                    ).isoformat()
                    emit(
                        "rate_limited",
                        current=done,
                        total=len(chunks),
                        status="rate_limited",
                        retry_at=retry_at,
                    )
                else:
                    emit("failed", status="failed", error=str(exc)[:300])
            except Exception:  # pragma: no cover - status reporting is best-effort
                logger.warning("failed to report ingest status", job_id=job_id)
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
        if local_file is not None:
            storage.cleanup_temp(local_file)


def _mark_failed(job_id: str, error: str) -> None:
    try:
        with SessionLocal() as session:
            _set_job_status(session, job_id, "failed", error=error[:2000])
            session.commit()
    except Exception:  # pragma: no cover - failure-path best effort
        logger.error("could not record job failure", job_id=job_id)


def _is_rate_limit(exc: BaseException) -> bool:
    """True if ``exc`` is an embedding-provider rate-limit / quota error (HTTP 429).

    Reliable signals first: a typed :class:`RateLimitError` (raised by adapters that
    detect a 429, e.g. Gemini) or an ``httpx.HTTPStatusError`` carrying status 429
    (OpenAI-compatible / Ollama backends). Only when neither is present does it fall back
    to matching a few *specific* rate-limit phrases — deliberately narrow (no bare
    "quota"/"429" substring) so an unrelated error isn't misclassified and then, because
    the rate-limit path retries indefinitely, retried forever.
    """
    import httpx

    from api.adapters.embeddings.errors import RateLimitError

    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    text = str(exc).lower()
    return any(
        s in text for s in ("resource_exhausted", "rate limit", "ratelimit", "too many requests")
    )


# Gemini/Google put the wait in a RetryInfo detail (``"retryDelay": "37.9s"``); other
# providers use an HTTP Retry-After. Match the seconds value from either.
_RETRY_DELAY_RE = re.compile(r'retry[_-]?(?:delay|after)"?\s*:?\s*"?(\d+)(?:\.\d+)?\s*s?', re.IGNORECASE)


def _retry_delay_seconds(exc: BaseException, default: int = 60, cap: int = 3600) -> int:
    """Seconds to wait before retrying, from the provider's suggested delay.

    Falls back to ``default`` when the error carries no hint, and is capped so a
    provider reporting a very long (e.g. daily-quota) delay still yields a sane timer.
    """
    match = _RETRY_DELAY_RE.search(str(exc))
    seconds = int(match.group(1)) + 2 if match else default  # +2s past the window edge
    return max(1, min(seconds, cap))


def _set_job_rate_limited(job_id: str, error: str) -> None:
    """Record that a job paused on a provider rate limit (retried by the beat sweep)."""
    try:
        with SessionLocal() as session:
            _set_job_status(session, job_id, "rate_limited", error=error[:2000])
            session.commit()
    except Exception:  # pragma: no cover - status write is best-effort
        logger.error("could not record rate-limit status", job_id=job_id)


def _retry_pending_key(job_id: str) -> str:
    return f"ingest:retry:{job_id}"


def _mark_retry_pending(job_id: str, delay: int) -> None:
    """Mark that a rate-limited job has a pending countdown retry, so the beat sweep
    leaves it alone until the delay elapses. The sweep only re-enqueues jobs whose marker
    has expired (orphaned by a worker crash), so it no longer double-fires alongside each
    job's own ``self.retry`` countdown. TTL outlives the delay slightly; best-effort.
    """
    try:
        _redis().set(_retry_pending_key(job_id), "1", ex=delay + 30)
    except Exception:  # pragma: no cover - best-effort
        logger.debug("retry-pending mark failed", job_id=job_id)


def _pending_job_ids(redis_client: Any, job_ids: list[str]) -> set[str]:
    """Job ids that still hold a live retry-pending marker — their countdown retry is scheduled,
    so the beat sweep leaves them alone. Fetched in ONE round-trip (MGET) rather than an
    ``exists`` per job. On a redis error return an empty set — fail open, so the sweep treats
    every job as orphaned and re-enqueues rather than stranding it.
    """
    if not job_ids:
        return set()
    try:
        marks = redis_client.mget([_retry_pending_key(j) for j in job_ids])
    except Exception:  # pragma: no cover - best-effort; fail open (re-enqueue) on redis error
        return set()
    return {job_id for job_id, mark in zip(job_ids, marks, strict=True) if mark is not None}


def _delete_stored_object(document_id: str, collection_id: str) -> None:
    """Delete a document's stored original from its backend (best-effort).

    Reads the backend + file type from the still-present row to rebuild the storage
    key, then deletes. Fully best-effort: any failure (row read or backend delete) is
    logged, not raised, so the vector/row cleanup still proceeds — a leaked object is
    strictly better than a stuck delete.
    """
    try:
        with SessionLocal() as db:
            meta = db.execute(
                select(documents.c.storage_backend, documents.c.file_type)
                .where(documents.c.id == document_id)
            ).fetchone()
        if meta is None:
            return
        from api.services.documents import document_key
        from api.services.storage import get_storage

        key = document_key(collection_id, document_id, meta.file_type)
        get_storage(get_config().storage, meta.storage_backend or "local").delete(key)
    except Exception as exc:  # best-effort: never block row/vector cleanup
        logger.warning("stored object delete skipped", document_id=document_id, error=str(exc))


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
        if _is_rate_limit(exc):
            # Provider quota reached — not a failure. Its partial progress is already
            # persisted (chunks embedded so far are upserted); reschedule this task to
            # resume after the provider's suggested delay. countdown gives a precise,
            # item-aligned retry time (surfaced to the queue as retry_at); max_retries
            # None keeps retrying until every chunk is embedded. The 5-min beat sweep
            # remains only as a backstop for a job orphaned by a worker crash.
            delay = _retry_delay_seconds(exc)
            logger.warning("ingest rate-limited; retrying", job_id=job_id, countdown=delay)
            _set_job_rate_limited(job_id, str(exc))
            _mark_retry_pending(job_id, delay)  # beat sweep skips it until the countdown is due
            raise self.retry(exc=exc, countdown=delay, max_retries=None) from exc
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
    no separate lexical index to prune. The stored original is removed first, while
    its backend + key are still recoverable from the row.
    """
    try:
        _delete_stored_object(document_id, collection_id)
        _vector_store().delete_document(collection_id, document_id)
        with SessionLocal() as db:
            db.execute(sa_delete(documents).where(documents.c.id == document_id))
            # Drop the job row too. Otherwise a paused (``rate_limited``) document that is
            # deleted or purged leaves an orphan job that the retry sweep keeps
            # re-enqueuing for a document that no longer exists.
            db.execute(sa_delete(job_records).where(job_records.c.document_id == document_id))
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


# Cap the rows swept per run so a large backlog drains over several sweeps instead of
# flooding the queue at once. ponytail: LIMIT 500/run — tighten the beat interval
# (PURGE_INTERVAL_SECONDS) if a backlog ever builds up.
_PURGE_BATCH = 500


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def purge_expired_documents(self) -> int:
    """Enqueue deletes for temporary documents whose retention window has elapsed.

    Selects rows with a due ``expires_at`` and fans each out to :func:`delete_document`,
    which already routes through ``get_storage(backend).delete(key)`` plus vector/row
    cleanup — one uniform purge path for local and every S3 backend (PR 4). Fanning out
    (rather than deleting inline) keeps this single worker free to interleave the deletes
    with ingestion. Idempotent: a document re-selected before its delete lands is simply
    enqueued again — delete is a no-op on an already-gone row — so no status tombstone is
    needed. Returns the number of deletes enqueued.
    """
    now = datetime.now(UTC).replace(tzinfo=None)  # naive UTC — matches the stored column
    with SessionLocal() as db:
        rows = db.execute(
            select(documents.c.id, documents.c.collection_id)
            .where(documents.c.expires_at.is_not(None), documents.c.expires_at <= now)
            .limit(_PURGE_BATCH)
        ).fetchall()
    for row in rows:
        delete_document.delay(row.id, row.collection_id)
    if rows:
        logger.info("purge: enqueued expired document deletes", count=len(rows))
    return len(rows)


_RETRY_BATCH = int(os.environ.get("RATE_LIMIT_RETRY_BATCH", "50"))


def requeue_rate_limited(limit: int = _RETRY_BATCH, *, respect_pending: bool = True) -> int:
    """Re-enqueue up to ``limit`` ingests paused at status ``rate_limited``.

    An ingest that hits the embedding provider's per-minute (or daily) limit stops with
    status ``rate_limited`` — not ``failed`` — leaving the document partly embedded. Each
    such job is re-enqueued under its existing ``job_id`` (so ``_claim_job`` re-claims it);
    the resume-aware pipeline embeds only the chunks not yet at the current model, so the
    document continues where it paused instead of restarting from chunk 0.

    ``respect_pending`` (the default, used by the beat sweep) skips jobs whose countdown
    retry is still scheduled, so the sweep only re-enqueues jobs whose retry marker has
    expired (orphaned by a worker crash) instead of re-firing every few minutes on top of
    each job's own ``self.retry`` countdown — which would hammer a provider that reported a
    long (e.g. daily-quota) delay. The on-config-change resume passes
    ``respect_pending=False`` to resume every paused job at once, since a new key / higher
    RPM has reset the quota. Idempotent either way: a job already re-claimed and processing
    is skipped by ``_claim_job``. Returns the number of ingests re-enqueued.
    """
    from api.services.documents import document_key

    redis_client = _redis() if respect_pending else None
    with SessionLocal() as db:
        rows = db.execute(
            select(
                job_records.c.job_id,
                job_records.c.document_id,
                job_records.c.collection_id,
                job_records.c.file_type,
            )
            .where(job_records.c.status == "rate_limited")
            .limit(limit)
        ).fetchall()
    pending = (
        _pending_job_ids(redis_client, [row.job_id for row in rows])
        if redis_client is not None
        else set()
    )
    requeued = 0
    for row in rows:
        if row.job_id in pending:
            continue  # its countdown retry is still scheduled — not orphaned, leave it
        key = document_key(row.collection_id, row.document_id, row.file_type)
        ingest_document.delay(row.job_id, key, row.collection_id, row.document_id, row.file_type)
        requeued += 1
    if requeued:
        logger.info("re-enqueued rate-limited ingests", count=requeued)
    return requeued


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def retry_rate_limited_ingests(self) -> int:
    """Beat sweep (every few minutes): re-enqueue ingests paused on a provider rate
    limit / quota — a backstop to each job's own retry countdown and to the immediate
    resume triggered the moment the embedding config changes. Returns the count swept.
    """
    return requeue_rate_limited()
