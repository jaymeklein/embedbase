"""Business logic for document ingestion and management.

Encapsulates all data-access and domain operations so that
api/routers/documents.py remains routing-only.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.parsers import SUPPORTED_EXTENSIONS
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.services import tasks as task_producer
from api.services.auth import Principal
from api.services.upload import stream_upload_with_size_guard
from api.settings import settings


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


async def ingest(
    db: AsyncSession, col_id: str, file: UploadFile, principal: Principal
) -> dict:
    """Validate, stream, record, and enqueue a document for ingestion.

    Returns a dict suitable for the 202 response body.
    """
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type: {ext!r}")

    doc_id = f"doc_{uuid4().hex[:12]}"
    job_id = f"job_{uuid4().hex[:12]}"
    dest = Path(settings.upload_dir) / col_id / f"{doc_id}{ext}"

    size = await stream_upload_with_size_guard(file, dest)

    now = _now()
    await db.execute(
        insert(doc_t).values(
            id=doc_id,
            collection_id=col_id,
            filename=filename,
            file_type=ext,
            file_size=size,
            chunk_count=None,
            created_at=now,
            updated_at=now,
        )
    )
    await db.execute(
        insert(job_t).values(
            job_id=job_id,
            document_id=doc_id,
            collection_id=col_id,
            filename=filename,
            file_type=ext,
            status="pending",
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()

    task_id = task_producer.enqueue_ingest(job_id, str(dest), col_id, doc_id, ext)
    if task_id:
        await db.execute(
            update(job_t).where(job_t.c.job_id == job_id).values(celery_task_id=task_id)
        )
        await db.commit()

    return {
        "job_id": job_id,
        "document_id": doc_id,
        "collection_id": col_id,
        "filename": filename,
        "file_type": ext,
        "file_size": size,
        "status": "pending",
    }


async def list_documents(db: AsyncSession, col_id: str) -> list[dict]:
    """Return all documents in ``col_id`` with their latest job status."""
    stmt = (
        select(
            doc_t.c.id.label("document_id"),
            doc_t.c.filename,
            doc_t.c.file_type,
            doc_t.c.file_size,
            doc_t.c.chunk_count,
            doc_t.c.created_at,
            doc_t.c.updated_at,
            job_t.c.status,
        )
        .select_from(doc_t.outerjoin(job_t, job_t.c.document_id == doc_t.c.id))
        .where(doc_t.c.collection_id == col_id)
        .order_by(doc_t.c.created_at.desc())
    )
    rows = (await db.execute(stmt)).fetchall()
    return [dict(r._mapping) for r in rows]


async def get_document_status(
    db: AsyncSession, col_id: str, doc_id: str
) -> dict:
    """Return the latest job record for ``doc_id`` in ``col_id``."""
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
    """Delete a document and its job records, then enqueue async cleanup."""
    exists = (
        await db.execute(
            select(doc_t.c.id).where(
                doc_t.c.id == doc_id, doc_t.c.collection_id == col_id
            )
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Document {doc_id!r} not found")

    await db.execute(delete(job_t).where(job_t.c.document_id == doc_id))
    await db.execute(delete(doc_t).where(doc_t.c.id == doc_id))
    await db.commit()

    task_producer.enqueue_delete(doc_id, col_id)


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
