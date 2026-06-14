"""Workspace persistence service.

Owns every workspace data operation (create/list/get/update/delete) so the
router stays routing-only (Section 5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import workspaces as ws_t
from api.schemas.workspaces import WorkspaceUpdate


async def require_workspace(ws_id: str, db: AsyncSession) -> None:
    """Raise 404 if the workspace does not exist.

    Args:
        ws_id: Workspace ID to check.
        db: Active async database session.

    Raises:
        HTTPException: 404 when the workspace is absent.
    """
    exists = (await db.execute(select(ws_t.c.id).where(ws_t.c.id == ws_id))).fetchone()
    if not exists:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")


async def create_workspace(
    name: str,
    description: str,
    color: str,
    icon: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """Create a new workspace and return its data.

    Args:
        name: Display name for the workspace.
        description: Optional longer description.
        color: Hex color string (e.g. ``#8b5cf6``).
        icon: Icon identifier string.
        db: Active async database session.

    Returns:
        The new workspace's fields with zeroed aggregate counts.
    """
    now = datetime.now(UTC).isoformat()
    values: dict[str, str] = {
        "id": f"ws_{uuid4().hex[:12]}",
        "name": name,
        "description": description,
        "color": color,
        "icon": icon,
        "created_at": now,
        "updated_at": now,
    }
    await db.execute(insert(ws_t).values(**values))
    await db.commit()
    return {**values, "collection_count": 0, "document_count": 0, "chunk_count": 0}


async def list_workspaces(db: AsyncSession) -> list[dict[str, Any]]:
    """Return all workspaces with their collection counts, newest first.

    Args:
        db: Active async database session.

    Returns:
        One mapping per workspace including ``collection_count``.
    """
    stmt = (
        select(ws_t, func.count(col_t.c.id).label("collection_count"))
        .outerjoin(col_t, col_t.c.workspace_id == ws_t.c.id)
        .group_by(ws_t.c.id)
        .order_by(ws_t.c.created_at.desc())
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.fetchall()]


async def get_workspace(ws_id: str, db: AsyncSession) -> dict[str, Any]:
    """Return a workspace and its (count-less) collection rows.

    Args:
        ws_id: Workspace ID to fetch.
        db: Active async database session.

    Returns:
        The workspace mapping with a ``collections`` list.

    Raises:
        HTTPException: 404 when the workspace is absent.
    """
    row = (await db.execute(select(ws_t).where(ws_t.c.id == ws_id))).fetchone()
    if not row:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")
    cols = (
        await db.execute(
            select(col_t).where(col_t.c.workspace_id == ws_id).order_by(col_t.c.created_at)
        )
    ).fetchall()
    result = dict(row._mapping)
    result["collections"] = [dict(c._mapping) for c in cols]
    return result


async def _fetch_workspace(ws_id: str, db: AsyncSession) -> dict[str, Any]:
    """Return a single workspace row as a mapping (assumes it exists)."""
    row = (await db.execute(select(ws_t).where(ws_t.c.id == ws_id))).fetchone()
    assert row is not None
    return dict(row._mapping)


async def update_workspace(
    ws_id: str, body: WorkspaceUpdate, db: AsyncSession
) -> dict[str, Any]:
    """Apply the non-null fields of ``body`` to a workspace and return it.

    Args:
        ws_id: Workspace ID to update.
        body: Partial update; ``None`` fields are ignored.
        db: Active async database session.

    Returns:
        The workspace's current state after the update.

    Raises:
        HTTPException: 404 when the workspace is absent.
    """
    await require_workspace(ws_id, db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        updates["updated_at"] = datetime.now(UTC).isoformat()
        await db.execute(update(ws_t).where(ws_t.c.id == ws_id).values(**updates))
        await db.commit()
    return await _fetch_workspace(ws_id, db)


async def delete_workspace(ws_id: str, db: AsyncSession) -> None:
    """Delete a workspace; cascades to its collections, keys, and documents.

    Args:
        ws_id: Workspace ID to delete.
        db: Active async database session.

    Raises:
        HTTPException: 404 when the workspace is absent.
    """
    await require_workspace(ws_id, db)
    await db.execute(delete(ws_t).where(ws_t.c.id == ws_id))
    await db.commit()
