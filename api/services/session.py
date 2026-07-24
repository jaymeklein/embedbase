"""Console session tokens (JWT) + password hashing.

Distinct from API-key auth: a *user* signs into the web console with username +
password and then carries a short-lived **HS256 JWT** (``Authorization: Bearer``).
The signing key is **derived from ``MASTER_API_KEY``** (domain-separated SHA-256),
so there is no separate session secret — rotating the master key invalidates every
session (a deliberate break-glass). Programmatic / MCP access stays API-key-only;
JWTs never reach the MCP transport (which calls ``authenticate_api_key`` directly).

Passwords are bcrypt-hashed at the same cost as API keys (``rounds=12``). The raw
password and the hash are never logged or returned.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from api.settings import settings

_BCRYPT_ROUNDS = 12
# token_urlsafe(12) → ~16 chars / ~96 bits: the one-time password handed to a user
# at creation or after an admin reset. Comfortably above the min a user must choose.
_TEMP_PASSWORD_BYTES = 12


def _bcrypt_input(raw: str) -> bytes:
    """UTF-8 bytes truncated to bcrypt's 72-byte limit.

    bcrypt hashes at most 72 bytes and (v5+) *raises* on longer input rather than
    truncating. Clamping here — identically in hash and verify — keeps them
    consistent and stops an over-long password (reachable pre-auth via
    ``/auth/login``) from becoming an unhandled 500.
    """
    return raw.encode()[:72]


# A fixed valid bcrypt hash to compare against when a username is unknown or has no
# password set yet, so login timing doesn't reveal which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(b"embedbase-timing-equalizer", bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))


def _signing_key() -> bytes:
    """Derive the HS256 signing key from the master key (domain-separated)."""
    return hashlib.sha256(b"embedbase.session.v1|" + settings.master_api_key.encode()).digest()


def hash_password(raw: str) -> str:
    """Bcrypt-hash a password (same cost as API keys); store the returned string."""
    return bcrypt.hashpw(_bcrypt_input(raw), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(raw: str, stored_hash: str) -> bool:
    """Check ``raw`` against a stored bcrypt hash, in ~constant time.

    Guards the empty-hash case — ``bcrypt.checkpw`` raises on an empty/invalid salt,
    and a user with no password set (existing rows, or before first mint) must simply
    fail rather than error. The dummy compare keeps the "no password / no such user"
    path costing the same as a real check.
    """
    if not stored_hash:
        bcrypt.checkpw(_bcrypt_input(raw), _DUMMY_HASH)
        return False
    return bcrypt.checkpw(_bcrypt_input(raw), stored_hash.encode())


def generate_temp_password() -> str:
    """A random one-time password for first login / after an admin reset."""
    return secrets.token_urlsafe(_TEMP_PASSWORD_BYTES)


def mint_session(user_id: str, *, must_change: bool, pwd_epoch: str | None) -> str:
    """Sign a short-lived console session token for ``user_id``.

    ``must_change`` marks a token that may only reach the change-password endpoint.
    ``pwd_epoch`` (the user's ``password_changed_at``) is embedded so a later reset or
    password change invalidates this token.
    """
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": user_id,
        "must_change": must_change,
        "pwd_epoch": pwd_epoch or "",
        "iat": now,
        "exp": now + timedelta(seconds=settings.session_ttl_seconds),
    }
    return jwt.encode(claims, _signing_key(), algorithm="HS256")


def decode_session(token: str) -> dict[str, Any] | None:
    """Verify a session token; return its claims, or ``None`` if invalid/expired.

    Pins ``algorithms=["HS256"]`` (blocks ``alg:none`` and algorithm-confusion) and
    requires ``exp`` (no non-expiring tokens).
    """
    try:
        return jwt.decode(
            token, _signing_key(), algorithms=["HS256"], options={"require": ["exp"]}
        )
    except jwt.InvalidTokenError:
        return None
