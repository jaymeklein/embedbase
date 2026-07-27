"""User accounts, their single API key, and console-login passwords.

The management plane (master-only) for the users/permissions module: CRUD users,
activate/deactivate, mint/rotate/revoke each user's one API key, and issue/reset
their login password. A user's data access is decided by grants, delegated to
:mod:`api.services.permissions`; password hashing + temp-password generation are
delegated to :mod:`api.services.session`.

Secrets are shown once and never stored in plaintext: a minted ``eb_`` key and a
generated temp password are each returned exactly once; only bcrypt hashes persist.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import bcrypt
from fastapi import HTTPException
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import api_keys as keys_t
from api.db import users as users_t
from api.schemas.users import GrantCreate, UserUpdate
from api.services import permissions, session


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def require_user(user_id: str, db: AsyncSession) -> None:
    """Raise 404 unless ``user_id`` exists."""
    exists = (
        await db.execute(select(users_t.c.id).where(users_t.c.id == user_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, f"User {user_id!r} not found")


def _key_summary_from_row(row: Any) -> dict[str, Any] | None:
    """Public key summary (never the hash/secret) from a key row, or None."""
    if row is None:
        return None
    return {
        "id": row.id,
        "key_prefix": row.key_prefix,
        "label": row.label,
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
    }


def _serialize_user(row: Any, api_key: dict[str, Any] | None) -> dict[str, Any]:
    """Serialize a user row plus its (already-resolved) key summary for API responses."""
    return {
        "id": row.id,
        "username": row.username,
        "email": row.email,
        "name": row.name,
        "is_active": bool(row.is_active),
        "is_admin": bool(row.is_admin),
        "rate_limit_rpm": int(row.rate_limit_rpm or 0),
        "must_change_password": bool(row.must_change_password),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "api_key": api_key,
    }


async def _key_summary(user_id: str, db: AsyncSession) -> dict[str, Any] | None:
    """The user's API-key metadata (never the hash/secret), or None if unset."""
    row = (
        await db.execute(
            select(
                keys_t.c.id,
                keys_t.c.key_prefix,
                keys_t.c.label,
                keys_t.c.created_at,
                keys_t.c.last_used_at,
            ).where(keys_t.c.user_id == user_id)
        )
    ).fetchone()
    return _key_summary_from_row(row)


