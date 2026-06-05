"""Collection and API key creation services."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import uuid4

import bcrypt
from fastapi import HTTPException
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import api_keys as keys_t
from api.db import collections as col_t


async def create_collection(
    workspace_id: str,
    name: str,
    description: str,
    color: str,
    icon: str,
    db: AsyncSession,
) -> dict[str, str | int]:
    """Create a new collection in the given workspace.

    Args:
        workspace_id: ID of the parent workspace.
        name: Display name for the collection.
        description: Optional longer description.
        color: Hex color string (e.g. ``#8b5cf6``).
        icon: Icon identifier string.
        db: Active async database session.

    Returns:
        Mapping of the newly created collection's fields including
        ``document_count`` and ``chunk_count`` initialised to zero.

    Raises:
        HTTPException: 409 when a collection with the same name already exists
            in this workspace.
    """
    col_id = f"col_{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    values: dict[str, str] = {
        "id": col_id,
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
        raise HTTPException(
            409,
            f"Collection {name!r} already exists in this workspace",
        ) from None
    return {**values, "document_count": 0, "chunk_count": 0}


async def mint_api_key(
    collection_id: str,
    label: str,
    db: AsyncSession,
) -> dict[str, str]:
    """Mint and persist a new API key for the given collection.

    Generates a cryptographically random ``eb_``-prefixed token, hashes it with
    bcrypt, stores the hash, and returns the raw key once — it cannot be
    retrieved again after this call.

    Args:
        collection_id: ID of the collection the key grants access to.
        label: Human-readable label for the key.
        db: Active async database session.

    Returns:
        Mapping containing key metadata and the one-time ``raw_key`` value.
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
