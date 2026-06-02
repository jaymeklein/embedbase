"""Document ingestion + management endpoints.

Upload streams the file to the shared data volume, records a ``document`` row and
a ``job_record`` (status ``pending``), then enqueues the Celery ingestion task.
The worker drives the status through ``processing`` → ``done``/``failed``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.parsers import SUPPORTED_EXTENSIONS
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.dependencies import get_db
from api.services import tasks as task_producer
from api.services.auth import Principal, require_auth
from api.services.upload import stream_upload_with_size_guard
from api.settings import settings

router = APIRouter(tags=["documents"])


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _resolve_collection(
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


async def _ingest(
    db: AsyncSession, col_id: str, file: UploadFile, principal: Principal
) -> dict:
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

    # Enqueue ingestion by task name over the broker (no worker import here).
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


# ── Nested routes ─────────────────────────────────────────────────────────────

@router.post("/workspaces/{ws_id}/collections/{col_id}/documents", status_code=202)
async def upload_document(
    ws_id: str,
    col_id: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _resolve_collection(db, col_id, ws_id)
    return await _ingest(db, col_id, file, principal)


@router.get("/workspaces/{ws_id}/collections/{col_id}/documents")
async def list_documents(
    ws_id: str,
    col_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _resolve_collection(db, col_id, ws_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")

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
        .select_from(
            doc_t.outerjoin(job_t, job_t.c.document_id == doc_t.c.id)
        )
        .where(doc_t.c.collection_id == col_id)
        .order_by(doc_t.c.created_at.desc())
    )
    rows = (await db.execute(stmt)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}/status")
async def get_document_status(
    ws_id: str,
    col_id: str,
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _resolve_collection(db, col_id, ws_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")

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


@router.delete(
    "/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}", status_code=204
)
async def delete_document(
    ws_id: str,
    col_id: str,
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _resolve_collection(db, col_id, ws_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    await _delete(db, col_id, doc_id)


async def _delete(db: AsyncSession, col_id: str, doc_id: str) -> None:
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

    # Best-effort async vector-store + BM25 cleanup (full path in Delivery 3).
    task_producer.enqueue_delete(doc_id, col_id)


# ── Flat aliases (convenience for MCP / programmatic clients) ─────────────────

@router.post("/documents", status_code=202)
async def upload_document_flat(
    collection_id: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _resolve_collection(db, collection_id)
    return await _ingest(db, collection_id, file, principal)


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document_flat(
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(doc_t.c.collection_id).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if not principal.can_access(row.collection_id):
        raise HTTPException(403, "API key not valid for this collection")
    await _delete(db, row.collection_id, doc_id)
