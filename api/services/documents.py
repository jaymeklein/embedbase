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
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.parsers import SUPPORTED_EXTENSIONS
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.dependencies import get_app_config
from api.services import tasks as task_producer
from api.services.auth import Principal
from api.services.upload import stream_upload_with_size_guard
from api.settings import settings

logger = structlog.get_logger()


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
) -> dict:
    """Insert the document + job rows and enqueue the ingest task.

    Shared by the HTTP upload path (:func:`ingest`) and the MCP local-path path
    (:func:`ingest_local_path`). Returns a dict suitable for a 202 response body.
    """
    now = _now()
    await db.execute(
        insert(doc_t).values(
            id=doc_id, collection_id=col_id, filename=filename, file_type=ext,
            file_size=size, chunk_count=None, created_at=now, updated_at=now,
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
    dest = Path(settings.upload_dir) / col_id / f"{doc_id}{ext}"
    config = get_app_config()
    max_bytes = config.max_file_size_bytes if config else None
    size = await stream_upload_with_size_guard(file, dest, max_bytes=max_bytes)
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=filename, ext=ext, size=size, file_path=str(dest),
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
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=path.name, ext=ext, size=path.stat().st_size, file_path=str(path),
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
    redis_client: Any = None,
) -> list[dict]:
    """Return active documents in ``col_id`` with status, tags, and optional filter.

    Args:
        db: Active async database session.
        col_id: Collection whose documents to list.
        tags: Optional tag names; only documents carrying *all* of them are
            returned (AND filter).
        redis_client: When provided, each row gets an ``indexed`` bool reflecting
            BM25 corpus membership (omitted entirely when not provided).

    Returns:
        One mapping per active document including its ``status`` and ``tags``.
    """
    from api.services.indexing import indexed_doc_ids
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
    if redis_client is not None:
        indexed = indexed_doc_ids(redis_client, col_id)
        for row in rows:
            row["indexed"] = row["document_id"] in indexed
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


async def get_document_file(
    db: AsyncSession, doc_id: str, principal: Principal
) -> tuple[Path, str]:
    """Resolve a document's on-disk original file and its display filename.

    Args:
        db: Active async database session.
        doc_id: Document to open.
        principal: Caller; must be able to access the owning collection.

    Returns:
        ``(path, filename)`` — the stored file path and the original upload name.

    Raises:
        HTTPException: 404 if the document or its file is gone, 403 if the
            principal cannot access the owning collection.
    """
    row = (
        await db.execute(
            select(doc_t.c.collection_id, doc_t.c.filename, doc_t.c.file_type).where(
                doc_t.c.id == doc_id
            )
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if not principal.can_access(row.collection_id):
        raise HTTPException(403, "API key not valid for this collection")
    path = Path(settings.upload_dir) / row.collection_id / f"{doc_id}{row.file_type}"
    if not path.is_file():
        raise HTTPException(404, "Original file is no longer available")
    return path, row.filename


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
