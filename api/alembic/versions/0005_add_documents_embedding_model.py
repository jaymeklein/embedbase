"""Add embedding_model column to documents

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-29

Records which embedding model produced each document's vectors, so a model
change can be reconciled by re-ingesting only the documents whose recorded
model differs from the live config. NULL = ingested before this column existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable embedding_model column to documents."""
    op.add_column(
        "documents",
        sa.Column("embedding_model", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove the embedding_model column from documents."""
    op.drop_column("documents", "embedding_model")
