from sqlalchemy import Column, DateTime, Integer, String, Table, Text

from api.tables.metadata import metadata

job_records = Table(
    "job_records",
    metadata,
    Column("job_id", String, primary_key=True),
    Column("document_id", String, nullable=False),
    Column("collection_id", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("file_type", String, nullable=False),
    Column("status", String, nullable=False, server_default="pending"),
    Column("chunk_count", Integer, nullable=True),
    Column("error", Text, nullable=True),
    Column("celery_task_id", String, nullable=True),
    Column("processing_started_at", DateTime, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
