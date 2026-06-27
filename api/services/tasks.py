"""Celery task producer for the API process.

The API never imports the worker package — it dispatches by task *name* over the
shared broker. This keeps the API image free of the heavy parser/embedding
dependencies the worker carries.
"""

from __future__ import annotations

from celery import Celery

from api.settings import settings

_producer = Celery(
    "embedbase-producer",
    broker=settings.redis_url,
    backend=settings.redis_url.replace("/0", "/1"),
)
_producer.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)

INGEST_TASK = "worker.tasks.ingest_document"
DELETE_TASK = "worker.tasks.delete_document"
SYNC_TAGS_TASK = "worker.tasks.sync_document_tags"
INDEX_DOC_TASK = "worker.tasks.index_document"
INDEX_COLLECTION_TASK = "worker.tasks.index_collection"


def enqueue_ingest(
    job_id: str,
    file_path: str,
    collection_id: str,
    document_id: str,
    file_type: str,
) -> str | None:
    result = _producer.send_task(
        INGEST_TASK,
        args=[job_id, file_path, collection_id, document_id, file_type],
    )
    return getattr(result, "id", None)


def enqueue_delete(document_id: str, collection_id: str) -> str | None:
    result = _producer.send_task(DELETE_TASK, args=[document_id, collection_id])
    return getattr(result, "id", None)


def enqueue_sync_tags(document_id: str, collection_id: str) -> str | None:
    """Dispatch a search-bridge tag sync for one document to the worker."""
    result = _producer.send_task(SYNC_TAGS_TASK, args=[document_id, collection_id])
    return getattr(result, "id", None)


def enqueue_index_document(document_id: str, collection_id: str) -> str | None:
    """Dispatch a BM25 (re)index of one document to the worker."""
    result = _producer.send_task(INDEX_DOC_TASK, args=[document_id, collection_id])
    return getattr(result, "id", None)


def enqueue_index_collection(collection_id: str) -> str | None:
    """Dispatch a BM25 (re)index of an entire collection to the worker."""
    result = _producer.send_task(INDEX_COLLECTION_TASK, args=[collection_id])
    return getattr(result, "id", None)
