"""Users table — a human identity that owns exactly one API key and can log in.

Authentication moved from collection-scoped keys to per-user keys: every
``api_keys`` row now belongs to a user (``UNIQUE(user_id)`` → one key each), and
a user's data access is decided by the grants in ``permissions``. Deactivating a
user (``is_active = False``) makes their key stop authenticating.

Web login: a user has a ``username`` (login id) + a bcrypt ``password_hash``.
``is_admin`` grants the full (master-equivalent) console; a non-admin is scoped
by their grants. ``must_change_password`` forces a password change on first login
(true until the user sets their own). ``password_changed_at`` doubles as the
session epoch — a JWT is rejected once it no longer matches, so a reset or change
invalidates prior sessions. The master key remains a bootstrap credential.
"""

from sqlalchemy import Boolean, Column, Index, Integer, String, Table, UniqueConstraint, false, true

from api.tables.metadata import metadata

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    # Optional (migration 0011): an account is keyed by ``username``, so it may have no
    # email. The unique constraint below still forbids duplicate addresses but permits
    # many NULLs (SQL treats NULLs as distinct).
    Column("email", String, nullable=True),
    Column("name", String, nullable=False, server_default=""),
    Column("is_active", Boolean, nullable=False, server_default=true()),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Web login (migration 0009). Server defaults let 0009 add them one-shot and
    # let existing rows backfill safely (username=email; empty hash = can't log in
    # until an admin resets the password).
    Column("username", String, nullable=False, server_default=""),
    Column("password_hash", String, nullable=False, server_default=""),
    Column("must_change_password", Boolean, nullable=False, server_default=true()),
    Column("password_changed_at", String, nullable=True),
    Column("is_admin", Boolean, nullable=False, server_default=false()),
    # Per-user MCP rate-limit override (migration 0010). 0 = inherit the global
    # mcp.rate_limit_rpm; a positive value caps this user's key. Read into the
    # Principal at auth and pushed into the MCP token bucket per key.
    Column("rate_limit_rpm", Integer, nullable=False, server_default="0"),
    UniqueConstraint("email", name="users_email_unique"),
    Index("users_username_key", "username", unique=True),
)
