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

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from api.constants import EMBEDDING_PAUSE_KEY
from api.constants import REDIS_URL as _REDIS_URL_DEFAULT
from api.services import realtime
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


def reload_adapters(prior_embedding: Any = None) -> None:
    """Rebuild the embedder + vector-store singletons from the current config.

    Called by the config hot-reload listener after ``get_config.cache_clear()`` so a live config
    change takes effect without restarting the worker. Building here (rather than nulling the
    singletons for lazy rebuild) surfaces a bad config immediately, so the listener can ack an error
    and the API can roll back.

    A provider rate limit (HTTP 429) is the exception: it is transient, not a bad config, and must
    never roll a config save back. ``prior_embedding`` (the embedding config from *before* this
    reload, passed by the listener) lets the store keep its size when the model is unchanged — the
    dimension can't have changed, so :func:`resolve_dimensions` reuses the live store's size. When
    even that isn't possible (this worker hasn't built a store yet, or the model itself changed), the
    store is *deferred*: the new embedder is still adopted (so a rotated key / raised ``max_rpm``
    takes effect) and the store is nulled for a lazy rebuild on next use, when the provider should be
    reachable. Ingestion already pauses + retries on 429, so a deferred store is safe. Any *other*
    build failure (a bad model / key — not a 429) still propagates, so the API rolls back.
    """
    global _embedder_singleton, _vector_store_singleton
    from api.adapters.embeddings import get_embedding_adapter, resolve_dimensions
    from api.adapters.embeddings.errors import RateLimitError
    from api.adapters.vector_store import get_vector_store

    config = get_config()
    prior_store = _vector_store_singleton  # still the previous store; its size is the 429 fallback
    embedder = get_embedding_adapter(config.embedding)
    try:
        dimensions = resolve_dimensions(
            embedder,
            config.embedding,
            prior_embedding,
            prior_store.dimensions if prior_store is not None else None,
        )
    except RateLimitError:
        _embedder_singleton = embedder
        _vector_store_singleton = None  # defer: lazy rebuild re-probes when the provider is reachable
        return
    _embedder_singleton = embedder
    _vector_store_singleton = get_vector_store(config.vector_store, dimensions)


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
    overwrites them by id).
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
        col_row = _s.execute(
            select(collections.c.name, collections.c.workspace_id).where(
                collections.c.id == collection_id
            )
        ).fetchone()
        collection_name = (col_row.name if col_row else None) or collection_id
        workspace_id = col_row.workspace_id if col_row else None  # for the queue's "open file" link
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
                "workspace_id": workspace_id,
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


# --- Global embedding-quota circuit breaker --------------------------------------------------
# One shared backoff timer so a single provider quota/429 can't snowball into a retry storm.
# Without it, every one of N queued documents independently re-hit the exhausted quota on its own
# countdown — turning one 429 into thousands of quota-burning retries that never let anything
# finish (and only ever grow the "waiting" count). Instead the first 429 pauses ALL ingestion for
# the provider's suggested delay: the beat sweep skips while paused, so no NEW work is enqueued, and
# any task already in flight just marks its job ``rate_limited`` and stops. The first sweep after the
# pause lifts re-enqueues a BOUNDED batch (``_RETRY_BATCH``); if the quota is still out, the first
# 429 in that batch re-arms the pause. Stored as a TTL key so it self-clears when the window elapses
# and survives a worker restart. The key name is shared with the API (constants.EMBEDDING_PAUSE_KEY)
# so the queue-stats endpoint can read the remaining backoff.
_PAUSE_KEY = EMBEDDING_PAUSE_KEY


def _pause_embedding(seconds: int) -> None:
    """Back ALL ingestion off for ``seconds`` after a provider rate-limit/quota 429."""
    try:
        _redis().set(_PAUSE_KEY, "1", ex=max(1, seconds))
    except Exception:  # pragma: no cover - best-effort; a missed pause just costs one more 429
        logger.debug("embedding pause mark failed")


def _embedding_paused_seconds() -> int:
    """Seconds until embedding un-pauses (0 when not paused), read from the pause key's TTL. Fails
    open (0) on a redis error so a redis blip can't wedge ingestion shut."""
    try:
        ttl = _redis().ttl(_PAUSE_KEY)
    except Exception:  # pragma: no cover - best-effort; fail open
        return 0
    return ttl if ttl > 0 else 0


def clear_embedding_pause() -> None:
    """Lift the global embedding pause — e.g. after a config change (new key / raised limit) resets
    the provider quota, so paused ingests should resume instead of waiting out the backoff."""
    try:
        _redis().delete(_PAUSE_KEY)
    except Exception:  # pragma: no cover - best-effort
        logger.debug("embedding pause clear failed")


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
            # Provider quota reached — NOT a failure. The chunks embedded so far are already
            # persisted, so leave the job at status ``rate_limited`` (resume-aware) and pause the
            # WHOLE worker for the provider's suggested delay: the beat sweep skips while paused, so
            # a single 429 can't snowball into every queued document re-hitting the exhausted quota.
            # The beat sweep (gated on that pause) re-enqueues this job as a FRESH task once the
            # backoff lifts. Deliberately NOT self.retry: its max_retries=None resolves to the task's
            # cap of 3 in Celery, so retrying would raise MaxRetriesExceeded and strand the doc after
            # three windows — with acks_late the failed task is acked and nothing re-enqueues it.
            delay = _retry_delay_seconds(exc)
            logger.warning("ingest rate-limited; pausing worker", job_id=job_id, backoff=delay)
            _pause_embedding(delay)  # back the whole worker off, not just this document
            _set_job_rate_limited(job_id, str(exc))
            return None  # ack this delivery; the beat sweep resumes it as a fresh task, uncapped
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


