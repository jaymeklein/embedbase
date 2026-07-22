from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.collections import CollectionCreate, CollectionUpdate
from api.services import collections as collection_svc
from api.services import permissions
from api.services import workspaces as workspace_svc
from api.services.auth import Principal, require_auth, require_master

# Writes are admin-only (per-route ``require_master``); reads are ``require_auth`` and
# grant-scoped so a non-admin sees only the collections their grants reach.
router = APIRouter(prefix="/workspaces/{ws_id}/collections", tags=["collections"])


@router.post("", status_code=201)
async def create_collection(
    ws_id: str,
    body: CollectionCreate,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Creating a collection is a write into its parent workspace — needs workspace write.
    # Authorize before the existence check so a scoped user gets a uniform 403 and can't
    # probe which workspaces exist (the read routes authorize-first for the same reason).
    await permissions.authorize_workspace(db, principal, ws_id, "write")
    await workspace_svc.require_workspace(ws_id, db)
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
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    collections = await collection_svc.list_collections(ws_id, db, tags=tag)
    allowed = set(
        await permissions.readable_collection_ids(db, principal, [c["id"] for c in collections])
    )
    return [c for c in collections if c["id"] in allowed]


@router.get("/{col_id}")
async def get_collection(
    ws_id: str,
    col_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_collection(db, principal, col_id, "read")
    return await collection_svc.get_collection(ws_id, col_id, db)


@router.patch("/{col_id}", dependencies=[Depends(require_master)])
async def update_collection(
    ws_id: str, col_id: str, body: CollectionUpdate, db: AsyncSession = Depends(get_db)
):
    return await collection_svc.update_collection(ws_id, col_id, body, db)


@router.delete("/{col_id}", status_code=204, dependencies=[Depends(require_master)])
async def delete_collection(ws_id: str, col_id: str, db: AsyncSession = Depends(get_db)):
    await collection_svc.delete_collection(ws_id, col_id, db)
