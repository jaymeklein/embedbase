"""BM25 index status reporting and (re)index orchestration.

Index membership is derived from the BM25 corpus in Redis (a document is
"indexed" when its chunks are present in ``bm25:{collection}:corpus``) — there is
no separate flag to keep in sync. (Re)indexing is delegated to the worker, which
rebuilds corpus entries straight from the vector store without re-embedding.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.db import workspaces as ws_t
from api.models.indexing import (
    CollectionIndexStatus,
    IndexEnqueueResponse,
    IndexStatusResponse,
    WorkspaceIndexStatus,
)
from api.models.redis import CorpusConfig
from api.services import tasks as task_producer
from api.services.redis.redis import get_corpus

_IN_FLIGHT = {"pending", "processing"}


def indexed_doc_ids(redis_client: Any, collection_id: str) -> set[str]:
    """Return the set of document_ids present in a collection's BM25 corpus."""
    corpus = get_corpus(redis_client, CorpusConfig(collection_id))
    return {entry[1] for entry in corpus.data}


async def _active_documents(db: AsyncSession) -> list[Any]:
    """Fetch every active document with its workspace, collection, and job status."""
    stmt = (
        select(
            ws_t.c.id.label("ws_id"),
            ws_t.c.name.label("ws_name"),
            col_t.c.id.label("col_id"),
            col_t.c.name.label("col_name"),
            doc_t.c.id.label("doc_id"),
            job_t.c.status.label("status"),
        )
        .select_from(
            ws_t.join(col_t, col_t.c.workspace_id == ws_t.c.id)
            .join(doc_t, doc_t.c.collection_id == col_t.c.id)
            .outerjoin(job_t, job_t.c.document_id == doc_t.c.id)
        )
        .where(doc_t.c.status.is_(None))
        .order_by(job_t.c.created_at)
    )
    return list((await db.execute(stmt)).fetchall())


def _collection_status(
    col_id: str, col_name: str, doc_status: dict[str, str | None], indexed: set[str]
) -> CollectionIndexStatus:
    """Build one collection's index status from its {doc_id: status} map."""
    indexed_here = sum(1 for doc_id in doc_status if doc_id in indexed)
    pending = sum(1 for status in doc_status.values() if status in _IN_FLIGHT)
    failed = sum(1 for status in doc_status.values() if status == "failed")
    return CollectionIndexStatus(
        collection_id=col_id,
        collection_name=col_name,
        total=len(doc_status),
        indexed=indexed_here,
        unindexed=len(doc_status) - indexed_here,
        pending=pending,
        failed=failed,
    )


async def get_index_overview(db: AsyncSession, redis_client: Any) -> IndexStatusResponse:
    """Return BM25 index coverage grouped by workspace then collection.

    Only collections that contain at least one active document appear — empty
    collections have nothing to index.
    """
    # ws_id -> (ws_name, {col_id -> (col_name, {doc_id -> status})})
    tree: dict[str, tuple[str, dict[str, tuple[str, dict[str, str | None]]]]] = defaultdict(
        lambda: ("", defaultdict(lambda: ("", {})))
    )
    for row in await _active_documents(db):
        _, cols = tree[row.ws_id]
        tree[row.ws_id] = (row.ws_name, cols)
        _, docs = cols[row.col_id]
        cols[row.col_id] = (row.col_name, docs)
        docs[row.doc_id] = row.status  # ordered by created_at → latest job wins

    workspaces = [
        WorkspaceIndexStatus(
            workspace_id=ws_id,
            workspace_name=ws_name,
            collections=[
                _collection_status(cid, cname, docs, indexed_doc_ids(redis_client, cid))
                for cid, (cname, docs) in cols.items()
            ],
        )
        for ws_id, (ws_name, cols) in tree.items()
    ]
    return IndexStatusResponse(workspaces=workspaces)


def enqueue_document(document_id: str, collection_id: str) -> IndexEnqueueResponse:
    """Enqueue a BM25 (re)index of a single document."""
    task_id = task_producer.enqueue_index_document(document_id, collection_id)
    return IndexEnqueueResponse(task_id=task_id)


def enqueue_collection(collection_id: str) -> IndexEnqueueResponse:
    """Enqueue a BM25 (re)index of every active document in a collection."""
    task_id = task_producer.enqueue_index_collection(collection_id)
    return IndexEnqueueResponse(task_id=task_id)
