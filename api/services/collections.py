"""Collection persistence service.

Owns every collection data operation so the router stays routing-only (Section 5).
API keys are no longer collection-scoped — they belong to users
(see :mod:`api.services.users`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import documents as doc_t
from api.schemas.collections import CollectionUpdate
from api.services.workspaces import require_workspace


async def require_collection(ws_id: str, col_id: str, db: AsyncSession) -> None:
    """Raise 404 unless ``col_id`` exists inside ``ws_id``.

    Args:
        ws_id: Parent workspace ID.
        col_id: Collection ID to check.
        db: Active async database session.

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    exists = (
        await db.execute(
            select(col_t.c.id).where(col_t.c.id == col_id, col_t.c.workspace_id == ws_id)
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Collection {col_id!r} not found")


async def create_collection(
    workspace_id: str,
    name: str,
    description: str,
    color: str,
    icon: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """Create a new collection in the given workspace.

    Args:
        workspace_id: ID of the parent workspace.
        name: Display name for the collection.
        description: Optional longer description.
        color: Hex color string (e.g. ``#8b5cf6``).
        icon: Icon identifier string.
        db: Active async database session.

    Returns:
        The new collection's fields with zeroed aggregate counts.

    Raises:
        HTTPException: 404 if the workspace is absent, 409 on a duplicate name.
    """
    await require_workspace(workspace_id, db)
    now = datetime.now(UTC).isoformat()
    values: dict[str, str] = {
        "id": f"col_{uuid4().hex[:12]}",
        "workspace_id": workspace_id,
        "name": name,
        "description": description,
        "color": color,
        "icon": icon,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.execute(insert(col_t).values(**values))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, f"Collection {name!r} already exists in this workspace") from None
    return {**values, "document_count": 0, "chunk_count": 0}


async def list_collections(
    ws_id: str, db: AsyncSession, tags: list[str] | None = None
) -> list[dict[str, Any]]:
    """Return a workspace's collections with document counts and assigned tags.

    Args:
        ws_id: Parent workspace ID.
        db: Active async database session.
        tags: Optional tag names; only collections carrying *all* of them are
            returned (AND filter).

    Returns:
        One mapping per collection including ``document_count`` and ``tags``.

    Raises:
        HTTPException: 404 when the workspace is absent.
    """
    from api.services.tags import attach_tags, matching_entity_ids

    await require_workspace(ws_id, db)
    stmt = (
        select(col_t, func.count(doc_t.c.id).label("document_count"))
        .outerjoin(doc_t, (doc_t.c.collection_id == col_t.c.id) & doc_t.c.status.is_(None))
        .where(col_t.c.workspace_id == ws_id)
        .group_by(col_t.c.id)
        .order_by(col_t.c.created_at)
    )
    if tags:
        stmt = stmt.where(col_t.c.id.in_(await matching_entity_ids("collection", tags, db)))
    rows = [dict(row._mapping) for row in (await db.execute(stmt)).fetchall()]
    return await attach_tags("collection", rows, "id", db)


async def _fetch_collection(col_id: str, db: AsyncSession) -> dict[str, Any]:
    """Return a single collection row as a mapping (assumes it exists)."""
    row = (await db.execute(select(col_t).where(col_t.c.id == col_id))).fetchone()
    assert row is not None
    return dict(row._mapping)


async def get_collection(ws_id: str, col_id: str, db: AsyncSession) -> dict[str, Any]:
    """Return a single collection scoped to its workspace.

    Args:
        ws_id: Parent workspace ID.
        col_id: Collection ID to fetch.
        db: Active async database session.

    Returns:
        The collection mapping.

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    await require_collection(ws_id, col_id, db)
    return await _fetch_collection(col_id, db)


async def update_collection(
    ws_id: str, col_id: str, body: CollectionUpdate, db: AsyncSession
) -> dict[str, Any]:
    """Apply the non-null fields of ``body`` to a collection and return it.

    Args:
        ws_id: Parent workspace ID.
        col_id: Collection ID to update.
        body: Partial update; ``None`` fields are ignored.
        db: Active async database session.

    Returns:
        The collection's current state after the update.

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    await require_collection(ws_id, col_id, db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        updates["updated_at"] = datetime.now(UTC).isoformat()
        await db.execute(update(col_t).where(col_t.c.id == col_id).values(**updates))
        await db.commit()
    return await _fetch_collection(col_id, db)


async def delete_collection(ws_id: str, col_id: str, db: AsyncSession) -> None:
    """Delete a collection; cascades to its documents and job records.

    Args:
        ws_id: Parent workspace ID.
        col_id: Collection ID to delete.
        db: Active async database session.

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    await require_collection(ws_id, col_id, db)
    await db.execute(delete(col_t).where(col_t.c.id == col_id))
    await db.commit()
