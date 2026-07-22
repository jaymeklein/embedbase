from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.workspaces import WorkspaceCreate, WorkspaceUpdate
from api.services import permissions
from api.services import workspaces as workspace_svc
from api.services.auth import Principal, require_auth

# No router-level gate: every route is ``require_auth`` and authorized against the caller's
# grants. Reads are grant-scoped (a non-admin browses only the workspaces their grants reach);
# create needs the ``create_workspace`` capability; update/delete need workspace **write**.
# Master / admin are unrestricted.
router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Creating a top-level workspace needs the create_workspace capability (master/admin
    # always). A scoped creator is then granted write on it so they can use what they made
    # — a separate commit, but fail-safe: a crash in between only self-locks the creator out
    # of their new empty workspace (no privilege leak), recoverable by an admin.
    await permissions.authorize_workspace_creation(db, principal)
    ws = await workspace_svc.create_workspace(
        name=body.name,
        description=body.description,
        color=body.color,
        icon=body.icon,
        db=db,
    )
    await permissions.grant_creator_access(db, principal, ws["id"])
    return ws


@router.get("")
async def list_workspaces(
    principal: Principal = Depends(require_auth), db: AsyncSession = Depends(get_db)
):
    workspaces = await workspace_svc.list_workspaces(db)
    if principal.is_master:
        return [{**w, "can_write": True} for w in workspaces]
    # Non-admin: keep only readable workspaces and recompute collection_count against
    # the collections the caller can actually see (reusing the grant-tree filter), so
    # the count never reveals collections they can't reach. ``can_write`` tells the UI
    # where a "new collection" action would succeed.
    tree = await permissions.filter_workspace_tree(
        db, principal, await workspace_svc.list_workspace_tree(db)
    )
    scoped_counts = {w["id"]: w["collection_count"] for w in tree}
    visible = [w for w in workspaces if w["id"] in scoped_counts]
    writable = set(
        await permissions.writable_workspace_ids(db, principal, [w["id"] for w in visible])
    )
    return [
        {
            **w,
            "collection_count": scoped_counts[w["id"]],
            "can_write": w["id"] in writable,
        }
        for w in visible
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
    result["can_write"] = bool(
        await permissions.writable_workspace_ids(db, principal, [ws_id])
    )
    return result


@router.patch("/{ws_id}")
async def update_workspace(
    ws_id: str,
    body: WorkspaceUpdate,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Editing a workspace is a write on it — needs workspace write (see + edit).
    await permissions.authorize_workspace(db, principal, ws_id, "write")
    return await workspace_svc.update_workspace(ws_id, body, db)


@router.delete("/{ws_id}", status_code=204)
async def delete_workspace(
    ws_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_workspace(db, principal, ws_id, "write")
    await workspace_svc.delete_workspace(ws_id, db)
