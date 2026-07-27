"""Add original-source-file columns to documents

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-27

An optional *original source file* kept alongside a document's parse — e.g. the raw PDF a
Markdown upload was converted from. It is stored as a second object under the same document
row (never embedded) and downloadable on demand. Three nullable columns hold its metadata,
mirroring the existing ``filename``/``file_type``/``file_size`` trio; all NULL = no original
(every existing row, so no backfill). Two of them gate different concerns on purpose:
``original_file_type`` is written when the upload is *requested* so cleanup can find the
object even if the upload is never confirmed, while ``original_file_size`` is written only at
*confirm*, so listings/downloads treat an original as present only once its bytes have landed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable original-source-file columns to documents."""
    op.add_column("documents", sa.Column("original_filename", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("original_file_type", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("original_file_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove the original-source-file columns from documents."""
    op.drop_column("documents", "original_file_size")
    op.drop_column("documents", "original_file_type")
    op.drop_column("documents", "original_filename")
