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


async def test_create_workspace_without_capability_returns_403(client, make_user_key):
    """A user without the create_workspace capability cannot create workspaces."""
    _, raw_key = await make_user_key()  # no capability

    r = await client.post(
        "/workspaces",
        json={"name": "hack"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 403


async def test_create_workspace_with_capability_returns_201(client, make_user_key):
    _, key = await make_user_key(grants=[("capability", "create_workspace", "write")])
    r = await client.post("/workspaces", json={"name": "Mine"}, headers={"X-API-Key": key})
    assert r.status_code == 201


async def test_scoped_creator_can_use_the_workspace_they_made(client, make_user_key):
    # Scope the user to some existing collection, then give them the create capability.
    ws0 = (await client.post("/workspaces", json={"name": "Other"}, headers=_MH)).json()["id"]
    col0 = (
        await client.post(f"/workspaces/{ws0}/collections", json={"name": "C"}, headers=_MH)
    ).json()["id"]
    _, key = await make_user_key(
        grants=[("collection", col0, "read"), ("capability", "create_workspace", "write")]
    )
    uh = {"X-API-Key": key}

    created = (await client.post("/workspaces", json={"name": "Mine"}, headers=uh)).json()
    # A scoped creator would not otherwise see a new top-level workspace — the auto-grant
    # gives them write, so it lists with can_write and they can add collections to it.
    listed = {w["id"]: w for w in (await client.get("/workspaces", headers=uh)).json()}
    assert listed.get(created["id"], {}).get("can_write") is True
    r = await client.post(
        f"/workspaces/{created['id']}/collections", json={"name": "New"}, headers=uh
    )
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# Edit / delete — scope-permissioned (write), not admin-only
# ---------------------------------------------------------------------------


async def test_update_workspace_unrestricted_user_allowed(client, make_user_key):
    """A user with no permissions is unrestricted → may edit any workspace."""
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=_MH)).json()["id"]
    _, raw = await make_user_key()  # no grants → unrestricted
    r = await client.patch(
        f"/workspaces/{ws_id}", json={"name": "Renamed"}, headers={"X-API-Key": raw}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"


async def test_update_workspace_requires_write(client, make_user_key):
    """A workspace read grant scopes visibility but denies edits (403); write allows them."""
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=_MH)).json()["id"]

    _, ro = await make_user_key(grants=[("workspace", ws_id, "read")])
    r = await client.patch(f"/workspaces/{ws_id}", json={"name": "X"}, headers={"X-API-Key": ro})
    assert r.status_code == 403

    _, rw = await make_user_key(grants=[("workspace", ws_id, "write")])
    r = await client.patch(f"/workspaces/{ws_id}", json={"name": "Y"}, headers={"X-API-Key": rw})
    assert r.status_code == 200


async def test_delete_workspace_requires_write(client, make_user_key):
    """Delete is a write: a read-scoped user is denied (403); a write-scoped one succeeds (204)."""
    ws_ro = (await client.post("/workspaces", json={"name": "RO"}, headers=_MH)).json()["id"]
    ws_rw = (await client.post("/workspaces", json={"name": "RW"}, headers=_MH)).json()["id"]

    _, ro = await make_user_key(grants=[("workspace", ws_ro, "read")])
    r = await client.delete(f"/workspaces/{ws_ro}", headers={"X-API-Key": ro})
    assert r.status_code == 403

    _, rw = await make_user_key(grants=[("workspace", ws_rw, "write")])
    r = await client.delete(f"/workspaces/{ws_rw}", headers={"X-API-Key": rw})
    assert r.status_code == 204


async def test_edit_workspace_out_of_scope_denied(client, make_user_key):
    """A user scoped to a different workspace can't edit an unrelated one (not even visible)."""
    target = (await client.post("/workspaces", json={"name": "Target"}, headers=_MH)).json()["id"]
    other = (await client.post("/workspaces", json={"name": "Other"}, headers=_MH)).json()["id"]
    _, key = await make_user_key(grants=[("workspace", other, "write")])
    r = await client.patch(f"/workspaces/{target}", json={"name": "X"}, headers={"X-API-Key": key})
    assert r.status_code == 403


async def test_workspace_list_and_get_report_can_write(client, make_user_key):
    """The workspace list + detail tag each row with can_write for the caller."""
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=_MH)).json()["id"]

    _, ro = await make_user_key(grants=[("workspace", ws_id, "read")])
    r = await client.get("/workspaces", headers={"X-API-Key": ro})
    listed = {w["id"]: w for w in r.json()}
    assert listed[ws_id]["can_write"] is False
    detail = await client.get(f"/workspaces/{ws_id}", headers={"X-API-Key": ro})
    assert detail.json()["can_write"] is False

    _, rw = await make_user_key(grants=[("workspace", ws_id, "write")])
    detail = await client.get(f"/workspaces/{ws_id}", headers={"X-API-Key": rw})
    assert detail.json()["can_write"] is True


async def test_edit_nonexistent_workspace_is_403_not_404_for_scoped_user(client, make_user_key):
    """Authorize-before-existence: a scoped user can't probe which workspaces exist via edit."""
    other = (await client.post("/workspaces", json={"name": "Other"}, headers=_MH)).json()["id"]
    _, key = await make_user_key(grants=[("workspace", other, "write")])
    r = await client.patch("/workspaces/ws_nope", json={"name": "X"}, headers={"X-API-Key": key})
    assert r.status_code == 403
