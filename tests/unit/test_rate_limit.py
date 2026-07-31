"""Unit tests for the rate-limit resume feature (worker/tasks.py).

Covers the pieces the pause/resume path relies on and that previously shipped
untested: the RPM throttle window, 429 classification (typed error + the narrowed
string fallback), the provider retry-delay parse, the pending-retry marker, and the
beat-sweep requeue with its pending guard.
"""

import time

import httpx
import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.adapters.embeddings.errors import RateLimitError, raise_for_status
from api.tables import job_records, metadata
from tests.unit.fakes import FakeRedis
from worker import config_reload, tasks
from worker.tasks import (
    _embedding_paused_seconds,
    _is_rate_limit,
    _pause_embedding,
    _retry_delay_seconds,
    _RpmLimiter,
    clear_embedding_pause,
    requeue_orphaned_pending,
    requeue_rate_limited,
    requeue_stuck_processing,
)

# --------------------------------------------------------------------------- #
# _RpmLimiter — sliding-window accounting                                      #
# --------------------------------------------------------------------------- #


def test_rpm_limiter_disabled_never_blocks():
    lim = _RpmLimiter()
    lim.throttle(1000, 0)  # rpm <= 0 disables — nothing recorded
    lim.throttle(0, 100)  # n <= 0 is a no-op
    assert lim._used(time.monotonic()) == 0


def test_rpm_limiter_prunes_events_older_than_a_minute():
    lim = _RpmLimiter()
    now = time.monotonic()
    lim._events.append((now - 61.0, 10))  # outside the trailing-60s window
    lim._events.append((now - 5.0, 4))  # inside it
    assert lim._used(now) == 4  # the stale batch is dropped


def test_rpm_limiter_under_cap_records_without_blocking():
    lim = _RpmLimiter()
    lim.throttle(5, 100)  # far under the cap → returns at once, records the batch
    assert lim._used(time.monotonic()) == 5


# --------------------------------------------------------------------------- #
# _is_rate_limit — typed first, then a deliberately narrow string fallback     #
# --------------------------------------------------------------------------- #


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(status, request=req))


def test_is_rate_limit_typed_error():
    assert _is_rate_limit(RateLimitError("Gemini API 429: RESOURCE_EXHAUSTED")) is True


def test_is_rate_limit_http_status():
    assert _is_rate_limit(_http_error(429)) is True
    assert _is_rate_limit(_http_error(500)) is False


@pytest.mark.parametrize(
    "msg", ["rate limit exceeded", "429 Too Many Requests", "RESOURCE_EXHAUSTED for model"]
)
def test_is_rate_limit_matches_specific_phrases(msg):
    assert _is_rate_limit(ValueError(msg)) is True


@pytest.mark.parametrize("msg", ["daily quota for field X missing", "code 429", "bad request"])
def test_is_rate_limit_narrowed_avoids_false_positive(msg):
    # A bare "quota"/"429" substring must NOT be classified as a rate limit — otherwise an
    # unrelated error would be retried forever (max_retries=None on the rate-limit path).
    assert _is_rate_limit(ValueError(msg)) is False


# --------------------------------------------------------------------------- #
# _retry_delay_seconds — provider hint, default, cap                          #
# --------------------------------------------------------------------------- #


def test_retry_delay_parses_provider_hint():
    assert _retry_delay_seconds(Exception('{"retryDelay": "37.9s"}')) == 39  # 37 + 2s past edge


def test_retry_delay_reads_retry_after():
    assert _retry_delay_seconds(Exception("Retry-After: 120")) == 122


def test_retry_delay_default_when_no_hint():
    assert _retry_delay_seconds(Exception("just a plain error")) == 60


def test_retry_delay_capped():
    assert _retry_delay_seconds(Exception('{"retryDelay": "999999s"}')) == 3600


# --------------------------------------------------------------------------- #
# retry-pending marker + requeue sweep                                         #
# --------------------------------------------------------------------------- #


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rl.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed_job(factory, job_id="job_rl", doc="doc_rl", col="col_rl", status="rate_limited", updated="t"):
    with factory() as s:
        s.execute(
            insert(job_records).values(
                job_id=job_id, document_id=doc, collection_id=col,
                filename="a.pdf", file_type=".pdf", status=status,
                created_at="t", updated_at=updated,
            )
        )
        s.commit()


def test_requeue_reenqueues_rate_limited_job(tmp_path, monkeypatch):
    factory = _db(tmp_path)
    _seed_job(factory)
    calls: list = []
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.ingest_document, "delay", lambda *a: calls.append(a))

    assert requeue_rate_limited() == 1
    # (job_id, storage_key via document_key, collection_id, document_id, file_type)
    assert calls == [("job_rl", "col_rl/doc_rl.pdf", "col_rl", "doc_rl", ".pdf")]


