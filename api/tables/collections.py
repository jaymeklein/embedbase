from sqlalchemy import Column, ForeignKey, Index, String, Table, Text, UniqueConstraint

from api.tables.metadata import metadata

collections = Table(
    "collections",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "workspace_id",
        String,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("color", String, nullable=False, server_default="#8b5cf6"),
    Column("icon", String, nullable=False, server_default="book"),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("workspace_id", "name", name="collections_name_workspace_unique"),
    Index("collections_workspace_idx", "workspace_id"),
)
