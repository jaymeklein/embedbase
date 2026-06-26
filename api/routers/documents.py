"""Document ingestion + management endpoints.

All business logic lives in api/services/documents.py.
This file is routing-only: path registration, dependency resolution, delegation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, require_redis_client
from api.services import documents as doc_svc
from api.services.auth import Principal, require_auth

router = APIRouter(tags=["documents"])


# ── Nested routes ─────────────────────────────────────────────────────────────

@router.post("/workspaces/{ws_id}/collections/{col_id}/documents", status_code=202)
async def upload_document(
    ws_id: str,
    col_id: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Upload and enqueue a document for ingestion.

    Accepted formats: PDF, TXT, Markdown, source code (py/js/ts/go/rs/java etc.),
    CSV, JSON, and -- via the docling parser -- DOCX and PPTX.
    """
    await doc_svc.resolve_collection(db, col_id, ws_id)
    return await doc_svc.ingest(db, col_id, file, principal)


@router.get("/workspaces/{ws_id}/collections/{col_id}/documents")
async def list_documents(
    ws_id: str,
    col_id: str,
    tag: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    redis_client: Any = Depends(require_redis_client),
):
    """List all documents in a collection with their ingestion + index status."""
    await doc_svc.resolve_collection(db, col_id, ws_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    return await doc_svc.list_documents(db, col_id, tags=tag, redis_client=redis_client)


@router.get("/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}/status")
async def get_document_status(
    ws_id: str,
    col_id: str,
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return the latest ingestion job status for a document."""
    await doc_svc.resolve_collection(db, col_id, ws_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    return await doc_svc.get_document_status(db, col_id, doc_id)


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
    """Delete a document and enqueue async vector-store cleanup."""
    await doc_svc.resolve_collection(db, col_id, ws_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    await doc_svc.delete_document(db, col_id, doc_id)


# ── Flat aliases (convenience for MCP / programmatic clients) ─────────────────

@router.post("/documents", status_code=202)
async def upload_document_flat(
    collection_id: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document by collection ID without the nested workspace path."""
    await doc_svc.resolve_collection(db, collection_id)
    return await doc_svc.ingest(db, collection_id, file, principal)


@router.get("/documents/{doc_id}/raw")
async def get_document_raw(
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Serve a document's original bytes for inline viewing / opening."""
    path, filename = await doc_svc.get_document_file(db, doc_id, principal)
    return FileResponse(path, filename=filename, content_disposition_type="inline")


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document_flat(
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document by ID without the nested workspace/collection path."""
    col_id = await doc_svc.resolve_document_collection(db, doc_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    await doc_svc.delete_document(db, col_id, doc_id)
