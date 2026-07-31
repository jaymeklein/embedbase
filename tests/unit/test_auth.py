"""Auth unit tests — key hashing, prefix narrowing, master key, user keys."""
import secrets

import pytest


def test_key_prefix_format():
    raw_key = "eb_" + secrets.token_urlsafe(32)
    assert raw_key.startswith("eb_")
    key_prefix = raw_key[3:11]
    assert len(key_prefix) == 8


def test_bcrypt_hash_and_verify():
    import bcrypt
    raw_key = "eb_" + secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12))
    assert bcrypt.checkpw(raw_key.encode(), key_hash)
    assert not bcrypt.checkpw(b"wrong_key", key_hash)


def test_master_key_constant_time_compare():
    master = secrets.token_urlsafe(32)
    assert secrets.compare_digest(master, master)
    assert not secrets.compare_digest(master, "not_the_key")


def test_principal_carries_user_identity():
    from api.services.auth import Principal

    master = Principal(is_master=True)
    assert master.is_master and master.user_id is None

    user = Principal(is_master=False, user_id="usr_a", api_key_id="k1")
    assert not user.is_master
    assert user.user_id == "usr_a"
    assert user.api_key_id == "k1"
    assert user.rate_limit_rpm == 0  # default: inherit the global MCP limit
    assert Principal(is_master=False, user_id="usr_b", rate_limit_rpm=15).rate_limit_rpm == 15


def test_extract_key_prefers_x_api_key_then_bearer():
    from api.services.auth import _extract_key

    assert _extract_key(None, "eb_abc") == "eb_abc"
    assert _extract_key("Bearer eb_xyz", None) == "eb_xyz"
    assert _extract_key("bearer eb_lower", None) == "eb_lower"  # case-insensitive scheme
    assert _extract_key("eb_raw", None) == "eb_raw"
    assert _extract_key(None, None) is None


# ---------------------------------------------------------------------------
# authenticate_api_key — exercised against a real in-memory DB session
# ---------------------------------------------------------------------------

import bcrypt  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import insert, select  # noqa: E402

from api.db import api_keys as keys_t  # noqa: E402
from api.db import users as users_t  # noqa: E402
from api.services.auth import authenticate_api_key, record_key_use  # noqa: E402

MASTER = "test-master-key-for-testing-only"  # matches conftest env


async def _seed_user_key(session, *, user_id="usr_seed", is_active=True, rate_limit_rpm=0):
    """Create a user + their api_key and return the raw key string."""
    await session.execute(
        insert(users_t).values(
            id=user_id, email=f"{user_id}@example.com", name="",
            is_active=is_active, rate_limit_rpm=rate_limit_rpm, created_at="t", updated_at="t",
        )
    )
    raw = "eb_" + secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=4)).decode()
    await session.execute(
        insert(keys_t).values(
            id="key_seed", user_id=user_id, key_prefix=raw[3:11],
            key_hash=key_hash, label="", created_at="t",
        )
    )
    await session.commit()
    return raw


async def test_validate_master_key(db_session):
    principal = await authenticate_api_key(MASTER, db_session)
    assert principal.is_master is True
    assert principal.user_id is None


async def test_validate_missing_key_raises_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(None, db_session)
    assert exc.value.status_code == 401


async def test_validate_non_eb_key_raises_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key("not-a-key", db_session)
    assert exc.value.status_code == 401


async def test_validate_user_key_success(db_session):
    raw = await _seed_user_key(db_session, user_id="usr_a")
    principal = await authenticate_api_key(raw, db_session)
    assert principal.is_master is False
    assert principal.user_id == "usr_a"
    assert principal.api_key_id == "key_seed"


async def test_validate_user_key_carries_rate_limit(db_session):
    raw = await _seed_user_key(db_session, user_id="usr_rl", rate_limit_rpm=30)
    principal = await authenticate_api_key(raw, db_session)
    assert principal.rate_limit_rpm == 30


async def test_validate_user_key_defaults_rate_limit_to_zero(db_session):
    raw = await _seed_user_key(db_session, user_id="usr_rl0")
    principal = await authenticate_api_key(raw, db_session)
    assert principal.rate_limit_rpm == 0  # unset → inherit the global default


async def test_validate_user_key_updates_last_used_at(db_session):
    raw = await _seed_user_key(db_session, user_id="usr_a")
    principal = await authenticate_api_key(raw, db_session)
    await record_key_use(principal.api_key_id, db_session)
    row = (
        await db_session.execute(
            select(keys_t.c.last_used_at).where(keys_t.c.id == "key_seed")
        )
    ).fetchone()
    assert row.last_used_at is not None


