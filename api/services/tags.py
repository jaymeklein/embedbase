"""Tag persistence, assignment, correlation, and filtering service.

Owns every tag data operation so the router stays routing-only (Section 5).
Tags are normalized rows scoped per workspace; three join tables attach them
to workspaces, collections, and documents. Assignment, correlation, and
``?tag=`` filtering are all driven by :data:`_JOIN_SPECS` so a new taggable
entity needs only a new spec entry, not new branching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Table, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collection_tags, document_tags, workspace_tags
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import tags as tag_t
from api.schemas.tags import TagMerge, TagUpdate
from api.services.collections import require_collection
from api.services.workspaces import require_workspace

# kind -> (join table, entity-id column name)
_JOIN_SPECS: dict[str, tuple[Table, str]] = {
    "workspace": (workspace_tags, "workspace_id"),
    "collection": (collection_tags, "collection_id"),
    "document": (document_tags, "document_id"),
}


def normalize_tag(name: str) -> str:
    """Lowercase, trim, and collapse internal whitespace in a tag name.

    Args:
        name: Raw tag name from the client.

    Returns:
        The normalized form used for storage and de-duplication.

    Raises:
        HTTPException: 422 when the name is empty after normalization.
    """
    normalized = " ".join(name.strip().lower().split())
    if not normalized:
        raise HTTPException(422, "Tag name must not be empty")
    return normalized


async def require_tag(ws_id: str, tag_id: str, db: AsyncSession) -> None:
    """Raise 404 unless ``tag_id`` exists inside ``ws_id``."""
    exists = (
        await db.execute(
            select(tag_t.c.id).where(tag_t.c.id == tag_id, tag_t.c.workspace_id == ws_id)
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Tag {tag_id!r} not found")


async def require_document(col_id: str, doc_id: str, db: AsyncSession) -> None:
    """Raise 404 unless an active ``doc_id`` belongs to ``col_id``."""
    exists = (
        await db.execute(
            select(doc_t.c.id).where(
                doc_t.c.id == doc_id,
                doc_t.c.collection_id == col_id,
                doc_t.c.status.is_(None),
            )
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Document {doc_id!r} not found")


async def _read_tags(
    ws_id: str, db: AsyncSession, tag_id: str | None = None
) -> list[dict[str, Any]]:
    """Return workspace tags with per-entity usage counts, ordered by name."""
    stmt = (
        select(
            tag_t,
            func.count(func.distinct(workspace_tags.c.workspace_id)).label("workspace_count"),
            func.count(func.distinct(collection_tags.c.collection_id)).label("collection_count"),
            func.count(func.distinct(document_tags.c.document_id)).label("document_count"),
        )
        .select_from(
            tag_t.outerjoin(workspace_tags, workspace_tags.c.tag_id == tag_t.c.id)
            .outerjoin(collection_tags, collection_tags.c.tag_id == tag_t.c.id)
            .outerjoin(document_tags, document_tags.c.tag_id == tag_t.c.id)
        )
        .where(tag_t.c.workspace_id == ws_id)
        .group_by(tag_t.c.id)
        .order_by(tag_t.c.name)
    )
    if tag_id is not None:
        stmt = stmt.where(tag_t.c.id == tag_id)
    rows = (await db.execute(stmt)).fetchall()
    return [dict(row._mapping) for row in rows]


async def create_tag(
    ws_id: str, name: str, color: str | None, db: AsyncSession
) -> dict[str, Any]:
    """Create a normalized tag in a workspace and return it with zero counts.

    Raises:
        HTTPException: 404 if the workspace is absent, 409 on a duplicate name.
    """
    await require_workspace(ws_id, db)
    values = {
        "id": f"tag_{uuid4().hex[:12]}",
        "workspace_id": ws_id,
        "name": normalize_tag(name),
        "color": color,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        await db.execute(insert(tag_t).values(**values))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, f"Tag {values['name']!r} already exists") from None
    return (await _read_tags(ws_id, db, tag_id=values["id"]))[0]


async def list_tags(ws_id: str, db: AsyncSession) -> list[dict[str, Any]]:
    """Return all tags in a workspace with usage counts.

    Raises:
        HTTPException: 404 when the workspace is absent.
    """
    await require_workspace(ws_id, db)
    return await _read_tags(ws_id, db)


async def update_tag(
    ws_id: str, tag_id: str, body: TagUpdate, db: AsyncSession
) -> dict[str, Any]:
    """Rename and/or recolor a tag, returning its current state with counts.

    Raises:
        HTTPException: 404 if the tag is absent, 409 if the new name collides.
    """
    await require_tag(ws_id, tag_id, db)
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = normalize_tag(body.name)
    if body.color is not None:
        updates["color"] = body.color
    if updates:
        try:
            await db.execute(update(tag_t).where(tag_t.c.id == tag_id).values(**updates))
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "A tag with that name already exists") from None
    return (await _read_tags(ws_id, db, tag_id=tag_id))[0]


async def delete_tag(ws_id: str, tag_id: str, db: AsyncSession) -> None:
    """Delete a tag; cascades to every workspace/collection/document assignment.

    Raises:
        HTTPException: 404 when the tag is absent from the workspace.
    """
    await require_tag(ws_id, tag_id, db)
    await db.execute(delete(tag_t).where(tag_t.c.id == tag_id))
    await db.commit()


async def _repoint(join: Table, col: str, source: str, target: str, db: AsyncSession) -> None:
    """Move ``source`` assignments to ``target`` in one join table, skipping dups."""
    entity = join.c[col]
    already = select(entity).where(join.c.tag_id == target)
    await db.execute(delete(join).where(join.c.tag_id == source, entity.in_(already)))
    await db.execute(update(join).where(join.c.tag_id == source).values(tag_id=target))


async def merge_tags(ws_id: str, body: TagMerge, db: AsyncSession) -> dict[str, Any]:
    """Repoint every assignment of ``source_id`` onto ``target_id``, then delete source.

    Raises:
        HTTPException: 404 if either tag is absent, 422 if source == target.
    """
    if body.source_id == body.target_id:
        raise HTTPException(422, "Cannot merge a tag into itself")
    await require_tag(ws_id, body.source_id, db)
    await require_tag(ws_id, body.target_id, db)
    for join, col in _JOIN_SPECS.values():
        await _repoint(join, col, body.source_id, body.target_id, db)
    await db.execute(delete(tag_t).where(tag_t.c.id == body.source_id))
    await db.commit()
    return (await _read_tags(ws_id, db, tag_id=body.target_id))[0]


async def tag_items(ws_id: str, tag_id: str, db: AsyncSession) -> dict[str, Any]:
    """Return the collections and active documents carrying ``tag_id``.

    Raises:
        HTTPException: 404 when the tag is absent from the workspace.
    """
    await require_tag(ws_id, tag_id, db)
    col_rows = (
        await db.execute(
            select(col_t.c.id, col_t.c.name)
            .select_from(col_t.join(collection_tags, collection_tags.c.collection_id == col_t.c.id))
            .where(collection_tags.c.tag_id == tag_id)
            .order_by(col_t.c.name)
        )
    ).fetchall()
    doc_rows = (
        await db.execute(
            select(doc_t.c.id, doc_t.c.filename, doc_t.c.collection_id)
            .select_from(doc_t.join(document_tags, document_tags.c.document_id == doc_t.c.id))
            .where(document_tags.c.tag_id == tag_id, doc_t.c.status.is_(None))
            .order_by(doc_t.c.filename)
        )
    ).fetchall()
    return {
        "collections": [dict(r._mapping) for r in col_rows],
        "documents": [dict(r._mapping) for r in doc_rows],
    }


async def _assign(kind: str, entity_id: str, tag_id: str, db: AsyncSession) -> None:
    """Idempotently attach ``tag_id`` to one entity (no-op if already attached)."""
    join, col = _JOIN_SPECS[kind]
    stmt = sqlite_insert(join).values({col: entity_id, "tag_id": tag_id}).on_conflict_do_nothing()
    await db.execute(stmt)
    await db.commit()


async def _unassign(kind: str, entity_id: str, tag_id: str, db: AsyncSession) -> None:
    """Detach ``tag_id`` from one entity (no-op if not attached)."""
    join, col = _JOIN_SPECS[kind]
    await db.execute(delete(join).where(join.c[col] == entity_id, join.c.tag_id == tag_id))
    await db.commit()


async def assign_workspace_tag(ws_id: str, tag_id: str, db: AsyncSession) -> None:
    """Attach a tag to the workspace itself."""
    await require_workspace(ws_id, db)
    await require_tag(ws_id, tag_id, db)
    await _assign("workspace", ws_id, tag_id, db)


async def unassign_workspace_tag(ws_id: str, tag_id: str, db: AsyncSession) -> None:
    """Detach a tag from the workspace itself."""
    await require_tag(ws_id, tag_id, db)
    await _unassign("workspace", ws_id, tag_id, db)


async def assign_collection_tag(
    ws_id: str, col_id: str, tag_id: str, db: AsyncSession
) -> None:
    """Attach a tag to a collection in the workspace."""
    await require_collection(ws_id, col_id, db)
    await require_tag(ws_id, tag_id, db)
    await _assign("collection", col_id, tag_id, db)


async def unassign_collection_tag(
    ws_id: str, col_id: str, tag_id: str, db: AsyncSession
) -> None:
    """Detach a tag from a collection in the workspace."""
    await require_collection(ws_id, col_id, db)
    await require_tag(ws_id, tag_id, db)
    await _unassign("collection", col_id, tag_id, db)


async def assign_document_tag(
    ws_id: str, col_id: str, doc_id: str, tag_id: str, db: AsyncSession
) -> None:
    """Attach a tag to a document in the collection."""
    await require_collection(ws_id, col_id, db)
    await require_document(col_id, doc_id, db)
    await require_tag(ws_id, tag_id, db)
    await _assign("document", doc_id, tag_id, db)


async def unassign_document_tag(
    ws_id: str, col_id: str, doc_id: str, tag_id: str, db: AsyncSession
) -> None:
    """Detach a tag from a document in the collection."""
    await require_collection(ws_id, col_id, db)
    await require_document(col_id, doc_id, db)
    await require_tag(ws_id, tag_id, db)
    await _unassign("document", doc_id, tag_id, db)


async def matching_entity_ids(
    kind: str, names: list[str], db: AsyncSession
) -> list[str]:
    """Return entity IDs of ``kind`` carrying every tag in ``names`` (AND filter)."""
    norm = {normalize_tag(n) for n in names}
    join, col = _JOIN_SPECS[kind]
    stmt = (
        select(join.c[col])
        .select_from(join.join(tag_t, tag_t.c.id == join.c.tag_id))
        .where(tag_t.c.name.in_(norm))
        .group_by(join.c[col])
        .having(func.count(func.distinct(tag_t.c.name)) == len(norm))
    )
    return [row[0] for row in (await db.execute(stmt)).fetchall()]


async def tags_by_entity(
    kind: str, entity_ids: list[str], db: AsyncSession
) -> dict[str, list[dict[str, Any]]]:
    """Map each entity ID to its list of ``{id, name, color}`` tags, ordered by name."""
    if not entity_ids:
        return {}
    join, col = _JOIN_SPECS[kind]
    rows = (
        await db.execute(
            select(join.c[col].label("eid"), tag_t.c.id, tag_t.c.name, tag_t.c.color)
            .select_from(join.join(tag_t, tag_t.c.id == join.c.tag_id))
            .where(join.c[col].in_(entity_ids))
            .order_by(tag_t.c.name)
        )
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row.eid, []).append({"id": row.id, "name": row.name, "color": row.color})
    return out


async def attach_tags(
    kind: str, rows: list[dict[str, Any]], id_key: str, db: AsyncSession
) -> list[dict[str, Any]]:
    """Add a ``tags`` list to each row mapping in place, keyed by ``id_key``."""
    mapping = await tags_by_entity(kind, [row[id_key] for row in rows], db)
    for row in rows:
        row["tags"] = mapping.get(row[id_key], [])
    return rows
