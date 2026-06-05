"""Tests for workspace creation service."""

from api.services.workspaces import create_workspace


async def test_create_workspace_success(db_session) -> None:
    result = await create_workspace(
        name="My Workspace",
        description="A test workspace",
        color="#8b5cf6",
        icon="home",
        db=db_session,
    )
    assert result["name"] == "My Workspace"
    assert result["description"] == "A test workspace"
    assert result["color"] == "#8b5cf6"
    assert result["icon"] == "home"
    assert result["id"].startswith("ws_")
    assert result["collection_count"] == 0
    assert result["document_count"] == 0
    assert result["chunk_count"] == 0
    assert "created_at" in result
    assert "updated_at" in result
