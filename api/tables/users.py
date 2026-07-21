"""Users table — a human identity that owns exactly one API key.

Authentication moved from collection-scoped keys to per-user keys: every
``api_keys`` row now belongs to a user (``UNIQUE(user_id)`` → one key each), and
a user's data access is decided by the grants in ``permissions``. Deactivating a
user (``is_active = False``) makes their key stop authenticating.
"""

from sqlalchemy import Boolean, Column, String, Table, UniqueConstraint, true

from api.tables.metadata import metadata

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("email", String, nullable=False),
    Column("name", String, nullable=False, server_default=""),
    Column("is_active", Boolean, nullable=False, server_default=true()),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("email", name="users_email_unique"),
)
