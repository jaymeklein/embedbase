from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


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
