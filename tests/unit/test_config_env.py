"""Unit tests for the vector-store env→config overlay."""

from api.models.config import AppConfig
from api.services.config_env import overlay_parser_env, overlay_vector_store_env


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
