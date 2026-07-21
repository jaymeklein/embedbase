"""Document ingestion + management endpoints.

All business logic lives in api/services/documents.py.
This file is routing-only: path registration, dependency resolution, delegation.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.document import DocumentListQuery
from api.services import documents as doc_svc
from api.services import permissions
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
    query: Annotated[DocumentListQuery, Query()],
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List documents in a collection: paginated, filtered, newest-first, with latest status.

    Returns ``{items, total, limit, offset}``. All filters are optional and AND-combined;
    ``filename`` is a case-insensitive substring, ``status`` is the latest ingestion status,
    ``indexed`` gates on stored chunks, and the ``*_size``/``*_after``/``*_before`` bounds are
    inclusive. See :class:`DocumentListQuery` for the full set of query parameters.
    """
    await doc_svc.resolve_collection(db, col_id, ws_id)
    await permissions.authorize_collection(db, principal, col_id, "read")
    return await doc_svc.list_documents(db, col_id, query)


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
    # Single-document op → authorize the document (honors document-level grants),
    # matching the flat routes; the collection subtree is covered by an ancestor grant.
    await permissions.authorize_document(db, principal, doc_id, "read")
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
    await permissions.authorize_document(db, principal, doc_id, "write")
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
    await permissions.authorize_document(db, principal, doc_id, "write")
    col_id = await doc_svc.resolve_document_collection(db, doc_id)
    await doc_svc.delete_document(db, col_id, doc_id)


@router.post("/documents/{doc_id}/reprocess", status_code=202)
async def reprocess_document_flat(
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Re-enqueue a document's ingestion — the manual retry for a failed file. Reuses the stored
    bytes (nothing is re-uploaded) and surfaces a fresh pending job in the queue."""
    await permissions.authorize_document(db, principal, doc_id, "write")
    col_id = await doc_svc.resolve_document_collection(db, doc_id)
    return await doc_svc.reprocess_document(db, col_id, doc_id)
