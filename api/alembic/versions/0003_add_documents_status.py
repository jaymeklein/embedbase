"""Add status column to documents

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-08

Adds a nullable String column so the API can soft-delete documents:
NULL = active; 'deleting' = pending vector / BM25 cleanup by the worker.
The worker hard-deletes the row once cleanup confirms.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable status column to documents.

    NULL means active; 'deleting' means soft-delete in progress, pending
    worker cleanup.  The worker hard-deletes the row once cleanup confirms.
    """
    op.add_column(
        "documents",
        sa.Column("status", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove the status column from documents."""
    op.drop_column("documents", "status")
