"""Integration tests for the ingestion-queue (job history) endpoint.

The Celery enqueue is stubbed and uploads stream to a tmp dir, so these run without
Redis/worker — each upload still writes the job_records row the listing reads.
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


async def _setup(client):
    ws_id = (await client.post("/workspaces", json={"name": "WS"}, headers=AUTH)).json()["id"]
    col_id = (
        await client.post(f"/workspaces/{ws_id}/collections", json={"name": "Handbook"}, headers=AUTH)
    ).json()["id"]
    return ws_id, col_id


async def _upload(client, ws_id, col_id, name="note.txt"):
    return (
        await client.post(
            f"/workspaces/{ws_id}/collections/{col_id}/documents",
            files={"file": (name, b"Hello.\n\nWorld.", "text/plain")},
            headers=AUTH,
        )
    ).json()


async def test_jobs_requires_api_key(client):
    r = await client.get("/ingestion/jobs")
    assert r.status_code == 401


async def test_jobs_lists_uploaded_job(client):
    ws_id, col_id = await _setup(client)
    up = await _upload(client, ws_id, col_id)

    r = await client.get("/ingestion/jobs", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["job_id"] == up["job_id"]
    assert row["document_id"] == up["document_id"]
    assert row["status"] == "pending"
    assert row["collection_name"] == "Handbook"  # joined from the collection


async def test_jobs_filters_by_status_and_filename(client):
    ws_id, col_id = await _setup(client)
    await _upload(client, ws_id, col_id, name="alpha.txt")
    await _upload(client, ws_id, col_id, name="beta.txt")

    # All uploads start pending, so a failed filter returns nothing.
    assert (await client.get("/ingestion/jobs?status=failed", headers=AUTH)).json()["total"] == 0
    assert (await client.get("/ingestion/jobs?status=pending", headers=AUTH)).json()["total"] == 2

    by_name = (await client.get("/ingestion/jobs?filename=alpha", headers=AUTH)).json()
    assert by_name["total"] == 1
    assert by_name["items"][0]["filename"] == "alpha.txt"


async def test_jobs_paginates(client):
    ws_id, col_id = await _setup(client)
    for i in range(3):
        await _upload(client, ws_id, col_id, name=f"f{i}.txt")

    page = (await client.get("/ingestion/jobs?limit=2&offset=0", headers=AUTH)).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert (page["limit"], page["offset"]) == (2, 0)

    # the JobListQuery limit bounds bind from the query string -> 422, not a silent clamp.
    assert (await client.get("/ingestion/jobs?limit=0", headers=AUTH)).status_code == 422
    assert (await client.get("/ingestion/jobs?limit=201", headers=AUTH)).status_code == 422


async def test_jobs_stats_reports_counts_and_pause(client):
    ws_id, col_id = await _setup(client)
    await _upload(client, ws_id, col_id, name="a.txt")
    await _upload(client, ws_id, col_id, name="b.txt")

    r = await client.get("/ingestion/jobs/stats", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["pending"] == 2  # both uploads start pending
    assert body["paused_seconds"] == 0  # no redis pause in the test env (best-effort read)


async def test_jobs_stats_requires_api_key(client):
    assert (await client.get("/ingestion/jobs/stats")).status_code == 401


# ── bulk retry (POST /ingestion/jobs/retry-failed) ──────────────────────────────

async def _mark_failed(client, doc_id):
    """Force a document's job to 'failed' (uploads start pending), the real retry precondition."""
    from sqlalchemy import update

    from api.tables import job_records as job_t

    async with client.session_factory() as s:
        await s.execute(update(job_t).where(job_t.c.document_id == doc_id).values(status="failed"))
        await s.commit()


async def test_retry_failed_reenqueues_only_failed_documents(client):
    ws_id, col_id = await _setup(client)
    bad = await _upload(client, ws_id, col_id, name="broken.txt")
    await _upload(client, ws_id, col_id, name="fine.txt")  # stays pending
    await _mark_failed(client, bad["document_id"])

    r = await client.post("/ingestion/jobs/retry-failed", headers=AUTH)
    assert r.status_code == 202
    assert r.json() == {"retried": 1}  # only the failed document

    # The failed document now carries a fresh pending attempt beside its failed history.
    rows = (await client.get("/ingestion/jobs", headers=AUTH)).json()["items"]
    statuses = sorted(j["status"] for j in rows if j["document_id"] == bad["document_id"])
    assert statuses == ["failed", "pending"]


async def test_retry_failed_respects_listing_filters(client):
    ws_id, col_id = await _setup(client)
    alpha = await _upload(client, ws_id, col_id, name="alpha.txt")
    beta = await _upload(client, ws_id, col_id, name="beta.txt")
    await _mark_failed(client, alpha["document_id"])
    await _mark_failed(client, beta["document_id"])

    # Only the failed document whose filename matches the filter is retried.
    r = await client.post("/ingestion/jobs/retry-failed?filename=alpha", headers=AUTH)
    assert r.status_code == 202
    assert r.json() == {"retried": 1}


async def test_retry_failed_is_zero_when_nothing_failed(client):
    ws_id, col_id = await _setup(client)
    await _upload(client, ws_id, col_id)  # pending, not failed

    r = await client.post("/ingestion/jobs/retry-failed", headers=AUTH)
    assert r.status_code == 202
    assert r.json() == {"retried": 0}


async def test_retry_failed_requires_api_key(client):
    assert (await client.post("/ingestion/jobs/retry-failed")).status_code == 401
