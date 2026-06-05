"""Add processing_started_at to job_records

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-05

Adds a nullable DateTime column so the worker can detect stale 'processing'
jobs (e.g. after an unclean crash with task_acks_late=True) and reclaim them
instead of letting them strand permanently.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_records",
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_records", "processing_started_at")
