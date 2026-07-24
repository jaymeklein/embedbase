"""Permissions + the authorization authority.

Permissions **scope a user down** rather than granting access from nothing:

- A user with **no permissions at all is unrestricted** — they read *and* write
  every workspace, collection, and document (an open default). Master and admin
  principals are always unrestricted.
- **Any permission scopes the user** to the workspace(s) it touches: a
  collection- or document-level permission scopes them to *that* resource's
  workspace; other workspaces disappear.
- Within a visible workspace, narrowing is **per level**: a workspace-only
  permission leaves every collection visible, while a collection permission
  narrows to just the permitted collections. A **document** permission is
  *direct-access* to that one document — it scopes the user into its
  collection's workspace but does **not** open the collection for browsing or
  search (so it can't leak the collection's other documents).
- **Read vs write**: a user with **no permissions writes everything**; a scoped
  user writes only where a *write* permission covers the resource (on it or an
  ancestor) and is otherwise view-only within their scope. ``write`` ⊇ ``read``.

This module is the single place that answers *"may this principal do X to
resource Y?"*: routers and MCP tools call :func:`authorize_collection` /
:func:`authorize_document` and let them raise ``403``. It also owns permission
CRUD for the management API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import permissions as perm_t
from api.db import workspaces as ws_t
from api.services.auth import Principal

Level = Literal["read", "write"]
ResourceType = Literal["workspace", "collection", "document", "capability"]

_RESOURCE_TABLES = {"workspace": ws_t, "collection": col_t, "document": doc_t}
# The human-facing name column per resource kind (documents label by filename).
_RESOURCE_NAME_COLS = {
    "workspace": ws_t.c.name,
    "collection": col_t.c.name,
    "document": doc_t.c.filename,
}

_Target = tuple[str, str]  # (resource_type, resource_id)

# Raised by the REST /search route and the MCP search tool when none of the
# requested collections are readable by the caller.
NO_READABLE_COLLECTIONS = "API key not authorized to read the requested collections"

# Grantable capabilities — permissions NOT tied to a resource (e.g. "may create
# workspaces", which has no parent resource to hold a write grant). Stored in the
# permissions table as ``resource_type="capability"`` and ignored by data-scope
# resolution, so a capability grant never scopes a user's read/write access.
CAP_CREATE_WORKSPACE = "create_workspace"
_CAPABILITIES = {CAP_CREATE_WORKSPACE}
_CAPABILITY_LABELS = {CAP_CREATE_WORKSPACE: "Create workspaces"}


def _denied(need: str) -> str:
    return f"API key not authorized to {need} this resource"


# ---------------------------------------------------------------------------
# Scope resolution — the "restrict / scope-down" model (see the module docstring)
# ---------------------------------------------------------------------------

async def _collection_ws(db: AsyncSession, collection_id: str) -> str | None:
    """The workspace a collection belongs to, or ``None`` if the collection is unknown."""
    return (
        await db.execute(select(col_t.c.workspace_id).where(col_t.c.id == collection_id))
    ).scalar_one_or_none()


async def _document_location(
    db: AsyncSession, document_id: str
) -> tuple[str, str] | None:
    """``(collection_id, workspace_id)`` for a document, or ``None`` if unknown."""
    row = (
        await db.execute(
            select(doc_t.c.collection_id, col_t.c.workspace_id)
            .select_from(doc_t.join(col_t, doc_t.c.collection_id == col_t.c.id))
            .where(doc_t.c.id == document_id)
        )
    ).first()
    return (row.collection_id, row.workspace_id) if row is not None else None


class _Scope:
    """A restricted user's resolved permissions, with parent links pre-fetched.

    Visibility narrows per level: a permission scopes the user to the workspace(s) it
    touches; within a touched workspace a collection/document permission narrows to just
    the permitted children, while a workspace-only permission leaves all children visible.
    Capability defaults to write; the nearest permission's level (on the resource or an
    ancestor) caps it, so a ``read`` permission makes its scope view-only.
    """

    def __init__(
        self,
        ws_level: dict[str, str],
        col_level: dict[str, str],
        doc_level: dict[str, str],
        col_ws: dict[str, str],
        doc_col: dict[str, str],
    ) -> None:
        self.ws_level = ws_level  # workspace_id -> level (direct workspace permissions)
        self.col_level = col_level  # collection_id -> level
        self.doc_level = doc_level  # document_id -> level
        self.col_ws = col_ws  # collection_id -> workspace_id (granted cols + granted docs' cols)
        self.doc_col = doc_col  # document_id -> collection_id (granted docs)
        # A collection- or document-level permission "narrows" its parent WORKSPACE: inside a
        # narrowed workspace only explicitly collection-granted collections are browsable.
        # (narrowed_cols — collections holding a granted document — feeds narrowed_ws so a
        # document grant scopes its workspace, but does not itself open its collection.)
        self.narrowed_cols: set[str] = {doc_col[d] for d in doc_level if d in doc_col}
        self.narrowed_ws: set[str] = {
            col_ws[c] for c in col_level if c in col_ws
        } | {col_ws[c] for c in self.narrowed_cols if c in col_ws}
        self.touched_ws: set[str] = set(ws_level) | self.narrowed_ws

    def is_empty(self) -> bool:
        """No permissions at all → the user is unrestricted (sees and writes everything)."""
        return not (self.ws_level or self.col_level or self.doc_level)

    def workspace_readable(self, ws_id: str) -> bool:
        return ws_id in self.touched_ws

    def collection_readable(self, col_id: str, ws_id: str) -> bool:
        """Whether the *whole* collection is browsable/searchable. A document permission does
        NOT open its collection here — only a collection- or workspace-level permission does
        (the granted document itself stays reachable via :meth:`document_readable`). This is
        what keeps a document grant from leaking its collection's siblings through the
        collection-grained readers (list/search/jobs/indexing)."""
        if ws_id not in self.touched_ws:
            return False
        if ws_id not in self.narrowed_ws:
            return True  # workspace-only scope → every collection under it
        return col_id in self.col_level

    def document_readable(self, doc_id: str, col_id: str, ws_id: str) -> bool:
        """A directly-granted document, or any document in a browse-readable collection."""
        return doc_id in self.doc_level or self.collection_readable(col_id, ws_id)

    def collection_writable(self, col_id: str, ws_id: str) -> bool:
        # A scoped user writes only where an explicit write permission covers the resource
        # (on it or an ancestor); an in-scope resource with no covering permission stays
        # view-only. (A user with *no* permissions is unrestricted and never reaches here.)
        return (self.col_level.get(col_id) or self.ws_level.get(ws_id)) == "write"

    def document_writable(self, doc_id: str, col_id: str, ws_id: str) -> bool:
        return (
            self.doc_level.get(doc_id)
            or self.col_level.get(col_id)
            or self.ws_level.get(ws_id)
        ) == "write"


async def _resolve_scope(db: AsyncSession, user_id: str) -> _Scope:
    """Load ``user_id``'s permissions and pre-fetch the parent links the scope needs."""
    ws_level: dict[str, str] = {}
    col_level: dict[str, str] = {}
    doc_level: dict[str, str] = {}
    bucket = {"workspace": ws_level, "collection": col_level, "document": doc_level}
    rows = (
        await db.execute(
            select(perm_t.c.resource_type, perm_t.c.resource_id, perm_t.c.level).where(
                perm_t.c.user_id == user_id
            )
        )
    ).fetchall()
    for r in rows:
        target = bucket.get(r.resource_type)
        if target is not None:
            target[r.resource_id] = r.level
    doc_col: dict[str, str] = {}
    if doc_level:
        rows = (
            await db.execute(
                select(doc_t.c.id, doc_t.c.collection_id).where(doc_t.c.id.in_(doc_level))
            )
        ).fetchall()
        doc_col = {r.id: r.collection_id for r in rows}
    need_cols = set(col_level) | set(doc_col.values())
    col_ws: dict[str, str] = {}
    if need_cols:
        rows = (
            await db.execute(
                select(col_t.c.id, col_t.c.workspace_id).where(col_t.c.id.in_(need_cols))
            )
        ).fetchall()
        col_ws = {r.id: r.workspace_id for r in rows}
    return _Scope(ws_level, col_level, doc_level, col_ws, doc_col)


