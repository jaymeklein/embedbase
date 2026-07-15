"""Unit tests for the ingestion-job listing service (pagination + filters)."""

from sqlalchemy import insert

from api.db import collections as col_t
from api.db import job_records as job_t
from api.db import workspaces as ws_t
from api.models.document import JobListQuery
from api.services.jobs import list_jobs

_NOW = "2024-01-01T00:00:00"
_WS_ID = "ws_jobs_test"
_COL_ID = "col_jobs_test"
_COL_NAME = "Handbook"


async def _seed_collection(db_session) -> None:
    """Seed a workspace + one named collection; tests add their own job rows."""
    await db_session.execute(
        insert(ws_t).values(id=_WS_ID, name="WS", description="", color="", icon="", created_at=_NOW, updated_at=_NOW)
    )
    await db_session.execute(
        insert(col_t).values(id=_COL_ID, workspace_id=_WS_ID, name=_COL_NAME, description="", color="", icon="", created_at=_NOW, updated_at=_NOW)
    )
    await db_session.commit()


async def _add_job(
    db_session,
    job_id: str,
    *,
    collection_id: str = _COL_ID,
    document_id: str | None = None,
    filename: str = "x.pdf",
    file_type: str = ".pdf",
    status: str = "done",
    chunk_count: int | None = None,
    error: str | None = None,
    created_at: str = _NOW,
) -> None:
    """Insert one job_records row (defaults to a finished PDF in the seeded collection)."""
    await db_session.execute(
        insert(job_t).values(
            job_id=job_id,
            document_id=document_id or f"doc_{job_id}",
            collection_id=collection_id,
            filename=filename,
            file_type=file_type,
            status=status,
            chunk_count=chunk_count,
            error=error,
            created_at=created_at,
            updated_at=created_at,
        )
    )


async def test_list_jobs_paginates_newest_first_with_total(db_session) -> None:
    await _seed_collection(db_session)
    for i in range(5):
        await _add_job(db_session, f"job_{i}", created_at=f"2024-01-0{i + 1}T00:00:00")
    await db_session.commit()

    page1 = await list_jobs(db_session, JobListQuery(limit=2, offset=0))
    assert page1["total"] == 5  # full match count, not the page length
    assert (page1["limit"], page1["offset"]) == (2, 0)
    assert [j["job_id"] for j in page1["items"]] == ["job_4", "job_3"]  # newest first

    last = await list_jobs(db_session, JobListQuery(limit=2, offset=4))
    assert last["total"] == 5
    assert [j["job_id"] for j in last["items"]] == ["job_0"]  # trailing partial page


async def test_list_jobs_keeps_every_attempt_for_a_document(db_session) -> None:
    """Unlike the documents listing, the queue shows each job row (re-ingests/retries)."""
    await _seed_collection(db_session)
    await _add_job(db_session, "job_first", document_id="doc_x", status="failed", created_at="2024-01-01T00:00:00")
    await _add_job(db_session, "job_retry", document_id="doc_x", status="done", created_at="2024-02-01T00:00:00")
    await db_session.commit()

    res = await list_jobs(db_session, JobListQuery())
    assert res["total"] == 2
    assert [j["job_id"] for j in res["items"]] == ["job_retry", "job_first"]


async def test_list_jobs_joins_collection_name(db_session) -> None:
    await _seed_collection(db_session)
    await _add_job(db_session, "job_a")
    await db_session.commit()

    res = await list_jobs(db_session, JobListQuery())
    assert res["items"][0]["collection_name"] == _COL_NAME
    assert res["items"][0]["workspace_id"] == _WS_ID  # for the "open in collection" link
    assert res["items"][0]["document_id"] == "doc_job_a"  # for the live overlay


async def test_list_jobs_lists_orphan_job_with_null_collection_name(db_session) -> None:
    """A job whose collection was deleted still appears (LEFT join), with a NULL name."""
    await _seed_collection(db_session)
    await _add_job(db_session, "job_orphan", collection_id="col_gone")
    await db_session.commit()

    res = await list_jobs(db_session, JobListQuery())
    assert res["total"] == 1
    assert res["items"][0]["job_id"] == "job_orphan"
    assert res["items"][0]["collection_name"] is None
    assert res["items"][0]["workspace_id"] is None  # gone collection → no link target


async def test_list_jobs_filters_by_status(db_session) -> None:
    await _seed_collection(db_session)
    await _add_job(db_session, "job_ok", status="done")
    await _add_job(db_session, "job_bad", status="failed")
    await db_session.commit()

    res = await list_jobs(db_session, JobListQuery(status="failed"))
    assert res["total"] == 1
    assert res["items"][0]["job_id"] == "job_bad"