def _requeue_by_status(status: str, limit: int, *, only_if_stale: bool = False) -> int:
    """Re-enqueue up to ``limit`` jobs at ``status`` as FRESH ingest tasks (new Celery lineage, so
    they never hit a task's bounded ``max_retries``). Each keeps its ``job_id`` so ``_claim_job``
    re-claims it and the resume-aware pipeline continues from the chunks already embedded — never
    from chunk 0. Idempotent: a job already processing/done is skipped by ``_claim_job``.

    ``only_if_stale`` (for ``processing`` recovery) skips jobs with a LIVE heartbeat — those are
    actively being worked by another delivery, not stuck. Returns the number re-enqueued.

    Oldest-touched first (``updated_at`` ascending) so a backlog larger than ``limit`` rotates fairly
    across sweeps — a re-attempt bumps ``updated_at``, moving the job to the back — instead of the
    same ``limit`` rows being re-selected every tick while the rest starve.
    """
    from api.services.documents import document_key

    redis_client = _redis() if only_if_stale else None
    with SessionLocal() as db:
        rows = db.execute(
            select(
                job_records.c.job_id,
                job_records.c.document_id,
                job_records.c.collection_id,
                job_records.c.file_type,
            )
            .where(job_records.c.status == status)
            .order_by(job_records.c.updated_at.asc())
            .limit(limit)
        ).fetchall()
    requeued_ids: list[str] = []
    for row in rows:
        if only_if_stale and _job_alive(redis_client, row.job_id):
            continue  # a fresh heartbeat means it's actively progressing — leave it be
        key = document_key(row.collection_id, row.document_id, row.file_type)
        ingest_document.delay(row.job_id, key, row.collection_id, row.document_id, row.file_type)
        requeued_ids.append(row.job_id)
    if requeued_ids:
        # Bump updated_at so the oldest-first ORDER BY rotates these to the BACK. updated_at
        # otherwise only moves when a job is claimed/settled, so a slow drain (worker busy on one
        # big doc) would re-select the SAME limit rows every tick and pile unbounded duplicate
        # deliveries onto the broker. Rotating means each sweep re-enqueues a different batch, so
        # the in-flight duplicates stay bounded by the backlog size (and _claim_job dedups them).
        with SessionLocal() as db:
            db.execute(
                update(job_records)
                .where(job_records.c.job_id.in_(requeued_ids))
                .values(updated_at=_now())
            )
            db.commit()
        logger.info("re-enqueued ingests", status=status, count=len(requeued_ids))
    return len(requeued_ids)


def requeue_rate_limited(limit: int = _RETRY_BATCH) -> int:
    """Re-enqueue ingests paused at status ``rate_limited`` (hit the provider's per-minute or daily
    limit, stopped mid-document). Resumed as fresh tasks, uncapped."""
    return _requeue_by_status("rate_limited", limit)


def requeue_stuck_processing(limit: int = _RETRY_BATCH) -> int:
    """Resume ingests stuck at status ``processing`` with a stale heartbeat — a worker that crashed
    or restarted mid-embed leaves the job ``processing`` with no live ``ingest:hb:`` key and, unlike
    a rate-limited job, no retry pending. Rather than tell the user to re-ingest it themselves, we
    pick it back up automatically. A job with a live heartbeat is actively progressing and is left
    alone."""
    return _requeue_by_status("processing", limit, only_if_stale=True)


def requeue_orphaned_pending(limit: int = _RETRY_BATCH) -> int:
    """Resume ingests stuck at status ``pending`` — enqueued but with no live task (their Celery
    message was lost to a broker purge or a worker restart that dropped an unstarted delivery).
    Nothing else re-enqueues ``pending``, so without this a file the user uploaded would sit queued
    forever. Re-enqueuing one that DOES still have its task is harmless: ``_claim_job`` dedups at run
    (the loser sees ``processing``/``done`` and skips), so a freshly-uploaded pending doc isn't
    double-ingested."""
    return _requeue_by_status("pending", limit)


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def retry_rate_limited_ingests(self) -> int:
    """Beat sweep (every few minutes): resume every ingest the worker can't finish on its own — jobs
    paused on a provider rate limit / quota, jobs stuck ``processing`` after a worker crash, and jobs
    orphaned at ``pending`` (lost task). All are re-enqueued as fresh tasks, so a file the user handed
    to the queue is never silently dropped. Returns the count swept.
    """
    if _embedding_paused_seconds() > 0:
        # A global quota backoff is in effect — re-enqueuing would just pause again. Wait it out: the
        # pause TTL self-clears, and the next sweep resumes everything.
        return 0
    return requeue_rate_limited() + requeue_stuck_processing() + requeue_orphaned_pending()
