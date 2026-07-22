"""Add username + password login (and is_admin role) to users

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-21

Users can now log into the web console. Adds to ``users``:
  * ``username`` — the login id (``UNIQUE`` via ``users_username_key``).
  * ``password_hash`` — bcrypt hash of the user's password (empty until set).
  * ``must_change_password`` — forces a password change on first login.
  * ``password_changed_at`` — doubles as the JWT session epoch (a reset/change
    invalidates prior sessions).
  * ``is_admin`` — grants the full (master-equivalent) console; a non-admin is
    scoped by their grants.

All columns carry server defaults so this is a one-shot ``ADD COLUMN`` on both
SQLite and Postgres. Existing rows backfill ``username = email`` (emails are
already ``UNIQUE`` + lowercased, so usernames stay unique) and keep an empty
``password_hash`` + ``must_change_password = true`` + ``is_admin = false`` — they
cannot log in until an admin resets their password (or promotes them).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the login columns, backfill username, then make username unique."""
    op.add_column("users", sa.Column("username", sa.String(), nullable=False, server_default=""))
    op.add_column(
        "users", sa.Column("password_hash", sa.String(), nullable=False, server_default="")
    )
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("users", sa.Column("password_changed_at", sa.String(), nullable=True))
    op.add_column(
        "users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    # Backfill BEFORE the unique index, or the empty-string defaults collide.
    op.execute("UPDATE users SET username = email WHERE username = ''")
    op.create_index("users_username_key", "users", ["username"], unique=True)


def downgrade() -> None:
    """Drop the username index and the login columns."""
    op.drop_index("users_username_key", table_name="users")
    op.drop_column("users", "is_admin")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "username")
