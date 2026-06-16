"""Integration tests for the tag-correlation graph endpoints."""

import pytest

from api.services import tasks as task_producer
from api.settings import settings

MASTER = "test-master-key-for-testing-only"


@pytest.fixture(autouse=True)
def _stub_broker(monkeypatch, tmp_path):
    """Redirect uploads to a tmp dir and neutralize the Celery broker."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "data"))
    monkeypatch.setattr(task_producer, "enqueue_ingest", lambda *a, **k: "task-x")
    monkeypatch.setattr(task_producer, "enqueue_delete", lambda *a, **k: "task-y")


async def _make_workspace(client, name="WS"):
    r = await client.post("/workspaces", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


async def _make_collection(client, ws_id, name="Col"):
    r = await client.post(f"/workspaces/{ws_id}/collections", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


async def _make_tag(client, ws_id, name="python"):
    r = await client.post(f"/workspaces/{ws_id}/tags", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


async def _upload_doc(client, ws, col, filename="note.txt"):
    r = await client.post(
        f"/workspaces/{ws}/collections/{col}/documents",
        files={"file": (filename, b"hello", "text/plain")},
        headers={"X-API-Key": MASTER},
    )
    assert r.status_code == 202
    return r.json()["document_id"]


async def _assign_doc_tag(client, ws, col, doc, tag):
    r = await client.put(f"/workspaces/{ws}/collections/{col}/documents/{doc}/tags/{tag}")
    assert r.status_code == 204


# ── workspace graph ───────────────────────────────────────────────────────────


async def test_workspace_graph_shape_and_heat(master_client):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    d1 = await _upload_doc(master_client, ws, col, "a.txt")
    d2 = await _upload_doc(master_client, ws, col, "b.txt")
    py = await _make_tag(master_client, ws, "python")
    await _assign_doc_tag(master_client, ws, col, d1, py)
    await _assign_doc_tag(master_client, ws, col, d2, py)

    r = await master_client.get(f"/workspaces/{ws}/graph")
    assert r.status_code == 200
    g = r.json()

    files = [n for n in g["nodes"] if n["kind"] == "file"]
    tags = [n for n in g["nodes"] if n["kind"] == "tag"]
    assert {f["id"] for f in files} == {d1, d2}
    assert len(tags) == 1
    assert tags[0]["heat"] == 2
    assert tags[0]["degree"] == 2
    assert g["max_heat"] == 2
    assert g["tag_counts"] == {"python": 2}
    assert len(g["edges"]) == 2


async def test_empty_workspace_graph(master_client):
    ws = await _make_workspace(master_client)

    r = await master_client.get(f"/workspaces/{ws}/graph")
    assert r.status_code == 200
    g = r.json()
    assert g == {"nodes": [], "edges": [], "tag_counts": {}, "max_heat": 0}


async def test_workspace_graph_404_when_absent(master_client):
    r = await master_client.get("/workspaces/ws_nope/graph")
    assert r.status_code == 404


# ── collection graph ──────────────────────────────────────────────────────────


async def test_collection_graph_excludes_other_collections(master_client):
    ws = await _make_workspace(master_client)
    col_a = await _make_collection(master_client, ws, "A")
    col_b = await _make_collection(master_client, ws, "B")
    da = await _upload_doc(master_client, ws, col_a, "a.txt")
    db_ = await _upload_doc(master_client, ws, col_b, "b.txt")
    tag = await _make_tag(master_client, ws, "shared")
    await _assign_doc_tag(master_client, ws, col_a, da, tag)
    await _assign_doc_tag(master_client, ws, col_b, db_, tag)

    r = await master_client.get(f"/workspaces/{ws}/collections/{col_a}/graph")
    assert r.status_code == 200
    g = r.json()

    file_ids = {n["id"] for n in g["nodes"] if n["kind"] == "file"}
    assert file_ids == {da}
    assert g["tag_counts"] == {"shared": 1}


async def test_collection_graph_404_when_absent(master_client):
    ws = await _make_workspace(master_client)
    r = await master_client.get(f"/workspaces/{ws}/collections/col_nope/graph")
    assert r.status_code == 404


async def test_graph_requires_auth(client):
    r = await client.get("/workspaces/ws_x/graph")
    assert r.status_code in (401, 403)
