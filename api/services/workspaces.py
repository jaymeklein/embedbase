"""Workspace creation service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import workspaces as ws_t


async def create_workspace(
    name: str,
    description: str,
    color: str,
    icon: str,
    db: AsyncSession,
) -> dict[str, str | int]:
    """Create a new workspace and return its data.

    Args:
        name: Display name for the workspace.
        description: Optional longer description.
        color: Hex color string (e.g. ``#8b5cf6``).
        icon: Icon identifier string.
        db: Active async database session.

    Returns:
        Mapping of the newly created workspace's fields including
        ``collection_count``, ``document_count``, and ``chunk_count``
        initialised to zero.
    """
    ws_id = f"ws_{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    values: dict[str, str] = {
        "id": ws_id,
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
