from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.collections import CollectionCreate, CollectionUpdate
from api.services import collections as collection_svc
from api.services import permissions
from api.services import workspaces as workspace_svc
from api.services.auth import Principal, require_auth

# Every route is ``require_auth`` and authorized against the caller's grants: reads are
# grant-scoped (a non-admin sees only the collections their grants reach), create needs
# workspace **write**, and update/delete need **write** on the collection (or an ancestor).
# Master / admin are unrestricted.
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
    visible = [c for c in collections if c["id"] in allowed]
    # ``can_write`` tells the UI where edit/delete/upload would succeed (write on the
    # collection or an ancestor). Master / no-permission users write everything.
    writable = set(
        await permissions.writable_collection_ids(db, principal, [c["id"] for c in visible])
    )
    return [{**c, "can_write": c["id"] in writable} for c in visible]


@router.get("/{col_id}")
async def get_collection(
    ws_id: str,
    col_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_collection(db, principal, col_id, "read")
    result = await collection_svc.get_collection(ws_id, col_id, db)
    result["can_write"] = bool(
        await permissions.writable_collection_ids(db, principal, [col_id])
    )
    return result


@router.patch("/{col_id}")
async def update_collection(
    ws_id: str,
    col_id: str,
    body: CollectionUpdate,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Editing a collection is a write on it — needs write on the collection or an ancestor
    # (which also requires read: you can't edit what you can't see). Authorize before the
    # existence check so a scoped user gets a uniform 403, not an existence oracle.
    await permissions.authorize_collection(db, principal, col_id, "write")
    return await collection_svc.update_collection(ws_id, col_id, body, db)


@router.delete("/{col_id}", status_code=204)
async def delete_collection(
    ws_id: str,
    col_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_collection(db, principal, col_id, "write")
    await collection_svc.delete_collection(ws_id, col_id, db)
