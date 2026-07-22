"""Permission grants + the authorization authority.

A grant gives a user a ``level`` (``read`` or ``write``) on one resource
(``workspace`` | ``collection`` | ``document``). Grants are **hierarchical** —
workspace ⊃ collection ⊃ document — and ``write`` implies ``read``. So a user
with ``read`` on a workspace can read every collection and document under it.

This module is the single place that answers *"may this principal do X to
resource Y?"*: routers and MCP tools call :func:`authorize_collection` /
:func:`authorize_document` and let them raise ``403``. The master principal
bypasses every check. It also owns grant CRUD for the management API.
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
ResourceType = Literal["workspace", "collection", "document"]

_LEVELS: dict[str, int] = {"read": 1, "write": 2}
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


def _satisfies(grant_level: str, need: str) -> bool:
    """True when a held ``grant_level`` meets the required ``need`` (write ⊇ read)."""
    return _LEVELS.get(grant_level, 0) >= _LEVELS[need]


def _denied(need: str) -> str:
    return f"API key not authorized to {need} this resource"


# ---------------------------------------------------------------------------
# Hierarchy resolution — a resource plus the ancestors a grant can cover
# ---------------------------------------------------------------------------

async def _collection_targets(db: AsyncSession, collection_id: str) -> list[_Target] | None:
    """``[(collection, id), (workspace, ws)]`` or ``None`` if the collection is unknown."""
    ws_id = (
        await db.execute(select(col_t.c.workspace_id).where(col_t.c.id == collection_id))
    ).scalar_one_or_none()
    if ws_id is None:
        return None
    return [("collection", collection_id), ("workspace", ws_id)]


async def _document_targets(db: AsyncSession, document_id: str) -> list[_Target] | None:
    """``[(document, id), (collection, c), (workspace, ws)]`` or ``None`` if unknown."""
    col_id = (
        await db.execute(select(doc_t.c.collection_id).where(doc_t.c.id == document_id))
    ).scalar_one_or_none()
    if col_id is None:
        return None
    parents = await _collection_targets(db, col_id) or []
    return [("document", document_id), *parents]


async def _has_grant(
    db: AsyncSession, user_id: str, targets: list[_Target], need: str
) -> bool:
    """True when the user holds a ``need``-satisfying grant on any of ``targets``."""
    resource_ids = [rid for _, rid in targets]
    rows = (
        await db.execute(
            select(perm_t.c.resource_type, perm_t.c.resource_id, perm_t.c.level).where(
                perm_t.c.user_id == user_id, perm_t.c.resource_id.in_(resource_ids)
            )
        )
    ).fetchall()
    target_set = set(targets)
    return any(
        (r.resource_type, r.resource_id) in target_set and _satisfies(r.level, need)
        for r in rows
    )


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
        targets = await _collection_targets(db, collection_id)
        if targets and await _has_grant(db, principal.user_id, targets, need):
            return
    raise HTTPException(403, _denied(need))


async def authorize_document(
    db: AsyncSession, principal: Principal, document_id: str, need: Level = "read"
) -> None:
    """Raise ``403`` unless ``principal`` may ``need`` (read/write) ``document_id``.

    A grant on the document, its collection, or its workspace all satisfy the check.
    """
    if principal.is_master:
        return
    if principal.user_id is not None:
        targets = await _document_targets(db, document_id)
        if targets and await _has_grant(db, principal.user_id, targets, need):
            return
    raise HTTPException(403, _denied(need))


async def _user_grant_targets(db: AsyncSession, user_id: str) -> set[_Target]:
    """Every ``(resource_type, resource_id)`` the user holds any grant on."""
    rows = (
        await db.execute(
            select(perm_t.c.resource_type, perm_t.c.resource_id).where(
                perm_t.c.user_id == user_id
            )
        )
    ).fetchall()
    return {(r.resource_type, r.resource_id) for r in rows}


async def readable_collection_ids(
    db: AsyncSession, principal: Principal, candidate_ids: list[str]
) -> list[str]:
    """Filter ``candidate_ids`` to the collections ``principal`` may read (order kept).

    A collection is readable when the user holds any grant on it or on its parent
    workspace. Master sees them all; a user with no grants sees none.
    """
    ids = list(dict.fromkeys(candidate_ids))
    if principal.is_master:
        return ids
    if principal.user_id is None or not ids:
        return []
    rows = (
        await db.execute(
            select(col_t.c.id, col_t.c.workspace_id).where(col_t.c.id.in_(ids))
        )
    ).fetchall()
    ws_by_col = {r.id: r.workspace_id for r in rows}
    grants = await _user_grant_targets(db, principal.user_id)
    return [
        cid
        for cid in ids
        if (ws := ws_by_col.get(cid)) is not None
        and (("collection", cid) in grants or ("workspace", ws) in grants)
    ]


async def readable_collection_scope(
    db: AsyncSession, principal: Principal
) -> list[str] | None:
    """The full set of collection ids ``principal`` may read — or ``None`` when unrestricted.

    ``None`` means master-equivalent (the master key or an admin user): callers skip
    filtering and see every collection. A non-admin gets the explicit list their grants
    make readable — each collection they hold a grant on, plus every collection under a
    granted workspace — and ``[]`` when no grant qualifies (reads nothing).

    Unlike :func:`readable_collection_ids`, which narrows a caller-supplied candidate list,
    this returns the *whole* readable set, for the global cross-collection views (the
    ingestion queue and index status) that have no natural candidate list to filter.
    Document-level grants are intentionally excluded: like the collection documents listing
    (authorized on the *collection*), these collection-grained views surface a collection
    only when a collection- or workspace-level grant makes it browsable.
    """
    if principal.is_master:
        return None
    if principal.user_id is None:
        return []
    grants = await _user_grant_targets(db, principal.user_id)
    ws_ids = [rid for (rtype, rid) in grants if rtype == "workspace"]
    col_ids = {rid for (rtype, rid) in grants if rtype == "collection"}
    if ws_ids:
        rows = (
            await db.execute(select(col_t.c.id).where(col_t.c.workspace_id.in_(ws_ids)))
        ).fetchall()
        col_ids.update(r.id for r in rows)
    return list(col_ids)


async def filter_workspace_tree(
    db: AsyncSession, principal: Principal, tree: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Prune a workspace tree (from ``list_workspace_tree``) to what ``principal`` reads.

    Keeps a workspace when the user holds a workspace-level grant (all its
    collections show) or has a grant on at least one of its collections (only
    those show). Counts are recomputed against the surviving collections.
    """
    if principal.is_master:
        return tree
    if principal.user_id is None:
        return []
    grants = await _user_grant_targets(db, principal.user_id)
    filtered: list[dict[str, Any]] = []
    for ws in tree:
        ws_granted = ("workspace", ws["id"]) in grants
        cols = [
            c for c in ws["collections"] if ws_granted or ("collection", c["id"]) in grants
        ]
        if not ws_granted and not cols:
            continue
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

    A workspace is readable when the user holds a grant on it, or on any collection
    within it (so they can navigate to that collection) — the same visibility rule as
    :func:`filter_workspace_tree`. Master sees all; a user with no grants sees none.
    """
    ids = list(dict.fromkeys(candidate_ids))
    if principal.is_master:
        return ids
    if principal.user_id is None or not ids:
        return []
    grants = await _user_grant_targets(db, principal.user_id)
    granted_ws = {rid for (rtype, rid) in grants if rtype == "workspace"}
    granted_cols = [rid for (rtype, rid) in grants if rtype == "collection"]
    ws_via_col: set[str] = set()
    if granted_cols:
        rows = (
            await db.execute(
                select(col_t.c.workspace_id).where(col_t.c.id.in_(granted_cols))
            )
        ).fetchall()
        ws_via_col = {r.workspace_id for r in rows}
    readable = granted_ws | ws_via_col
    return [wid for wid in ids if wid in readable]


async def authorize_workspace(
    db: AsyncSession, principal: Principal, workspace_id: str, need: Level = "read"
) -> None:
    """Raise ``403`` unless ``principal`` may ``need`` ``workspace_id``.

    Browsing (``read``) passes on a grant on the workspace or any collection within it;
    ``write`` requires a workspace-level grant. Workspace writes are admin-only in the
    router, so ``read`` is the path non-admins use.
    """
    if principal.is_master:
        return
    if principal.user_id is not None:
        if need == "read":
            if await readable_workspace_ids(db, principal, [workspace_id]):
                return
        elif await _has_grant(db, principal.user_id, [("workspace", workspace_id)], need):
            return
    raise HTTPException(403, _denied(need))


# ---------------------------------------------------------------------------
# Grant CRUD — used by the users management API (master-only)
# ---------------------------------------------------------------------------

async def _require_resource(db: AsyncSession, resource_type: str, resource_id: str) -> None:
    """Raise ``404`` when the target of a grant does not exist."""
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
