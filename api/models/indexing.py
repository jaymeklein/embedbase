"""Response models for BM25 index status and (re)index actions."""

from pydantic import BaseModel


class CollectionIndexStatus(BaseModel):
    collection_id: str
    collection_name: str
    total: int  # active documents in the collection
    indexed: int  # documents present in the BM25 corpus
    unindexed: int
    pending: int  # documents with an in-flight ingestion job
    failed: int  # documents whose last ingestion job failed


class WorkspaceIndexStatus(BaseModel):
    workspace_id: str
    workspace_name: str
    collections: list[CollectionIndexStatus] = []


class IndexStatusResponse(BaseModel):
    workspaces: list[WorkspaceIndexStatus] = []


class IndexEnqueueResponse(BaseModel):
    task_id: str | None = None
