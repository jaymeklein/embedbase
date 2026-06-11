"""Workspace creation + read services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import documents as doc_t
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


def _workspace_node(
    ws: Any, col_rows: Sequence[Any], doc_counts: Mapping[str, int]
) -> dict[str, Any]:
    """Build a single workspace tree node from pre-fetched collection rows."""
    cols = [
        {"id": c.id, "name": c.name, "document_count": doc_counts.get(c.id, 0)}
        for c in col_rows
        if c.workspace_id == ws.id
    ]
    return {
        "id": ws.id,
        "name": ws.name,
        "collection_count": len(cols),
        "document_count": sum(int(c["document_count"]) for c in cols),
        "collections": cols,
    }


async def list_workspace_tree(db: AsyncSession) -> list[dict[str, Any]]:
    """Return every workspace with its collections and active-document counts.

    Powers the MCP ``list_workspaces`` tool. Each workspace carries a
    ``collection_count`` and ``document_count`` (active documents only — those
    not soft-deleted), plus a ``collections`` list where every entry has its own
    ``document_count``.

    Args:
        db: Active async database session.

    Returns:
        A list of workspace nodes ordered by creation time.
    """
    ws_rows = (
        await db.execute(select(ws_t.c.id, ws_t.c.name).order_by(ws_t.c.created_at))
    ).fetchall()
    col_rows = (
        await db.execute(
            select(col_t.c.id, col_t.c.workspace_id, col_t.c.name).order_by(col_t.c.created_at)
        )
    ).fetchall()
    count_rows = (
        await db.execute(
            select(doc_t.c.collection_id, func.count(doc_t.c.id))
            .where(doc_t.c.status.is_(None))
            .group_by(doc_t.c.collection_id)
        )
    ).fetchall()
    doc_counts: dict[str, int] = {row[0]: row[1] for row in count_rows}
    return [_workspace_node(ws, col_rows, doc_counts) for ws in ws_rows]
