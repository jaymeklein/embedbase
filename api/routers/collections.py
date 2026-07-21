from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.collections import CollectionCreate, CollectionUpdate
from api.services import collections as collection_svc
from api.services.auth import require_master

router = APIRouter(
    prefix="/workspaces/{ws_id}/collections",
    tags=["collections"],
    dependencies=[Depends(require_master)],
)


@router.post("", status_code=201)
async def create_collection(
    ws_id: str, body: CollectionCreate, db: AsyncSession = Depends(get_db)
):
    return await collection_svc.create_collection(
        workspace_id=ws_id,
        name=body.name,
        description=body.description,
        color=body.color,
        icon=body.icon,
        db=db,
    )


@router.get("")
async def list_collections(
    ws_id: str,
    tag: list[str] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await collection_svc.list_collections(ws_id, db, tags=tag)


@router.get("/{col_id}")
async def get_collection(ws_id: str, col_id: str, db: AsyncSession = Depends(get_db)):
    return await collection_svc.get_collection(ws_id, col_id, db)


@router.patch("/{col_id}")
async def update_collection(
    ws_id: str, col_id: str, body: CollectionUpdate, db: AsyncSession = Depends(get_db)
):
    return await collection_svc.update_collection(ws_id, col_id, body, db)


@router.delete("/{col_id}", status_code=204)
async def delete_collection(ws_id: str, col_id: str, db: AsyncSession = Depends(get_db)):
    await collection_svc.delete_collection(ws_id, col_id, db)