async def test_list_jobs_filters_by_filename_substring_ci(db_session) -> None:
    await _seed_collection(db_session)
    await _add_job(db_session, "job_a", filename="Report-2024.pdf")
    await _add_job(db_session, "job_b", filename="notes.md", file_type=".md")
    await db_session.commit()

    res = await list_jobs(db_session, JobListQuery(filename="report"))  # case-insensitive
    assert res["total"] == 1
    assert res["items"][0]["job_id"] == "job_a"


async def test_list_jobs_filters_by_file_type(db_session) -> None:
    await _seed_collection(db_session)
    await _add_job(db_session, "job_pdf", file_type=".pdf")
    await _add_job(db_session, "job_md", file_type=".md")
    await db_session.commit()

    res = await list_jobs(db_session, JobListQuery(file_type=".md"))
    assert res["total"] == 1
    assert res["items"][0]["job_id"] == "job_md"


async def test_list_jobs_filters_by_collection_name_substring(db_session) -> None:
    await _seed_collection(db_session)  # "Handbook"
    await db_session.execute(
        insert(col_t).values(id="col_other", workspace_id=_WS_ID, name="Invoices", description="", color="", icon="", created_at=_NOW, updated_at=_NOW)
    )
    await _add_job(db_session, "job_h", collection_id=_COL_ID)
    await _add_job(db_session, "job_i", collection_id="col_other")
    await db_session.commit()

    res = await list_jobs(db_session, JobListQuery(collection="hand"))  # matches "Handbook", case-insensitive
    assert res["total"] == 1
    assert res["items"][0]["job_id"] == "job_h"


async def test_list_jobs_filters_by_created_date_range_inclusive(db_session) -> None:
    await _seed_collection(db_session)
    await _add_job(db_session, "job_jan", created_at="2024-01-15T00:00:00")
    await _add_job(db_session, "job_mar", created_at="2024-03-15T14:30:00")
    await db_session.commit()

    # A date-only upper bound covers the whole bound day (service expands it to end-of-day).
    res = await list_jobs(db_session, JobListQuery(created_after="2024-02-01", created_before="2024-03-15"))
    assert res["total"] == 1
    assert res["items"][0]["job_id"] == "job_mar"


async def test_list_jobs_filename_filter_escapes_wildcards(db_session) -> None:
    await _seed_collection(db_session)
    await _add_job(db_session, "job_underscore", filename="a_b.pdf")
    await _add_job(db_session, "job_axb", filename="axb.pdf")
    await db_session.commit()

    # "a_b" must match the literal underscore only, not "_" as a single-char LIKE wildcard.
    res = await list_jobs(db_session, JobListQuery(filename="a_b"))
    assert [j["job_id"] for j in res["items"]] == ["job_underscore"]


async def test_list_jobs_lists_processing_first(db_session) -> None:
    await _seed_collection(db_session)
    # An older processing job must still outrank newer non-processing ones (live card leads).
    await _add_job(db_session, "job_old_proc", status="processing", created_at="2024-01-01T00:00:00")
    await _add_job(db_session, "job_new_done", status="done", created_at="2024-06-01T00:00:00")
    await _add_job(db_session, "job_mid_pending", status="pending", created_at="2024-03-01T00:00:00")
    await db_session.commit()

    order = [j["job_id"] for j in (await list_jobs(db_session, JobListQuery()))["items"]]
    assert order[0] == "job_old_proc"  # processing floats up despite being the oldest
    assert order[1:] == ["job_new_done", "job_mid_pending"]  # the rest stay newest-first


async def test_job_status_counts(db_session) -> None:
    from api.services.jobs import job_status_counts

    await _seed_collection(db_session)
    await _add_job(db_session, "job_a", status="done")
    await _add_job(db_session, "job_b", status="rate_limited")
    await _add_job(db_session, "job_c", status="rate_limited")
    await db_session.commit()

    counts = await job_status_counts(db_session)
    assert counts == {"done": 1, "rate_limited": 2}  # statuses with no rows are absent


def test_embedding_pause_seconds_reads_ttl_best_effort() -> None:
    from api.constants import EMBEDDING_PAUSE_KEY
    from api.services.jobs import embedding_pause_seconds
    from tests.unit.fakes import FakeRedis

    assert embedding_pause_seconds(None) == 0  # no client → not paused
    fake = FakeRedis()
    assert embedding_pause_seconds(fake) == 0  # key absent → 0
    fake.set(EMBEDDING_PAUSE_KEY, "1", ex=90)
    assert embedding_pause_seconds(fake) == 90  # reads the backoff TTL

    class _Boom:
        def ttl(self, _key: str) -> int:
            raise RuntimeError("redis down")

    assert embedding_pause_seconds(_Boom()) == 0  # redis error → not-paused, never raises
