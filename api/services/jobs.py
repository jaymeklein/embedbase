"""Business logic for the ingestion-queue (job history) listing.

``api/routers/jobs.py`` stays routing-only; this owns the paginated, filtered read over the
``job_records`` table — the durable ingestion history that the live ``ingestion-queue`` WebSocket
only shows the recent tail of. Each row is joined to its collection name so a global, cross-workspace
queue view can label where a job came from.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import EMBEDDING_PAUSE_KEY
from api.db import collections as col_t
from api.db import job_records as job_t
from api.models.document import JobListQuery
from api.services.filters import to_conditions
from api.services.job_filters import build_specs


async def list_jobs(db: AsyncSession, query: JobListQuery) -> dict:
    """Return one filtered, paginated page of ingestion jobs across all collections.

    Ordering: actively-``processing`` jobs first (so the live card leads), then newest-first.

    Each row is a ``job_records`` entry left-joined to its collection name (``NULL`` when the
    collection was since deleted — the job still appears). Unlike the documents listing this keeps
    *every* job row (re-ingests and retries each show), because the queue is a history of attempts.
    Pagination and every (optional, AND-combined) filter come from ``query`` — see
    :class:`JobListQuery`; each filter is a ``FilterSpec`` in api/services/job_filters.py.

    Returns:
        ``{"items": [...], "total": N, "limit": L, "offset": O}`` — ``total`` is the full match
        count (for the pager); ``items`` is the requested page, each a mapping including
        ``collection_name`` and ``document_id`` (the latter lets the UI overlay live progress).
    """
    conds = await to_conditions(build_specs(query), db)

    # LEFT join so a job whose collection was deleted still lists (with a NULL collection_name);
    # the join is 1:1, so it doesn't change the job row count the pager reports. `.where(*conds)`
    # with no conds is a no-op, so an unfiltered call needs no special-casing.
    src = job_t.outerjoin(col_t, job_t.c.collection_id == col_t.c.id)

    total = (
        await db.execute(select(func.count()).select_from(src).where(*conds))
    ).scalar_one()
    rows = (
        await db.execute(
            select(
                job_t.c.job_id,
                job_t.c.document_id,
                job_t.c.collection_id,
                col_t.c.name.label("collection_name"),
                col_t.c.workspace_id.label("workspace_id"),
                job_t.c.filename,
                job_t.c.file_type,
                job_t.c.status,
                job_t.c.chunk_count,
                job_t.c.error,
                job_t.c.created_at,
                job_t.c.updated_at,
            )
            .select_from(src)
            .where(*conds)
            # Actively-ingesting jobs float to the top so the live card is the first thing seen;
            # the rest are newest-first, with job_id as a stable tiebreaker so rows sharing a
            # created_at can't shuffle across pages. Portable: the boolean sorts 1/0 on SQLite too.
            .order_by(
                (job_t.c.status == "processing").desc(),
                job_t.c.created_at.desc(),
                job_t.c.job_id.desc(),
            )
            .limit(query.limit)
            .offset(query.offset)
        )
    ).fetchall()
    return {
        "items": [dict(r._mapping) for r in rows],
        "total": total,
        "limit": query.limit,
        "offset": query.offset,
    }


async def job_status_counts(db: AsyncSession) -> dict[str, int]:
    """Count ingestion jobs per status, for the queue header's live totals — a real server-side
    depth that drains as jobs finish, unlike the WebSocket buffer that only ever accumulates.
    Statuses with no rows are simply absent from the returned map.
    """
    rows = (
        await db.execute(select(job_t.c.status, func.count()).group_by(job_t.c.status))
    ).all()
    return {status: count for status, count in rows}


def embedding_pause_seconds(redis_client: Any) -> int:
    """Seconds left on the worker's global embedding-quota backoff (0 when not paused). Lets the
    queue header explain *why* nothing is moving during a provider quota backoff. Best-effort: a
    missing client or a redis error reads as not-paused, so the stats endpoint never fails on it.
    """
    if redis_client is None:
        return 0
    try:
        ttl = redis_client.ttl(EMBEDDING_PAUSE_KEY)
    except Exception:
        return 0
    return ttl if ttl > 0 else 0
