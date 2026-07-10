from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"
    deleted = "deleted"


class JobRecord(BaseModel):
    job_id: str
    document_id: str
    collection_id: str
    filename: str
    file_type: str
    status: DocumentStatus
    chunk_count: int | None = None
    error: str | None = None
    celery_task_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    file_size_bytes: int | None = None
    created_at: datetime
    updated_at: datetime


class DocumentListQuery(BaseModel):
    """Pagination + filters for :func:`api.services.documents.list_documents`.

    Bound straight from the request query string (a FastAPI query-parameter model) and
    handed to the service as one object, so neither the endpoint nor the service carries a
    long parameter list. Every field is optional; unset filters are skipped and AND-combined.
    The ``*_after``/``*_before`` bounds are inclusive ISO-8601 strings compared lexically (the
    timestamp columns store ``isoformat()`` text, so lexical order is chronological).
    """

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    filename: str | None = None  # case-insensitive substring match
    file_type: str | None = None
    status: str | None = None  # latest ingestion status (free-text: includes rate_limited, not just DocumentStatus)
    indexed: bool | None = None  # True = has stored chunks, False = none yet, None = either
    embedding_model: str | None = None
    storage_backend: str | None = None
    min_size: int | None = Field(default=None, ge=0)  # inclusive file_size bounds, bytes
    max_size: int | None = Field(default=None, ge=0)
    created_after: str | None = None
    created_before: str | None = None
    updated_after: str | None = None
    updated_before: str | None = None
    tag: list[str] | None = None  # documents carrying *all* of these tag names (AND filter)
