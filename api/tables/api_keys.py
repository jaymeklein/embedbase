from sqlalchemy import Column, ForeignKey, Index, String, Table

from api.tables.metadata import metadata

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "collection_id",
        String,
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("key_prefix", String, nullable=False),
    Column("key_hash", String, nullable=False),
    Column("label", String, nullable=False, server_default=""),
    Column("created_at", String, nullable=False),
    Column("last_used_at", String, nullable=True),
    Index("api_keys_prefix_idx", "key_prefix"),
)
