"""Collection and API key persistence services.

Owns every collection and API-key data operation so the router stays
routing-only (Section 5).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import bcrypt
from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import api_keys as keys_t
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
    """Delete a collection; cascades to its keys, documents, and job records.

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


async def mint_api_key(collection_id: str, label: str, db: AsyncSession) -> dict[str, str]:
    """Mint and persist a new API key, returning the raw value once.

    Generates a cryptographically random ``eb_``-prefixed token, stores only its
    bcrypt hash, and returns the raw key a single time.

    Args:
        collection_id: Collection the key grants access to.
        label: Human-readable label for the key.
        db: Active async database session.

    Returns:
        Key metadata plus the one-time ``raw_key`` value.
    """
    raw_key = "eb_" + secrets.token_urlsafe(32)
    key_prefix = raw_key[3:11]
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()
    key_id = uuid4().hex
    now = datetime.now(UTC).isoformat()
    await db.execute(
        insert(keys_t).values(
            id=key_id,
            collection_id=collection_id,
            key_prefix=key_prefix,
            key_hash=key_hash,
            label=label,
            created_at=now,
        )
    )
    await db.commit()
    return {
        "id": key_id,
        "collection_id": collection_id,
        "key_prefix": key_prefix,
        "label": label,
        "created_at": now,
        "raw_key": raw_key,
    }


async def create_api_key(
    ws_id: str, col_id: str, label: str, db: AsyncSession
) -> dict[str, str]:
    """Validate the collection then mint a key for it.

    Args:
        ws_id: Parent workspace ID.
        col_id: Collection the key grants access to.
        label: Human-readable label for the key.
        db: Active async database session.

    Returns:
        Key metadata plus the one-time ``raw_key`` value.

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    await require_collection(ws_id, col_id, db)
    return await mint_api_key(collection_id=col_id, label=label, db=db)


async def list_api_keys(col_id: str, db: AsyncSession) -> list[dict[str, Any]]:
    """Return a collection's API keys (metadata only — never the hash or secret).

    Args:
        col_id: Collection whose keys to list.
        db: Active async database session.

    Returns:
        One mapping per key with prefix, label, and usage timestamps.
    """
    stmt = (
        select(
            keys_t.c.id,
            keys_t.c.collection_id,
            keys_t.c.key_prefix,
            keys_t.c.label,
            keys_t.c.created_at,
            keys_t.c.last_used_at,
        )
        .where(keys_t.c.collection_id == col_id)
        .order_by(keys_t.c.created_at)
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.fetchall()]


async def revoke_api_key(col_id: str, key_id: str, db: AsyncSession) -> None:
    """Delete an API key after confirming it belongs to the collection.

    Args:
        col_id: Collection the key should belong to.
        key_id: API key ID to revoke.
        db: Active async database session.

    Raises:
        HTTPException: 404 when the key is absent from the collection.
    """
    exists = (
        await db.execute(
            select(keys_t.c.id).where(keys_t.c.id == key_id, keys_t.c.collection_id == col_id)
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"API key {key_id!r} not found")
    await db.execute(delete(keys_t).where(keys_t.c.id == key_id))
    await db.commit()
