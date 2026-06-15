"""Unit tests for the config read/apply service (config page Phase 2)."""

from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException

from api import dependencies
from api.models.config import AppConfig, EmbeddingConfig, VectorStoreConfig
from api.services import config_reload as cr
from api.services import config_service as cs
from tests.unit.fake_redis import FakeRedis


class _FakeEmbed:
    dimensions = 384


class _FakeStore:
    pass


@pytest.fixture(autouse=True)
def _reset_state():
    """Isolate the module-level live config / adapters / reload records per test."""
    dependencies._app_config = None
    dependencies._embedding_adapter = None
    dependencies._vector_store = None
    dependencies._redis_client = None
    cs._reload_status.clear()
    yield
    dependencies._app_config = None
    dependencies._embedding_adapter = None
    dependencies._vector_store = None
    dependencies._redis_client = None
    cs._reload_status.clear()


def _patch_adapters(monkeypatch, tmp_path):
    """Monkeypatch adapter builders + the config path so apply touches no infra."""
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _FakeEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, _d: _FakeStore())
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")


# ── GET (masking) ─────────────────────────────────────────────────────────────


def test_get_masked_config_requires_loaded_config():
    with pytest.raises(HTTPException) as exc:
        cs.get_masked_config()
    assert exc.value.status_code == 503


def test_get_masked_config_masks_set_secrets_and_blanks_empty():
    dependencies.set_app_config(
        AppConfig(
            embedding=EmbeddingConfig(api_key="sk-secret"),
            vector_store=VectorStoreConfig(),  # chroma.auth_token set, pgvector.password ""
        )
    )
    data = cs.get_masked_config()
    assert data["embedding"]["api_key"] == cs.SECRET_MASK  # set -> masked
    assert data["vector_store"]["chroma"]["auth_token"] == cs.SECRET_MASK
    assert data["vector_store"]["pgvector"]["password"] == ""  # unset -> blank
    assert data["embedding"]["provider"] == "sentence_transformers"  # non-secret intact


# ── Secret merge (write-only preservation) ────────────────────────────────────


def test_merge_secrets_preserves_masked_value():
    current = AppConfig(embedding=EmbeddingConfig(api_key="real-key"))
    incoming = current.model_dump()
    incoming["embedding"]["api_key"] = cs.SECRET_MASK
    merged = cs._merge_secrets(incoming, current)
    assert merged["embedding"]["api_key"] == "real-key"


def test_merge_secrets_accepts_new_value():
    current = AppConfig(embedding=EmbeddingConfig(api_key="real-key"))
    incoming = current.model_dump()
    incoming["embedding"]["api_key"] = "rotated-key"
    merged = cs._merge_secrets(incoming, current)
    assert merged["embedding"]["api_key"] == "rotated-key"


# ── Atomic write ──────────────────────────────────────────────────────────────


def test_atomic_write_persists_yaml_and_keeps_backup(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("old: 1", encoding="utf-8")
    cs._atomic_write({"max_file_size_mb": 99}, path)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["max_file_size_mb"] == 99
    assert (tmp_path / "config.yaml.bak").read_text(encoding="utf-8") == "old: 1"
    assert not (tmp_path / "config.yaml.tmp").exists()  # renamed away


# ── apply_config (build-then-commit) ──────────────────────────────────────────


def test_apply_config_persists_swaps_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _FakeEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, _d: _FakeStore())
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")
    dependencies.set_app_config(AppConfig())

    payload = AppConfig(embedding=EmbeddingConfig(provider="ollama", model="nomic"))
    result = cs.apply_config(payload)

    assert result["status"] == "applied"
    assert dependencies.get_app_config().embedding.provider == "ollama"  # live swap
    assert isinstance(dependencies.get_embedding_adapter(), _FakeEmbed)
    assert isinstance(dependencies.get_vector_store(), _FakeStore)
    assert (tmp_path / "config.yaml").exists()
    assert cs.get_reload_status(result["version_id"])["api"] == "ok"


def test_apply_config_invalid_adapter_raises_422_without_writing(tmp_path, monkeypatch):
    def _boom(_c):
        raise ValueError("model 'ghost' not found")

    monkeypatch.setattr(cs, "resolve_embedding", _boom)
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")
    dependencies.set_app_config(AppConfig())

    with pytest.raises(HTTPException) as exc:
        cs.apply_config(AppConfig(embedding=EmbeddingConfig(model="ghost")))
    assert exc.value.status_code == 422
    assert not (tmp_path / "config.yaml").exists()  # build failed before persist


def test_apply_config_preserves_masked_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _FakeEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, _d: _FakeStore())
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")
    dependencies.set_app_config(AppConfig(embedding=EmbeddingConfig(api_key="keep-me")))

    payload = AppConfig(embedding=EmbeddingConfig(api_key=cs.SECRET_MASK))
    cs.apply_config(payload)
    assert dependencies.get_app_config().embedding.api_key == "keep-me"


def test_get_reload_status_unknown_version_404():
    with pytest.raises(HTTPException) as exc:
        cs.get_reload_status("does-not-exist")
    assert exc.value.status_code == 404


# ── apply_config (Phase 3 worker propagation) ─────────────────────────────────


def test_apply_config_publishes_and_applies_when_workers_ack(tmp_path, monkeypatch):
    _patch_adapters(monkeypatch, tmp_path)
    redis = FakeRedis(subscribers=1, worker_acks={"worker:w1": "ok"})
    dependencies.set_redis_client(redis)
    dependencies.set_app_config(AppConfig())

    result = cs.apply_config(AppConfig(embedding=EmbeddingConfig(provider="ollama")))

    assert result["status"] == "applied"
    assert result["acked_workers"] == 1
    assert redis.published[0][0] == cr.RELOAD_CHANNEL  # workers were notified


def test_apply_config_returns_pending_when_acks_outstanding(tmp_path, monkeypatch):
    _patch_adapters(monkeypatch, tmp_path)
    monkeypatch.setattr(cs, "_ACK_WAIT_SECONDS", 0.0)  # don't actually wait
    dependencies.set_redis_client(FakeRedis(subscribers=2))  # no acks injected
    dependencies.set_app_config(AppConfig())

    result = cs.apply_config(AppConfig(embedding=EmbeddingConfig(provider="ollama")))

    assert result["status"] == "pending"
    assert result["expected_workers"] == 2


def test_apply_config_rolls_back_on_worker_error(tmp_path, monkeypatch):
    _patch_adapters(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text("embedding:\n  provider: original\n", encoding="utf-8")
    redis = FakeRedis(subscribers=1, worker_acks={"worker:w1": "error: bad model"})
    dependencies.set_redis_client(redis)
    dependencies.set_app_config(AppConfig(embedding=EmbeddingConfig(provider="sentence_transformers")))

    with pytest.raises(HTTPException) as exc:
        cs.apply_config(AppConfig(embedding=EmbeddingConfig(provider="ollama")))

    assert exc.value.status_code == 409
    # config.yaml restored from the .bak, and the API reverted to the prior provider.
    assert "original" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert dependencies.get_app_config().embedding.provider == "sentence_transformers"
    assert any(b'"rollback": true' in p[1].encode() for p in redis.published)


def test_get_reload_status_reads_from_redis(monkeypatch):
    redis = FakeRedis()
    cr.init_status(redis, "v1", 1)
    cr.record_worker_ack(redis, "v1", "ok")
    dependencies.set_redis_client(redis)
    assert cs.get_reload_status("v1")["status"] == "applied"
