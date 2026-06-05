"""Tests for collection and API key creation services."""

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select

from api.db import api_keys as keys_t
from api.db import collections as col_t
from api.db import workspaces as ws_t
from api.services.collections import create_collection, mint_api_key


async def _seed_workspace(db_session) -> str:
    """Create a test workspace and return its ID."""
    ws_id = "ws_test"
    await db_session.execute(
        insert(ws_t).values(
            id=ws_id,
            name="Test Workspace",
            description="",
            color="",
            icon="",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
    )
    await db_session.commit()
    return ws_id


async def _seed_collection(db_session, ws_id: str) -> str:
    """Create a test collection and return its ID."""
    col_id = "col_test"
    await db_session.execute(
        insert(col_t).values(
            id=col_id,
            workspace_id=ws_id,
            name="Test Collection",
            description="",
            color="",
            icon="",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
    )
    await db_session.commit()
    return col_id


async def test_create_collection_success(db_session) -> None:
    ws_id = await _seed_workspace(db_session)
    result = await create_collection(
        workspace_id=ws_id,
        name="My Collection",
        description="A test collection",
        color="#8b5cf6",
        icon="book",
        db=db_session,
    )
    assert result["name"] == "My Collection"
    assert result["workspace_id"] == ws_id
    assert result["description"] == "A test collection"
    assert result["color"] == "#8b5cf6"
    assert result["icon"] == "book"
    assert result["id"].startswith("col_")
    assert result["document_count"] == 0
    assert result["chunk_count"] == 0
    assert "created_at" in result
    assert "updated_at" in result


async def test_create_collection_duplicate_name_raises_409(db_session) -> None:
    ws_id = await _seed_workspace(db_session)
    await create_collection(
        workspace_id=ws_id,
        name="Duplicate",
        description="",
        color="",
        icon="",
        db=db_session,
    )
    with pytest.raises(HTTPException) as exc:
        await create_collection(
            workspace_id=ws_id,
            name="Duplicate",
            description="",
            color="",
            icon="",
            db=db_session,
        )
    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail


async def test_mint_api_key_success(db_session) -> None:
    ws_id = await _seed_workspace(db_session)
    col_id = await _seed_collection(db_session, ws_id)
    result = await mint_api_key(
        collection_id=col_id,
        label="test-key",
        db=db_session,
    )
    assert result["collection_id"] == col_id
    assert result["label"] == "test-key"
    assert result["raw_key"].startswith("eb_")
    assert len(result["key_prefix"]) == 8
    assert result["id"] is not None
    assert "created_at" in result
    row = (
        await db_session.execute(
            select(keys_t.c.key_hash).where(keys_t.c.id == result["id"])
        )
    ).fetchone()
    assert row is not None
    assert row.key_hash is not None
