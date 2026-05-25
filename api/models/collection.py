from datetime import datetime
from uuid import uuid4
from pydantic import BaseModel, Field


class Workspace(BaseModel):
    id: str = Field(default_factory=lambda: f"ws_{uuid4().hex[:12]}")
    name: str
    description: str = ""
    color: str = "#6366f1"
    icon: str = "folder"
    created_at: datetime
    updated_at: datetime

    # Computed — derived from collection/document counts, not stored
    collection_count: int = 0
    document_count: int = 0
    chunk_count: int = 0


class Collection(BaseModel):
    id: str = Field(default_factory=lambda: f"col_{uuid4().hex[:12]}")
    workspace_id: str  # required — no orphan collections
    name: str
    description: str = ""
    color: str = "#8b5cf6"
    icon: str = "book"
    created_at: datetime
    updated_at: datetime

    # Computed
    document_count: int = 0
    chunk_count: int = 0
    last_ingested_at: datetime | None = None


class APIKey(BaseModel):
    id: str
    collection_id: str
    key_prefix: str  # first 8 chars after "eb_" prefix — shown in UI
    key_hash: str    # bcrypt hash — never returned to clients
    label: str
    created_at: datetime
    last_used_at: datetime | None = None


class APIKeyPublic(BaseModel):
    """Safe to return to the client — no key_hash."""
    id: str
    collection_id: str
    key_prefix: str
    label: str
    created_at: datetime
    last_used_at: datetime | None = None