def test_requeue_stuck_processing_resumes_only_stale_heartbeat(tmp_path, monkeypatch):
    """A crashed/restarted worker leaves a job 'processing' with no heartbeat — resume it
    automatically. A job with a LIVE heartbeat is actively being worked and must be left alone."""
    factory = _db(tmp_path)
    _seed_job(factory, "job_live", "doc_live", "col_l", status="processing")
    _seed_job(factory, "job_stale", "doc_stale", "col_s", status="processing")
    calls: list = []
    fake = FakeRedis()
    fake.set(tasks._heartbeat_key("job_live"), "1")  # live → actively progressing
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_redis", lambda: fake)
    monkeypatch.setattr(tasks.ingest_document, "delay", lambda *a: calls.append(a[0]))

    assert requeue_stuck_processing() == 1  # only the heartbeat-less one
    assert calls == ["job_stale"]


def test_requeue_rotates_batch_via_updated_at_bump(tmp_path, monkeypatch):
    """A re-enqueue bumps updated_at so the oldest-first sweep rotates to a DIFFERENT batch next
    tick — instead of re-piling the same not-yet-claimed rows onto the broker while the worker is
    busy (the amplification that would partly reintroduce the storm)."""
    factory = _db(tmp_path)
    _seed_job(factory, "job_old", "doc_o", "col_o", updated="2020-01-01")
    _seed_job(factory, "job_new", "doc_n", "col_n", updated="2020-06-01")
    calls: list = []
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_now", lambda: "2020-12-31")  # bump target → newest
    monkeypatch.setattr(tasks.ingest_document, "delay", lambda *a: calls.append(a[0]))

    assert requeue_rate_limited(limit=1) == 1  # oldest first
    assert calls == ["job_old"]  # and its updated_at is now 2020-12-31 (newest)
    assert requeue_rate_limited(limit=1) == 1  # next sweep picks the other one, not job_old again
    assert calls == ["job_old", "job_new"]


def test_requeue_orphaned_pending_reenqueues(tmp_path, monkeypatch):
    """A pending job whose task was lost (broker purge / worker restart) has no other resume path,
    so the beat re-enqueues it — a queued file is never silently dropped."""
    factory = _db(tmp_path)
    _seed_job(factory, "job_p", "doc_p", "col_p", status="pending")
    calls: list = []
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.ingest_document, "delay", lambda *a: calls.append(a[0]))

    assert requeue_orphaned_pending() == 1
    assert calls == ["job_p"]


def test_ingest_document_rate_limit_pauses_and_does_not_strand(tmp_path, monkeypatch):
    """The rate-limit path must pause + mark 'rate_limited' + return cleanly — NOT self.retry, whose
    max_retries cap raises MaxRetriesExceeded and would strand the doc. Regression guard for that."""
    factory = _db(tmp_path)
    _seed_job(factory, "job_x", "doc_x", "col_x", status="processing")
    fake = FakeRedis()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_redis", lambda: fake)

    def _raise_rate_limit(*_a):
        raise RateLimitError('{"retryDelay": "40s"}')

    monkeypatch.setattr(tasks, "_run_ingestion", _raise_rate_limit)

    # Runs the real task body eagerly; a strand would surface as MaxRetriesExceeded out of .get().
    result = tasks.ingest_document.apply(args=["job_x", "k", "col_x", "doc_x", ".pdf"]).get()
    assert result is None  # acked cleanly, no exception escaped
    assert _embedding_paused_seconds() == 42  # global backoff armed (retryDelay 40 + 2s past-edge)
    with factory() as s:
        row = s.execute(
            select(job_records.c.status).where(job_records.c.job_id == "job_x")
        ).fetchone()
    assert row.status == "rate_limited"  # left resume-able for the beat sweep


