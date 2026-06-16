"""Search bridge: propagate tag-assignment changes into the vector store.

When a tag is (un)assigned to a document, collection, or workspace, the
*effective* tags of the affected documents change (effective tags inherit
downward: workspace → collection → document). The actual vector write happens
in the worker — it owns the vector store and the heavy ML deps — so this module
only resolves *which* documents are affected and enqueues a per-document sync
task. The worker recomputes each document's effective tags and folds them into
its stored chunks so D3 ``apply_filters`` tag filtering returns the right chunks.

Kept separate from :mod:`api.services.tags` so the tag service stays focused and
there is no import cycle (this module reads only table objects and the task
producer; the tag service imports it lazily on assignment changes).

Consistency model (CAP): the bridge favors **availability over consistency**.
Assignment/rename/merge/delete commit to SQLite and return to the client
immediately; the vector-store update is enqueued and applied asynchronously by
the worker (``worker.tasks.sync_document_tags``). Tag-filtered search is
therefore *eventually* consistent — it may briefly return stale results until
the sync lands and then reconverges. SQLite remains the source of truth; the
vector store holds only a denormalized copy of effective tags for filtering.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import documents as doc_t
from api.services import tasks as task_producer


async def sync_document(col_id: str, doc_id: str) -> None:
    """Enqueue a vector-store tag sync for a single document."""
    task_producer.enqueue_sync_tags(doc_id, col_id)


async def sync_collection(col_id: str, db: AsyncSession) -> None:
    """Enqueue a tag sync for every active document in a collection."""
    rows = (
        await db.execute(
            select(doc_t.c.id).where(
                doc_t.c.collection_id == col_id, doc_t.c.status.is_(None)
            )
        )
    ).fetchall()
    for row in rows:
        task_producer.enqueue_sync_tags(row[0], col_id)


async def sync_workspace(ws_id: str, db: AsyncSession) -> None:
    """Enqueue a tag sync for every active document in a workspace."""
    rows = (
        await db.execute(
            select(doc_t.c.id, doc_t.c.collection_id)
            .select_from(doc_t.join(col_t, col_t.c.id == doc_t.c.collection_id))
            .where(col_t.c.workspace_id == ws_id, doc_t.c.status.is_(None))
        )
    ).fetchall()
    for row in rows:
        task_producer.enqueue_sync_tags(row[0], row[1])
