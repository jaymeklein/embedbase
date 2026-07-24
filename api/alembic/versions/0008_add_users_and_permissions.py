"""Add users + permissions; move api_keys from collection-scoped to user-scoped

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-20

Introduces the users/permissions module:
  * ``users`` — a human identity that owns exactly one API key; ``is_active``
    gates authentication.
  * ``permissions`` — per-user access grants (``read``/``write``) over the
    workspace/collection/document hierarchy.
  * ``api_keys`` is repointed from ``collection_id`` to ``user_id``
    (``UNIQUE(user_id)`` → one key per user). Collection-scoped keys are retired,
    so any existing rows are dropped: the old table is dropped and recreated with
    the new shape rather than migrated column-by-column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create users + permissions and repoint api_keys at users."""
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="users_email_unique"),
    )

    op.create_table(
        "permissions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "resource_type", "resource_id", name="permissions_user_resource_unique"
        ),
    )
    op.create_index("permissions_user_idx", "permissions", ["user_id"])

    # Collection keys are retired; drop and recreate api_keys pointing at users.
    op.drop_index("api_keys_prefix_idx", table_name="api_keys")
    op.drop_table("api_keys")
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("key_prefix", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="api_keys_user_unique"),
    )
    op.create_index("api_keys_prefix_idx", "api_keys", ["key_prefix"])


def downgrade() -> None:
    """Restore collection-scoped api_keys and drop users + permissions."""
    op.drop_index("api_keys_prefix_idx", table_name="api_keys")
    op.drop_table("api_keys")
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("collection_id", sa.String(), nullable=False),
        sa.Column("key_prefix", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("api_keys_prefix_idx", "api_keys", ["key_prefix"])

    op.drop_index("permissions_user_idx", table_name="permissions")
    op.drop_table("permissions")
    op.drop_table("users")