def test_ingest_document_non_retryable_fails_without_retry(tmp_path, monkeypatch):
    """A NonRetryableIngestError (e.g. the stored object is over the size cap) must fail terminally —
    mark 'failed' and raise plainly, NOT self.retry, which would re-fail the same object through the
    whole 3-retry ladder. Regression guard so the size breach never lands on the retry path."""
    factory = _db(tmp_path)
    _seed_job(factory, "job_big", "doc_big", "col_big", status="processing")
    monkeypatch.setattr(tasks, "SessionLocal", factory)

    def _raise_oversize(*_a):
        raise tasks.NonRetryableIngestError(
            "Stored object is 2097152 bytes, over the 1048576-byte size limit"
        )

    monkeypatch.setattr(tasks, "_run_ingestion", _raise_oversize)

    # Eager run: a self.retry would surface as a Retry out of .get(); the terminal path re-raises
    # the original NonRetryableIngestError instead.
    with pytest.raises(tasks.NonRetryableIngestError):
        tasks.ingest_document.apply(args=["job_big", "k", "col_big", "doc_big", ".pdf"]).get()
    with factory() as s:
        row = s.execute(
            select(job_records.c.status).where(job_records.c.job_id == "job_big")
        ).fetchone()
    assert row.status == "failed"  # terminal; the beat sweep never re-enqueues a failed job


# --------------------------------------------------------------------------- #
# raise_for_status — uniform typed 429 across every httpx embedding adapter    #
# --------------------------------------------------------------------------- #


def _http_response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "http://x"), text="body")


def test_raise_for_status_maps_429_to_rate_limit_error():
    with pytest.raises(RateLimitError):
        raise_for_status(_http_response(429))


def test_raise_for_status_non_429_error_is_httpx_error():
    with pytest.raises(httpx.HTTPStatusError):
        raise_for_status(_http_response(500))


def test_raise_for_status_ok_does_not_raise():
    raise_for_status(_http_response(200))  # 2xx returns without raising


def test_raise_for_status_keeps_retry_delay_in_a_long_body():
    """A 429 body long enough to push retryDelay past the old 500-char cap must still retain it,
    so _retry_delay_seconds reads the provider's hint instead of falling back to the 60s default."""
    body = "quota exhausted; " * 40 + '{"retryDelay": "42s"}'  # retryDelay lands well past char 500
    assert len(body) > 500
    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"), text=body)
    with pytest.raises(RateLimitError) as ei:
        raise_for_status(resp)
    assert "42s" in str(ei.value)  # not truncated away
    assert _retry_delay_seconds(ei.value) == 44  # 42 + 2s past-edge, not the 60s default


def test_gemini_adapter_raises_rate_limit_on_429(monkeypatch):
    """The Gemini adapter surfaces a 429 as the typed RateLimitError (via raise_for_status)."""
    from api.adapters.embeddings.gemini import GeminiAdapter

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _http_response(429))
    with pytest.raises(RateLimitError):
        GeminiAdapter("model", "key").embed_batch(["x"])


# --------------------------------------------------------------------------- #
# Global embedding-quota circuit breaker (pause) — the storm guard             #
# --------------------------------------------------------------------------- #


def test_embedding_pause_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(tasks, "_redis", lambda: fake)
    assert _embedding_paused_seconds() == 0  # not paused → 0 (fail-open default too)
    _pause_embedding(120)
    assert _embedding_paused_seconds() == 120  # reads back the pause key's TTL
    assert fake.ttls[tasks._PAUSE_KEY] == 120
    clear_embedding_pause()
    assert _embedding_paused_seconds() == 0  # lifted


def test_embedding_paused_seconds_fails_open_on_redis_error(monkeypatch):
    class _Boom:
        def ttl(self, _key):
            raise RuntimeError("redis down")

    monkeypatch.setattr(tasks, "_redis", lambda: _Boom())
    # A redis blip must not wedge ingestion shut — read as not-paused so work still flows.
    assert _embedding_paused_seconds() == 0


def test_beat_sweep_skips_while_paused(monkeypatch):
    """The 5-min sweep must not re-enqueue jobs into a paused worker (that would just make them
    re-defer, churning the queue). While paused it returns 0 without touching requeue."""
    called: list = []
    monkeypatch.setattr(tasks, "_embedding_paused_seconds", lambda: 42)
    monkeypatch.setattr(tasks, "requeue_rate_limited", lambda *a, **k: called.append(1) or 99)
    assert tasks.retry_rate_limited_ingests.apply().get() == 0
    assert called == []  # requeue never invoked while paused


def test_config_resume_lifts_pause_and_requeues(monkeypatch):
    """A config change (new key / raised limit) resets the quota: it must LIFT the pause and
    re-enqueue the rate-limited jobs so they resume on the fresh quota."""
    cleared: list = []
    requeued: list = []
    monkeypatch.setattr(tasks, "clear_embedding_pause", lambda: cleared.append(1))
    monkeypatch.setattr(tasks, "requeue_rate_limited", lambda *a, **k: requeued.append((a, k)) or 0)
    config_reload._resume_rate_limited()
    assert cleared == [1]  # pause lifted first
    assert requeued == [((), {})]  # then a plain re-enqueue of the rate-limited backlog
