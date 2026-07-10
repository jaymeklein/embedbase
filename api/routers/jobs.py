"""Ingestion-queue (job history) listing endpoint.

Routing-only: path registration, dependency resolution, delegation. All logic lives in
api/services/jobs.py. The list spans every collection (a global admin view, mirroring
``/indexing/status``), so it requires the master key.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_redis_client
from api.services import jobs as jobs_svc
from api.services.auth import require_master

router = APIRouter(tags=["jobs"])


@router.get("/ingestion/jobs")
async def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    filename: str | None = Query(default=None),
    file_type: str | None = Query(default=None),
    collection: str | None = Query(default=None),
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    _principal: object = Depends(require_master),
    db: AsyncSession = Depends(get_db),
):
    """List ingestion jobs across all collections: paginated, filtered, newest-first.

    Returns ``{items, total, limit, offset}``. All filters are optional and AND-combined;
    ``filename`` and ``collection`` are case-insensitive substrings, ``status`` and ``file_type``
    are exact, and the ``created_*`` bounds are inclusive ISO-8601 (a date-only ``created_before``
    covers the whole day).
    """
    return await jobs_svc.list_jobs(
        db,
        limit=limit,
        offset=offset,
        status=status,
        filename=filename,
        file_type=file_type,
        collection=collection,
        created_after=created_after,
        created_before=created_before,
    )


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
