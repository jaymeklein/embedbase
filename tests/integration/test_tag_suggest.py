"""Integration tests for the AI suggest-tags endpoints."""

import json

import pytest

import api.dependencies as deps
from api.services import tasks as task_producer
from api.settings import settings

MASTER = "test-master-key-for-testing-only"


class FakeRedis:
    def __init__(self) -> None:
        self.m: dict[str, str] = {}

    def get(self, key: str):
        return self.m.get(key)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    """Stub the broker/upload dir and make a fake Redis the live client."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "data"))
    monkeypatch.setattr(task_producer, "enqueue_ingest", lambda *a, **k: "task-x")
    fake = FakeRedis()
    monkeypatch.setattr(deps, "_redis_client", fake)
    return fake


async def _make_workspace(client, name="WS"):
    return (await client.post("/workspaces", json={"name": name})).json()["id"]


async def _make_collection(client, ws_id, name="Col"):
    return (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": name})
    ).json()["id"]


async def _upload_doc(client, ws, col):
    r = await client.post(
        f"/workspaces/{ws}/collections/{col}/documents",
        files={"file": ("note.txt", b"hello", "text/plain")},
        headers={"X-API-Key": MASTER},
    )
    assert r.status_code == 202
    return r.json()["document_id"]


def _seed_corpus(fake, col, entries):
    fake.m[f"bm25:{col}:corpus"] = json.dumps([list(e) for e in entries])


async def test_suggest_document_tags_endpoint(master_client, _env):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    doc = await _upload_doc(master_client, ws, col)
    _seed_corpus(_env, col, [("c1", doc, "kubernetes kubernetes scaling deployment")])

    r = await master_client.post(
        f"/workspaces/{ws}/collections/{col}/documents/{doc}/suggest-tags"
    )
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["suggestions"]}
    assert "kubernetes" in names


async def test_suggest_collection_tags_endpoint(master_client, _env):
    ws = await _make_workspace(master_client)
    col = await _make_collection(master_client, ws)
    _seed_corpus(_env, col, [("c1", "doc_1", "alpha alpha beta")])

    r = await master_client.post(f"/workspaces/{ws}/collections/{col}/suggest-tags")
    assert r.status_code == 200
    assert any(s["name"] == "alpha" for s in r.json()["suggestions"])


async def test_suggest_collection_not_found_404(master_client):
    ws = await _make_workspace(master_client)
    r = await master_client.post(f"/workspaces/{ws}/collections/col_nope/suggest-tags")
    assert r.status_code == 404


async def test_suggest_without_master_401(client):
    r = await client.post("/workspaces/ws_any/collections/col_any/suggest-tags")
    assert r.status_code == 401
