"""BM25 indexing status + (re)index endpoints.

Routing-only: path registration, dependency resolution, delegation. All logic
lives in api/services/indexing.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.indexing import IndexEnqueueResponse, IndexStatusResponse
from api.services import documents as doc_svc
from api.services import indexing as index_svc
from api.services import permissions
from api.services.auth import Principal, require_auth, require_master

router = APIRouter(tags=["indexing"])


@router.get("/indexing/status", response_model=IndexStatusResponse)
async def index_status(
    _principal: object = Depends(require_master),
    db: AsyncSession = Depends(get_db),
) -> IndexStatusResponse:
    """Return BM25 index coverage grouped by workspace and collection."""
    return await index_svc.get_index_overview(db)


@router.post(
    "/workspaces/{ws_id}/collections/{col_id}/index",
    response_model=IndexEnqueueResponse,
)
async def index_collection(
    ws_id: str,
    col_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> IndexEnqueueResponse:
    """Enqueue a BM25 (re)index of every active document in a collection."""
    await doc_svc.resolve_collection(db, col_id, ws_id)
    await permissions.authorize_collection(db, principal, col_id, "write")
    return index_svc.enqueue_collection(col_id)


@router.post(
    "/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}/index",
    response_model=IndexEnqueueResponse,
)
async def index_document(
    ws_id: str,
    col_id: str,
    doc_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> IndexEnqueueResponse:
    """Enqueue a BM25 (re)index of a single document."""
    await doc_svc.resolve_collection(db, col_id, ws_id)
    # Single-document op → authorize the document (honors document-level grants).
    await permissions.authorize_document(db, principal, doc_id, "write")
    return index_svc.enqueue_document(doc_id, col_id)
