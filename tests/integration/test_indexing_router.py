"""Integration tests for the BM25 index status + (re)index endpoints.

Routing-only handlers: authenticate, resolve the collection, delegate. Uses the
``client`` fixture (real app, in-memory SQLite); the enqueue producers are
patched so no Celery broker is touched.
"""

from sqlalchemy import insert

from api.tables import collections, documents, job_records, workspaces

MASTER = "test-master-key-for-testing-only"
AUTH = {"Authorization": f"Bearer {MASTER}"}
_TS = "2026-01-01T00:00:00"


async def _seed(factory) -> None:
    async with factory() as s:
        await s.execute(insert(workspaces).values(id="ws1", name="WS", created_at=_TS, updated_at=_TS))
        await s.execute(
            insert(collections).values(id="col1", workspace_id="ws1", name="C", created_at=_TS, updated_at=_TS)
        )
        await s.execute(
            insert(documents).values(
                id="doc1", collection_id="col1", filename="f.txt", file_type=".txt",
                chunk_count=3, created_at=_TS, updated_at=_TS,
            )
        )
        await s.execute(
            insert(job_records).values(
                job_id="j1", document_id="doc1", collection_id="col1", filename="f.txt",
                file_type=".txt", status="done", created_at=_TS, updated_at=_TS,
            )
        )
        await s.commit()


async def _add_collection(factory, col_id: str, doc_id: str) -> None:
    """A second indexed collection under ws1 — the resource a non-admin must *not* see."""
    async with factory() as s:
        await s.execute(
            insert(collections).values(
                id=col_id, workspace_id="ws1", name=col_id, created_at=_TS, updated_at=_TS
            )
        )
        await s.execute(
            insert(documents).values(
                id=doc_id, collection_id=col_id, filename=f"{doc_id}.txt", file_type=".txt",
                chunk_count=2, created_at=_TS, updated_at=_TS,
            )
        )
        await s.execute(
            insert(job_records).values(
                job_id=f"j_{doc_id}", document_id=doc_id, collection_id=col_id,
                filename=f"{doc_id}.txt", file_type=".txt", status="done",
                created_at=_TS, updated_at=_TS,
            )
        )
        await s.commit()


async def test_index_status_reports_coverage(client):
    await _seed(client.session_factory)

    r = await client.get("/indexing/status", headers=AUTH)

    assert r.status_code == 200
    ws = r.json()["workspaces"][0]
    assert ws["workspace_id"] == "ws1"
    col = ws["collections"][0]
    assert col["total"] == 1
    assert col["indexed"] == 1  # chunk_count set → counts as indexed


async def test_index_status_scoped_to_user_grants(client, make_user_key):
    await _seed(client.session_factory)  # ws1 / col1
    await _add_collection(client.session_factory, "col2", "doc2")
    _, key = await make_user_key(grants=[("collection", "col1", "read")])

    r = await client.get("/indexing/status", headers={"X-API-Key": key})

    assert r.status_code == 200
    seen = [c["collection_id"] for w in r.json()["workspaces"] for c in w["collections"]]
    assert seen == ["col1"]  # col2 is not granted → hidden


async def test_index_status_unrestricted_for_user_without_grants(client, make_user_key):
    await _seed(client.session_factory)  # ws1 / col1
    _, key = await make_user_key(grants=[])
    r = await client.get("/indexing/status", headers={"X-API-Key": key})
    assert r.status_code == 200
    seen = [c["collection_id"] for w in r.json()["workspaces"] for c in w["collections"]]
    assert seen == ["col1"]  # no permissions → sees all coverage


async def test_index_collection_enqueues(client, monkeypatch):
    await _seed(client.session_factory)
    monkeypatch.setattr(
        "api.services.indexing.task_producer.enqueue_index_collection", lambda col: "task-col"
    )

    r = await client.post("/workspaces/ws1/collections/col1/index", headers=AUTH)

    assert r.status_code == 200
    assert r.json()["task_id"] == "task-col"


async def test_index_document_enqueues(client, monkeypatch):
    await _seed(client.session_factory)
    monkeypatch.setattr(
        "api.services.indexing.task_producer.enqueue_index_document", lambda doc, col: "task-doc"
    )

    r = await client.post("/workspaces/ws1/collections/col1/documents/doc1/index", headers=AUTH)

    assert r.status_code == 200
    assert r.json()["task_id"] == "task-doc"
