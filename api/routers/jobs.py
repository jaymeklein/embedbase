"""Ingestion-queue (job history) listing endpoint.

Routing-only: path registration, dependency resolution, delegation. All logic lives in
api/services/jobs.py. The list spans every collection (a global admin view, mirroring
``/indexing/status``), so it requires the master key.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_redis_client
from api.models.document import JobListQuery
from api.services import jobs as jobs_svc
from api.services.auth import require_master

router = APIRouter(tags=["jobs"])


@router.get("/ingestion/jobs")
async def list_jobs(
    query: Annotated[JobListQuery, Query()],
    _principal: object = Depends(require_master),
    db: AsyncSession = Depends(get_db),
):
    """List ingestion jobs across all collections: paginated, filtered, newest-first.

    Returns ``{items, total, limit, offset}``. All filters are optional and AND-combined;
    ``filename`` and ``collection`` are case-insensitive substrings, ``status`` and ``file_type``
    are exact, and the ``created_*`` bounds are inclusive ISO-8601 (a date-only ``created_before``
    covers the whole day). See :class:`JobListQuery` for the full set of query parameters.
    """
    return await jobs_svc.list_jobs(db, query)


@router.get("/ingestion/jobs/stats")
async def job_stats(
    _principal: object = Depends(require_master),
    db: AsyncSession = Depends(get_db),
    redis_client: Any = Depends(get_redis_client),
):
    """Live queue totals for the header: per-status job counts plus the global embedding-quota
    backoff, so the UI can show a real depth that *drains* (not the accumulating WebSocket buffer)
    and explain when ingestion is paused waiting on a provider rate limit.

    Returns ``{"counts": {status: n, ...}, "paused_seconds": n}`` (``paused_seconds`` 0 = running).
    """
    return {
        "counts": await jobs_svc.job_status_counts(db),
        # Offload the sync redis TTL read to a thread (repo convention, cf. api/routers/config.py):
        # the client has no socket_timeout, so a redis stall on the event loop would hang every
        # concurrent request, not just this one.
        "paused_seconds": await asyncio.to_thread(jobs_svc.embedding_pause_seconds, redis_client),
    }


@router.post("/ingestion/jobs/retry-failed", status_code=202)
async def retry_failed_jobs(
    query: Annotated[JobListQuery, Query()],
    _principal: object = Depends(require_master),
    db: AsyncSession = Depends(get_db),
):
    """Re-enqueue every currently-failed document matching the given filters — the queue's bulk
    "retry all errors". Targets documents whose *latest* ingestion attempt failed; the ``status``
    filter is ignored (forced to failed) while the other filters (filename, file_type, collection,
    created range) scope the set exactly like the listing. Idempotent per document.

    Returns ``{"retried": n}`` — how many documents were re-enqueued.
    """
    return await jobs_svc.reprocess_failed_documents(db, query)
