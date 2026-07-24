"""Unit tests for the console session service — JWT mint/verify + password hashing."""

import jwt

from api.services import session
from api.settings import settings


def test_password_hash_and_verify_roundtrip():
    h = session.hash_password("s3cret-password")
    assert h and h != "s3cret-password"
    assert session.verify_password("s3cret-password", h) is True
    assert session.verify_password("wrong", h) is False


def test_verify_password_empty_hash_is_false_not_error():
    # Backfilled/pre-login rows carry password_hash="" — must fail, never raise.
    assert session.verify_password("anything", "") is False


def test_long_password_hashes_and_verifies_without_error():
    # bcrypt (v5+) raises on >72 bytes; hash+verify clamp identically so an over-long
    # password (reachable pre-auth via /auth/login) never 500s.
    pw = "x" * 100
    h = session.hash_password(pw)
    assert session.verify_password(pw, h) is True
    assert session.verify_password("y" * 100, "") is False  # unknown-user path, no raise


def test_generate_temp_password_is_random_and_long():
    a, b = session.generate_temp_password(), session.generate_temp_password()
    assert a != b
    assert len(a) >= 12


def test_mint_and_decode_roundtrip():
    token = session.mint_session("usr_1", must_change=True, pwd_epoch="epoch-1")
    claims = session.decode_session(token)
    assert claims is not None
    assert claims["sub"] == "usr_1"
    assert claims["must_change"] is True
    assert claims["pwd_epoch"] == "epoch-1"
    assert "exp" in claims


def test_decode_rejects_tampered_token():
    token = session.mint_session("usr_1", must_change=False, pwd_epoch="e")
    tampered = token[:-3] + ("abc" if token[-3:] != "abc" else "xyz")
    assert session.decode_session(tampered) is None


def test_decode_rejects_token_signed_with_other_key():
    other_key = b"a-different-signing-key-at-least-32-bytes-long"
    forged = jwt.encode({"sub": "x", "exp": 9_999_999_999}, other_key, algorithm="HS256")
    assert session.decode_session(forged) is None


def test_decode_rejects_alg_none():
    forged = jwt.encode({"sub": "x", "exp": 9_999_999_999}, None, algorithm="none")
    assert session.decode_session(forged) is None


def test_decode_rejects_missing_exp():
    token = jwt.encode({"sub": "x"}, session._signing_key(), algorithm="HS256")
    assert session.decode_session(token) is None


def test_decode_rejects_expired(monkeypatch):
    monkeypatch.setattr(settings, "session_ttl_seconds", -10)  # already expired
    token = session.mint_session("usr_1", must_change=False, pwd_epoch="e")
    assert session.decode_session(token) is None


def test_signing_key_derives_from_master_key(monkeypatch):
    k1 = session._signing_key()
    monkeypatch.setattr(settings, "master_api_key", "a-completely-different-master")
    assert session._signing_key() != k1  # rotating the master invalidates every session
