"""Integration tests for the /workspaces endpoints."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_workspace(client, name="Test WS", **kwargs):
    r = await client.post("/workspaces", json={"name": name, **kwargs})
    assert r.status_code == 201
    return r.json()


# ---------------------------------------------------------------------------
# POST /workspaces
# ---------------------------------------------------------------------------

async def test_create_workspace_returns_201(client):
    r = await client.post("/workspaces", json={"name": "My WS"})
    assert r.status_code == 201


async def test_create_workspace_response_fields(client):
    data = await _create_workspace(client, "My WS")
    assert data["id"].startswith("ws_")
    assert data["name"] == "My WS"
    assert data["description"] == ""
    assert data["color"] == "#6366f1"
    assert data["icon"] == "folder"
    assert data["collection_count"] == 0
    assert "created_at" in data
    assert "updated_at" in data


async def test_create_workspace_custom_fields(client):
    data = await _create_workspace(
        client, "Custom", description="desc", color="#ff0000", icon="star"
    )
    assert data["description"] == "desc"
    assert data["color"] == "#ff0000"
    assert data["icon"] == "star"


async def test_create_workspace_missing_name_returns_422(client):
    r = await client.post("/workspaces", json={})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /workspaces
# ---------------------------------------------------------------------------

async def test_list_workspaces_empty(client):
    r = await client.get("/workspaces")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_workspaces_returns_all(client):
    await _create_workspace(client, "WS1")
    await _create_workspace(client, "WS2")
    r = await client.get("/workspaces")
    names = {ws["name"] for ws in r.json()}
    assert {"WS1", "WS2"}.issubset(names)


async def test_list_workspaces_includes_collection_count(client):
    ws = await _create_workspace(client, "WS")
    ws_id = ws["id"]
    await client.post(f"/workspaces/{ws_id}/collections", json={"name": "Col"})

    workspaces = (await client.get("/workspaces")).json()
    entry = next(w for w in workspaces if w["id"] == ws_id)
    assert entry["collection_count"] == 1


# ---------------------------------------------------------------------------
# GET /workspaces/{ws_id}
# ---------------------------------------------------------------------------

async def test_get_workspace(client):
    ws_id = (await _create_workspace(client, "Find Me"))["id"]
    r = await client.get(f"/workspaces/{ws_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == ws_id
    assert data["name"] == "Find Me"
    assert data["collections"] == []


async def test_get_workspace_includes_nested_collections(client):
    ws_id = (await _create_workspace(client, "Parent"))["id"]
    await client.post(f"/workspaces/{ws_id}/collections", json={"name": "Col1"})
    await client.post(f"/workspaces/{ws_id}/collections", json={"name": "Col2"})

    data = (await client.get(f"/workspaces/{ws_id}")).json()
    names = {c["name"] for c in data["collections"]}
    assert names == {"Col1", "Col2"}


async def test_get_workspace_not_found(client):
    r = await client.get("/workspaces/ws_doesnotexist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /workspaces/{ws_id}
# ---------------------------------------------------------------------------

async def test_update_workspace_name(client):
    ws_id = (await _create_workspace(client, "Old"))["id"]
    r = await client.patch(f"/workspaces/{ws_id}", json={"name": "New"})
    assert r.status_code == 200
    assert r.json()["name"] == "New"


async def test_update_workspace_partial_leaves_other_fields(client):
    ws_id = (await _create_workspace(client, "WS", color="#aabbcc"))["id"]
    r = await client.patch(f"/workspaces/{ws_id}", json={"icon": "cube"})
    data = r.json()
    assert data["icon"] == "cube"
    assert data["color"] == "#aabbcc"  # untouched


async def test_update_workspace_empty_body_returns_current(client):
    ws_id = (await _create_workspace(client, "WS"))["id"]
    r = await client.patch(f"/workspaces/{ws_id}", json={})
    assert r.status_code == 200
    assert r.json()["name"] == "WS"


async def test_update_workspace_not_found(client):
    r = await client.patch("/workspaces/ws_nope", json={"name": "X"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /workspaces/{ws_id}
# ---------------------------------------------------------------------------

async def test_delete_workspace_returns_204(client):
    ws_id = (await _create_workspace(client, "Bye"))["id"]
    r = await client.delete(f"/workspaces/{ws_id}")
    assert r.status_code == 204


async def test_delete_workspace_removes_it(client):
    ws_id = (await _create_workspace(client, "Gone"))["id"]
    await client.delete(f"/workspaces/{ws_id}")
    assert (await client.get(f"/workspaces/{ws_id}")).status_code == 404


async def test_delete_workspace_not_found(client):
    r = await client.delete("/workspaces/ws_nope")
    assert r.status_code == 404


async def test_delete_workspace_cascades_to_collections(client):
    ws_id = (await _create_workspace(client, "Parent"))["id"]
    await client.post(f"/workspaces/{ws_id}/collections", json={"name": "Child"})

    await client.delete(f"/workspaces/{ws_id}")

    # Workspace gone → listing its collections must 404
    r = await client.get(f"/workspaces/{ws_id}/collections")
    assert r.status_code == 404