# ---------------------------------------------------------------------------
# Enforcement — the entrypoints routers/tools call
# ---------------------------------------------------------------------------

async def authorize_collection(
    db: AsyncSession, principal: Principal, collection_id: str, need: Level = "read"
) -> None:
    """Raise ``403`` unless ``principal`` may ``need`` (read/write) ``collection_id``."""
    if principal.is_master:
        return
    if principal.user_id is not None:
        scope = await _resolve_scope(db, principal.user_id)
        if scope.is_empty():
            return  # no permissions set → unrestricted
        ws_id = await _collection_ws(db, collection_id)
        if (
            ws_id is not None
            and scope.collection_readable(collection_id, ws_id)
            and (need == "read" or scope.collection_writable(collection_id, ws_id))
        ):
            return
    raise HTTPException(403, _denied(need))


async def authorize_document(
    db: AsyncSession, principal: Principal, document_id: str, need: Level = "read"
) -> None:
    """Raise ``403`` unless ``principal`` may ``need`` (read/write) ``document_id``.

    A permission on the document, its collection, or its workspace scopes access; with no
    permission on it or an ancestor the document is fully accessible (open default).
    """
    if principal.is_master:
        return
    if principal.user_id is not None:
        scope = await _resolve_scope(db, principal.user_id)
        if scope.is_empty():
            return
        loc = await _document_location(db, document_id)
        if loc is not None:
            col_id, ws_id = loc
            if scope.document_readable(document_id, col_id, ws_id) and (
                need == "read" or scope.document_writable(document_id, col_id, ws_id)
            ):
                return
    raise HTTPException(403, _denied(need))


