"""Tag CRUD, assignment, correlation, and merge endpoints.

Routing-only (Section 5): every handler resolves dependencies and delegates a single
call to api/services/tags.py. Authorization is two-tier: the ``manage_tags`` capability
(``permissions.authorize_tag_management``, scoped to a readable workspace) grants the
tag-management *privilege* and covers the workspace-level tag vocabulary
(create/update/delete/merge/list) and workspace tag assignment; the finer routes then add
a **data-scope** check so a scoped tag-manager can't reach resources their grants hide —
(un)assigning a collection/document tag also needs **write** on that resource, and
``tag_items`` is pruned to the caller's readable collections/documents. Master/admin are
unrestricted. See [`permissions.md`](../../.claude/rules/permissions.md).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.tags import TagCreate, TagMerge, TagUpdate
from api.services import permissions
from api.services import tags as tag_svc
from api.services.auth import Principal, require_auth

router = APIRouter(prefix="/workspaces/{ws_id}", tags=["tags"])


@router.post("/tags", status_code=201)
async def create_tag(
    ws_id: str,
    body: TagCreate,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    return await tag_svc.create_tag(ws_id, body.name, body.color, db)


@router.get("/tags")
async def list_tags(
    ws_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    return await tag_svc.list_tags(ws_id, db)


@router.post("/tags/merge")
async def merge_tags(
    ws_id: str,
    body: TagMerge,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    return await tag_svc.merge_tags(ws_id, body, db)


@router.patch("/tags/{tag_id}")
async def update_tag(
    ws_id: str,
    tag_id: str,
    body: TagUpdate,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    return await tag_svc.update_tag(ws_id, tag_id, body, db)


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(
    ws_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    await tag_svc.delete_tag(ws_id, tag_id, db)


@router.get("/tags/{tag_id}/items")
async def tag_items(
    ws_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # tag_items enumerates the collections/documents carrying a tag across the whole workspace.
    # The capability gate admits a scoped tag-manager, so the result is then pruned to the
    # collections/documents the caller may actually read — a collection/document grant must not
    # leak sibling resources' names/filenames through this admin view.
    await permissions.authorize_tag_management(db, principal, ws_id)
    items = await tag_svc.tag_items(ws_id, tag_id, db)
    return await permissions.filter_tag_items(db, principal, items)


# ── Assignment (manual) ───────────────────────────────────────────────────────


@router.put("/assigned-tags/{tag_id}", status_code=204)
async def assign_workspace_tag(
    ws_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    await tag_svc.assign_workspace_tag(ws_id, tag_id, db)


@router.delete("/assigned-tags/{tag_id}", status_code=204)
async def unassign_workspace_tag(
    ws_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    await tag_svc.unassign_workspace_tag(ws_id, tag_id, db)


@router.put("/collections/{col_id}/tags/{tag_id}", status_code=204)
async def assign_collection_tag(
    ws_id: str,
    col_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Capability grants the privilege; write on this collection scopes *which* one — so a
    # tag-manager can only (un)tag collections their data grants let them write, never siblings.
    await permissions.authorize_tag_management(db, principal, ws_id)
    await permissions.authorize_collection(db, principal, col_id, "write")
    await tag_svc.assign_collection_tag(ws_id, col_id, tag_id, db)


@router.delete("/collections/{col_id}/tags/{tag_id}", status_code=204)
async def unassign_collection_tag(
    ws_id: str,
    col_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    await permissions.authorize_collection(db, principal, col_id, "write")
    await tag_svc.unassign_collection_tag(ws_id, col_id, tag_id, db)


@router.put(
    "/collections/{col_id}/documents/{doc_id}/tags/{tag_id}", status_code=204
)
async def assign_document_tag(
    ws_id: str,
    col_id: str,
    doc_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Write on the document scopes which one — honouring document-level grants, so a scoped
    # tag-manager can't tag a document their data grants hide.
    await permissions.authorize_tag_management(db, principal, ws_id)
    await permissions.authorize_document(db, principal, doc_id, "write")
    await tag_svc.assign_document_tag(ws_id, col_id, doc_id, tag_id, db)


@router.delete(
    "/collections/{col_id}/documents/{doc_id}/tags/{tag_id}", status_code=204
)
async def unassign_document_tag(
    ws_id: str,
    col_id: str,
    doc_id: str,
    tag_id: str,
    principal: Principal = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await permissions.authorize_tag_management(db, principal, ws_id)
    await permissions.authorize_document(db, principal, doc_id, "write")
    await tag_svc.unassign_document_tag(ws_id, col_id, doc_id, tag_id, db)
