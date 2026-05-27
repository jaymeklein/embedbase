from sqlalchemy import Column, String, Table, Text

from api.tables.metadata import metadata

workspaces = Table(
    "workspaces",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("color", String, nullable=False, server_default="#6366f1"),
    Column("icon", String, nullable=False, server_default="folder"),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
