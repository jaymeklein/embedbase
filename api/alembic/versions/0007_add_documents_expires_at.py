"""Add expires_at column to documents

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-03

Stamps when a *temporary* document (ingested with ``temporary=true`` while
``storage.temp_retention_hours > 0``) should be auto-purged — its stored object,
row, chunks, and vectors removed by the periodic worker sweep. NULL = permanent:
every existing row and every non-temporary upload, so no backfill is needed. Naive
UTC, matching the ``processing_started_at`` convention (0002) so the worker's
``expires_at <= now`` comparison is dialect-uniform (Postgres + SQLite).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable expires_at column to documents."""
    op.add_column(
        "documents",
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Remove the expires_at column from documents."""
    op.drop_column("documents", "expires_at")
