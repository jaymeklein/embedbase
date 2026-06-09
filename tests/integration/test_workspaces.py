"""Integration tests for the /workspaces endpoints."""

# Master key is set by conftest; used inline in auth-negative tests.
_MH = {"Authorization": "Bearer test-master-key-for-testing-only"}


# ---------------------------------------------------------------------------
# Helpers (pass master_client so all management calls are authenticated)
# ---------------------------------------------------------------------------


async def _create_workspace(client, name="Test WS", **kwargs):
    r = await client.post("/workspaces", json={"name": name, **kwargs})
    assert r.status_code == 201
    return r.json()


# ---------------------------------------------------------------------------
# POST /workspaces
# ---------------------------------------------------------------------------


async def test_create_workspace_returns_201(master_client):
    r = await master_client.post("/workspaces", json={"name": "My WS"})
    assert r.status_code == 201


async def test_create_workspace_response_fields(master_client):
    data = await _create_workspace(master_client, "My WS")
    assert data["id"].startswith("ws_")
    assert data["name"] == "My WS"
    assert data["description"] == ""
    assert data["color"] == "#6366f1"
    assert data["icon"] == "folder"
    assert data["collection_count"] == 0
    assert "created_at" in data
    assert "updated_at" in data


async def test_create_workspace_custom_fields(master_client):
    data = await _create_workspace(
        master_client, "Custom", description="desc", color="#ff0000", icon="star"
    )
    assert data["description"] == "desc"
    assert data["color"] == "#ff0000"
    assert data["icon"] == "star"


async def test_create_workspace_missing_name_returns_422(master_client):
    r = await master_client.post("/workspaces", json={})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /workspaces
# ---------------------------------------------------------------------------


async def test_list_workspaces_empty(master_client):
    r = await master_client.get("/workspaces")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_workspaces_returns_all(master_client):
    await _create_workspace(master_client, "WS1")
    await _create_workspace(master_client, "WS2")
    r = await master_client.get("/workspaces")
    names = {ws["name"] for ws in r.json()}
    assert {"WS1", "WS2"}.issubset(names)


async def test_list_workspaces_includes_collection_count(master_client):
    ws = await _create_workspace(master_client, "WS")
    ws_id = ws["id"]
    await master_client.post(f"/workspaces/{ws_id}/collections", json={"name": "Col"})

    workspaces = (await master_client.get("/workspaces")).json()
    entry = next(w for w in workspaces if w["id"] == ws_id)
    assert entry["collection_count"] == 1


# ---------------------------------------------------------------------------
# GET /workspaces/{ws_id}
# ---------------------------------------------------------------------------


async def test_get_workspace(master_client):
    ws_id = (await _create_workspace(master_client, "Find Me"))["id"]
    r = await master_client.get(f"/workspaces/{ws_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == ws_id
    assert data["name"] == "Find Me"
    assert data["collections"] == []


async def test_get_workspace_includes_nested_collections(master_client):
    ws_id = (await _create_workspace(master_client, "Parent"))["id"]
    await master_client.post(f"/workspaces/{ws_id}/collections", json={"name": "Col1"})
    await master_client.post(f"/workspaces/{ws_id}/collections", json={"name": "Col2"})

    data = (await master_client.get(f"/workspaces/{ws_id}")).json()
    names = {c["name"] for c in data["collections"]}
    assert names == {"Col1", "Col2"}


async def test_get_workspace_not_found(master_client):
    r = await master_client.get("/workspaces/ws_doesnotexist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /workspaces/{ws_id}
# ---------------------------------------------------------------------------


async def test_update_workspace_name(master_client):
    ws_id = (await _create_workspace(master_client, "Old"))["id"]
    r = await master_client.patch(f"/workspaces/{ws_id}", json={"name": "New"})
    assert r.status_code == 200
    assert r.json()["name"] == "New"


async def test_update_workspace_partial_leaves_other_fields(master_client):
    ws_id = (await _create_workspace(master_client, "WS", color="#aabbcc"))["id"]
    r = await master_client.patch(f"/workspaces/{ws_id}", json={"icon": "cube"})
    data = r.json()
    assert data["icon"] == "cube"
    assert data["color"] == "#aabbcc"  # untouched


async def test_update_workspace_empty_body_returns_current(master_client):
    ws_id = (await _create_workspace(master_client, "WS"))["id"]
    r = await master_client.patch(f"/workspaces/{ws_id}", json={})
    assert r.status_code == 200
    assert r.json()["name"] == "WS"


async def test_update_workspace_not_found(master_client):
    r = await master_client.patch("/workspaces/ws_nope", json={"name": "X"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /workspaces/{ws_id}
# ---------------------------------------------------------------------------


async def test_delete_workspace_returns_204(master_client):
    ws_id = (await _create_workspace(master_client, "Bye"))["id"]
    r = await master_client.delete(f"/workspaces/{ws_id}")
    assert r.status_code == 204


async def test_delete_workspace_removes_it(master_client):
    ws_id = (await _create_workspace(master_client, "Gone"))["id"]
    await master_client.delete(f"/workspaces/{ws_id}")
    assert (await master_client.get(f"/workspaces/{ws_id}")).status_code == 404


async def test_delete_workspace_not_found(master_client):
    r = await master_client.delete("/workspaces/ws_nope")
    assert r.status_code == 404


async def test_delete_workspace_cascades_to_collections(master_client):
    ws_id = (await _create_workspace(master_client, "Parent"))["id"]
    await master_client.post(f"/workspaces/{ws_id}/collections", json={"name": "Child"})

    await master_client.delete(f"/workspaces/{ws_id}")

    r = await master_client.get(f"/workspaces/{ws_id}/collections")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Auth — negative tests
# ---------------------------------------------------------------------------


async def test_no_auth_returns_401(client):
    r = await client.post("/workspaces", json={"name": "x"})
    assert r.status_code == 401


async def test_no_auth_list_returns_401(client):
    r = await client.get("/workspaces")
    assert r.status_code == 401


async def test_no_auth_get_returns_401(client):
    r = await client.get("/workspaces/ws_any")
    assert r.status_code == 401


async def test_no_auth_patch_returns_401(client):
    r = await client.patch("/workspaces/ws_any", json={"name": "x"})
    assert r.status_code == 401


async def test_no_auth_delete_returns_401(client):
    r = await client.delete("/workspaces/ws_any")
    assert r.status_code == 401


async def test_collection_key_on_workspace_route_returns_403(client):
    """A collection-scoped key must be rejected (403) on management routes."""
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=_MH)).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"}, headers=_MH)
    ).json()["id"]
    raw_key = (
        await client.post(f"/workspaces/{ws_id}/collections/{col_id}/keys", json={}, headers=_MH)
    ).json()["raw_key"]

    r = await client.post(
        "/workspaces",
        json={"name": "hack"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 403
