"""Unit tests for the config read/apply service (config page Phase 2)."""

from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException

from api import dependencies
from api.adapters.embeddings.errors import RateLimitError
from api.models.config import AppConfig, EmbeddingConfig, RerankerConfig, VectorStoreConfig
from api.services import config_reload as cr
from api.services import config_service as cs
from tests.unit.fake_redis import FakeRedis


class _FakeEmbed:
    dimensions = 384


class _RateLimitedEmbed:
    """Embedding adapter whose dimension probe hits the provider's rate limit (HTTP 429)."""

    @property
    def dimensions(self) -> int:
        raise RateLimitError("embedding provider HTTP 429: quota exceeded")


class _FakeStore:
    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions


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
            vector_store=VectorStoreConfig(password="pg-secret"),
        )
    )
    data = cs.get_masked_config()
    assert data["embedding"]["api_key"] == cs.SECRET_MASK  # set -> masked
    assert data["vector_store"]["password"] == cs.SECRET_MASK
    assert data["embedding"]["provider"] == "ollama"  # non-secret intact


def test_get_masked_config_blanks_unset_vector_store_password():
    dependencies.set_app_config(AppConfig())
    data = cs.get_masked_config()
    assert data["vector_store"]["password"] == ""  # unset -> blank


def test_get_masked_config_masks_reranker_api_key():
    dependencies.set_app_config(
        AppConfig(
            reranker=RerankerConfig(
                provider="rerank_api", api_key="co-secret", base_url="https://x/rerank"
            )
        )
    )
    data = cs.get_masked_config()
    assert data["reranker"]["api_key"] == cs.SECRET_MASK  # set -> masked
    assert data["reranker"]["provider"] == "rerank_api"  # non-secret intact


def test_merge_secrets_preserves_masked_reranker_api_key():
    current = AppConfig(
        reranker=RerankerConfig(
            provider="rerank_api", api_key="real-co-key", base_url="https://x/rerank"
        )
    )
    incoming = current.model_dump()
    incoming["reranker"]["api_key"] = cs.SECRET_MASK
    merged = cs._merge_secrets(incoming, current)
    assert merged["reranker"]["api_key"] == "real-co-key"


def test_get_masked_config_masks_s3_backend_secrets():
    """S3 creds arrive via env overlay into the live config; GET must not leak them."""
    from api.models.config import LocalBackendConfig, S3BackendConfig, StorageConfig

    dependencies.set_app_config(
        AppConfig(
            storage=StorageConfig(
                default="minio",
                backends={
                    "local": LocalBackendConfig(),
                    "minio": S3BackendConfig(access_key_id="AKIA", secret_access_key="shh"),
                },
            )
        )
    )
    data = cs.get_masked_config()
    minio = data["storage"]["backends"]["minio"]
    assert minio["access_key_id"] == cs.SECRET_MASK
    assert minio["secret_access_key"] == cs.SECRET_MASK
    assert minio["bucket"] == "embedbase"  # non-secret field intact
    assert "access_key_id" not in data["storage"]["backends"]["local"]  # local has no creds


def test_merge_secrets_preserves_masked_s3_backend_secrets():
    from api.models.config import S3BackendConfig, StorageConfig

    current = AppConfig(
        storage=StorageConfig(
            default="minio",
            backends={"minio": S3BackendConfig(access_key_id="AKIA-real", secret_access_key="shh-real")},
        )
    )
    incoming = current.model_dump()
    incoming["storage"]["backends"]["minio"]["access_key_id"] = cs.SECRET_MASK
    incoming["storage"]["backends"]["minio"]["secret_access_key"] = cs.SECRET_MASK
    merged = cs._merge_secrets(incoming, current)
    assert merged["storage"]["backends"]["minio"]["access_key_id"] == "AKIA-real"
    assert merged["storage"]["backends"]["minio"]["secret_access_key"] == "shh-real"


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


# ── Config write + boot recovery ──────────────────────────────────────────────


