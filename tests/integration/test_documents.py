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


async def test_upload_docx_rejected_without_docling_returns_415(client):
    # .docx needs the docling backend; with the default config docling is off, so the office
    # format is rejected up front (415) — not accepted and then failed later in the worker.
    ws_id, col_id = await _setup(client)
    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files={"file": ("deck.docx", b"data", "application/octet-stream")},
        headers=AUTH,
    )
    assert r.status_code == 415
    assert "docling" in r.json()["detail"].lower()


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
    assert docs["total"] == 1
    assert docs["items"][0]["document_id"] == up["document_id"]
    assert docs["items"][0]["status"] == "pending"


async def test_list_documents_reports_indexed_flag(client):
    """Each document carries an ``indexed`` flag: true once it has stored chunks
    (``chunk_count`` set, as the worker does when ingestion finishes)."""
    from sqlalchemy import update

    from api.tables import documents as doc_t

    ws_id, col_id = await _setup(client)
    base = f"/workspaces/{ws_id}/collections/{col_id}/documents"
    a = (await client.post(base, files=_txt("a.txt"), headers=AUTH)).json()["document_id"]
    b = (await client.post(base, files=_txt("b.txt"), headers=AUTH)).json()["document_id"]
    # Only document `a` has stored chunks → only `a` is FTS-indexed.
    async with client.session_factory() as s:
        await s.execute(update(doc_t).where(doc_t.c.id == a).values(chunk_count=1))
        await s.commit()

    by_id = {d["document_id"]: d for d in (await client.get(base, headers=AUTH)).json()["items"]}
    assert by_id[a]["indexed"] is True
    assert by_id[b]["indexed"] is False


async def test_list_documents_endpoint_binds_query_params(client):
    """The listing endpoint builds its ``DocumentListQuery`` straight from the query string:
    pagination, a filename filter, and the limit bounds all go through FastAPI's model binding —
    the refactor's one real risk surface, since every other test calls the service directly."""
    ws_id, col_id = await _setup(client)
    base = f"/workspaces/{ws_id}/collections/{col_id}/documents"
    for name in ("alpha.txt", "beta.txt", "gamma.txt"):
        await client.post(base, files=_txt(name), headers=AUTH)

    # limit/offset coerce from the query string and page the results: 2 of 3.
    page = (await client.get(f"{base}?limit=2&offset=0", headers=AUTH)).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert (page["limit"], page["offset"]) == (2, 0)

    # a filename substring filter binds and applies.
    filtered = (await client.get(f"{base}?filename=alpha", headers=AUTH)).json()
    assert filtered["total"] == 1 and filtered["items"][0]["filename"] == "alpha.txt"

    # the Field(ge=1, le=200) bounds survive the model refactor -> 422, never a silent clamp.
    assert (await client.get(f"{base}?limit=0", headers=AUTH)).status_code == 422
    assert (await client.get(f"{base}?limit=201", headers=AUTH)).status_code == 422


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
    assert after["total"] == 0 and after["items"] == []


async def test_delete_unknown_document_returns_404(client):
    ws_id, col_id = await _setup(client)
    r = await client.delete(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/doc_nope", headers=AUTH
    )
    assert r.status_code == 404


# ── reprocess ─────────────────────────────────────────────────────────────────

async def test_reprocess_document_reenqueues(client):
    from sqlalchemy import update

    from api.tables import job_records as job_t

    ws_id, col_id = await _setup(client)
    up = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents", files=_txt(), headers=AUTH
        )
    ).json()
    doc_id = up["document_id"]
    # Simulate the upload's ingest having failed (the real reprocess case; a still-pending job no-ops).
    async with client.session_factory() as s:
        await s.execute(update(job_t).where(job_t.c.document_id == doc_id).values(status="failed"))
        await s.commit()

    r = await client.post(f"/documents/{doc_id}/reprocess", headers=AUTH)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["document_id"] == doc_id
    assert body["job_id"] != up["job_id"]  # a fresh retry attempt, not the original failed job

    docs = (
        await client.get(f"/workspaces/{ws_id}/collections/{col_id}/documents", headers=AUTH)
    ).json()
    assert docs["total"] == 1  # still one document; its latest job is the pending retry
    assert docs["items"][0]["status"] == "pending"


async def test_reprocess_in_flight_document_is_noop(client):
    """Reprocessing a document whose latest attempt is still pending/processing returns that job
    instead of minting a duplicate — the idempotency guard against double-clicks."""
    ws_id, col_id = await _setup(client)
    up = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents", files=_txt(), headers=AUTH
        )
    ).json()  # its job is 'pending' straight after upload

    body = (await client.post(f"/documents/{up['document_id']}/reprocess", headers=AUTH)).json()
    assert body["job_id"] == up["job_id"]  # the in-flight job, not a fresh one
    assert body["status"] == "pending"


async def test_reprocess_unknown_document_returns_404(client):
    r = await client.post("/documents/doc_nope/reprocess", headers=AUTH)
    assert r.status_code == 404


async def test_reprocess_requires_api_key(client):
    ws_id, col_id = await _setup(client)
    doc_id = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents", files=_txt(), headers=AUTH
        )
    ).json()["document_id"]
    assert (await client.post(f"/documents/{doc_id}/reprocess")).status_code == 401


# ── user keys + grants ────────────────────────────────────────────────────────

async def test_user_key_with_write_can_upload(client, make_user_key):
    ws_id, col_id = await _setup(client)
    _, raw = await make_user_key([("collection", col_id, "write")])

    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files=_txt(),
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 202


async def test_user_key_read_grant_cannot_upload(client, make_user_key):
    """A read grant is not enough to upload (write required) → 403."""
    ws_id, col_id = await _setup(client)
    _, raw = await make_user_key([("collection", col_id, "read")])

    r = await client.post(
        f"/workspaces/{ws_id}/collections/{col_id}/documents",
        files=_txt(),
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 403


async def test_user_key_cannot_upload_to_ungranted_collection(client, make_user_key):
    ws_id, col_id = await _setup(client)
    other = (
        await client.post(
            f"/workspaces/{ws_id}/collections", json={"name": "Other"}, headers=AUTH
        )
    ).json()["id"]
    _, raw = await make_user_key([("collection", col_id, "write")])

    r = await client.post(
        f"/workspaces/{ws_id}/collections/{other}/documents",
        files=_txt(),
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 403


async def test_user_key_without_grant_cannot_list(client, make_user_key):
    """An active user with no grant is denied (403) reading a collection's documents."""
    ws_id, col_id = await _setup(client)
    _, raw = await make_user_key()  # no grants

    r = await client.get(
        f"/workspaces/{ws_id}/collections/{col_id}/documents", headers={"X-API-Key": raw}
    )
    assert r.status_code == 403


async def test_document_grant_allows_nested_status(client, make_user_key):
    """A document-level read grant authorizes the nested single-document status route."""
    ws_id, col_id = await _setup(client)
    doc_id = (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents", files=_txt(), headers=AUTH
        )
    ).json()["document_id"]
    # Granted read on the DOCUMENT only — no collection/workspace grant.
    _, raw = await make_user_key([("document", doc_id, "read")])

    r = await client.get(
        f"/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}/status",
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 200

