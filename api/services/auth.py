"""API key authentication.

Two kinds of credentials are accepted:

* **Master key** — the `MASTER_API_KEY` from the environment. Grants access to
  every workspace/collection/document. Compared in constant time to avoid leaking
  the key length/content through timing.
* **User key** — an ``eb_`` prefixed token minted for a single user (one key per
  user). The 8-char prefix narrows the candidate rows to (usually) one, then a
  single ``bcrypt.checkpw`` confirms the full secret. What the user may reach is
  decided by their grants (see :mod:`api.services.permissions`); a deactivated
  user's key is rejected here with ``403``.

Authentication is split into two commands per CQS:

* :func:`authenticate_api_key` — pure query; returns a :class:`Principal` or raises.
* :func:`record_key_use` — pure command; writes ``last_used_at``; returns nothing.

The FastAPI dependencies call both in sequence.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import api_keys as keys_t
from api.db import users as users_t
from api.dependencies import get_db
from api.settings import settings


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    ``is_master`` callers may access anything. A user-key caller carries a
    ``user_id``; what it may reach is resolved from that user's grants by
    :mod:`api.services.permissions` (this dataclass holds no scope of its own).
    """

    is_master: bool
    user_id: str | None = None
    api_key_id: str | None = None


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
    """FastAPI dependency: authenticate the request (master or user key).

    Records ``last_used_at`` for a user key. Resource authorization is enforced
    per-route via :mod:`api.services.permissions` (the path's collection/document
    is not known here).
    """
    raw_key = _extract_key(authorization, x_api_key)
    principal = await authenticate_api_key(raw_key, db)
    if principal.api_key_id is not None:
        await record_key_use(principal.api_key_id, db)
    return principal


async def require_master(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """FastAPI dependency: require the master key (raises 403 for user keys).

    Used as a router-level dependency on management routers so no endpoint can be
    added later without authentication.
    """
    raw_key = _extract_key(authorization, x_api_key)
    principal = await authenticate_api_key(raw_key, db)
    if not principal.is_master:
        raise HTTPException(403, "Master API key required")
    return principal