def test_write_config_persists_yaml_and_keeps_backup(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("old: 1", encoding="utf-8")
    cs._write_config({"max_file_size_mb": 99}, path)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["max_file_size_mb"] == 99
    assert (tmp_path / "config.yaml.bak").read_text(encoding="utf-8") == "old: 1"
    # In-place write (bind-mount safe) never leaves a stray .tmp behind.
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_load_recovers_from_backup_when_config_is_corrupt(tmp_path):
    """A crash mid-write can truncate config.yaml into invalid YAML; the loader recovers .bak."""
    from api.services import config_env

    path = tmp_path / "config.yaml"
    (tmp_path / "config.yaml.bak").write_text("max_file_size_mb: 42\n", encoding="utf-8")
    path.write_text("max_file_size_mb: {unterminated", encoding="utf-8")  # invalid YAML
    assert config_env.load_config_data(path)["max_file_size_mb"] == 42


def test_empty_config_recovers_from_backup(tmp_path):
    """An in-place write truncates config.yaml before rewriting, so a crash can leave it 0-byte
    — indistinguishable from a torn write. An empty primary therefore recovers a non-empty .bak
    rather than booting on lost config (an empty file parses as None, NOT a YAMLError, so it
    must be handled explicitly)."""
    from api.services import config_env

    path = tmp_path / "config.yaml"
    (tmp_path / "config.yaml.bak").write_text("max_file_size_mb: 7\n", encoding="utf-8")
    path.write_text("", encoding="utf-8")
    assert config_env.load_config_data(path)["max_file_size_mb"] == 7


def test_empty_config_without_backup_returns_empty(tmp_path):
    """An empty config with no .bak (a fresh install / intentional empty) degrades to defaults
    ({}) rather than raising."""
    from api.services import config_env

    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    assert config_env.load_config_data(path) == {}


def test_corrupt_config_without_backup_returns_empty(tmp_path):
    """A corrupt config with no .bak degrades to defaults ({}) rather than raising at boot."""
    from api.services import config_env

    path = tmp_path / "config.yaml"
    path.write_text("{unterminated", encoding="utf-8")
    assert config_env.load_config_data(path) == {}


def test_non_ascii_config_roundtrips_utf8(tmp_path):
    """The writer encodes UTF-8; the reader must decode UTF-8 too, or a non-ASCII value (e.g. an
    accented storage path) raises UnicodeDecodeError on a non-UTF-8 default locale and crashes
    boot instead of loading."""
    from api.services import config_env

    path = tmp_path / "config.yaml"
    cs._write_config({"storage": {"default": "arquivo-ção"}}, path)
    assert config_env.load_config_data(path)["storage"]["default"] == "arquivo-ção"


def test_write_config_skips_backup_when_current_is_empty(tmp_path):
    """A crash-truncated (empty) config.yaml must NOT overwrite the last-good .bak: an empty file
    parses as None without error, so guarding the backup on parse alone would let it clobber the
    only recovery source."""
    path = tmp_path / "config.yaml"
    (tmp_path / "config.yaml.bak").write_text("max_file_size_mb: 5\n", encoding="utf-8")
    path.write_text("", encoding="utf-8")  # torn write left it empty
    cs._write_config({"max_file_size_mb": 99}, path)
    # the good backup is preserved, not replaced with the empty content
    assert yaml.safe_load((tmp_path / "config.yaml.bak").read_text(encoding="utf-8"))[
        "max_file_size_mb"
    ] == 5


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


def test_apply_config_survives_rate_limited_probe_when_model_unchanged(tmp_path, monkeypatch):
    # A throttled provider (HTTP 429) during the dimension probe must not fail a save that doesn't
    # change the model: the dimension can't have changed, so the probe's 429 is caught and the live
    # store's known size (768) is reused. This is the reported bug — editing max-requests/min was
    # rejected because the probe hit Gemini's daily quota.
    #
    # The live embedding *adapter* is left unset (None) on purpose: it mirrors the real breakage,
    # where a boot whose warm-up probe was itself rate-limited swallows the error and never registers
    # an adapter. The reused size therefore comes from the live *store*, which always carries one.
    live = AppConfig(embedding=EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k"))
    dependencies.set_app_config(live)
    dependencies.set_vector_store(_FakeStore(dimensions=768))  # live store carries the known size
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _RateLimitedEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, dims: _FakeStore(dims))
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")

    payload = AppConfig(
        embedding=EmbeddingConfig(
            provider="gemini", model="gemini-embedding-2", api_key=cs.SECRET_MASK, max_rpm=90
        )
    )
    result = cs.apply_config(payload)  # must not raise on the 429

    assert result["status"] == "applied"
    assert dependencies.get_app_config().embedding.max_rpm == 90  # the throttle edit landed
    assert dependencies.get_vector_store().dimensions == 768  # rebuilt store reused the known size


def test_apply_config_returns_503_when_model_change_probe_is_rate_limited(tmp_path, monkeypatch):
    # Changing the model *while* the provider is throttled: the live store's size is for the OLD
    # model, so the new model's dimension genuinely can't be reused — it must be probed, and that
    # probe is rate-limited. Surface a transient 503 ("try again"), not a misleading 422.
    live = AppConfig(embedding=EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="k"))
    dependencies.set_app_config(live)
    dependencies.set_vector_store(_FakeStore(dimensions=768))
    monkeypatch.setattr(cs, "resolve_embedding", lambda _c: _RateLimitedEmbed())
    monkeypatch.setattr(cs, "resolve_store", lambda _c, dims: _FakeStore(dims))
    monkeypatch.setattr(cs, "_config_path", lambda: tmp_path / "config.yaml")

    payload = AppConfig(
        embedding=EmbeddingConfig(provider="gemini", model="gemini-embedding-99", api_key=cs.SECRET_MASK)
    )
    with pytest.raises(HTTPException) as exc:
        cs.apply_config(payload)
    assert exc.value.status_code == 503
    assert "rate-limited" in exc.value.detail


def test_list_ollama_models_uses_explicit_base_url(monkeypatch):
    monkeypatch.setattr(
        "api.adapters.llm_chat.list_ollama_models", lambda url: [f"model@{url}"]
    )
    assert cs.list_ollama_models("http://x:11434") == ["model@http://x:11434"]


def test_list_ollama_models_without_base_url_defers_to_the_adapter(monkeypatch):
    # No base URL resolves to Ollama's default host inside the adapter, so the
    # wrapper forwards None rather than reading one out of the live config.
    monkeypatch.setattr("api.adapters.llm_chat.list_ollama_models", lambda url: [repr(url)])
    assert cs.list_ollama_models() == ["None"]


def test_list_ollama_models_unreachable_raises_502(monkeypatch):
    def _boom(_url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("api.adapters.llm_chat.list_ollama_models", _boom)
    with pytest.raises(HTTPException) as exc:
        cs.list_ollama_models("http://x:11434")
    assert exc.value.status_code == 502


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
