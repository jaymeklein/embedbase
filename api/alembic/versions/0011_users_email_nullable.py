"""Make users.email nullable

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-24

Email becomes optional — an account is identified by its ``username``, so it can exist
without an address. The ``users_email_unique`` constraint stays: it still forbids two
accounts sharing one address, but SQL treats NULLs as distinct, so any number of accounts
may have no email. Batch mode makes the nullable change portable to SQLite (dev/tests),
which can't ALTER a column's nullability in place.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Relax users.email to nullable (optional email)."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("email", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Restore the NOT NULL constraint on users.email."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("email", existing_type=sa.String(), nullable=False)