async def _require_email_free(email: str, db: AsyncSession, *, exclude_id: str | None = None) -> None:
    """Raise 409 when ``email`` is already taken by a different user."""
    stmt = select(users_t.c.id).where(users_t.c.email == email)
    if exclude_id is not None:
        stmt = stmt.where(users_t.c.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(409, f"User with email {email!r} already exists")


async def _require_username_free(
    username: str, db: AsyncSession, *, exclude_id: str | None = None
) -> None:
    """Raise 409 when ``username`` is already taken by a different user."""
    stmt = select(users_t.c.id).where(users_t.c.username == username)
    if exclude_id is not None:
        stmt = stmt.where(users_t.c.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(409, f"Username {username!r} is already taken")


async def create_user(
    username: str,
    email: str | None,
    name: str,
    is_active: bool,
    is_admin: bool,
    db: AsyncSession,
    *,
    rate_limit_rpm: int = 0,
) -> dict[str, Any]:
    """Create a user (unique username; email optional) with a random one-time password.

    Returns the user plus ``temp_password`` — shown **once** (like a minted key);
    the user must change it on first login. No API key is minted yet. ``email`` may be
    ``None`` (an account is identified by its username). ``rate_limit_rpm`` is the per-user
    MCP override (0 = inherit the global default).
    """
    if email is not None:
        await _require_email_free(email, db)
    await _require_username_free(username, db)
    now = _now()
    user_id = f"usr_{uuid4().hex[:12]}"
    temp_password = session.generate_temp_password()
    await db.execute(
        insert(users_t).values(
            id=user_id, username=username, email=email, name=name,
            is_active=is_active, is_admin=is_admin, rate_limit_rpm=rate_limit_rpm,
            password_hash=session.hash_password(temp_password),
            must_change_password=True, password_changed_at=now,
            created_at=now, updated_at=now,
        )
    )
    await db.commit()
    return {**await get_user(user_id, db), "temp_password": temp_password}


async def list_users(db: AsyncSession) -> list[dict[str, Any]]:
    """Return all users (with key summaries), newest first.

    Two queries regardless of user count: all users, then all keys grouped by
    user (one key each) — no per-row key lookup.
    """
    user_rows = (
        await db.execute(select(users_t).order_by(users_t.c.created_at.desc()))
    ).fetchall()
    key_rows = (
        await db.execute(
            select(
                keys_t.c.user_id,
                keys_t.c.id,
                keys_t.c.key_prefix,
                keys_t.c.label,
                keys_t.c.created_at,
                keys_t.c.last_used_at,
            )
        )
    ).fetchall()
    keys_by_user = {r.user_id: _key_summary_from_row(r) for r in key_rows}
    return [_serialize_user(u, keys_by_user.get(u.id)) for u in user_rows]


async def get_user(user_id: str, db: AsyncSession) -> dict[str, Any]:
    """Return one user with its key summary, or 404."""
    row = (await db.execute(select(users_t).where(users_t.c.id == user_id))).fetchone()
    if not row:
        raise HTTPException(404, f"User {user_id!r} not found")
    return _serialize_user(row, await _key_summary(user_id, db))


async def update_user(user_id: str, body: UserUpdate, db: AsyncSession) -> dict[str, Any]:
    """Apply the provided fields of ``body`` (username/name/email/is_active/is_admin/rate_limit_rpm).

    Email is optional: an explicitly-provided blank clears it (set to NULL). Passwords are
    not updatable here — use :func:`reset_password` (admin) or :func:`change_password`.
    """
    await require_user(user_id, db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    # Email is nullable, so a provided blank/null must clear it — the None-filter above
    # would otherwise drop it as "field omitted". model_fields_set distinguishes the two.
    if "email" in body.model_fields_set:
        updates["email"] = body.email
        if body.email is not None:
            await _require_email_free(body.email, db, exclude_id=user_id)
    if "username" in updates:
        await _require_username_free(updates["username"], db, exclude_id=user_id)
    if updates:
        updates["updated_at"] = _now()
        await db.execute(update(users_t).where(users_t.c.id == user_id).values(**updates))
        await db.commit()
    return await get_user(user_id, db)


async def delete_user(user_id: str, db: AsyncSession) -> None:
    """Delete a user; cascades to their API key and permission grants."""
    await require_user(user_id, db)
    await db.execute(delete(users_t).where(users_t.c.id == user_id))
    await db.commit()


async def authenticate_login(username: str, password: str, db: AsyncSession) -> dict[str, Any]:
    """Verify ``username`` + ``password`` → the inputs to mint a session, or raise 401.

    Coarse and constant-ish: an unknown username, a wrong password, and an inactive
    user all raise the same ``401`` after one bcrypt compare (via
    :func:`session.verify_password`, which dummy-compares when there's no hash), so
    login never reveals which usernames exist or which accounts are disabled.
    """
    row = (
        await db.execute(
            select(
                users_t.c.id,
                users_t.c.password_hash,
                users_t.c.is_active,
                users_t.c.must_change_password,
                users_t.c.password_changed_at,
            ).where(users_t.c.username == username)
        )
    ).fetchone()
    stored_hash = row.password_hash if row is not None else ""
    if not session.verify_password(password, stored_hash) or row is None or not row.is_active:
        raise HTTPException(401, "Invalid username or password")
    return {
        "user_id": row.id,
        "must_change": bool(row.must_change_password),
        "pwd_epoch": row.password_changed_at or "",
    }


async def change_password(
    user_id: str, current_password: str, new_password: str, db: AsyncSession
) -> dict[str, Any]:
    """Set a new password after verifying the current one; clears the must-change flag.

    Bumps ``password_changed_at`` (the session epoch), so every prior token is
    invalidated. Returns the inputs to mint a fresh session.
    """
    row = (
        await db.execute(
            select(users_t.c.password_hash).where(users_t.c.id == user_id)
        )
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"User {user_id!r} not found")
    if not session.verify_password(current_password, row.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    now = _now()
    await db.execute(
        update(users_t)
        .where(users_t.c.id == user_id)
        .values(
            password_hash=session.hash_password(new_password),
            must_change_password=False,
            password_changed_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return {"user_id": user_id, "must_change": False, "pwd_epoch": now}


async def reset_password(user_id: str, db: AsyncSession) -> dict[str, Any]:
    """Admin reset: issue a new random one-time password and force a change on next login.

    Bumps ``password_changed_at`` (invalidating any active session). Returns
    ``temp_password`` — shown **once**.
    """
    await require_user(user_id, db)
    now = _now()
    temp_password = session.generate_temp_password()
    await db.execute(
        update(users_t)
        .where(users_t.c.id == user_id)
        .values(
            password_hash=session.hash_password(temp_password),
            must_change_password=True,
            password_changed_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return {"user_id": user_id, "temp_password": temp_password}


async def mint_user_key(user_id: str, label: str, db: AsyncSession) -> dict[str, Any]:
    """Mint (or rotate) the user's single API key, returning the raw value once.

    Any existing key is replaced (one key per user), so calling this again rotates
    the credential. Only the bcrypt hash is stored; the raw ``eb_`` key is returned
    exactly once and never retrievable again.
    """
    await require_user(user_id, db)
    await db.execute(delete(keys_t).where(keys_t.c.user_id == user_id))
    raw_key = "eb_" + secrets.token_urlsafe(32)
    key_prefix = raw_key[3:11]
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()
    key_id = uuid4().hex
    now = _now()
    await db.execute(
        insert(keys_t).values(
            id=key_id, user_id=user_id, key_prefix=key_prefix,
            key_hash=key_hash, label=label, created_at=now,
        )
    )
    await db.commit()
    return {
        "id": key_id,
        "user_id": user_id,
        "key_prefix": key_prefix,
        "label": label,
        "created_at": now,
        "raw_key": raw_key,
    }


async def revoke_user_key(user_id: str, db: AsyncSession) -> None:
    """Delete the user's API key (404 when they have none)."""
    await require_user(user_id, db)
    result: Any = await db.execute(delete(keys_t).where(keys_t.c.user_id == user_id))
    if result.rowcount == 0:
        raise HTTPException(404, "User has no API key")
    await db.commit()


async def list_user_permissions(user_id: str, db: AsyncSession) -> list[dict[str, Any]]:
    """Return the user's grants (404 when the user is absent)."""
    await require_user(user_id, db)
    return await permissions.list_permissions(db, user_id)


async def add_permission(user_id: str, body: GrantCreate, db: AsyncSession) -> dict[str, Any]:
    """Grant the user access to a resource (404 when the user/resource is absent)."""
    await require_user(user_id, db)
    return await permissions.grant_permission(
        db, user_id, body.resource_type, body.resource_id, body.level
    )


async def remove_permission(user_id: str, grant_id: str, db: AsyncSession) -> None:
    """Revoke one of the user's grants (404 when the user/grant is absent)."""
    await require_user(user_id, db)
    await permissions.revoke_permission(db, user_id, grant_id)
