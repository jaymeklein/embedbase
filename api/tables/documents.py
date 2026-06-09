from sqlalchemy import Column, ForeignKey, Index, Integer, String, Table

from api.tables.metadata import metadata

documents = Table(
    "documents",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "collection_id",
        String,
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("filename", String, nullable=False),
    Column("file_type", String, nullable=False),
    Column("file_size", Integer, nullable=True),
    Column("chunk_count", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("status", String, nullable=True),
    Index("documents_collection_idx", "collection_id"),
)
