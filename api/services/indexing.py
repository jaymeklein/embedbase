"""BM25 index status reporting and (re)index orchestration.

Lexical/BM25 lives in the ``chunks.text_tsv`` generated column (Phase 3), so a
document is "indexed" once it has stored chunks — i.e. ``documents.chunk_count``
is set. There is no separate corpus to keep in sync, and (re)indexing is a no-op
(the tsvector is auto-maintained); the /index endpoints remain as instantly
satisfied calls.
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
from api.services import tasks as task_producer

# Statuses that keep a document "in flight" (still working toward all-chunks-done, so
# it stays in the queue rather than counting as finished or failed). ``rate_limited``
# is a document paused on a provider quota that the beat sweep keeps retrying.
_IN_FLIGHT = {"pending", "processing", "rate_limited"}


async def _active_documents(
    db: AsyncSession, collection_ids: list[str] | None = None
) -> list[Any]:
    """Fetch every active document with its workspace, collection, job status, and chunk_count.

    ``collection_ids`` is the caller's grant scope: ``None`` = unrestricted; a list narrows to
    those collections (the empty case is short-circuited by :func:`get_index_overview`).
    """
    stmt = (
        select(
            ws_t.c.id.label("ws_id"),
            ws_t.c.name.label("ws_name"),
            col_t.c.id.label("col_id"),
            col_t.c.name.label("col_name"),
            doc_t.c.id.label("doc_id"),
            doc_t.c.chunk_count.label("chunk_count"),
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
    if collection_ids is not None:
        stmt = stmt.where(col_t.c.id.in_(collection_ids))
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


async def get_index_overview(
    db: AsyncSession, collection_ids: list[str] | None = None
) -> IndexStatusResponse:
    """Return BM25 index coverage grouped by workspace then collection.

    A document counts as indexed once it has stored chunks (``chunk_count`` set),
    since its text is FTS-searchable via ``chunks.text_tsv``. Only collections
    with at least one active document appear — empty ones have nothing to index.

    ``collection_ids`` is the caller's grant scope: ``None`` = unrestricted (master/admin);
    a list restricts coverage to those collections (an empty list shows nothing).
    """
    if collection_ids is not None and not collection_ids:
        return IndexStatusResponse(workspaces=[])
    # ws_id -> (ws_name, {col_id -> (col_name, {doc_id -> status})})
    tree: dict[str, tuple[str, dict[str, tuple[str, dict[str, str | None]]]]] = defaultdict(
        lambda: ("", defaultdict(lambda: ("", {})))
    )
    indexed: set[str] = set()
    for row in await _active_documents(db, collection_ids):
        _, cols = tree[row.ws_id]
        tree[row.ws_id] = (row.ws_name, cols)
        _, docs = cols[row.col_id]
        cols[row.col_id] = (row.col_name, docs)
        docs[row.doc_id] = row.status  # ordered by created_at → latest job wins
        if row.chunk_count:
            indexed.add(row.doc_id)

    workspaces = [
        WorkspaceIndexStatus(
            workspace_id=ws_id,
            workspace_name=ws_name,
            collections=[
                _collection_status(cid, cname, docs, indexed)
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
