"""Tag tables — a normalized tag system scoped per workspace.

``tags`` holds one row per distinct tag within a workspace
(``UNIQUE(workspace_id, name)``). Three join tables attach tags to
workspaces, collections, and documents. Every foreign key cascades, so
deleting a tag or any tagged entity removes the matching join rows.
"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from api.tables.metadata import metadata

tags = Table(
    "tags",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "workspace_id",
        String,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String, nullable=False),
    Column("color", String, nullable=True),
    Column("created_at", String, nullable=False),
    UniqueConstraint("workspace_id", "name", name="tags_name_workspace_unique"),
    Index("tags_workspace_idx", "workspace_id"),
)

workspace_tags = Table(
    "workspace_tags",
    metadata,
    Column(
        "workspace_id",
        String,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "tag_id",
        String,
        ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
    ),
    PrimaryKeyConstraint("workspace_id", "tag_id"),
    Index("workspace_tags_tag_idx", "tag_id"),
)

collection_tags = Table(
    "collection_tags",
    metadata,
    Column(
        "collection_id",
        String,
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "tag_id",
        String,
        ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
    ),
    PrimaryKeyConstraint("collection_id", "tag_id"),
    Index("collection_tags_tag_idx", "tag_id"),
)

document_tags = Table(
    "document_tags",
    metadata,
    Column(
        "document_id",
        String,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "tag_id",
        String,
        ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
    ),
    PrimaryKeyConstraint("document_id", "tag_id"),
    Index("document_tags_tag_idx", "tag_id"),
)
