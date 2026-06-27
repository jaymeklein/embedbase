"""Unit tests for the BM25 index-status / orchestration service."""

import json

import pytest
from sqlalchemy import insert

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.db import workspaces as ws_t
from api.services.indexing import (
    _collection_status,
    enqueue_collection,
    enqueue_document,
    get_index_overview,
    indexed_doc_ids,
)

_TS = "2024-01-01T00:00:00"


class FakeRedis:
    def __init__(self, initial: dict | None = None) -> None:
        self.store = dict(initial or {})

    def get(self, key: str) -> str | None:
        return self.store.get(key)


def _corpus(doc_ids: list[str]) -> str:
    return json.dumps([[f"c_{d}", d, "text"] for d in doc_ids])


# --- indexed_doc_ids --------------------------------------------------------


def test_indexed_doc_ids_reads_corpus():
    rds = FakeRedis({"bm25:col1:corpus": _corpus(["doc1", "doc1", "doc2"])})
    assert indexed_doc_ids(rds, "col1") == {"doc1", "doc2"}


def test_indexed_doc_ids_empty_when_no_corpus():
    assert indexed_doc_ids(FakeRedis(), "col1") == set()


# --- _collection_status -----------------------------------------------------


def test_collection_status_counts():
    doc_status = {"d1": None, "d2": "processing", "d3": "failed", "d4": None}
    status = _collection_status("col1", "C", doc_status, indexed={"d1", "d3"})
    assert (status.total, status.indexed, status.unindexed) == (4, 2, 2)
    assert status.pending == 1  # d2
    assert status.failed == 1  # d3


# --- enqueue ----------------------------------------------------------------


def test_enqueue_document(monkeypatch):
    monkeypatch.setattr(
        "api.services.indexing.task_producer.enqueue_index_document",
        lambda doc, col: "task-1",
    )
    assert enqueue_document("doc1", "col1").task_id == "task-1"


def test_enqueue_collection(monkeypatch):
    monkeypatch.setattr(
        "api.services.indexing.task_producer.enqueue_index_collection",
        lambda col: "task-2",
    )
    assert enqueue_collection("col1").task_id == "task-2"


# --- overview / collection enqueue (async, real sqlite) ---------------------


async def _seed(db_session) -> None:
    await db_session.execute(insert(ws_t).values(
        id="ws1", name="WS", description="", color="", icon="",
        created_at=_TS, updated_at=_TS,
    ))
    await db_session.execute(insert(col_t).values(
        id="col1", workspace_id="ws1", name="C", description="", color="", icon="",
        created_at=_TS, updated_at=_TS,
    ))
    for doc_id, status in [("d1", "done"), ("d2", "done"), ("d3", "failed")]:
        await db_session.execute(insert(doc_t).values(
            id=doc_id, collection_id="col1", filename=f"{doc_id}.md", file_type=".md",
            file_size=1, chunk_count=1, created_at=_TS, updated_at=_TS, status=None,
        ))
        await db_session.execute(insert(job_t).values(
            job_id=f"job_{doc_id}", document_id=doc_id, collection_id="col1",
            filename=f"{doc_id}.md", file_type=".md", status=status,
            created_at=_TS, updated_at=_TS,
        ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_get_index_overview_groups_and_counts(db_session):
    await _seed(db_session)
    rds = FakeRedis({"bm25:col1:corpus": _corpus(["d1"])})  # only d1 indexed
    resp = await get_index_overview(db_session, rds)

    assert len(resp.workspaces) == 1
    ws = resp.workspaces[0]
    assert ws.workspace_id == "ws1"
    assert len(ws.collections) == 1
    cs = ws.collections[0]
    assert cs.total == 3
    assert cs.indexed == 1
    assert cs.unindexed == 2
    assert cs.failed == 1
