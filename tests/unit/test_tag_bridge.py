"""Unit tests for the API-side search bridge (api/services/tag_bridge.py).

Verifies which documents get a vector-store sync enqueued for each scope. The
enqueue is captured with a recorder that overrides the conftest no-op stub;
active documents (``status IS NULL``) are included, soft-deleted ones excluded.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import workspaces as ws_t
from api.services import tag_bridge
from api.services import tasks as task_producer


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def calls(monkeypatch):
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        task_producer, "enqueue_sync_tags",
        lambda doc_id, col_id: recorded.append((doc_id, col_id)),
    )
    return recorded


async def _ws(db, ws_id="ws_1"):
    await db.execute(insert(ws_t).values(
        id=ws_id, name="WS", created_at=_now(), updated_at=_now()))
    await db.commit()
    return ws_id


async def _col(db, ws_id, col_id):
    await db.execute(insert(col_t).values(
        id=col_id, workspace_id=ws_id, name=col_id, created_at=_now(), updated_at=_now()))
    await db.commit()
    return col_id


async def _doc(db, col_id, doc_id, status=None):
    await db.execute(insert(doc_t).values(
        id=doc_id, collection_id=col_id, filename="f.txt", file_type=".txt",
        created_at=_now(), updated_at=_now(), status=status))
    await db.commit()
    return doc_id


async def test_sync_document_enqueues_single(calls, db_session):
    await tag_bridge.sync_document("col_1", "doc_1")
    assert calls == [("doc_1", "col_1")]


async def test_sync_collection_enqueues_active_docs_only(calls, db_session):
    ws = await _ws(db_session)
    col = await _col(db_session, ws, "col_1")
    await _doc(db_session, col, "doc_1")
    await _doc(db_session, col, "doc_2")
    await _doc(db_session, col, "doc_gone", status="deleting")

    await tag_bridge.sync_collection(col, db_session)

    assert sorted(calls) == [("doc_1", "col_1"), ("doc_2", "col_1")]


async def test_sync_workspace_enqueues_across_collections(calls, db_session):
    ws = await _ws(db_session)
    c1 = await _col(db_session, ws, "col_1")
    c2 = await _col(db_session, ws, "col_2")
    await _doc(db_session, c1, "doc_1")
    await _doc(db_session, c2, "doc_2")
    await _doc(db_session, c2, "doc_gone", status="deleting")
    # a document in another workspace must be ignored
    other = await _ws(db_session, "ws_other")
    co = await _col(db_session, other, "col_other")
    await _doc(db_session, co, "doc_other")

    await tag_bridge.sync_workspace(ws, db_session)

    assert sorted(calls) == [("doc_1", "col_1"), ("doc_2", "col_2")]


async def test_sync_collection_no_docs_enqueues_nothing(calls, db_session):
    ws = await _ws(db_session)
    col = await _col(db_session, ws, "col_empty")
    await tag_bridge.sync_collection(col, db_session)
    assert calls == []
