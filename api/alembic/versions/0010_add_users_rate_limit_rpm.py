"""Add rate_limit_rpm column to users

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-24

Per-user override for the MCP request rate limit. ``0`` (the server default, so
every existing row backfills without a data migration) means "inherit the global
``mcp.rate_limit_rpm``"; any positive value caps that user's key instead. The MCP
middleware reads it off the authenticated principal and pushes it into the per-key
token bucket (see api/services/mcp/rate_limit.py). NOT NULL keeps the sentinel
uniform — a row is never "unset", it is 0 = inherit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the non-null rate_limit_rpm column (0 = inherit the global default)."""
    op.add_column(
        "users",
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Remove the rate_limit_rpm column from users."""
    op.drop_column("users", "rate_limit_rpm")
