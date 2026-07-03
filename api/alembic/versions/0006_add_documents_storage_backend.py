"""Add storage_backend column to documents

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-03

Records which named storage backend (api.services.storage registry) holds each
document's bytes, so files never orphan when backends are added/switched and
several S3 instances can coexist. NULL = legacy/local: files written to the
shared disk before this column existed, which the read paths resolve as "local".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable storage_backend column to documents."""
    op.add_column(
        "documents",
        sa.Column("storage_backend", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove the storage_backend column from documents."""
    op.drop_column("documents", "storage_backend")
