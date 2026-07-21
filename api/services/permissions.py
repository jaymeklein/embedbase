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


async def list_permissions(db: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    """Return every grant held by ``user_id``, oldest first."""
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
    return [dict(r._mapping) for r in rows]


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
