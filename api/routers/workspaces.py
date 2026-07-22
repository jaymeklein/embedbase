from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.workspaces import WorkspaceCreate, WorkspaceUpdate
from api.services import permissions
from api.services import workspaces as workspace_svc
from api.services.auth import Principal, require_auth, require_master

# No router-level gate: writes are admin-only (per-route ``require_master``), while
# reads are ``require_auth`` and grant-scoped so a non-admin user browses only the
# workspaces their grants reach. Master / admin see everything.
router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", status_code=201, dependencies=[Depends(require_master)])
async def create_workspace(body: WorkspaceCreate, db: AsyncSession = Depends(get_db)):
    return await workspace_svc.create_workspace(
        name=body.name,
        description=body.description,
        color=body.color,
        icon=body.icon,
        db=db,
    )


@router.get("")
async def list_workspaces(
    principal: Principal = Depends(require_auth), db: AsyncSession = Depends(get_db)
):
    workspaces = await workspace_svc.list_workspaces(db)
    if principal.is_master:
        return workspaces
    # Non-admin: keep only readable workspaces and recompute collection_count against
    # the collections the caller can actually see (reusing the grant-tree filter), so
    # the count never reveals collections they can't reach.
    tree = await permissions.filter_workspace_tree(
        db, principal, await workspace_svc.list_workspace_tree(db)
    )
    scoped_counts = {w["id"]: w["collection_count"] for w in tree}
    return [
        {**w, "collection_count": scoped_counts[w["id"]]}
        for w in workspaces
        if w["id"] in scoped_counts
    ]


@router.get("/{ws_id}")
async def get_workspace(
    ws_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_workspace(db, principal, ws_id, "read")
    result = await workspace_svc.get_workspace(ws_id, db)
    if not principal.is_master:
        # A workspace is browsable via a single collection grant, so prune the nested
        # collections to what the caller may read — don't leak the others' metadata.
        allowed = set(
            await permissions.readable_collection_ids(
                db, principal, [c["id"] for c in result["collections"]]
            )
        )
        result["collections"] = [c for c in result["collections"] if c["id"] in allowed]
    return result


@router.patch("/{ws_id}", dependencies=[Depends(require_master)])
async def update_workspace(
    ws_id: str, body: WorkspaceUpdate, db: AsyncSession = Depends(get_db)
):
    return await workspace_svc.update_workspace(ws_id, body, db)


@router.delete("/{ws_id}", status_code=204, dependencies=[Depends(require_master)])
async def delete_workspace(ws_id: str, db: AsyncSession = Depends(get_db)):
    await workspace_svc.delete_workspace(ws_id, db)
