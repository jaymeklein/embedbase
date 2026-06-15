"""Unit tests for the worker-side config hot-reload listener (Phase 3)."""

from __future__ import annotations

import json

import pytest

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


class _FakeStore:
    pass


class _FakeConfig:
    embedding = object()
    vector_store = object()


def test_redis_client_builds_a_client(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    client = wcr._redis_client()  # lazy connection — does not dial the server
    assert client is not None


def test_reload_adapters_clears_cache_and_rebuilds(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tasks, "reload_adapters", lambda: calls.append("reload"))
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


@pytest.fixture(autouse=True)
def _reset_task_singletons():
    yield
    tasks._embedder_singleton = None
    tasks._vector_store_singleton = None
