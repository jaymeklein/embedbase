"""Unit tests for the vector-store env→config overlay."""

from api.models.config import AppConfig, S3BackendConfig
from api.services.config_env import (
    _set_nested,
    overlay_parser_env,
    overlay_storage_env,
    overlay_vector_store_env,
)


def test_overlay_maps_postgres_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "mydb")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

    data = overlay_vector_store_env({})

    vs = data["vector_store"]
    assert vs["host"] == "db"
    assert vs["port"] == 5433  # coerced to int
    assert vs["database"] == "mydb"  # POSTGRES_DB → database
    assert vs["user"] == "u"
    assert vs["password"] == "secret"


def test_overlay_ignores_unset_and_empty(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "")  # empty → treated as unset
    data = overlay_vector_store_env({"vector_store": {"host": "myhost"}})
    assert data["vector_store"]["host"] == "myhost"
    assert "password" not in data["vector_store"]


def test_overlay_preserves_existing_file_values(monkeypatch):
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    data = overlay_vector_store_env({"vector_store": {"host": "myhost"}})
    assert data["vector_store"]["host"] == "myhost"


def test_overlaid_dict_validates_to_vector_store_config(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("POSTGRES_PORT", "5432")

    cfg = AppConfig.model_validate(overlay_vector_store_env({}))

    assert cfg.vector_store.password == "pw"
    assert cfg.vector_store.port == 5432


# ── Parser env overlay (docling artifacts path) ───────────────────────────────


def test_overlay_parser_maps_docling_artifacts_path(monkeypatch):
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", "/models/docling")
    data = overlay_parser_env({})
    assert data["parsers"]["docling_artifacts_path"] == "/models/docling"


def test_overlay_parser_ignores_unset_and_empty(monkeypatch):
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", "")  # empty → treated as unset
    data = overlay_parser_env({"parsers": {"pdf_backend": "docling"}})
    assert "docling_artifacts_path" not in data["parsers"]
    assert data["parsers"]["pdf_backend"] == "docling"


def test_overlaid_parser_dict_validates_to_parser_config(monkeypatch):
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", "/opt/models")
    cfg = AppConfig.model_validate(overlay_parser_env({}))
    assert cfg.parsers.docling_artifacts_path == "/opt/models"


# ── Storage env overlay (per-instance S3 secrets + default) ───────────────────


def test_overlay_storage_maps_per_instance_secrets(monkeypatch):
    monkeypatch.setenv("S3__MINIO__ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("S3__MINIO__SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("S3__MINIO__ENDPOINT_URL", "http://minio:9000")

    data = overlay_storage_env(
        {"storage": {"backends": {"minio": {"type": "s3", "bucket": "b"}}}}
    )

    minio = data["storage"]["backends"]["minio"]
    assert minio["access_key_id"] == "ak"
    assert minio["secret_access_key"] == "sk"
    assert minio["endpoint_url"] == "http://minio:9000"
    assert minio["bucket"] == "b"  # untouched — no env var for it


def test_overlay_storage_sets_default(monkeypatch):
    monkeypatch.setenv("STORAGE_DEFAULT", "minio")
    assert overlay_storage_env({})["storage"]["default"] == "minio"


def test_overlay_storage_ignores_non_s3_and_unset(monkeypatch):
    monkeypatch.delenv("S3__LOCAL__ACCESS_KEY_ID", raising=False)
    data = overlay_storage_env({"storage": {"backends": {"local": {"type": "local"}}}})
    assert data["storage"]["backends"]["local"] == {"type": "local"}


def test_overlaid_storage_dict_validates_and_routes_secret(monkeypatch):
    monkeypatch.setenv("S3__MINIO__SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("STORAGE_DEFAULT", "minio")

    cfg = AppConfig.model_validate(
        overlay_storage_env(
            {
                "storage": {
                    "backends": {
                        "local": {"type": "local"},
                        "minio": {"type": "s3", "bucket": "embedbase"},
                    }
                }
            }
        )
    )

    assert cfg.storage.default == "minio"
    minio = cfg.storage.backends["minio"]
    assert isinstance(minio, S3BackendConfig)
    assert minio.secret_access_key == "sk"


# ── Malformed sections are returned untouched (not coerced) ───────────────────


def test_overlay_vector_store_ignores_non_dict_section():
    data = {"vector_store": "not-a-dict"}
    assert overlay_vector_store_env(data) is data  # left as-is; validation reports it later


def test_overlay_parser_ignores_non_dict_section():
    data = {"parsers": ["oops"]}
    assert overlay_parser_env(data) is data


def test_overlay_storage_ignores_non_dict_section():
    data = {"storage": 42}
    assert overlay_storage_env(data) is data


def test_set_nested_descends_and_resets_non_dict_intermediate():
    # 'a' is a scalar but the path needs it to be a dict → it's replaced, not merged.
    target = {"a": "scalar"}
    _set_nested(target, ("a", "b"), "v")
    assert target == {"a": {"b": "v"}}
