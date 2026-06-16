"""Tag CRUD, assignment, correlation, and merge endpoints.

Routing-only (Section 5): every handler resolves dependencies and delegates a
single call to api/services/tags.py. Master-key protected, like the other
management-plane routers.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_tagging_config, require_redis_client
from api.models.config import TaggingConfig
from api.schemas.tags import TagCreate, TagMerge, TagUpdate
from api.services import tag_suggest
from api.services import tags as tag_svc
from api.services.auth import require_master

router = APIRouter(
    prefix="/workspaces/{ws_id}",
    tags=["tags"],
    dependencies=[Depends(require_master)],
)


@router.post("/tags", status_code=201)
async def create_tag(ws_id: str, body: TagCreate, db: AsyncSession = Depends(get_db)):
    return await tag_svc.create_tag(ws_id, body.name, body.color, db)


@router.get("/tags")
async def list_tags(ws_id: str, db: AsyncSession = Depends(get_db)):
    return await tag_svc.list_tags(ws_id, db)


@router.post("/tags/merge")
async def merge_tags(ws_id: str, body: TagMerge, db: AsyncSession = Depends(get_db)):
    return await tag_svc.merge_tags(ws_id, body, db)


@router.patch("/tags/{tag_id}")
async def update_tag(
    ws_id: str, tag_id: str, body: TagUpdate, db: AsyncSession = Depends(get_db)
):
    return await tag_svc.update_tag(ws_id, tag_id, body, db)


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(ws_id: str, tag_id: str, db: AsyncSession = Depends(get_db)):
    await tag_svc.delete_tag(ws_id, tag_id, db)


@router.get("/tags/{tag_id}/items")
async def tag_items(ws_id: str, tag_id: str, db: AsyncSession = Depends(get_db)):
    return await tag_svc.tag_items(ws_id, tag_id, db)


# ── Assignment (manual) ───────────────────────────────────────────────────────


@router.put("/assigned-tags/{tag_id}", status_code=204)
async def assign_workspace_tag(
    ws_id: str, tag_id: str, db: AsyncSession = Depends(get_db)
):
    await tag_svc.assign_workspace_tag(ws_id, tag_id, db)


@router.delete("/assigned-tags/{tag_id}", status_code=204)
async def unassign_workspace_tag(
    ws_id: str, tag_id: str, db: AsyncSession = Depends(get_db)
):
    await tag_svc.unassign_workspace_tag(ws_id, tag_id, db)


@router.put("/collections/{col_id}/tags/{tag_id}", status_code=204)
async def assign_collection_tag(
    ws_id: str, col_id: str, tag_id: str, db: AsyncSession = Depends(get_db)
):
    await tag_svc.assign_collection_tag(ws_id, col_id, tag_id, db)


@router.delete("/collections/{col_id}/tags/{tag_id}", status_code=204)
async def unassign_collection_tag(
    ws_id: str, col_id: str, tag_id: str, db: AsyncSession = Depends(get_db)
):
    await tag_svc.unassign_collection_tag(ws_id, col_id, tag_id, db)


@router.put(
    "/collections/{col_id}/documents/{doc_id}/tags/{tag_id}", status_code=204
)
async def assign_document_tag(
    ws_id: str, col_id: str, doc_id: str, tag_id: str, db: AsyncSession = Depends(get_db)
):
    await tag_svc.assign_document_tag(ws_id, col_id, doc_id, tag_id, db)


@router.delete(
    "/collections/{col_id}/documents/{doc_id}/tags/{tag_id}", status_code=204
)
async def unassign_document_tag(
    ws_id: str, col_id: str, doc_id: str, tag_id: str, db: AsyncSession = Depends(get_db)
):
    await tag_svc.unassign_document_tag(ws_id, col_id, doc_id, tag_id, db)


# ── AI tag suggestions (ephemeral — nothing persisted until applied) ──────────


@router.post("/collections/{col_id}/suggest-tags")
async def suggest_collection_tags(
    ws_id: str,
    col_id: str,
    db: AsyncSession = Depends(get_db),
    redis: object = Depends(require_redis_client),
    tagging: TaggingConfig = Depends(get_tagging_config),
):
    return await tag_suggest.suggest_collection_tags(
        ws_id, col_id, db=db, redis=redis, tagging=tagging
    )


@router.post("/collections/{col_id}/documents/{doc_id}/suggest-tags")
async def suggest_document_tags(
    ws_id: str,
    col_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    redis: object = Depends(require_redis_client),
    tagging: TaggingConfig = Depends(get_tagging_config),
):
    return await tag_suggest.suggest_document_tags(
        ws_id, col_id, doc_id, db=db, redis=redis, tagging=tagging
    )
