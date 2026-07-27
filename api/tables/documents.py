from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Table

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
    # The embedding model that produced this document's vectors. NULL = unknown
    # (ingested before this column existed). Compare to the live config to find
    # documents needing re-ingestion after a model change.
    Column("embedding_model", String, nullable=True),
    # Named storage backend (api.services.storage registry) holding this document's
    # bytes. NULL = legacy/local — files written to disk before this column existed,
    # so read paths treat a missing value as "local".
    Column("storage_backend", String, nullable=True),
    # When a *temporary* document expires and is purged by the worker sweep. NULL =
    # permanent (every existing row + every non-temporary upload). Naive UTC to match
    # processing_started_at, so ``expires_at <= now`` compares uniformly on any dialect.
    Column("expires_at", DateTime, nullable=True),
    # Optional *original source file* kept alongside the parse (e.g. the raw PDF a Markdown
    # upload was converted from), stored as a second object under this same row — never
    # embedded. All NULL = none attached. ``original_file_type`` is set when the upload is
    # requested (so cleanup can delete the object even if never confirmed); ``original_file_size``
    # only at confirm (so listings/downloads treat it as present only once bytes land).
    Column("original_filename", String, nullable=True),
    Column("original_file_type", String, nullable=True),
    Column("original_file_size", Integer, nullable=True),
    Index("documents_collection_idx", "collection_id"),
)
