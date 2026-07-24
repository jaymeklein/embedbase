"""Authentication — API keys and console login sessions.

Three kinds of credentials resolve to a :class:`Principal`:

* **Master key** — the `MASTER_API_KEY` from the environment. Master-equivalent
  (access to everything). Compared in constant time to avoid leaking the key
  length/content through timing.
* **User key** — an ``eb_`` prefixed token minted for a single user (one key per
  user). The 8-char prefix narrows the candidate rows to (usually) one, then a
  single ``bcrypt.checkpw`` confirms the full secret. What the user may reach is
  decided by their grants (see :mod:`api.services.permissions`); a deactivated
  user's key is rejected here with ``403``.
* **Console session (JWT)** — a signed, expiring token from ``POST /auth/login``
  (see :mod:`api.services.session`). An **admin** user resolves master-equivalent;
  a **non-admin** resolves to a grant-scoped principal. Sent as
  ``Authorization: Bearer``; :func:`resolve_bearer` verifies it *before* falling
  back to the API-key path. The MCP transport deliberately calls
  :func:`authenticate_api_key` directly, so JWTs never reach it (key-only).

Query/command split (CQS):

* :func:`authenticate_api_key` / :func:`resolve_bearer` — pure query; return a
  :class:`Principal` or raise.
* :func:`record_key_use` — pure command; writes ``last_used_at``; returns nothing.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import api_keys as keys_t
from api.db import users as users_t
from api.dependencies import get_db
from api.services.session import decode_session
from api.settings import settings


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    ``is_master`` means **master-equivalent** — the master key *or* an admin user's
    session; such callers may access anything (the whole management plane). A
    non-admin user carries a ``user_id`` with ``is_master=False``; what it may reach
    is resolved from that user's grants by :mod:`api.services.permissions` (this
    dataclass holds no scope of its own).
    """

    is_master: bool
    user_id: str | None = None
    api_key_id: str | None = None
    #: Set on a console-session principal whose user must change their password:
    #: such a principal may only reach the change-password endpoint.
    must_change: bool = False


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    """Pull the raw key from either ``Authorization: Bearer`` or ``X-API-Key``."""
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return value
    return None


async def authenticate_api_key(raw_key: str | None, db: AsyncSession) -> Principal:
    """Resolve ``raw_key`` to a :class:`Principal` or raise ``401``/``403``.

    Pure query — no state is mutated. Call :func:`record_key_use` separately to
    update ``last_used_at`` after a successful authentication. A key belonging to
    a deactivated user is rejected with ``403``.
    """
    if not raw_key:
        raise HTTPException(401, "Missing API key")

    # Master key — constant-time compare (never short-circuit on length).
    if secrets.compare_digest(raw_key, settings.master_api_key):
        return Principal(is_master=True)

    if not raw_key.startswith("eb_"):
        raise HTTPException(401, "Invalid API key")

    key_prefix = raw_key[3:11]
    rows = (
        await db.execute(
            select(keys_t.c.id, keys_t.c.user_id, keys_t.c.key_hash).where(
                keys_t.c.key_prefix == key_prefix
            )
        )
    ).fetchall()

    raw_bytes = raw_key.encode()
    for row in rows:
        if bcrypt.checkpw(raw_bytes, row.key_hash.encode()):
            is_active = (
                await db.execute(
                    select(users_t.c.is_active).where(users_t.c.id == row.user_id)
                )
            ).scalar_one_or_none()
            if not is_active:
                raise HTTPException(403, "User is inactive")
            return Principal(is_master=False, user_id=row.user_id, api_key_id=row.id)

    raise HTTPException(401, "Invalid API key")


async def _principal_from_claims(
    claims: dict[str, Any], db: AsyncSession, *, allow_must_change: bool
) -> Principal:
    """Turn verified JWT ``claims`` into a :class:`Principal` (or raise).

    Loads the user fresh so a deactivation, deletion, promotion/demotion, or
    password reset takes effect immediately: ``is_active`` and ``is_admin`` are read
    live, and the token's ``pwd_epoch`` must still match ``password_changed_at`` (a
    reset/change invalidates prior sessions). An admin resolves master-equivalent.
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid session token")
    row = (
        await db.execute(
            select(
                users_t.c.is_active, users_t.c.is_admin, users_t.c.password_changed_at
            ).where(users_t.c.id == user_id)
        )
    ).fetchone()
    if row is None:
        raise HTTPException(401, "Invalid session token")
    if not row.is_active:
        raise HTTPException(403, "User is inactive")
    if (claims.get("pwd_epoch") or "") != (row.password_changed_at or ""):
        raise HTTPException(401, "Session expired")
    must_change = bool(claims.get("must_change"))
    if must_change and not allow_must_change:
        raise HTTPException(403, "Password change required")
    return Principal(is_master=bool(row.is_admin), user_id=user_id, must_change=must_change)


async def resolve_bearer(
    raw: str | None, db: AsyncSession, *, allow_must_change: bool = False
) -> Principal:
    """Resolve a Bearer credential: a console session JWT, else an API key.

    A JWT is tried first — it's identified by structure and confirmed by *signature*,
    so a non-token (master key / ``eb_`` key) simply fails verification and falls
    through to :func:`authenticate_api_key`. There is no collision: ``eb_`` keys
    contain no ``.``; the master key is the signing seed, never a valid token.

    Only the console/data planes use this. The MCP transport keeps calling
    :func:`authenticate_api_key`, so a JWT is rejected there (MCP stays key-only).
    """
    if raw and raw.count(".") == 2:
        claims = decode_session(raw)
        if claims is not None:
            return await _principal_from_claims(claims, db, allow_must_change=allow_must_change)
    return await authenticate_api_key(raw, db)


async def record_key_use(key_id: str, db: AsyncSession) -> None:
    """Write ``last_used_at`` for a successfully authenticated user key.

    Best-effort: usage bookkeeping must never fail an otherwise-valid request, so a
    write error is rolled back and swallowed rather than propagated (graceful
    degradation — a failed audit write is not an auth failure).
    """
    try:
        await db.execute(
            update(keys_t)
            .where(keys_t.c.id == key_id)
            .values(last_used_at=datetime.now(UTC).isoformat())
        )
        await db.commit()
    except Exception:
        await db.rollback()


async def require_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """FastAPI dependency: authenticate the request (master key, user key, or session).

    Records ``last_used_at`` for a user key. A must-change session is rejected here
    (it may only reach the change-password endpoint). Resource authorization is
    enforced per-route via :mod:`api.services.permissions` (the path's
    collection/document is not known here).
    """
    raw_key = _extract_key(authorization, x_api_key)
    principal = await resolve_bearer(raw_key, db)
    if principal.api_key_id is not None:
        await record_key_use(principal.api_key_id, db)
    return principal


async def require_master(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """FastAPI dependency: require master-equivalent access (master key or admin user).

    Raises 403 for a non-admin user (key or session). Used as a router-level
    dependency on management routers so no endpoint can be added later without
    authentication.
    """
    raw_key = _extract_key(authorization, x_api_key)
    principal = await resolve_bearer(raw_key, db)
    if not principal.is_master:
        raise HTTPException(403, "Administrator access required")
    return principal


async def require_operator(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """FastAPI dependency for the self-service ``/auth`` routes (change-password, me).

    Accepts a must-change session (so a first-login user can set their password) and
    requires a *user* identity — the bare master key has no user, so it's rejected.
    """
    raw_key = _extract_key(authorization, x_api_key)
    principal = await resolve_bearer(raw_key, db, allow_must_change=True)
    if principal.user_id is None:
        raise HTTPException(403, "A user session is required")
    return principal
