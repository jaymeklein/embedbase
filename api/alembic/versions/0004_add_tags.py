"""Add tags and tag-assignment join tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-15

Creates the normalized tag system for Delivery 6:
  tags (per-workspace, UNIQUE(workspace_id, name)) plus three join tables
  attaching tags to workspaces, collections, and documents. Every foreign
  key cascades on delete.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the tags table and the three assignment join tables."""
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="tags_name_workspace_unique"),
    )
    op.create_index("tags_workspace_idx", "tags", ["workspace_id"])

    _create_join_table("workspace_tags", "workspace_id", "workspaces")
    _create_join_table("collection_tags", "collection_id", "collections")
    _create_join_table("document_tags", "document_id", "documents")


def _create_join_table(table: str, entity_col: str, entity_table: str) -> None:
    """Create one ``entity_id``/``tag_id`` join table with cascading FKs."""
    op.create_table(
        table,
        sa.Column(entity_col, sa.String(), nullable=False),
        sa.Column("tag_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint([entity_col], [f"{entity_table}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(entity_col, "tag_id"),
    )
    op.create_index(f"{table}_tag_idx", table, ["tag_id"])


def downgrade() -> None:
    """Drop the join tables and the tags table."""
    for table in ("document_tags", "collection_tags", "workspace_tags"):
        op.drop_index(f"{table}_tag_idx", table_name=table)
        op.drop_table(table)
    op.drop_index("tags_workspace_idx", table_name="tags")
    op.drop_table("tags")
