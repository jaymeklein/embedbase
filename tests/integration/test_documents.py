"""Integration tests for document endpoints.

The Celery enqueue is stubbed and uploads stream to a tmp dir, so these run
without Redis/Chroma/worker. The 501 search route is verified separately.
"""

import pytest

from api.services import tasks as task_producer
from api.settings import settings

MASTER = "test-master-key-for-testing-only"
AUTH = {"X-API-Key": MASTER}


@pytest.fixture(autouse=True)
def _isolate_io(monkeypatch, tmp_path):
    """Redirect uploads to a tmp dir and neutralize the broker."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "data"))
    monkeypatch.setattr(task_producer, "enqueue_ingest", lambda *a, **k: "task-x")
    monkeypatch.setattr(task_producer, "enqueue_delete", lambda *a, **k: "task-y")


async def _setup(client):
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=AUTH)).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "C"}, headers=AUTH)
    ).json()["id"]
    return ws_id, col_id


def _txt(name="note.txt", body=b"Hello.\n\nWorld."):
    return {"file": (name, body, "text/plain")}


# ── auth ──────────────────────────────────────────────────────────────────────

async def test_upload_requires_api_key(client):
    ws_id, col_id = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents", files=_txt()
    )
    assert r.status_code == 401


async def test_upload_rejects_bad_key(client):
    ws_id, col_id = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files=_txt(),
        headers={"X-API-Key": "eb_not_a_real_key"},
    )
    assert r.status_code == 401


# ── upload ────────────────────────────────────────────────────────────────────

async def test_upload_returns_202_and_job(client):
    ws_id, col_id = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files=_txt(),
        headers=AUTH,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["document_id"].startswith("doc_")
    assert body["job_id"].startswith("job_")
    assert body["file_type"] == ".txt"
    assert body["file_size"] > 0


async def test_upload_unsupported_type_returns_415(client):
    ws_id, col_id = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files={"file": ("bad.xyz", b"data", "application/octet-stream")},
        headers=AUTH,
    )
    assert r.status_code == 415


async def test_upload_unknown_collection_returns_404(client):
    ws_id, _ = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/col_nope/documents",
        files=_txt(),
        headers=AUTH,
    )
    assert r.status_code == 404


# ── list / status ─────────────────────────────────────────────────────────────

async def test_list_documents_shows_uploaded(client):
    ws_id, col_id = await _setup(client)
    up = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents",
            files=_txt(),
            headers=AUTH,
        )
    ).json()

    docs = (
        await client.get(
            f"/workspaces/{ws_id}/collections/{col_id}/documents", headers=AUTH
        )
    ).json()
    assert len(docs) == 1
    assert docs[0]["document_id"] == up["document_id"]
    assert docs[0]["status"] == "pending"


async def test_document_status_returns_job(client):
    ws_id, col_id = await _setup(client)
    up = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents",
            files=_txt(),
            headers=AUTH,
        )
    ).json()

    r = await client.get(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/{up['document_id']}/status",
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    assert r.json()["job_id"] == up["job_id"]


async def test_status_unknown_document_returns_404(client):
    ws_id, col_id = await _setup(client)
    r = await client.get(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/doc_missing/status",
        headers=AUTH,
    )
    assert r.status_code == 404


# ── delete ────────────────────────────────────────────────────────────────────

async def test_delete_document(client):
    ws_id, col_id = await _setup(client)
    up = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents",
            files=_txt(),
            headers=AUTH,
        )
    ).json()
    doc_id = up["document_id"]

    r = await client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}", headers=AUTH
    )
    assert r.status_code == 204

    after = (
        await client.get(
            f"/workspaces/{ws_id}/collections/{col_id}/documents", headers=AUTH
        )
    ).json()
    assert after == []


async def test_delete_unknown_document_returns_404(client):
    ws_id, col_id = await _setup(client)
    r = await client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/doc_nope", headers=AUTH
    )
    assert r.status_code == 404


# ── collection-scoped keys ────────────────────────────────────────────────────

async def test_collection_key_can_upload_to_own_collection(client):
    ws_id, col_id = await _setup(client)
    raw = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/keys", json={"label": "k"}, headers=AUTH
        )
    ).json()["raw_key"]

    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files=_txt(),
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 202


async def test_collection_key_cannot_upload_to_other_collection(client):
    ws_id, col_id = await _setup(client)
    other = (
        await client.post(
            f"/workspaces/{ws_id}/collections", json={"name": "Other"}, headers=AUTH
        )
    ).json()["id"]
    raw = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/keys", json={"label": "k"}, headers=AUTH
        )
    ).json()["raw_key"]

    r = await client.post(
        f"/workspaces/{ws_id}/collections/{other}/documents",
        files=_txt(),
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 403