async def readable_collection_ids(
    db: AsyncSession, principal: Principal, candidate_ids: list[str]
) -> list[str]:
    """Filter ``candidate_ids`` to the collections ``principal`` may read (order kept).

    Master and users with no permissions see them all; a scoped user sees only the
    collections their permissions leave visible.
    """
    ids = list(dict.fromkeys(candidate_ids))
    if principal.is_master:
        return ids
    if principal.user_id is None or not ids:
        return []
    scope = await _resolve_scope(db, principal.user_id)
    if scope.is_empty():
        return ids
    rows = (
        await db.execute(
            select(col_t.c.id, col_t.c.workspace_id).where(col_t.c.id.in_(ids))
        )
    ).fetchall()
    ws_by_col = {r.id: r.workspace_id for r in rows}
    return [
        cid
        for cid in ids
        if (ws := ws_by_col.get(cid)) is not None and scope.collection_readable(cid, ws)
    ]


async def writable_collection_ids(
    db: AsyncSession, principal: Principal, candidate_ids: list[str]
) -> list[str]:
    """Filter collection ids to those ``principal`` may write — edit/delete or ingest into.

    Master and users with no permissions may write all; a scoped user only where a
    collection- or ancestor-workspace **write** permission covers a *visible* collection
    (write requires read — you cannot edit what you cannot see). Lets the UI show the
    edit/delete/upload affordances only where they would actually succeed.
    """
    ids = list(dict.fromkeys(candidate_ids))
    if principal.is_master:
        return ids
    if principal.user_id is None or not ids:
        return []
    scope = await _resolve_scope(db, principal.user_id)
    if scope.is_empty():
        return ids
    rows = (
        await db.execute(
            select(col_t.c.id, col_t.c.workspace_id).where(col_t.c.id.in_(ids))
        )
    ).fetchall()
    ws_by_col = {r.id: r.workspace_id for r in rows}
    return [
        cid
        for cid in ids
        if (ws := ws_by_col.get(cid)) is not None
        and scope.collection_readable(cid, ws)
        and scope.collection_writable(cid, ws)
    ]


async def readable_collection_scope(
    db: AsyncSession, principal: Principal
) -> list[str] | None:
    """The full set of collection ids ``principal`` may read — or ``None`` when unrestricted.

    ``None`` = master, an admin, or a user with **no permissions** (see everything; callers
    skip filtering). A scoped user gets the explicit set their permissions leave visible:
    every collection under a workspace-only scope, plus each explicitly collection-granted
    collection under a narrowed workspace (a document grant does not surface its collection
    here). For the global cross-collection views (ingestion queue, index status, the
    ingestion-queue WS) that have no candidate list to filter.
    """
    if principal.is_master:
        return None
    if principal.user_id is None:
        return []
    scope = await _resolve_scope(db, principal.user_id)
    if scope.is_empty():
        return None
    readable: set[str] = {
        cid for cid, ws in scope.col_ws.items() if scope.collection_readable(cid, ws)
    }
    open_ws = [ws for ws in scope.touched_ws if ws not in scope.narrowed_ws]
    if open_ws:
        rows = (
            await db.execute(select(col_t.c.id).where(col_t.c.workspace_id.in_(open_ws)))
        ).fetchall()
        readable.update(r.id for r in rows)
    return list(readable)


