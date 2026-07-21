"""Integration tests for the users + permissions management API (master-only)."""

_MH = {"Authorization": "Bearer test-master-key-for-testing-only"}


async def _make_user(client, email="a@example.com", **extra):
    r = await client.post("/users", json={"email": email, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _make_collection(client):
    """Create a workspace + collection, returning (ws_id, col_id)."""
    ws_id = (await client.post("/workspaces", json={"name": "WS"})).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"})
    ).json()["id"]
    return ws_id, col_id


# ── users CRUD ────────────────────────────────────────────────────────────────

async def test_create_user_returns_201_with_fields(master_client):
    data = await _make_user(master_client, "jane@example.com", name="Jane")
    assert data["id"].startswith("usr_")
    assert data["email"] == "jane@example.com"
    assert data["name"] == "Jane"
    assert data["is_active"] is True
    assert data["api_key"] is None


async def test_create_user_duplicate_email_returns_409(master_client):
    await _make_user(master_client, "dup@example.com")
    r = await master_client.post("/users", json={"email": "dup@example.com"})
    assert r.status_code == 409


async def test_create_user_rejects_invalid_email(master_client):
    r = await master_client.post("/users", json={"email": "not-an-email"})
    assert r.status_code == 422


async def test_update_user_rejects_invalid_email(master_client):
    uid = (await _make_user(master_client, "good@example.com"))["id"]
    r = await master_client.patch(f"/users/{uid}", json={"email": "bad"})
    assert r.status_code == 422


async def test_create_user_canonicalizes_email(master_client):
    data = await _make_user(master_client, "MixedCase@Example.COM")
    assert data["email"] == "mixedcase@example.com"


async def test_duplicate_email_is_case_insensitive(master_client):
    await _make_user(master_client, "Case@Example.com")
    r = await master_client.post("/users", json={"email": "case@example.com"})
    assert r.status_code == 409


async def test_list_users_returns_created(master_client):
    await _make_user(master_client, "u1@example.com")
    await _make_user(master_client, "u2@example.com")
    emails = {u["email"] for u in (await master_client.get("/users")).json()}
    assert {"u1@example.com", "u2@example.com"} <= emails


async def test_get_user_404(master_client):
    assert (await master_client.get("/users/usr_nope")).status_code == 404


async def test_update_user_deactivate(master_client):
    uid = (await _make_user(master_client, "off@example.com"))["id"]
    r = await master_client.patch(f"/users/{uid}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False


async def test_delete_user_removes_it(master_client):
    uid = (await _make_user(master_client, "gone@example.com"))["id"]
    assert (await master_client.delete(f"/users/{uid}")).status_code == 204
    assert (await master_client.get(f"/users/{uid}")).status_code == 404


# ── API key (one per user) ────────────────────────────────────────────────────

async def test_mint_user_key_returns_raw_once(master_client):
    uid = (await _make_user(master_client, "k@example.com"))["id"]
    r = await master_client.post(f"/users/{uid}/key", json={"label": "cli"})
    assert r.status_code == 201
    body = r.json()
    assert body["raw_key"].startswith("eb_")
    assert body["key_prefix"] == body["raw_key"][3:11]
    # Reading the user back exposes only key metadata, never the secret.
    summary = (await master_client.get(f"/users/{uid}")).json()["api_key"]
    assert summary["key_prefix"] == body["key_prefix"]
    assert "raw_key" not in summary and "key_hash" not in summary


async def test_mint_user_key_rotates_existing(master_client):
    uid = (await _make_user(master_client, "rot@example.com"))["id"]
    first = (await master_client.post(f"/users/{uid}/key", json={})).json()["raw_key"]
    second = (await master_client.post(f"/users/{uid}/key", json={})).json()["raw_key"]
    assert first != second
    # Still exactly one key on the user (rotation replaced it).
    assert (await master_client.get(f"/users/{uid}")).json()["api_key"] is not None


async def test_revoke_user_key(master_client):
    uid = (await _make_user(master_client, "rev@example.com"))["id"]
    await master_client.post(f"/users/{uid}/key", json={})
    assert (await master_client.delete(f"/users/{uid}/key")).status_code == 204
    assert (await master_client.get(f"/users/{uid}")).json()["api_key"] is None


async def test_revoke_missing_key_404(master_client):
    uid = (await _make_user(master_client, "nokey@example.com"))["id"]
    assert (await master_client.delete(f"/users/{uid}/key")).status_code == 404


# ── permission grants ─────────────────────────────────────────────────────────

async def test_grant_and_list_permission(master_client):
    uid = (await _make_user(master_client, "g@example.com"))["id"]
    _, col_id = await _make_collection(master_client)
    r = await master_client.post(
        f"/users/{uid}/permissions",
        json={"resource_type": "collection", "resource_id": col_id, "level": "read"},
    )
    assert r.status_code == 201
    grants = (await master_client.get(f"/users/{uid}/permissions")).json()
    assert len(grants) == 1
    assert grants[0]["resource_id"] == col_id
    assert grants[0]["level"] == "read"


async def test_grant_is_idempotent_relevel(master_client):
    uid = (await _make_user(master_client, "re@example.com"))["id"]
    _, col_id = await _make_collection(master_client)
    body = {"resource_type": "collection", "resource_id": col_id, "level": "read"}
    await master_client.post(f"/users/{uid}/permissions", json=body)
    await master_client.post(f"/users/{uid}/permissions", json={**body, "level": "write"})
    grants = (await master_client.get(f"/users/{uid}/permissions")).json()
    assert len(grants) == 1  # re-granting updated the level, not a duplicate
    assert grants[0]["level"] == "write"


async def test_grant_on_missing_resource_404(master_client):
    uid = (await _make_user(master_client, "m@example.com"))["id"]
    r = await master_client.post(
        f"/users/{uid}/permissions",
        json={"resource_type": "collection", "resource_id": "col_ghost", "level": "read"},
    )
    assert r.status_code == 404


async def test_revoke_permission(master_client):
    uid = (await _make_user(master_client, "rv@example.com"))["id"]
    _, col_id = await _make_collection(master_client)
    grant_id = (
        await master_client.post(
            f"/users/{uid}/permissions",
            json={"resource_type": "collection", "resource_id": col_id, "level": "read"},
        )
    ).json()["id"]
    assert (
        await master_client.delete(f"/users/{uid}/permissions/{grant_id}")
    ).status_code == 204
    assert (await master_client.get(f"/users/{uid}/permissions")).json() == []


async def test_delete_user_cascades_key_and_grants(master_client):
    from sqlalchemy import select

    from api.db import api_keys as keys_t
    from api.db import permissions as perm_t

    uid = (await _make_user(master_client, "casc@example.com"))["id"]
    _, col_id = await _make_collection(master_client)
    await master_client.post(f"/users/{uid}/key", json={})
    await master_client.post(
        f"/users/{uid}/permissions",
        json={"resource_type": "collection", "resource_id": col_id, "level": "read"},
    )
    await master_client.delete(f"/users/{uid}")

    # The user is gone; its grants endpoint 404s (user absent).
    assert (await master_client.get(f"/users/{uid}/permissions")).status_code == 404
    # The FK CASCADE actually removed the child rows (not just hid them behind the 404).
    async with master_client.session_factory() as db:
        keys = (await db.execute(select(keys_t.c.id).where(keys_t.c.user_id == uid))).fetchall()
        grants = (await db.execute(select(perm_t.c.id).where(perm_t.c.user_id == uid))).fetchall()
    assert keys == [] and grants == []


async def test_rotating_key_invalidates_old_key(client, make_user_key):
    """After rotation the previous raw key is rejected (401) and the new one works."""
    ws_id, col_id = await _make_collection_via(client)
    uid, key1 = await make_user_key([("collection", col_id, "read")])
    docs_url = f"/workspaces/{ws_id}/collections/{col_id}/documents"

    assert (await client.get(docs_url, headers={"X-API-Key": key1})).status_code == 200
    key2 = (await client.post(f"/users/{uid}/key", json={}, headers=_MH)).json()["raw_key"]
    assert key1 != key2
    assert (await client.get(docs_url, headers={"X-API-Key": key1})).status_code == 401
    assert (await client.get(docs_url, headers={"X-API-Key": key2})).status_code == 200


# ── auth posture ──────────────────────────────────────────────────────────────

async def test_users_route_requires_master(client):
    assert (await client.post("/users", json={"email": "x@example.com"})).status_code == 401


async def test_user_key_cannot_manage_users(client, make_user_key):
    """A user key is not the master key → 403 on the management plane."""
    _, raw = await make_user_key()
    r = await client.post(
        "/users", json={"email": "y@example.com"}, headers={"X-API-Key": raw}
    )
    assert r.status_code == 403


async def test_inactive_user_key_is_rejected(client, make_user_key):
    """Deactivating a user makes their key stop authenticating (403)."""
    ws_id, col_id = await _make_collection_via(client)
    uid, raw = await make_user_key([("collection", col_id, "read")])
    # The key works while active.
    ok = await client.get(
        f"/workspaces/{ws_id}/collections/{col_id}/documents", headers={"X-API-Key": raw}
    )
    assert ok.status_code == 200
    # Deactivate → the same key is now rejected.
    await client.patch(f"/users/{uid}", json={"is_active": False}, headers=_MH)
    r = await client.get(
        f"/workspaces/{ws_id}/collections/{col_id}/documents", headers={"X-API-Key": raw}
    )
    assert r.status_code == 403


async def _make_collection_via(client):
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=_MH)).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"}, headers=_MH)
    ).json()["id"]
    return ws_id, col_id
