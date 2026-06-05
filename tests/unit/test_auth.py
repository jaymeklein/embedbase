"""Auth unit tests — key hashing, prefix narrowing, master key, scoping."""
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


def test_principal_master_can_access_anything():
    from api.services.auth import Principal

    p = Principal(is_master=True)
    assert p.can_access("col_anything")


def test_principal_collection_key_is_scoped():
    from api.services.auth import Principal

    p = Principal(is_master=False, collection_id="col_a", api_key_id="k1")
    assert p.can_access("col_a")
    assert not p.can_access("col_b")


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
from api.db import collections as col_t  # noqa: E402
from api.db import workspaces as ws_t  # noqa: E402
from api.services.auth import authenticate_api_key, record_key_use  # noqa: E402

MASTER = "test-master-key-for-testing-only"  # matches conftest env


async def _seed_key(session, *, collection_id="col_seed"):
    """Create ws → collection → api_key and return the raw key string."""
    await session.execute(
        insert(ws_t).values(
            id="ws_seed", name="W", description="", color="", icon="",
            created_at="t", updated_at="t",
        )
    )
    await session.execute(
        insert(col_t).values(
            id=collection_id, workspace_id="ws_seed", name="C", description="",
            color="", icon="", created_at="t", updated_at="t",
        )
    )
    raw = "eb_" + secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=4)).decode()
    await session.execute(
        insert(keys_t).values(
            id="key_seed", collection_id=collection_id, key_prefix=raw[3:11],
            key_hash=key_hash, label="", created_at="t",
        )
    )
    await session.commit()
    return raw


async def test_validate_master_key(db_session):
    principal = await authenticate_api_key(MASTER, db_session)
    assert principal.is_master is True
    assert principal.can_access("any_collection")


async def test_validate_missing_key_raises_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(None, db_session)
    assert exc.value.status_code == 401


async def test_validate_non_eb_key_raises_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key("not-a-key", db_session)
    assert exc.value.status_code == 401


async def test_validate_collection_key_success(db_session):
    raw = await _seed_key(db_session, collection_id="col_a")
    principal = await authenticate_api_key(raw, db_session)
    assert principal.is_master is False
    assert principal.collection_id == "col_a"
    assert principal.api_key_id == "key_seed"


async def test_validate_collection_key_updates_last_used_at(db_session):
    raw = await _seed_key(db_session, collection_id="col_a")
    principal = await authenticate_api_key(raw, db_session)
    await record_key_use(principal.api_key_id, db_session)
    row = (
        await db_session.execute(
            select(keys_t.c.last_used_at).where(keys_t.c.id == "key_seed")
        )
    ).fetchone()
    assert row.last_used_at is not None


async def test_validate_wrong_secret_same_prefix_raises_401(db_session):
    raw = await _seed_key(db_session, collection_id="col_a")
    # Same eb_ prefix, different secret body → bcrypt mismatch.
    forged = raw[:11] + "X" * (len(raw) - 11)
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(forged, db_session)
    assert exc.value.status_code == 401


async def test_validate_collection_scope_mismatch_raises_403(db_session):
    raw = await _seed_key(db_session, collection_id="col_a")
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(raw, db_session, collection_id="col_other")
    assert exc.value.status_code == 403


async def test_validate_master_ignores_collection_scope(db_session):
    principal = await authenticate_api_key(MASTER, db_session, collection_id="col_x")
    assert principal.is_master is True
