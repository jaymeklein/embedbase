"""Integration tests for the config page endpoints (Phase 2).

Cover master-key enforcement and the GET/PUT/reload-status wiring end-to-end.
Adapter construction is monkeypatched so PUT does not load a real embedding model.
"""

import pytest

from api import dependencies
from api.models.config import AppConfig, EmbeddingConfig
from api.services import config_service as cs
from tests.unit.fake_redis import FakeRedis


class _FakeEmbed:
    dimensions = 384


class _FakeStore:
    pass


@pytest.fixture(autouse=True)
def _seed_and_reset_config():
    """Seed a live config (the noop test lifespan skips it) and reset after."""
    dependencies.set_app_config(AppConfig(embedding=EmbeddingConfig(api_key="live-secret")))
    yield
    dependencies._app_config = None
    dependencies._embedding_adapter = None
    dependencies._vector_store = None
    dependencies._redis_client = None
    cs._reload_status.clear()


async def test_config_get_requires_master_key(client):
    assert (await client.get("/config")).status_code == 401


async def test_config_get_returns_masked_secrets(master_client):
    r = await master_client.get("/config")
    assert r.status_code == 200
    body = r.json()
    assert body["embedding"]["api_key"] == cs.SECRET_MASK  # never the real value
    assert body["embedding"]["provider"] == "sentence_transformers"


async def test_config_put_applies_and_returns_version(master_client, monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _FakeEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, _d: _FakeStore())
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")

    payload = AppConfig(embedding=EmbeddingConfig(provider="ollama", model="nomic")).model_dump()
    r = await master_client.put("/config", json=payload)
    assert r.status_code == 200
    version_id = r.json()["version_id"]

    status = await master_client.get(f"/config/reload-status/{version_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "applied"


async def test_reload_status_unknown_returns_404(master_client):
    assert (await master_client.get("/config/reload-status/nope")).status_code == 404


async def test_config_put_propagates_to_workers(master_client, monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _FakeEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, _d: _FakeStore())
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")
    redis = FakeRedis(subscribers=1, worker_acks={"worker:w1": "ok"})
    dependencies.set_redis_client(redis)

    payload = AppConfig(embedding=EmbeddingConfig(provider="ollama", model="nomic")).model_dump()
    r = await master_client.put("/config", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied"
    assert body["acked_workers"] == 1

    status = await master_client.get(f"/config/reload-status/{body['version_id']}")
    assert status.json()["status"] == "applied"


async def test_config_put_rolls_back_on_worker_error_returns_409(master_client, monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _FakeEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, _d: _FakeStore())
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("embedding:\n  provider: original\n", encoding="utf-8")
    dependencies.set_redis_client(FakeRedis(subscribers=1, worker_acks={"worker:w1": "error: boom"}))

    payload = AppConfig(embedding=EmbeddingConfig(provider="ollama")).model_dump()
    r = await master_client.put("/config", json=payload)
    assert r.status_code == 409
    assert "original" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
