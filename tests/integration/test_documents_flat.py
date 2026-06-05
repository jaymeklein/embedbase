"""Integration tests for flat document aliases + delete-with-documents cascade."""

import pytest

from api.services import tasks as task_producer
from api.settings import settings

MASTER = "test-master-key-for-testing-only"
AUTH = {"X-API-Key": MASTER}


@pytest.fixture(autouse=True)
def _isolate_io(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "data"))
    monkeypatch.setattr(task_producer, "enqueue_ingest", lambda *a, **k: "task-x")
    monkeypatch.setattr(task_producer, "enqueue_delete", lambda *a, **k: "task-y")


async def _setup(client):
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=AUTH)).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"}, headers=AUTH)
    ).json()["id"]
    return ws_id, col_id


def _txt(name="f.txt", body=b"hi\n\nthere"):
    return {"file": (name, body, "text/plain")}


async def test_flat_upload_requires_auth(client):
    _, col_id = await _setup(client)
    r = await client.post("/documents", data={"collection_id": col_id}, files=_txt())
    assert r.status_code == 401


async def test_flat_upload_creates_document(client):
    _, col_id = await _setup(client)
    r = await client.post(
        "/documents", data={"collection_id": col_id}, files=_txt(), headers=AUTH
    )
    assert r.status_code == 202
    assert r.json()["collection_id"] == col_id
    assert r.json()["status"] == "pending"


async def test_flat_upload_unknown_collection_404(client):
    await _setup(client)
    r = await client.post(
        "/documents", data={"collection_id": "col_nope"}, files=_txt(), headers=AUTH
    )
    assert r.status_code == 404


async def test_flat_delete_removes_document(client):
    ws_id, col_id = await _setup(client)
    doc_id = (
        await client.post(
            "/documents", data={"collection_id": col_id}, files=_txt(), headers=AUTH
        )
    ).json()["document_id"]

    r = await client.delete(f"/documents/{doc_id}", headers=AUTH)
    assert r.status_code == 204

    remaining = (
        await client.get(
            f"/workspaces/{ws_id}/collections/{col_id}/documents", headers=AUTH
        )
    ).json()
    assert remaining == []


async def test_flat_delete_unknown_returns_404(client):
    await _setup(client)
    r = await client.delete("/documents/doc_missing", headers=AUTH)
    assert r.status_code == 404


async def test_deleting_collection_with_documents_cascades(client):
    ws_id, col_id = await _setup(client)
    await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files=_txt(),
        headers=AUTH,
    )

    # Collection delete must succeed even with documents present (FK CASCADE).
    r = await client.delete(f"/workspaces/{ws_id}/collections/{col_id}", headers=AUTH)
    assert r.status_code == 204

    # Collection (and its documents) are gone.
    assert (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}", headers=AUTH)
    ).status_code == 404
