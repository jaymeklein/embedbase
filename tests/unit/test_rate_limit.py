"""Unit tests for the rate-limit resume feature (worker/tasks.py).

Covers the pieces the pause/resume path relies on and that previously shipped
untested: the RPM throttle window, 429 classification (typed error + the narrowed
string fallback), the provider retry-delay parse, the pending-retry marker, and the
beat-sweep requeue with its pending guard.
"""

import time

import httpx
import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.adapters.embeddings.errors import RateLimitError, raise_for_status
from api.tables import job_records, metadata
from tests.unit.fakes import FakeRedis
from worker import tasks
from worker.tasks import (
    _is_rate_limit,
    _mark_retry_pending,
    _pending_job_ids,
    _retry_delay_seconds,
    _retry_pending_key,
    _RpmLimiter,
    requeue_rate_limited,
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


def test_retry_pending_marker_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(tasks, "_redis", lambda: fake)
    assert _pending_job_ids(fake, ["job1"]) == set()
    _mark_retry_pending("job1", 120)
    assert _pending_job_ids(fake, ["job1"]) == {"job1"}  # marker read back via the batch MGET
    assert fake.ttls[_retry_pending_key("job1")] == 150  # delay + 30s buffer


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rl.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed_rate_limited(factory, job_id="job_rl", doc="doc_rl", col="col_rl"):
    with factory() as s:
        s.execute(
            insert(job_records).values(
                job_id=job_id, document_id=doc, collection_id=col,
                filename="a.pdf", file_type=".pdf", status="rate_limited",
                created_at="t", updated_at="t",
            )
        )
        s.commit()


def test_requeue_reenqueues_rate_limited_job(tmp_path, monkeypatch):
    factory = _db(tmp_path)
    _seed_rate_limited(factory)
    calls: list = []
    fake = FakeRedis()  # no pending marker → the job is treated as orphaned/resumable
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_redis", lambda: fake)
    monkeypatch.setattr(tasks.ingest_document, "delay", lambda *a: calls.append(a))

    assert requeue_rate_limited() == 1
    # (job_id, storage_key via document_key, collection_id, document_id, file_type)
    assert calls == [("job_rl", "col_rl/doc_rl.pdf", "col_rl", "doc_rl", ".pdf")]


def test_requeue_respects_pending_marker(tmp_path, monkeypatch):
    factory = _db(tmp_path)
    _seed_rate_limited(factory)
    calls: list = []
    fake = FakeRedis()
    fake.set(_retry_pending_key("job_rl"), "1")  # its countdown retry is still scheduled
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_redis", lambda: fake)
    monkeypatch.setattr(tasks.ingest_document, "delay", lambda *a: calls.append(a))

    assert requeue_rate_limited() == 0  # default respect_pending → skipped (not orphaned)
    assert requeue_rate_limited(respect_pending=False) == 1  # config-change resume ignores it
    assert len(calls) == 1


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