async def filter_workspace_tree(
    db: AsyncSession, principal: Principal, tree: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Prune a workspace tree (from ``list_workspace_tree``) to what ``principal`` reads.

    Master and users with no permissions get the whole tree; a scoped user keeps only
    readable workspaces, each narrowed to its readable collections with counts recomputed.
    """
    if principal.is_master:
        return tree
    if principal.user_id is None:
        return []
    scope = await _resolve_scope(db, principal.user_id)
    if scope.is_empty():
        return tree
    filtered: list[dict[str, Any]] = []
    for ws in tree:
        if not scope.workspace_readable(ws["id"]):
            continue
        cols = [
            c for c in ws["collections"] if scope.collection_readable(c["id"], ws["id"])
        ]
        filtered.append(
            {
                **ws,
                "collections": cols,
                "collection_count": len(cols),
                "document_count": sum(int(c["document_count"]) for c in cols),
            }
        )
    return filtered


async def readable_workspace_ids(
    db: AsyncSession, principal: Principal, candidate_ids: list[str]
) -> list[str]:
    """Filter workspace ids to those ``principal`` may browse (order kept).

    Master and users with no permissions see all; a scoped user sees only the workspaces
    their permissions touch (granted directly, or holding a permitted collection/document).
    """
    ids = list(dict.fromkeys(candidate_ids))
    if principal.is_master:
        return ids
    if principal.user_id is None or not ids:
        return []
    scope = await _resolve_scope(db, principal.user_id)
    if scope.is_empty():
        return ids
    return [wid for wid in ids if scope.workspace_readable(wid)]


async def authorize_workspace(
    db: AsyncSession, principal: Principal, workspace_id: str, need: Level = "read"
) -> None:
    """Raise ``403`` unless ``principal`` may ``need`` ``workspace_id``.

    Read passes when the workspace is in scope; write passes only with a workspace-level
    ``write`` grant — a workspace-only ``read`` grant (or a workspace reached only via a
    collection/document grant) is view-only. Drives the workspace update/delete routes.
    """
    if principal.is_master:
        return
    if principal.user_id is not None:
        scope = await _resolve_scope(db, principal.user_id)
        if scope.is_empty():
            return
        if scope.workspace_readable(workspace_id) and (
            need == "read" or scope.ws_level.get(workspace_id) == "write"
        ):
            return
    raise HTTPException(403, _denied(need))


async def writable_workspace_ids(
    db: AsyncSession, principal: Principal, candidate_ids: list[str]
) -> list[str]:
    """Filter workspace ids to those ``principal`` may write — create collections in, edit, delete.

    Master and users with no permissions may write all; a scoped user only where a
    workspace-level write permission covers it. Drives the workspace ``can_write`` signal, so the
    UI shows new-collection + edit/delete only where they would succeed.
    """
    ids = list(dict.fromkeys(candidate_ids))
    if principal.is_master:
        return ids
    if principal.user_id is None or not ids:
        return []
    scope = await _resolve_scope(db, principal.user_id)
    if scope.is_empty():
        return ids
    return [
        wid
        for wid in ids
        if scope.workspace_readable(wid) and scope.ws_level.get(wid) == "write"
    ]


# ---------------------------------------------------------------------------
# Capabilities — grantable permissions not tied to a resource
# ---------------------------------------------------------------------------

async def has_capability(db: AsyncSession, principal: Principal, capability: str) -> bool:
    """Whether ``principal`` holds a grantable ``capability`` (master/admin always do)."""
    if principal.is_master:
        return True
    if principal.user_id is None:
        return False
    row = (
        await db.execute(
            select(perm_t.c.id).where(
                perm_t.c.user_id == principal.user_id,
                perm_t.c.resource_type == "capability",
                perm_t.c.resource_id == capability,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def authorize_workspace_creation(db: AsyncSession, principal: Principal) -> None:
    """Raise ``403`` unless ``principal`` may create workspaces.

    Master/admin always may; a non-admin needs the ``create_workspace`` capability grant. A
    no-permission user does *not* get it implicitly — creating top-level workspaces is a
    deliberate privilege, unlike data access (open by default).
    """
    if not await has_capability(db, principal, CAP_CREATE_WORKSPACE):
        raise HTTPException(403, "Not authorized to create workspaces")


async def grant_creator_access(
    db: AsyncSession, principal: Principal, workspace_id: str
) -> None:
    """Give a *scoped* creator write on a workspace they just made, so they can use it.

    Master/admin and unrestricted (no-permission) users already see everything, so no grant
    is made for them — granting an unrestricted user would wrongly scope their access down.
    """
    if principal.user_id is None or principal.is_master:
        return
    scope = await _resolve_scope(db, principal.user_id)
    if scope.is_empty():
        return
    await grant_permission(db, principal.user_id, "workspace", workspace_id, "write")


# ---------------------------------------------------------------------------
# Grant CRUD — used by the users management API (master-only)
# ---------------------------------------------------------------------------

async def _require_resource(db: AsyncSession, resource_type: str, resource_id: str) -> None:
    """Raise ``404``/``422`` when the target of a grant does not exist."""
    if resource_type == "capability":  # not a resource — validate the known capability id
        if resource_id not in _CAPABILITIES:
            raise HTTPException(422, f"Unknown capability {resource_id!r}")
        return
    table = _RESOURCE_TABLES.get(resource_type)
    if table is None:  # defensive — the schema Literal should prevent this
        raise HTTPException(422, f"Unknown resource_type {resource_type!r}")
    exists = (
        await db.execute(select(table.c.id).where(table.c.id == resource_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, f"{resource_type} {resource_id!r} not found")


async def _get_grant(db: AsyncSession, grant_id: str) -> dict[str, Any]:
    row = (
        await db.execute(
            select(
                perm_t.c.id,
                perm_t.c.user_id,
                perm_t.c.resource_type,
                perm_t.c.resource_id,
                perm_t.c.level,
                perm_t.c.created_at,
            ).where(perm_t.c.id == grant_id)
        )
    ).fetchone()
    assert row is not None
    return dict(row._mapping)


async def grant_permission(
    db: AsyncSession,
    user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    level: Level,
) -> dict[str, Any]:
    """Grant (or re-level) ``user_id`` on a resource; idempotent per resource.

    Re-granting an existing ``(user, resource)`` updates its level rather than
    inserting a duplicate (``UNIQUE(user_id, resource_type, resource_id)``).
    """
    await _require_resource(db, resource_type, resource_id)
    existing = (
        await db.execute(
            select(perm_t.c.id).where(
                perm_t.c.user_id == user_id,
                perm_t.c.resource_type == resource_type,
                perm_t.c.resource_id == resource_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.execute(update(perm_t).where(perm_t.c.id == existing).values(level=level))
        await db.commit()
        return await _get_grant(db, existing)
    grant_id = uuid4().hex
    await db.execute(
        insert(perm_t).values(
            id=grant_id,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            level=level,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    await db.commit()
    return await _get_grant(db, grant_id)


async def _resource_names(db: AsyncSession, grants: list[dict[str, Any]]) -> dict[_Target, str]:
    """Map each grant's ``(resource_type, resource_id)`` to its display name.

    One query per resource kind present (≤3). A resource deleted since the grant was
    made has no entry — the grant is dangling (harmless by design), and the caller
    falls back to the raw id.
    """
    ids_by_type: dict[str, set[str]] = {}
    for g in grants:
        ids_by_type.setdefault(g["resource_type"], set()).add(g["resource_id"])
    names: dict[_Target, str] = {}
    for rtype, ids in ids_by_type.items():
        if rtype == "capability":  # not a resource — label from the capability registry
            for rid in ids:
                label = _CAPABILITY_LABELS.get(rid)
                if label is not None:
                    names[(rtype, rid)] = label
            continue
        table = _RESOURCE_TABLES.get(rtype)
        if table is None:  # defensive — unknown kind stays unnamed, never a 500
            continue
        rows = (
            await db.execute(
                select(table.c.id, _RESOURCE_NAME_COLS[rtype].label("name")).where(
                    table.c.id.in_(ids)
                )
            )
        ).fetchall()
        for r in rows:
            names[(rtype, r.id)] = r.name
    return names


async def list_permissions(db: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    """Return every grant held by ``user_id``, oldest first.

    Each grant carries a ``resource_name`` — the workspace/collection name or document
    filename — or ``None`` when the resource has since been deleted.
    """
    rows = (
        await db.execute(
            select(
                perm_t.c.id,
                perm_t.c.user_id,
                perm_t.c.resource_type,
                perm_t.c.resource_id,
                perm_t.c.level,
                perm_t.c.created_at,
            )
            .where(perm_t.c.user_id == user_id)
            .order_by(perm_t.c.created_at)
        )
    ).fetchall()
    grants = [dict(r._mapping) for r in rows]
    names = await _resource_names(db, grants)
    for g in grants:
        g["resource_name"] = names.get((g["resource_type"], g["resource_id"]))
    return grants


async def revoke_permission(db: AsyncSession, user_id: str, grant_id: str) -> None:
    """Delete a grant after confirming it belongs to ``user_id`` (``404`` otherwise)."""
    exists = (
        await db.execute(
            select(perm_t.c.id).where(perm_t.c.id == grant_id, perm_t.c.user_id == user_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, f"Permission {grant_id!r} not found")
    await db.execute(delete(perm_t).where(perm_t.c.id == grant_id))
    await db.commit()
