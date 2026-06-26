"""BM25 indexing status + (re)index endpoints.

Routing-only: path registration, dependency resolution, delegation. All logic
lives in api/services/indexing.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, require_redis_client
from api.models.indexing import IndexEnqueueResponse, IndexStatusResponse
from api.services import documents as doc_svc
from api.services import indexing as index_svc
from api.services.auth import Principal, require_auth, require_master

router = APIRouter(tags=["indexing"])


@router.get("/indexing/status", response_model=IndexStatusResponse)
async def index_status(
    _principal: object = Depends(require_master),
    db: AsyncSession = Depends(get_db),
    redis_client: Any = Depends(require_redis_client),
) -> IndexStatusResponse:
    """Return BM25 index coverage grouped by workspace and collection."""
    return await index_svc.get_index_overview(db, redis_client)


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
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    return await index_svc.enqueue_collection(db, col_id)


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
    if not principal.can_access(col_id):
        raise HTTPException(403, "API key not valid for this collection")
    return index_svc.enqueue_document(doc_id, col_id)
