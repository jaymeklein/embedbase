"""Document ingestion + management endpoints.

All business logic lives in api/services/documents.py.
This file is routing-only: path registration, dependency resolution, delegation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.services import documents as doc_svc
from api.services.auth import Principal, require_auth

router = APIRouter(tags=["documents"])


# ── Nested routes ─────────────────────────────────────────────────────────────

@router.post("/workspaces/{ws_id}/collections/{col_id}/documents", status_code=202)
async def upload_document(
    ws_id: str,
    col_id: str,
    file: UploadFile = File(...),
    temporary: bool = Form(False),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Upload and enqueue a document for ingestion.

    Accepted formats: PDF, TXT, and Markdown; DOCX and PPTX also require the docling
    parser backend (``parsers.pdf_backend: docling``). Set ``temporary`` to auto-purge
    the document after ``storage.temp_retention_hours`` (no-op when 0).
    """
    await doc_svc.resolve_collection(db, col_id, ws_id)
    return await doc_svc.ingest(db, col_id, file, principal, temporary=temporary)


@router.get("/workspaces/{ws_id}/collections/{col_id}/documents")
async def list_documents(
    ws_id: str,
    col_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    filename: str | None = Query(default=None),
    file_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    indexed: bool | None = Query(default=None),
    embedding_model: str | None = Query(default=None),
    storage_backend: str | None = Query(default=None),
    min_size: int | None = Query(default=None, ge=0),
    max_size: int | None = Query(default=None, ge=0),
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    updated_after: str | None = Query(default=None),
    updated_before: str | None = Query(default=None),
    tag: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List documents in a collection: paginated, filtered, newest-first, with latest status.

    Returns ``{items, total, limit, offset}``. All filters are optional and AND-combined;
    ``filename`` is a case-insensitive substring, ``status`` is the latest ingestion status,
    ``indexed`` gates on stored chunks, and the ``*_size``/``*_after``/``*_before`` bounds are
    inclusive.
    """
    await doc_svc.resolve_collection(db, col_id, ws_id)
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    return await doc_svc.list_documents(
        db,
        col_id,
        limit=limit,
        offset=offset,
        filename=filename,
        file_type=file_type,
        status=status,
        indexed=indexed,
        embedding_model=embedding_model,
        storage_backend=storage_backend,
        min_size=min_size,
        max_size=max_size,
        created_after=created_after,
        created_before=created_before,
        updated_after=updated_after,
        updated_before=updated_before,
        tags=tag,
    )


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
    temporary: bool = Form(False),
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document by collection ID without the nested workspace path."""
    await doc_svc.resolve_collection(db, collection_id)
    return await doc_svc.ingest(db, collection_id, file, principal, temporary=temporary)


@router.get("/documents/{doc_id}/raw")
async def get_document_raw(
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a document's original bytes: inline FileResponse (local) or 302 to a
    presigned URL (S3), resolved from the document's storage backend."""
    return await doc_svc.resolve_document_download(db, doc_id, principal)


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
