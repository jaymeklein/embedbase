"""Business logic for document ingestion and management.

Encapsulates all data-access and domain operations so that
api/routers/documents.py remains routing-only.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from fastapi import HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.parsers import DOCLING_EXTENSIONS, supported_extensions
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.dependencies import get_app_config
from api.models.config import ParserConfig, StorageConfig
from api.models.document import DocumentListQuery
from api.services import tasks as task_producer
from api.services.auth import Principal
from api.services.document_filters import build_specs, latest_status_subquery
from api.services.filters import to_conditions
from api.services.storage import get_storage

logger = structlog.get_logger()


def _storage_config() -> StorageConfig:
    """The active storage registry, or an all-local default when config is unset."""
    config = get_app_config()
    return config.storage if config else StorageConfig()


def _reject_unsupported(ext: str, parsers: ParserConfig | None) -> None:
    """Raise 415 when ``ext`` is not ingestible under the active parser config.

    Office formats (``.docx``/``.pptx``) require the docling backend, so when they are rejected
    only because docling is off, the message points at the switch that enables them — the upload
    fails here, at the API boundary, instead of later in the worker on a missing dependency.
    """
    if ext in supported_extensions(parsers):
        return
    # Reaching here for an office format means docling is not configured (else it would be
    # supported), so point at the switch that enables it.
    base = f"Unsupported file type: {ext!r}"
    if ext in DOCLING_EXTENSIONS:
        raise HTTPException(
            415,
            f"{base}. Office formats (.docx/.pptx) need the docling parser backend — "
            "set parsers.pdf_backend to 'docling' to enable them.",
        )
    raise HTTPException(415, base)


def document_key(col_id: str, doc_id: str, ext: str) -> str:
    """The storage key for a document's bytes — the single source of truth for the key
    layout, shared by the API upload/delete paths and the worker's resume/purge sweeps.
    Import it rather than re-encoding ``{col}/{doc}{ext}`` so the scheme lives in one place.
    """
    return f"{col_id}/{doc_id}{ext}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _expiry(storage_cfg: StorageConfig, temporary: bool) -> datetime | None:
    """Absolute purge time for a temporary upload, or None (permanent).

    A temporary upload lives ``temp_retention_hours`` from now; the feature is off
    (returns None) when the upload isn't flagged temporary or retention is 0 — so a
    ``temporary=true`` upload with retention 0 is byte-identical to a normal one.
    Naive UTC to match the stored column (see api/tables/documents.py).
    """
    hours = storage_cfg.temp_retention_hours
    if not temporary or hours <= 0:
        return None
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=hours)


async def resolve_collection(
    db: AsyncSession, col_id: str, ws_id: str | None = None
) -> str:
    """Return the workspace id for ``col_id``, validating ``ws_id`` if given."""
    row = (
        await db.execute(
            select(col_t.c.id, col_t.c.workspace_id).where(col_t.c.id == col_id)
        )
    ).fetchone()
    if not row or (ws_id is not None and row.workspace_id != ws_id):
        raise HTTPException(404, f"Collection {col_id!r} not found")
    return row.workspace_id


async def _persist_and_enqueue(
    db: AsyncSession,
    *,
    col_id: str,
    doc_id: str,
    job_id: str,
    filename: str,
    ext: str,
    size: int,
    file_path: str,
    storage_backend: str,
    expires_at: datetime | None = None,
) -> dict:
    """Insert the document + job rows and enqueue the ingest task.

    Shared by the HTTP upload path (:func:`ingest`) and the MCP local-path path
    (:func:`ingest_local_path`). ``file_path`` is the storage key the worker
    resolves via the recorded ``storage_backend``. ``expires_at`` (NULL = permanent)
    stamps a temporary document for the worker purge sweep. Returns a dict suitable
    for a 202 response body.
    """
    now = _now()
    await db.execute(
        insert(doc_t).values(
            id=doc_id, collection_id=col_id, filename=filename, file_type=ext,
            file_size=size, chunk_count=None, storage_backend=storage_backend,
            expires_at=expires_at, created_at=now, updated_at=now,
        )
    )
    await _create_pending_job_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=filename, ext=ext, file_path=file_path,
    )
    return {
        "job_id": job_id, "document_id": doc_id, "collection_id": col_id,
        "filename": filename, "file_type": ext, "file_size": size, "status": "pending",
    }


async def _create_pending_job_and_enqueue(
    db: AsyncSession,
    *,
    col_id: str,
    doc_id: str,
    job_id: str,
    filename: str,
    ext: str,
    file_path: str,
) -> None:
    """Insert a ``pending`` job row for ``doc_id`` and dispatch the ingest task, recording the
    celery task id back onto the row. Shared by the initial upload (:func:`_persist_and_enqueue`)
    and the reprocess-after-failure path (:func:`reprocess_document`). Commits the session, so any
    caller's still-uncommitted document insert lands with it.
    """
    now = _now()
    await db.execute(
        insert(job_t).values(
            job_id=job_id, document_id=doc_id, collection_id=col_id, filename=filename,
            file_type=ext, status="pending", created_at=now, updated_at=now,
        )
    )
    await db.commit()
    task_id = task_producer.enqueue_ingest(job_id, file_path, col_id, doc_id, ext)
    if task_id:
        await db.execute(
            update(job_t).where(job_t.c.job_id == job_id).values(celery_task_id=task_id)
        )
        await db.commit()


async def reprocess_document(db: AsyncSession, col_id: str, doc_id: str) -> dict:
    """Re-enqueue a document's ingestion — the manual retry for a failed (or otherwise stuck) file.

    A fresh ``pending`` job row is created (the prior failed attempt stays in the queue history) and
    an ingest task dispatched; the resume-aware pipeline re-embeds from where a prior run stopped, or
    from scratch. The document's stored bytes are reused — nothing is re-uploaded — so a file whose
    bytes are genuinely gone simply fails again with a clear error rather than being lost.
    """
    row = (
        await db.execute(
            select(doc_t.c.filename, doc_t.c.file_type, doc_t.c.status).where(
                doc_t.c.id == doc_id, doc_t.c.collection_id == col_id
            )
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if row.status == "deleting":
        raise HTTPException(409, "Document is being deleted; cannot reprocess")
    # Don't pile a duplicate onto a document that's already queued or running — that attempt will
    # finish, or the beat sweep resumes it. Minting a second job_id here would defeat _claim_job's
    # dedup (it keys on job_id) and, under a multi-process worker, embed the document twice. Return
    # the in-flight job instead (idempotent against a double-click or a stale "failed" button).
    latest = (
        await db.execute(
            select(job_t.c.job_id, job_t.c.status)
            .where(job_t.c.document_id == doc_id)
            .order_by(job_t.c.created_at.desc())
            .limit(1)
        )
    ).fetchone()
    if latest is not None and latest.status in ("pending", "processing", "rate_limited"):
        return {
            "job_id": latest.job_id, "document_id": doc_id, "collection_id": col_id,
            "filename": row.filename, "file_type": row.file_type, "status": latest.status,
        }
    job_id = f"job_{uuid4().hex[:12]}"
    await _create_pending_job_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=row.filename, ext=row.file_type,
        file_path=document_key(col_id, doc_id, row.file_type),
    )
    return {
        "job_id": job_id, "document_id": doc_id, "collection_id": col_id,
        "filename": row.filename, "file_type": row.file_type, "status": "pending",
    }


async def ingest(
    db: AsyncSession, col_id: str, file: UploadFile, principal: Principal,
    *, temporary: bool = False,
) -> dict:
    """Validate, stream, record, and enqueue an uploaded document for ingestion.

    When ``temporary`` is set and ``storage.temp_retention_hours > 0`` the document
    is stamped with an ``expires_at`` and later auto-purged by the worker sweep.
    """
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    config = get_app_config()
    _reject_unsupported(ext, config.parsers if config else None)

    doc_id = f"doc_{uuid4().hex[:12]}"
    job_id = f"job_{uuid4().hex[:12]}"
    max_bytes = config.max_file_size_bytes if config else None
    storage_cfg = _storage_config()
    key = document_key(col_id, doc_id, ext)
    # put_upload streams through the same size guard before any bytes reach the
    # backend, so an oversize upload never lands remotely.
    size = await get_storage(storage_cfg).put_upload(file, key, max_bytes=max_bytes)
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=filename, ext=ext, size=size, file_path=key,
        storage_backend=storage_cfg.default,
        expires_at=_expiry(storage_cfg, temporary),
    )


async def ingest_local_path(
    db: AsyncSession, col_id: str, file_path: str, principal: Principal,
    *, temporary: bool = False,
) -> dict:
    """Record + enqueue a container-local file for ingestion (MCP ingest tool).

    Unlike :func:`ingest`, the bytes are already on disk at ``file_path`` (a path
    the MCP client can see inside the container), so nothing is streamed. ``temporary``
    behaves as in :func:`ingest` (stamps ``expires_at`` when retention is enabled).

    Raises:
        HTTPException: 403 if the principal cannot access the collection, 415 for
            an unsupported extension, or 404 if ``file_path`` does not exist.
    """
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")

    path = Path(file_path)
    ext = path.suffix.lower()
    config = get_app_config()
    _reject_unsupported(ext, config.parsers if config else None)
    if not path.is_file():
        raise HTTPException(404, f"File not found: {file_path!r}")

    doc_id = f"doc_{uuid4().hex[:12]}"
    job_id = f"job_{uuid4().hex[:12]}"
    storage_cfg = _storage_config()
    key = document_key(col_id, doc_id, ext)
    # Copy the on-disk file into the active backend so the worker reads it the
    # same way as an HTTP upload (local: copy under upload_dir; s3: upload).
    get_storage(storage_cfg).put_path(path, key)
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=path.name, ext=ext, size=path.stat().st_size, file_path=key,
        storage_backend=storage_cfg.default,
        expires_at=_expiry(storage_cfg, temporary),
    )


async def list_documents(db: AsyncSession, col_id: str, query: DocumentListQuery) -> dict:
    """Return one filtered, paginated page of active documents in ``col_id``.

    Each document appears once, carrying its *latest* job ``status`` (a scalar-subquery pick of
    the most recent ``job_records`` row — a document accrues several as it re-ingests/retries),
    its tags, and an ``indexed`` flag (true once chunks are stored). Newest-first. Pagination and
    every (optional, AND-combined) filter come from ``query`` — see :class:`DocumentListQuery`.

    Returns:
        ``{"items": [...], "total": N, "limit": L, "offset": O}`` — ``total`` is the full match
        count (for the pager); ``items`` is the requested page, each a mapping with ``status``,
        ``tags``, and ``indexed``.
    """
    from api.services.tags import attach_tags

    latest_status = latest_status_subquery()

    # Base scope (collection + not soft-deleted), then each active filter as a spec. A new filter
    # is a new FilterSpec in api/services/document_filters.py, not another branch here.
    conds = [
        doc_t.c.collection_id == col_id,
        doc_t.c.status.is_(None),
        *await to_conditions(build_specs(query), db),
    ]
    where = and_(*conds)

    total = (await db.execute(select(func.count()).select_from(doc_t).where(where))).scalar_one()
    rows = (
        await db.execute(
            select(
                doc_t.c.id.label("document_id"),
                doc_t.c.filename,
                doc_t.c.file_type,
                doc_t.c.file_size,
                doc_t.c.chunk_count,
                doc_t.c.embedding_model,
                doc_t.c.storage_backend,
                doc_t.c.created_at,
                doc_t.c.updated_at,
                latest_status.label("status"),
            )
            .where(where)
            # id as a stable tiebreaker so rows sharing a created_at can't shuffle across pages.
            .order_by(doc_t.c.created_at.desc(), doc_t.c.id.desc())
            .limit(query.limit)
            .offset(query.offset)
        )
    ).fetchall()
    items = [dict(r._mapping) for r in rows]
    items = await attach_tags("document", items, "document_id", db)
    for row in items:
        # FTS-indexed once chunks are stored; chunk_count is set when ingestion finishes.
        row["indexed"] = bool(row.get("chunk_count"))
    return {"items": items, "total": total, "limit": query.limit, "offset": query.offset}


async def get_document_status(
    db: AsyncSession, col_id: str, doc_id: str
) -> dict:
    """Return current status for ``doc_id``, including soft-delete state."""
    doc_row = (
        await db.execute(
            select(doc_t.c.status).where(
                doc_t.c.id == doc_id, doc_t.c.collection_id == col_id
            )
        )
    ).fetchone()
    if doc_row and doc_row.status == "deleting":
        return {"status": "deleting", "document_id": doc_id}
    row = (
        await db.execute(
            select(job_t)
            .where(job_t.c.document_id == doc_id, job_t.c.collection_id == col_id)
            .order_by(job_t.c.created_at.desc())
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"No job found for document {doc_id!r}")
    return dict(row._mapping)


async def delete_document(db: AsyncSession, col_id: str, doc_id: str) -> None:
    """Soft-delete a document and enqueue async vector / BM25 / row cleanup.

    Marks the document row as ``status='deleting'`` instead of removing it so
    the worker has a durable tombstone to retry against if the first cleanup
    attempt fails. The worker hard-deletes the row after all stores are clean.
    """
    result: Any = await db.execute(
        update(doc_t)
        .where(doc_t.c.id == doc_id, doc_t.c.collection_id == col_id, doc_t.c.status.is_(None))
        .values(status="deleting", updated_at=_now())
    )
    if result.rowcount == 0:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    await db.execute(delete(job_t).where(job_t.c.document_id == doc_id))
    await db.commit()
    try:
        task_id = task_producer.enqueue_delete(doc_id, col_id)
        if task_id:
            logger.info("delete task enqueued", document_id=doc_id, celery_task_id=task_id)
    except Exception:
        await db.execute(
            update(doc_t).where(doc_t.c.id == doc_id).values(status=None, updated_at=_now())
        )
        await db.commit()
        raise HTTPException(503, "Cleanup queue unavailable, please retry") from None


async def resolve_document_download(
    db: AsyncSession, doc_id: str, principal: Principal
) -> Response:
    """Build the download Response for a document's original bytes.

    Resolves the backend that holds the document (``storage_backend``; NULL =
    legacy/local) and returns the right Response for it: a 302 redirect to a
    short-lived presigned URL for S3-backed documents, or an inline
    ``FileResponse`` for local ones. Building the Response here (rather than in
    the router) keeps the router routing-only.

    Args:
        db: Active async database session.
        doc_id: Document to open.
        principal: Caller; must be able to access the owning collection.

    Returns:
        A ``RedirectResponse`` (S3) or ``FileResponse`` (local) for the bytes.

    Raises:
        HTTPException: 404 if the document or its file is gone, 403 if the
            principal cannot access the owning collection.
    """
    row = (
        await db.execute(
            select(
                doc_t.c.collection_id, doc_t.c.filename,
                doc_t.c.file_type, doc_t.c.storage_backend,
            ).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if not principal.can_access(row.collection_id):
        raise HTTPException(403, "API key not valid for this collection")

    # NULL storage_backend = ingested before the column existed → bytes on local disk.
    storage = get_storage(_storage_config(), row.storage_backend or "local")
    key = document_key(row.collection_id, doc_id, row.file_type)

    url = storage.presigned_get(key, row.filename)
    if url is not None:
        return RedirectResponse(url, status_code=302)

    path = storage.local_path(key)
    if path is None or not path.is_file():
        raise HTTPException(404, "Original file is no longer available")
    return FileResponse(path, filename=row.filename, content_disposition_type="inline")


async def resolve_document_collection(db: AsyncSession, doc_id: str) -> str:
    """Return the collection_id that owns ``doc_id``, raising 404 if absent."""
    row = (
        await db.execute(
            select(doc_t.c.collection_id).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    return row.collection_id
