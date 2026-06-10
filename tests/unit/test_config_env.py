"""Unit tests for the vector-store env→config overlay."""

from api.models.config import AppConfig
from api.services.config_env import overlay_vector_store_env


def test_overlay_sets_backend_from_env(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE", "pgvector")
    data = overlay_vector_store_env({})
    assert data["vector_store"]["backend"] == "pgvector"


def test_overlay_maps_postgres_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "mydb")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

    data = overlay_vector_store_env({"vector_store": {"backend": "pgvector"}})

    pg = data["vector_store"]["pgvector"]
    assert pg["host"] == "db"
    assert pg["port"] == 5433  # coerced to int
    assert pg["database"] == "mydb"  # POSTGRES_DB → database
    assert pg["user"] == "u"
    assert pg["password"] == "secret"


def test_overlay_maps_qdrant_env(monkeypatch):
    monkeypatch.setenv("QDRANT_HOST", "qd")
    monkeypatch.setenv("QDRANT_PORT", "7000")
    data = overlay_vector_store_env({})
    qd = data["vector_store"]["qdrant"]
    assert qd["host"] == "qd"
    assert qd["port"] == 7000


def test_overlay_ignores_unset_and_empty(monkeypatch):
    monkeypatch.delenv("VECTOR_STORE", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "")  # empty → treated as unset
    data = overlay_vector_store_env({"vector_store": {"backend": "chroma"}})
    assert data["vector_store"]["backend"] == "chroma"
    assert "password" not in data["vector_store"].get("pgvector", {})


def test_overlay_preserves_existing_file_values(monkeypatch):
    monkeypatch.delenv("CHROMA_HOST", raising=False)
    data = overlay_vector_store_env({"vector_store": {"chroma": {"host": "myhost"}}})
    assert data["vector_store"]["chroma"]["host"] == "myhost"


def test_overlaid_dict_validates_to_pgvector_config(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE", "pgvector")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("POSTGRES_PORT", "5432")

    cfg = AppConfig.model_validate(overlay_vector_store_env({}))

    assert cfg.vector_store.backend == "pgvector"
    assert cfg.vector_store.pgvector.password == "pw"
    assert cfg.vector_store.pgvector.port == 5432
