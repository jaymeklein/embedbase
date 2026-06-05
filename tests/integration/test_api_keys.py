"""Integration tests for API key management."""

_MH = {"Authorization": "Bearer test-master-key-for-testing-only"}


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

async def test_create_api_key_returns_201(master_client):
    ws_id, col_id = await _setup(master_client)
    r = await master_client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/keys", json={"label": "k"}
    )
    assert r.status_code == 201


async def test_create_api_key_response_fields(master_client):
    ws_id, col_id = await _setup(master_client)
    data = await _create_key(master_client, ws_id, col_id, "my key")
    assert "id" in data
    assert data["collection_id"] == col_id
    assert data["label"] == "my key"
    assert "raw_key" in data
    assert "key_prefix" in data
    assert "created_at" in data


async def test_create_api_key_default_label(master_client):
    ws_id, col_id = await _setup(master_client)
    data = await _create_key(master_client, ws_id, col_id)
    assert data["label"] == ""


async def test_raw_key_starts_with_eb(master_client):
    ws_id, col_id = await _setup(master_client)
    raw_key = (await _create_key(master_client, ws_id, col_id))["raw_key"]
    assert raw_key.startswith("eb_")


async def test_raw_key_prefix_matches_stored_prefix(master_client):
    ws_id, col_id = await _setup(master_client)
    data = await _create_key(master_client, ws_id, col_id)
    assert data["key_prefix"] == data["raw_key"][3:11]
    assert len(data["key_prefix"]) == 8


async def test_create_api_key_collection_not_found(master_client):
    ws_id, _ = await _setup(master_client)
    r = await master_client.post(
        f"/workspaces/{ws_id}/collections/col_nope/keys", json={}
    )
    assert r.status_code == 404


async def test_each_raw_key_is_unique(master_client):
    ws_id, col_id = await _setup(master_client)
    k1 = (await _create_key(master_client, ws_id, col_id))["raw_key"]
    k2 = (await _create_key(master_client, ws_id, col_id))["raw_key"]
    assert k1 != k2


# ---------------------------------------------------------------------------
# GET .../keys
# ---------------------------------------------------------------------------

async def test_list_api_keys_returns_200(master_client):
    ws_id, col_id = await _setup(master_client)
    r = await master_client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    assert r.status_code == 200


async def test_list_api_keys_empty(master_client):
    ws_id, col_id = await _setup(master_client)
    assert (
        await master_client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json() == []


async def test_list_api_keys_shows_created_keys(master_client):
    ws_id, col_id = await _setup(master_client)
    await _create_key(master_client, ws_id, col_id, "k1")
    await _create_key(master_client, ws_id, col_id, "k2")

    keys = (
        await master_client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json()
    assert len(keys) == 2
    labels = {k["label"] for k in keys}
    assert labels == {"k1", "k2"}


async def test_list_api_keys_excludes_raw_key_and_hash(master_client):
    ws_id, col_id = await _setup(master_client)
    await _create_key(master_client, ws_id, col_id)

    for key in (
        await master_client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json():
        assert "raw_key" not in key
        assert "key_hash" not in key


# ---------------------------------------------------------------------------
# DELETE .../keys/{key_id}
# ---------------------------------------------------------------------------

async def test_revoke_api_key_returns_204(master_client):
    ws_id, col_id = await _setup(master_client)
    key_id = (await _create_key(master_client, ws_id, col_id))["id"]
    r = await master_client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/keys/{key_id}"
    )
    assert r.status_code == 204


async def test_revoke_api_key_removes_it_from_list(master_client):
    ws_id, col_id = await _setup(master_client)
    key_id = (await _create_key(master_client, ws_id, col_id, "gone"))["id"]

    await master_client.delete(f"/workspaces/{ws_id}/collections/{col_id}/keys/{key_id}")

    remaining = (
        await master_client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json()
    assert all(k["id"] != key_id for k in remaining)


async def test_revoke_api_key_not_found(master_client):
    ws_id, col_id = await _setup(master_client)
    r = await master_client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/keys/nope"
    )
    assert r.status_code == 404


async def test_revoke_one_key_leaves_others(master_client):
    ws_id, col_id = await _setup(master_client)
    k1 = await _create_key(master_client, ws_id, col_id, "keep")
    k2 = await _create_key(master_client, ws_id, col_id, "delete")

    await master_client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/keys/{k2['id']}"
    )

    remaining = (
        await master_client.get(f"/workspaces/{ws_id}/collections/{col_id}/keys")
    ).json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == k1["id"]


# ---------------------------------------------------------------------------
# Auth — negative tests
# ---------------------------------------------------------------------------

async def test_create_api_key_no_auth_returns_401(client):
    r = await client.post("/workspaces/ws_any/collections/col_any/keys", json={})
    assert r.status_code == 401


async def test_list_api_keys_no_auth_returns_401(client):
    r = await client.get("/workspaces/ws_any/collections/col_any/keys")
    assert r.status_code == 401


async def test_revoke_api_key_no_auth_returns_401(client):
    r = await client.delete("/workspaces/ws_any/collections/col_any/keys/key_any")
    assert r.status_code == 401


async def test_collection_key_cannot_mint_new_key_returns_403(client):
    """A collection-scoped key must not be able to mint additional keys (403)."""
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=_MH)).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"}, headers=_MH)
    ).json()["id"]
    raw_key = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/keys", json={}, headers=_MH
        )
    ).json()["raw_key"]

    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/keys",
        json={},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 403
