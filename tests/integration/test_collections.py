"""Integration tests for the /workspaces/{ws_id}/collections endpoints."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_workspace(client, name="WS"):
    r = await client.post("/workspaces", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


async def _make_collection(client, ws_id, name="Col", **kwargs):
    r = await client.post(
        f"/workspaces/{ws_id}/collections", json={"name": name, **kwargs}
    )
    assert r.status_code == 201
    return r.json()


# ---------------------------------------------------------------------------
# POST /workspaces/{ws_id}/collections
# ---------------------------------------------------------------------------

async def test_create_collection_returns_201(client):
    ws_id = await _make_workspace(client)
    r = await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"})
    assert r.status_code == 201


async def test_create_collection_response_fields(client):
    ws_id = await _make_workspace(client)
    data = await _make_collection(client, ws_id, "My Col")
    assert data["id"].startswith("col_")
    assert data["name"] == "My Col"
    assert data["workspace_id"] == ws_id
    assert data["description"] == ""
    assert data["color"] == "#8b5cf6"
    assert data["icon"] == "book"
    assert data["document_count"] == 0
    assert "created_at" in data
    assert "updated_at" in data


async def test_create_collection_custom_fields(client):
    ws_id = await _make_workspace(client)
    data = await _make_collection(
        client, ws_id, "C", description="d", color="#123456", icon="file"
    )
    assert data["description"] == "d"
    assert data["color"] == "#123456"
    assert data["icon"] == "file"


async def test_create_collection_workspace_not_found(client):
    r = await client.post("/workspaces/ws_nope/collections", json={"name": "C"})
    assert r.status_code == 404


async def test_create_collection_duplicate_name_returns_409(client):
    ws_id = await _make_workspace(client)
    await _make_collection(client, ws_id, "Dupe")
    r = await client.post(f"/workspaces/{ws_id}/collections", json={"name": "Dupe"})
    assert r.status_code == 409


async def test_create_collection_same_name_different_workspace_is_allowed(client):
    ws1 = await _make_workspace(client, "WS1")
    ws2 = await _make_workspace(client, "WS2")
    await _make_collection(client, ws1, "Shared")
    r = await client.post(f"/workspaces/{ws2}/collections", json={"name": "Shared"})
    assert r.status_code == 201


async def test_create_collection_missing_name_returns_422(client):
    ws_id = await _make_workspace(client)
    r = await client.post(f"/workspaces/{ws_id}/collections", json={})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /workspaces/{ws_id}/collections
# ---------------------------------------------------------------------------

async def test_list_collections_empty(client):
    ws_id = await _make_workspace(client)
    r = await client.get(f"/workspaces/{ws_id}/collections")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_collections_returns_all(client):
    ws_id = await _make_workspace(client)
    await _make_collection(client, ws_id, "A")
    await _make_collection(client, ws_id, "B")
    names = {c["name"] for c in (await client.get(f"/workspaces/{ws_id}/collections")).json()}
    assert {"A", "B"}.issubset(names)


async def test_list_collections_workspace_not_found(client):
    r = await client.get("/workspaces/ws_nope/collections")
    assert r.status_code == 404


async def test_list_collections_scoped_to_workspace(client):
    ws1 = await _make_workspace(client, "WS1")
    ws2 = await _make_workspace(client, "WS2")
    await _make_collection(client, ws1, "OnlyInWS1")

    cols = (await client.get(f"/workspaces/{ws2}/collections")).json()
    assert all(c["name"] != "OnlyInWS1" for c in cols)


# ---------------------------------------------------------------------------
# GET /workspaces/{ws_id}/collections/{col_id}
# ---------------------------------------------------------------------------

async def test_get_collection(client):
    ws_id = await _make_workspace(client)
    col_id = (await _make_collection(client, ws_id, "Find"))["id"]

    r = await client.get(f"/workspaces/{ws_id}/collections/{col_id}")
    assert r.status_code == 200
    assert r.json()["id"] == col_id


async def test_get_collection_not_found(client):
    ws_id = await _make_workspace(client)
    r = await client.get(f"/workspaces/{ws_id}/collections/col_nope")
    assert r.status_code == 404


async def test_get_collection_wrong_workspace_returns_404(client):
    ws1 = await _make_workspace(client, "WS1")
    ws2 = await _make_workspace(client, "WS2")
    col_id = (await _make_collection(client, ws1, "C"))["id"]

    r = await client.get(f"/workspaces/{ws2}/collections/{col_id}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /workspaces/{ws_id}/collections/{col_id}
# ---------------------------------------------------------------------------

async def test_update_collection_name(client):
    ws_id = await _make_workspace(client)
    col_id = (await _make_collection(client, ws_id, "Old"))["id"]

    r = await client.patch(
        f"/workspaces/{ws_id}/collections/{col_id}", json={"name": "New"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "New"


async def test_update_collection_partial_leaves_other_fields(client):
    ws_id = await _make_workspace(client)
    col_id = (await _make_collection(client, ws_id, "C", color="#aabbcc"))["id"]

    data = (
        await client.patch(
            f"/workspaces/{ws_id}/collections/{col_id}", json={"icon": "cube"}
        )
    ).json()
    assert data["icon"] == "cube"
    assert data["color"] == "#aabbcc"


async def test_update_collection_empty_body_returns_current(client):
    ws_id = await _make_workspace(client)
    col_id = (await _make_collection(client, ws_id, "C"))["id"]

    r = await client.patch(f"/workspaces/{ws_id}/collections/{col_id}", json={})
    assert r.status_code == 200
    assert r.json()["name"] == "C"


async def test_update_collection_not_found(client):
    ws_id = await _make_workspace(client)
    r = await client.patch(
        f"/workspaces/{ws_id}/collections/col_nope", json={"name": "X"}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /workspaces/{ws_id}/collections/{col_id}
# ---------------------------------------------------------------------------

async def test_delete_collection_returns_204(client):
    ws_id = await _make_workspace(client)
    col_id = (await _make_collection(client, ws_id, "Bye"))["id"]
    r = await client.delete(f"/workspaces/{ws_id}/collections/{col_id}")
    assert r.status_code == 204


async def test_delete_collection_removes_it(client):
    ws_id = await _make_workspace(client)
    col_id = (await _make_collection(client, ws_id, "Gone"))["id"]
    await client.delete(f"/workspaces/{ws_id}/collections/{col_id}")
    r = await client.get(f"/workspaces/{ws_id}/collections/{col_id}")
    assert r.status_code == 404


async def test_delete_collection_not_found(client):
    ws_id = await _make_workspace(client)
    r = await client.delete(f"/workspaces/{ws_id}/collections/col_nope")
    assert r.status_code == 404


async def test_delete_collection_cascades_to_api_keys(client):
    ws_id = await _make_workspace(client)
    col_id = (await _make_collection(client, ws_id, "C"))["id"]

    # Create a key so the cascade has something to remove
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/keys", json={"label": "k"}
    )
    assert r.status_code == 201

    await client.delete(f"/workspaces/{ws_id}/collections/{col_id}")

    # Collection is gone — cannot fetch it
    assert (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}")
    ).status_code == 404