async def test_validate_wrong_secret_same_prefix_raises_401(db_session):
    raw = await _seed_user_key(db_session, user_id="usr_a")
    # Same eb_ prefix, different secret body → bcrypt mismatch.
    forged = raw[:11] + "X" * (len(raw) - 11)
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(forged, db_session)
    assert exc.value.status_code == 401


async def test_validate_inactive_user_key_raises_403(db_session):
    raw = await _seed_user_key(db_session, user_id="usr_off", is_active=False)
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(raw, db_session)
    assert exc.value.status_code == 403


async def test_validate_master_ignores_user_lookup(db_session):
    principal = await authenticate_api_key(MASTER, db_session)
    assert principal.is_master is True


# ---------------------------------------------------------------------------
# resolve_bearer — console session JWTs vs API keys. A JWT must resolve on this
# path (REST/WS) but NOT via authenticate_api_key (the MCP path stays key-only).
# ---------------------------------------------------------------------------

from api.services import session as session_svc  # noqa: E402
from api.services.auth import resolve_bearer  # noqa: E402


async def _seed_login_user(
    db, *, user_id="usr_login", is_admin=False, is_active=True, epoch="ep0"
):
    """Seed a user able to hold a session (username + role + password epoch set)."""
    await db.execute(
        insert(users_t).values(
            id=user_id, username=user_id, email=f"{user_id}@example.com", name="",
            is_active=is_active, is_admin=is_admin, password_hash="",
            must_change_password=False, password_changed_at=epoch,
            created_at="t", updated_at="t",
        )
    )
    await db.commit()


async def test_authenticate_api_key_rejects_jwt(db_session):
    """MCP calls authenticate_api_key directly, so a session JWT must 401 there."""
    await _seed_login_user(db_session, user_id="usr_j")
    token = session_svc.mint_session("usr_j", must_change=False, pwd_epoch="ep0")
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(token, db_session)
    assert exc.value.status_code == 401


async def test_resolve_bearer_admin_jwt_is_master_equivalent(db_session):
    await _seed_login_user(db_session, user_id="usr_admin", is_admin=True)
    token = session_svc.mint_session("usr_admin", must_change=False, pwd_epoch="ep0")
    p = await resolve_bearer(token, db_session)
    assert p.is_master is True and p.user_id == "usr_admin"


async def test_resolve_bearer_nonadmin_jwt_is_scoped(db_session):
    await _seed_login_user(db_session, user_id="usr_plain", is_admin=False)
    token = session_svc.mint_session("usr_plain", must_change=False, pwd_epoch="ep0")
    p = await resolve_bearer(token, db_session)
    assert p.is_master is False and p.user_id == "usr_plain"


async def test_resolve_bearer_falls_through_to_api_key(db_session):
    raw = await _seed_user_key(db_session, user_id="usr_k")
    p = await resolve_bearer(raw, db_session)
    assert p.user_id == "usr_k" and p.api_key_id == "key_seed"


async def test_resolve_bearer_master_key_still_works(db_session):
    p = await resolve_bearer(MASTER, db_session)
    assert p.is_master is True


async def test_resolve_bearer_must_change_blocked_by_default(db_session):
    await _seed_login_user(db_session, user_id="usr_mc")
    token = session_svc.mint_session("usr_mc", must_change=True, pwd_epoch="ep0")
    with pytest.raises(HTTPException) as exc:
        await resolve_bearer(token, db_session)
    assert exc.value.status_code == 403
    # allow_must_change lets it through (only the change-password endpoint does this).
    p = await resolve_bearer(token, db_session, allow_must_change=True)
    assert p.must_change is True and p.user_id == "usr_mc"


async def test_resolve_bearer_stale_epoch_rejected(db_session):
    await _seed_login_user(db_session, user_id="usr_e", epoch="new-epoch")
    token = session_svc.mint_session("usr_e", must_change=False, pwd_epoch="old-epoch")
    with pytest.raises(HTTPException) as exc:
        await resolve_bearer(token, db_session)
    assert exc.value.status_code == 401


async def test_resolve_bearer_inactive_user_rejected(db_session):
    await _seed_login_user(db_session, user_id="usr_inactive", is_active=False)
    token = session_svc.mint_session("usr_inactive", must_change=False, pwd_epoch="ep0")
    with pytest.raises(HTTPException) as exc:
        await resolve_bearer(token, db_session)
    assert exc.value.status_code == 403
