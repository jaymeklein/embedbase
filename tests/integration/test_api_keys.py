"""Integration tests for API key management."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup(client):
    """Returns (ws_id, col_id) after creating a workspace + collection."""
    ws_id = (
        await client.post("/workspaces", json={"name": "WS"})
    ).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"})
    ).json()["id"]
    return ws_id, col_id


async def _create_key(client, ws_id, col_id, label=""):
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/keys", json={"label": label}
    )
    assert r.status_code == 201
    return r.json()


# ---------------------------------------------------------------------------
# POST .../keys
# ---------------------------------------------------------------------------

async def test_create_api_key_returns_201(client):
    ws_id, col_id = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/keys", json={"label": "k"}
    )
    assert r.status_code == 201


async def test_create_api_key_response_fields(client):
    ws_id, col_id = await _setup(client)
    data = await _create_key(client, ws_id, col_id, "my key")
    assert "id" in data
    assert data["collection_id"] == col_id
    assert data["label"] == "my key"
    assert "raw_key" in data
    assert "key_prefix" in data
    assert "created_at" in data


async def test_create_api_key_default_label(client):
    ws_id, col_id = await _setup(client)
    data = await _create_key(client, ws_id, col_id)
    assert data["label"] == ""


async def test_raw_key_starts_with_eb(client):
    ws_id, col_id = await _setup(client)
    raw_key = (await _create_key(client, ws_id, col_id))["raw_key"]
    assert raw_key.startswith("eb_")


async def test_raw_key_prefix_matches_stored_prefix(client):
    ws_id, col_id = await _setup(client)
    data = await _create_key(client, ws_id, col_id)
    # prefix is 8 chars starting at position 3 (after "eb_")
    assert data["key_prefix"] == data["raw_key"][3:11]
    assert len(data["key_prefix"]) == 8


async def test_create_api_key_collection_not_found(client):
    ws_id, _ = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/col_nope/keys", json={}
    )
    assert r.status_code == 404


async def test_each_raw_key_is_unique(client):
    ws_id, col_id = await _setup(client)
    k1 = (await _create_key(client, ws_id, col_id))["raw_key"]
    k2 = (await _create_key(client, ws_id, col_id))["raw_key"]
    assert k1 != k2


# ---------------------------------------------------------------------------
# GET .../keys
# ---------------------------------------------------------------------------

async def test_list_api_keys_returns_200(client):
    ws_id, col_id = await _setup(client)
    r = await client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    assert r.status_code == 200


async def test_list_api_keys_empty(client):
    ws_id, col_id = await _setup(client)
    assert (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json() == []


async def test_list_api_keys_shows_created_keys(client):
    ws_id, col_id = await _setup(client)
    await _create_key(client, ws_id, col_id, "k1")
    await _create_key(client, ws_id, col_id, "k2")

    keys = (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json()
    assert len(keys) == 2
    labels = {k["label"] for k in keys}
    assert labels == {"k1", "k2"}


async def test_list_api_keys_excludes_raw_key_and_hash(client):
    ws_id, col_id = await _setup(client)
    await _create_key(client, ws_id, col_id)

    for key in (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json():
        assert "raw_key" not in key
        assert "key_hash" not in key


# ---------------------------------------------------------------------------
# DELETE .../keys/{key_id}
# ---------------------------------------------------------------------------

async def test_revoke_api_key_returns_204(client):
    ws_id, col_id = await _setup(client)
    key_id = (await _create_key(client, ws_id, col_id))["id"]
    r = await client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/keys/{key_id}"
    )
    assert r.status_code == 204


async def test_revoke_api_key_removes_it_from_list(client):
    ws_id, col_id = await _setup(client)
    key_id = (await _create_key(client, ws_id, col_id, "gone"))["id"]

    await client.delete(f"/workspaces/{ws_id}/collections/{col_id}/keys/{key_id}")

    remaining = (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json()
    assert all(k["id"] != key_id for k in remaining)


async def test_revoke_api_key_not_found(client):
    ws_id, col_id = await _setup(client)
    r = await client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/keys/nope"
    )
    assert r.status_code == 404


async def test_revoke_one_key_leaves_others(client):
    ws_id, col_id = await _setup(client)
    k1 = await _create_key(client, ws_id, col_id, "keep")
    k2 = await _create_key(client, ws_id, col_id, "delete")

    await client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/keys/{k2['id']}"
    )

    remaining = (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == k1["id"]
