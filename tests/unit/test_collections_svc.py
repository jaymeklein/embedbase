"""Tests for the collection creation service."""

import pytest
from fastapi import HTTPException
from sqlalchemy import insert

from api.db import workspaces as ws_t
from api.services.collections import create_collection


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
