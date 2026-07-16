"""Unit tests for the worker-side config hot-reload listener (Phase 3)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from api.adapters.embeddings.errors import RateLimitError
from api.models.config import EmbeddingConfig
from api.services import config_reload as cr
from tests.unit.fake_redis import FakeRedis
from worker import config_reload as wcr
from worker import tasks


def _message(version_id: str, *, rollback: bool = False) -> str:
    return json.dumps({"version_id": version_id, "rollback": rollback})


def test_handle_message_records_ok_ack(monkeypatch):
    monkeypatch.setattr(wcr, "_reload_adapters", lambda: None)
    redis = FakeRedis()
    wcr._handle_message(redis, _message("v1"))
    bucket = redis.hashes[cr.status_key("v1")]
    assert bucket[f"worker:{cr.worker_id()}"] == "ok"


def test_handle_message_records_error_ack_on_failure(monkeypatch):
    def _boom() -> None:
        raise ValueError("bad model")

    monkeypatch.setattr(wcr, "_reload_adapters", _boom)
    redis = FakeRedis()
    wcr._handle_message(redis, _message("v1"))
    ack = redis.hashes[cr.status_key("v1")][f"worker:{cr.worker_id()}"]
    assert ack.startswith("error: bad model")


def test_handle_message_rollback_reloads_without_acking(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(wcr, "_reload_adapters", lambda: calls.append("reload"))
    redis = FakeRedis()
    wcr._handle_message(redis, _message("v1", rollback=True))
    assert calls == ["reload"]
    assert cr.status_key("v1") not in redis.hashes  # no ack written


def test_listen_dispatches_only_message_events(monkeypatch):
    handled: list[str] = []
    monkeypatch.setattr(wcr, "_handle_message", lambda _r, data: handled.append(data))
    redis = FakeRedis(messages=[_message("v1")])
    wcr._listen(redis)
    assert handled == [_message("v1")]  # the subscribe confirmation is skipped


def test_safe_reload_swallows_errors(monkeypatch):
    def _boom() -> None:
        raise RuntimeError("nope")

    monkeypatch.setattr(wcr, "_reload_adapters", _boom)
    wcr._safe_reload()  # must not raise


class _FakeEmbed:
    dimensions = 384


class _RateLimitedEmbed:
    """Embedding adapter whose dimension probe hits the provider rate limit (HTTP 429)."""

    @property
    def dimensions(self) -> int:
        raise RateLimitError("embedding provider HTTP 429: quota exceeded")


class _FakeStore:
    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions


class _FakeConfig:
    embedding = object()
    vector_store = object()


def test_redis_client_builds_a_client(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    client = wcr._redis_client()  # lazy connection — does not dial the server
    assert client is not None


def test_reload_adapters_clears_cache_and_rebuilds(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tasks, "reload_adapters", lambda _prior: calls.append("reload"))
    wcr._reload_adapters()
    assert calls == ["reload"]


def test_start_listener_runs_and_exits_on_empty_stream(monkeypatch):
    monkeypatch.setattr(wcr, "_redis_client", lambda: FakeRedis(messages=[]))
    thread = wcr.start_listener()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_worker_process_init_starts_listener(monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(wcr, "start_listener", lambda: started.append("started"))
    wcr._on_worker_process_init()
    assert started == ["started"]


def test_reload_adapters_rebuilds_singletons(monkeypatch):
    monkeypatch.setattr(tasks, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr("api.adapters.embeddings.get_embedding_adapter", lambda _c: _FakeEmbed())
    monkeypatch.setattr("api.adapters.vector_store.get_vector_store", lambda _c, _d: _FakeStore())
    tasks._embedder_singleton = None
    tasks._vector_store_singleton = None

    tasks.reload_adapters()

    assert isinstance(tasks._embedder_singleton, _FakeEmbed)
    assert isinstance(tasks._vector_store_singleton, _FakeStore)


def test_reload_adapters_reuses_store_dim_when_rate_limited_and_shape_unchanged(monkeypatch):
    # Mirrors the API apply: a 429 dimension probe during an unchanged-model reload (e.g. a raised
    # max_rpm) must reuse the still-live store's size, not raise — a raise would error-ack and force
    # the API to roll the whole config change back.
    before = EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k", max_rpm=50)
    after = EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k", max_rpm=90)
    monkeypatch.setattr(tasks, "get_config", lambda: SimpleNamespace(embedding=after, vector_store=object()))
    monkeypatch.setattr("api.adapters.embeddings.get_embedding_adapter", lambda _c: _RateLimitedEmbed())
    monkeypatch.setattr("api.adapters.vector_store.get_vector_store", lambda _c, d: _FakeStore(d))
    tasks._embedder_singleton = None
    tasks._vector_store_singleton = _FakeStore(dimensions=768)  # still-live store carries the size

    tasks.reload_adapters(before)  # must not raise on the 429

    assert tasks._vector_store_singleton.dimensions == 768  # rebuilt store reused the known size


def test_reload_adapters_defers_store_on_rate_limit_when_store_not_built(monkeypatch):
    # The reported 409: a worker whose store singleton isn't built yet (continuously throttled since
    # its last restart) gets an unchanged-model reload (a raised max_rpm). It has no prior dimension
    # to reuse, but a 429 must NOT roll the save back — so it adopts the new embedder and defers the
    # store to a lazy rebuild rather than raising (which would error-ack and force a rollback).
    before = EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k", max_rpm=50)
    after = EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k", max_rpm=90)
    monkeypatch.setattr(tasks, "get_config", lambda: SimpleNamespace(embedding=after, vector_store=object()))
    monkeypatch.setattr("api.adapters.embeddings.get_embedding_adapter", lambda _c: _RateLimitedEmbed())
    monkeypatch.setattr("api.adapters.vector_store.get_vector_store", lambda _c, d: _FakeStore(d))
    tasks._embedder_singleton = None
    tasks._vector_store_singleton = None  # never built (throttled since restart)

    tasks.reload_adapters(before)  # must NOT raise on the 429

    assert isinstance(tasks._embedder_singleton, _RateLimitedEmbed)  # new config's embedder adopted
    assert tasks._vector_store_singleton is None  # store deferred for lazy rebuild


def test_reload_adapters_defers_store_on_rate_limit_when_model_changed(monkeypatch):
    # A model change while throttled can't reuse the prior size (it was for the old model), but a 429
    # still must not roll the save back — defer the store to a lazy rebuild, which re-probes the new
    # model's size once the provider is reachable.
    before = EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k")
    after = EmbeddingConfig(provider="gemini", model="gemini-embedding-99", api_key="k")
    monkeypatch.setattr(tasks, "get_config", lambda: SimpleNamespace(embedding=after, vector_store=object()))
    monkeypatch.setattr("api.adapters.embeddings.get_embedding_adapter", lambda _c: _RateLimitedEmbed())
    monkeypatch.setattr("api.adapters.vector_store.get_vector_store", lambda _c, d: _FakeStore(d))
    tasks._embedder_singleton = None
    tasks._vector_store_singleton = _FakeStore(dimensions=768)

    tasks.reload_adapters(before)  # must NOT raise

    assert tasks._vector_store_singleton is None  # deferred; the stale 768 is not kept for a new model


def test_reload_adapters_propagates_non_rate_limit_error(monkeypatch):
    # Only a *rate limit* defers. A genuine build failure (a bad model / key — not a 429) must still
    # propagate, so the listener error-acks and the API rolls back rather than silently accepting it.
    class _BrokenEmbed:
        @property
        def dimensions(self) -> int:
            raise ValueError("model 'ghost' not found")

    after = EmbeddingConfig(provider="gemini", model="ghost", api_key="k")
    monkeypatch.setattr(tasks, "get_config", lambda: SimpleNamespace(embedding=after, vector_store=object()))
    monkeypatch.setattr("api.adapters.embeddings.get_embedding_adapter", lambda _c: _BrokenEmbed())
    monkeypatch.setattr("api.adapters.vector_store.get_vector_store", lambda _c, d: _FakeStore(d))
    tasks._embedder_singleton = None
    tasks._vector_store_singleton = _FakeStore(dimensions=768)

    with pytest.raises(ValueError):
        tasks.reload_adapters(EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k"))


@pytest.fixture(autouse=True)
def _reset_task_singletons():
    yield
    tasks._embedder_singleton = None
    tasks._vector_store_singleton = None
