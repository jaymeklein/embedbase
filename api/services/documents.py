"""Business logic for document ingestion and management.

Encapsulates all data-access and domain operations so that
api/routers/documents.py remains routing-only.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from fastapi import HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.parsers import SUPPORTED_EXTENSIONS
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.dependencies import get_app_config
from api.models.config import StorageConfig
from api.services import tasks as task_producer
from api.services.auth import Principal
from api.services.storage import get_storage

logger = structlog.get_logger()


def _storage_config() -> StorageConfig:
    """The active storage registry, or an all-local default when config is unset."""
    config = get_app_config()
    return config.storage if config else StorageConfig()


def _document_key(col_id: str, doc_id: str, ext: str) -> str:
    """The storage key for a document's bytes — the same layout used on disk."""
    return f"{col_id}/{doc_id}{ext}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
) -> dict:
    """Insert the document + job rows and enqueue the ingest task.

    Shared by the HTTP upload path (:func:`ingest`) and the MCP local-path path
    (:func:`ingest_local_path`). ``file_path`` is the storage key the worker
    resolves via the recorded ``storage_backend``. Returns a dict suitable for a
    202 response body.
    """
    now = _now()
    await db.execute(
        insert(doc_t).values(
            id=doc_id, collection_id=col_id, filename=filename, file_type=ext,
            file_size=size, chunk_count=None, storage_backend=storage_backend,
            created_at=now, updated_at=now,
        )
    )
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

    return {
        "job_id": job_id, "document_id": doc_id, "collection_id": col_id,
        "filename": filename, "file_type": ext, "file_size": size, "status": "pending",
    }


async def ingest(
    db: AsyncSession, col_id: str, file: UploadFile, principal: Principal
) -> dict:
    """Validate, stream, record, and enqueue an uploaded document for ingestion."""
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type: {ext!r}")

    doc_id = f"doc_{uuid4().hex[:12]}"
    job_id = f"job_{uuid4().hex[:12]}"
    config = get_app_config()
    max_bytes = config.max_file_size_bytes if config else None
    storage_cfg = _storage_config()
    key = _document_key(col_id, doc_id, ext)
    # put_upload streams through the same size guard before any bytes reach the
    # backend, so an oversize upload never lands remotely.
    size = await get_storage(storage_cfg).put_upload(file, key, max_bytes=max_bytes)
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=filename, ext=ext, size=size, file_path=key,
        storage_backend=storage_cfg.default,
    )


async def ingest_local_path(
    db: AsyncSession, col_id: str, file_path: str, principal: Principal
) -> dict:
    """Record + enqueue a container-local file for ingestion (MCP ingest tool).

    Unlike :func:`ingest`, the bytes are already on disk at ``file_path`` (a path
    the MCP client can see inside the container), so nothing is streamed.

    Raises:
        HTTPException: 403 if the principal cannot access the collection, 415 for
            an unsupported extension, or 404 if ``file_path`` does not exist.
    """
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")

    path = Path(file_path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type: {ext!r}")
    if not path.is_file():
        raise HTTPException(404, f"File not found: {file_path!r}")

    doc_id = f"doc_{uuid4().hex[:12]}"
    job_id = f"job_{uuid4().hex[:12]}"
    storage_cfg = _storage_config()
    key = _document_key(col_id, doc_id, ext)
    # Copy the on-disk file into the active backend so the worker reads it the
    # same way as an HTTP upload (local: copy under upload_dir; s3: upload).
    get_storage(storage_cfg).put_path(path, key)
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=path.name, ext=ext, size=path.stat().st_size, file_path=key,
        storage_backend=storage_cfg.default,
    )


def _dedupe_by_document(mappings: Any) -> list[dict]:
    """Keep one row per ``document_id`` — the first seen (latest job, per ordering)."""
    seen: set[str] = set()
    rows: list[dict] = []
    for mapping in mappings:
        row = dict(mapping)
        if row["document_id"] in seen:
            continue
        seen.add(row["document_id"])
        rows.append(row)
    return rows


async def list_documents(
    db: AsyncSession,
    col_id: str,
    tags: list[str] | None = None,
) -> list[dict]:
    """Return active documents in ``col_id`` with status, tags, and optional filter.

    Args:
        db: Active async database session.
        col_id: Collection whose documents to list.
        tags: Optional tag names; only documents carrying *all* of them are
            returned (AND filter).

    Returns:
        One mapping per active document including its ``status``, ``tags``, and an
        ``indexed`` bool (true once the document has stored/FTS-searchable chunks).
    """
    from api.services.tags import attach_tags, matching_entity_ids

    stmt = (
        select(
            doc_t.c.id.label("document_id"),
            doc_t.c.filename,
            doc_t.c.file_type,
            doc_t.c.file_size,
            doc_t.c.chunk_count,
            doc_t.c.embedding_model,
            doc_t.c.created_at,
            doc_t.c.updated_at,
            job_t.c.status,
        )
        .select_from(doc_t.outerjoin(job_t, job_t.c.document_id == doc_t.c.id))
        .where(doc_t.c.collection_id == col_id, doc_t.c.status.is_(None))
        # A document can have several job rows (re-ingest, retries); order so the
        # latest job is first, then keep one row per document below.
        .order_by(doc_t.c.created_at.desc(), job_t.c.created_at.desc())
    )
    if tags:
        stmt = stmt.where(doc_t.c.id.in_(await matching_entity_ids("document", tags, db)))
    rows = _dedupe_by_document(r._mapping for r in (await db.execute(stmt)).fetchall())
    rows = await attach_tags("document", rows, "document_id", db)
    for row in rows:
        # FTS-indexed once chunks are stored; chunk_count is set when ingestion finishes.
        row["indexed"] = bool(row.get("chunk_count"))
    return rows


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
    key = _document_key(row.collection_id, doc_id, row.file_type)

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
