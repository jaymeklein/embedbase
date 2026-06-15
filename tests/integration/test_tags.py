"""Integration tests for the tag CRUD, assignment, and correlation endpoints."""

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


async def _make_tag(client, ws_id, name="python", color=None):
    r = await client.post(f"/workspaces/{ws_id}/tags", json={"name": name, "color": color})
    assert r.status_code == 201
    return r.json()


# ── create / list ─────────────────────────────────────────────────────────────

async def test_create_tag_returns_201_normalized(master_client):
    ws = await _make_workspace(master_client)
    data = await _make_tag(master_client, ws, "  My Tag ", "#fff")
    assert data["id"].startswith("tag_")
    assert data["name"] == "my tag"
    assert data["color"] == "#fff"
    assert data["document_count"] == 0


async def test_create_tag_duplicate_409(master_client):
    ws = await _make_workspace(master_client)
    await _make_tag(master_client, ws, "dup")
    r = await master_client.post(f"/workspaces/{ws}/tags", json={"name": "DUP"})
    assert r.status_code == 409


async def test_create_tag_empty_name_422(master_client):
    ws = await _make_workspace(master_client)
    r = await master_client.post(f"/workspaces/{ws}/tags", json={"name": "   "})
    assert r.status_code == 422


async def test_create_tag_workspace_not_found_404(master_client):
    r = await master_client.post("/workspaces/ws_nope/tags", json={"name": "x"})
    assert r.status_code == 404


async def test_list_tags_with_counts(master_client):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    tag = await _make_tag(master_client, ws, "t")
    await master_client.put(f"/workspaces/{ws}/collections/{col}/tags/{tag['id']}")

    rows = (await master_client.get(f"/workspaces/{ws}/tags")).json()
    assert len(rows) == 1
    assert rows[0]["collection_count"] == 1


# ── update / delete ───────────────────────────────────────────────────────────

async def test_patch_tag_renames(master_client):
    ws = await _make_workspace(master_client)
    tag = await _make_tag(master_client, ws, "old")
    r = await master_client.patch(f"/workspaces/{ws}/tags/{tag['id']}", json={"name": "new"})
    assert r.status_code == 200
    assert r.json()["name"] == "new"


async def test_delete_tag_204(master_client):
    ws = await _make_workspace(master_client)
    tag = await _make_tag(master_client, ws, "bye")
    r = await master_client.delete(f"/workspaces/{ws}/tags/{tag['id']}")
    assert r.status_code == 204
    assert (await master_client.get(f"/workspaces/{ws}/tags")).json() == []


# ── assignment + correlation + filtering ──────────────────────────────────────

async def test_assign_collection_tag_echoes_and_filters(master_client):
    ws = await _make_workspace(master_client)
    c1 = await _make_collection(master_client, ws, "C1")
    await _make_collection(master_client, ws, "C2")
    tag = await _make_tag(master_client, ws, "keep")

    r = await master_client.put(f"/workspaces/{ws}/collections/{c1}/tags/{tag['id']}")
    assert r.status_code == 204

    cols = (await master_client.get(f"/workspaces/{ws}/collections")).json()
    tagged = next(c for c in cols if c["id"] == c1)
    assert tagged["tags"][0]["name"] == "keep"

    filtered = (await master_client.get(f"/workspaces/{ws}/collections?tag=keep")).json()
    assert [c["id"] for c in filtered] == [c1]


async def test_assign_then_unassign_workspace_tag(master_client):
    ws = await _make_workspace(master_client)
    tag = await _make_tag(master_client, ws, "w")
    assert (await master_client.put(f"/workspaces/{ws}/assigned-tags/{tag['id']}")).status_code == 204
    rows = (await master_client.get(f"/workspaces/{ws}/tags")).json()
    assert rows[0]["workspace_count"] == 1

    assert (
        await master_client.delete(f"/workspaces/{ws}/assigned-tags/{tag['id']}")
    ).status_code == 204
    rows = (await master_client.get(f"/workspaces/{ws}/tags")).json()
    assert rows[0]["workspace_count"] == 0


async def test_unassign_collection_tag(master_client):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    tag = await _make_tag(master_client, ws, "t")
    await master_client.put(f"/workspaces/{ws}/collections/{col}/tags/{tag['id']}")
    r = await master_client.delete(f"/workspaces/{ws}/collections/{col}/tags/{tag['id']}")
    assert r.status_code == 204
    cols = (await master_client.get(f"/workspaces/{ws}/collections")).json()
    assert next(c for c in cols if c["id"] == col)["tags"] == []


async def _upload_doc(master_client, ws, col):
    r = await master_client.post(
        f"/workspaces/{ws}/collections/{col}/documents",
        files={"file": ("note.txt", b"hello", "text/plain")},
        headers={"X-API-Key": MASTER},
    )
    assert r.status_code == 202
    return r.json()["document_id"]


async def test_assign_then_unassign_document_tag(master_client):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    doc = await _upload_doc(master_client, ws, col)
    tag = await _make_tag(master_client, ws, "d")

    base = f"/workspaces/{ws}/collections/{col}/documents/{doc}/tags/{tag['id']}"
    assert (await master_client.put(base)).status_code == 204
    items = (await master_client.get(f"/workspaces/{ws}/tags/{tag['id']}/items")).json()
    assert [d["id"] for d in items["documents"]] == [doc]

    assert (await master_client.delete(base)).status_code == 204
    items = (await master_client.get(f"/workspaces/{ws}/tags/{tag['id']}/items")).json()
    assert items["documents"] == []


async def test_tag_items_endpoint(master_client):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    tag = await _make_tag(master_client, ws, "t")
    await master_client.put(f"/workspaces/{ws}/collections/{col}/tags/{tag['id']}")

    items = (await master_client.get(f"/workspaces/{ws}/tags/{tag['id']}/items")).json()
    assert [c["id"] for c in items["collections"]] == [col]
    assert items["documents"] == []


async def test_merge_endpoint(master_client):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    src = await _make_tag(master_client, ws, "src")
    dst = await _make_tag(master_client, ws, "dst")
    await master_client.put(f"/workspaces/{ws}/collections/{col}/tags/{src['id']}")

    r = await master_client.post(
        f"/workspaces/{ws}/tags/merge",
        json={"source_id": src["id"], "target_id": dst["id"]},
    )
    assert r.status_code == 200
    assert r.json()["collection_count"] == 1
    assert len((await master_client.get(f"/workspaces/{ws}/tags")).json()) == 1


async def test_merge_into_self_422(master_client):
    ws = await _make_workspace(master_client)
    tag = await _make_tag(master_client, ws, "t")
    r = await master_client.post(
        f"/workspaces/{ws}/tags/merge",
        json={"source_id": tag["id"], "target_id": tag["id"]},
    )
    assert r.status_code == 422


# ── auth ──────────────────────────────────────────────────────────────────────

async def test_create_tag_without_master_401(client):
    r = await client.post("/workspaces/ws_any/tags", json={"name": "x"})
    assert r.status_code == 401


async def test_list_tags_without_master_401(client):
    r = await client.get("/workspaces/ws_any/tags")
    assert r.status_code == 401
