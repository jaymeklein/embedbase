"""Integration tests: console login flow + the admin/non-admin access matrix."""

from api.services import session

_MH = {"Authorization": "Bearer test-master-key-for-testing-only"}


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def _make_collection(client):
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=_MH)).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"}, headers=_MH)
    ).json()["id"]
    return ws_id, col_id


# ── login (coarse failures) ────────────────────────────────────────────────────

async def test_login_wrong_password_is_401(client, make_operator):
    op = await make_operator()
    r = await client.post("/auth/login", json={"username": op["username"], "password": "nope-wrong-1"})
    assert r.status_code == 401


async def test_login_unknown_user_is_401(client):
    r = await client.post("/auth/login", json={"username": "ghostuser", "password": "whatever-12345"})
    assert r.status_code == 401


async def test_login_inactive_user_is_401(client, make_operator):
    op = await make_operator()
    await client.patch(f"/users/{op['user_id']}", json={"is_active": False}, headers=_MH)
    r = await client.post(
        "/auth/login", json={"username": op["username"], "password": op["password"]}
    )
    assert r.status_code == 401


# ── forced first-login password change ─────────────────────────────────────────

async def test_first_login_must_change_blocks_app_until_changed(client):
    created = (
        await client.post(
            "/users", json={"username": "fresh1", "email": "fresh1@example.com"}, headers=_MH
        )
    ).json()
    temp = created["temp_password"]
    login = (await client.post("/auth/login", json={"username": "fresh1", "password": temp})).json()
    assert login["must_change_password"] is True
    mc = _bearer(login["access_token"])

    # A must-change session reaches only /auth/change-password + /auth/me.
    assert (await client.get("/workspaces", headers=mc)).status_code == 403
    assert (await client.get("/users", headers=mc)).status_code == 403
    assert (await client.get("/auth/me", headers=mc)).status_code == 200

    changed = (
        await client.post(
            "/auth/change-password",
            json={"current_password": temp, "new_password": "brand-new-pass-123"},
            headers=mc,
        )
    ).json()
    assert changed["must_change_password"] is False
    full = _bearer(changed["access_token"])
    assert (await client.get("/workspaces", headers=full)).status_code == 200


async def test_change_password_wrong_current_is_403(client, make_operator):
    op = await make_operator()
    r = await client.post(
        "/auth/change-password",
        json={"current_password": "not-the-current", "new_password": "another-strong-pw"},
        headers=_bearer(op["token"]),
    )
    assert r.status_code == 403


async def test_change_password_too_short_is_422(client, make_operator):
    op = await make_operator()
    r = await client.post(
        "/auth/change-password",
        json={"current_password": op["password"], "new_password": "short"},
        headers=_bearer(op["token"]),
    )
    assert r.status_code == 422


# ── admin vs non-admin access matrix ───────────────────────────────────────────

async def test_admin_session_has_full_console(client, make_operator):
    admin = await make_operator(is_admin=True)
    h = _bearer(admin["token"])
    assert (await client.post("/workspaces", json={"name": "AW"}, headers=h)).status_code == 201
    assert (await client.get("/users", headers=h)).status_code == 200
    r = await client.post(
        "/users", json={"username": "byadmin", "email": "byadmin@example.com"}, headers=h
    )
    assert r.status_code == 201


async def test_nonadmin_session_cannot_manage(client, make_operator):
    """The core guarantee: a non-admin can never manage/revoke users or write to the mgmt plane."""
    op = await make_operator(is_admin=False)
    h = _bearer(op["token"])
    assert (await client.get("/users", headers=h)).status_code == 403
    assert (
        await client.post("/users", json={"username": "xuser", "email": "x@example.com"}, headers=h)
    ).status_code == 403
    assert (await client.post("/workspaces", json={"name": "nope"}, headers=h)).status_code == 403
    assert (
        await client.post(
            f"/users/{op['user_id']}/permissions",
            json={"resource_type": "collection", "resource_id": "c", "level": "read"},
            headers=h,
        )
    ).status_code == 403


async def test_nonadmin_sees_only_granted_resources(client, make_operator):
    ws_a, col_a = await _make_collection(client)
    ws_b, _col_b = await _make_collection(client)  # never granted
    op = await make_operator(is_admin=False, grants=[("collection", col_a, "read")])
    h = _bearer(op["token"])

    listed = {w["id"] for w in (await client.get("/workspaces", headers=h)).json()}
    assert ws_a in listed and ws_b not in listed
    cols = {c["id"] for c in (await client.get(f"/workspaces/{ws_a}/collections", headers=h)).json()}
    assert cols == {col_a}


async def test_nonadmin_with_no_grants_sees_everything(client, make_operator):
    ws_id, _ = await _make_collection(client)
    op = await make_operator(is_admin=False)
    wss = (await client.get("/workspaces", headers=_bearer(op["token"]))).json()
    assert [w["id"] for w in wss] == [ws_id]  # no permissions → sees all (open default)


async def test_nonadmin_get_workspace_hides_ungranted_collections(client, make_operator):
    """A collection grant makes the workspace browsable, but the OTHER collections in it
    (and the workspace's total count) must not leak to the caller."""
    ws, col_a = await _make_collection(client)
    # A second collection in the same workspace that the caller has NO grant on.
    await client.post(f"/workspaces/{ws}/collections", json={"name": "B"}, headers=_MH)
    op = await make_operator(is_admin=False, grants=[("collection", col_a, "read")])
    h = _bearer(op["token"])

    detail = (await client.get(f"/workspaces/{ws}", headers=h)).json()
    assert {c["id"] for c in detail["collections"]} == {col_a}  # col_b not leaked
    listed = (await client.get("/workspaces", headers=h)).json()
    entry = next(w for w in listed if w["id"] == ws)
    assert entry["collection_count"] == 1  # scoped count, not the global 2


# ── reset + master regression + expiry ─────────────────────────────────────────

async def test_reset_password_invalidates_active_session(client, make_operator):
    op = await make_operator()
    h = _bearer(op["token"])
    assert (await client.get("/auth/me", headers=h)).status_code == 200
    reset = await client.post(f"/users/{op['user_id']}/reset-password", headers=_MH)
    assert reset.status_code == 201 and reset.json()["temp_password"]
    # The old token's epoch no longer matches → rejected.
    assert (await client.get("/auth/me", headers=h)).status_code == 401


async def test_deactivation_kills_active_session_with_stable_detail(client, make_operator):
    op = await make_operator()
    h = _bearer(op["token"])
    assert (await client.get("/auth/me", headers=h)).status_code == 200
    await client.patch(f"/users/{op['user_id']}", json={"is_active": False}, headers=_MH)
    r = await client.get("/auth/me", headers=h)
    assert r.status_code == 403
    # The console signs out on this exact detail (ui/src/api/client.ts::raiseForStatus);
    # rewording it would strand deactivated users signed-in with errors — keep it stable.
    assert r.json()["detail"] == "User is inactive"


async def test_master_key_still_manages(client):
    assert (await client.get("/users", headers=_MH)).status_code == 200
    assert (await client.post("/workspaces", json={"name": "M"}, headers=_MH)).status_code == 201


async def test_expired_token_is_401(client, make_operator, monkeypatch):
    from api.settings import settings as app_settings

    op = await make_operator()
    monkeypatch.setattr(app_settings, "session_ttl_seconds", -5)  # mint already-expired
    expired = session.mint_session(op["user_id"], must_change=False, pwd_epoch="whatever")
    assert (await client.get("/auth/me", headers=_bearer(expired))).status_code == 401
