"""API key authentication.

Two kinds of credentials are accepted:

* **Master key** — the `MASTER_API_KEY` from the environment. Grants access to
  every workspace/collection. Compared in constant time to avoid leaking the
  key length/content through timing.
* **Collection key** — an ``eb_`` prefixed token minted via the API key
  endpoints. Only grants access to the single collection it was created for.
  The 8-char prefix narrows the candidate rows to (usually) one, then a single
  ``bcrypt.checkpw`` confirms the full secret.

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
from api.dependencies import get_db
from api.settings import settings


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    ``is_master`` callers may access any collection. Collection-key callers are
    restricted to ``collection_id``.
    """

    is_master: bool
    collection_id: str | None = None
    api_key_id: str | None = None

    def can_access(self, collection_id: str) -> bool:
        return self.is_master or self.collection_id == collection_id


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


async def authenticate_api_key(
    raw_key: str | None,
    db: AsyncSession,
    *,
    collection_id: str | None = None,
) -> Principal:
    """Resolve ``raw_key`` to a :class:`Principal` or raise ``401``/``403``.

    Pure query — no state is mutated. Call :func:`record_key_use` separately
    to update ``last_used_at`` after a successful authentication.

    When ``collection_id`` is supplied, a collection key is additionally
    required to match that collection (``403`` otherwise). The master key always
    passes the collection check.
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
            select(keys_t.c.id, keys_t.c.collection_id, keys_t.c.key_hash).where(
                keys_t.c.key_prefix == key_prefix
            )
        )
    ).fetchall()

    raw_bytes = raw_key.encode()
    for row in rows:
        if bcrypt.checkpw(raw_bytes, row.key_hash.encode()):
            if collection_id is not None and row.collection_id != collection_id:
                raise HTTPException(403, "API key not valid for this collection")
            return Principal(
                is_master=False,
                collection_id=row.collection_id,
                api_key_id=row.id,
            )

    raise HTTPException(401, "Invalid API key")


async def record_key_use(key_id: str, db: AsyncSession) -> None:
    """Write ``last_used_at`` for a successfully authenticated collection key."""
    await db.execute(
        update(keys_t)
        .where(keys_t.c.id == key_id)
        .values(last_used_at=datetime.now(UTC).isoformat())
    )
    await db.commit()


async def require_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """FastAPI dependency: authenticate the request (master or collection key).

    Pure query — delegates to :func:`authenticate_api_key`. Route handlers that
    need to record key usage must call :func:`record_key_use` explicitly after
    receiving the :class:`Principal`.

    Collection scoping is enforced per-route via :meth:`Principal.can_access`,
    since the path's ``col_id`` is not known here.
    """
    raw_key = _extract_key(authorization, x_api_key)
    return await authenticate_api_key(raw_key, db)


async def require_master(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """FastAPI dependency: require the master key (raises 403 for collection keys).

    Used as a router-level dependency on management routers so no endpoint can be
    added later without authentication.
    """
    raw_key = _extract_key(authorization, x_api_key)
    principal = await authenticate_api_key(raw_key, db)
    if not principal.is_master:
        raise HTTPException(403, "Master API key required")
    return principal
