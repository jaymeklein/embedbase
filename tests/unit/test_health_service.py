"""Unit tests for the health snapshot service (no real backends)."""

from api.services.health import build_health


class _FakeStore:
    """Minimal vector-store stub exposing only the liveness probe."""

    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def ping(self) -> bool:
        return self._alive


class _FakeEmbed:
    pass


async def test_build_health_store_none_reports_disconnected():
    data = await build_health(None, None)
    assert data["status"] == "ok"
    assert data["vector_store_connected"] is False
    assert data["embedding_model_loaded"] is False


async def test_build_health_pings_store_when_present():
    data = await build_health(_FakeStore(alive=True), _FakeEmbed())
    assert data["vector_store_connected"] is True
    assert data["embedding_model_loaded"] is True


async def test_build_health_reflects_failed_ping():
    data = await build_health(_FakeStore(alive=False), None)
    assert data["vector_store_connected"] is False


async def test_build_health_includes_version_and_uptime():
    data = await build_health(None, None)
    assert data["version"] == "1.0.0"
    assert data["uptime_seconds"] >= 0


async def test_build_health_reads_display_values_from_config():
    from api.models.config import AppConfig, EmbeddingConfig, VectorStoreConfig

    config = AppConfig(
        embedding=EmbeddingConfig(provider="ollama", model="nomic-embed-text"),
        vector_store=VectorStoreConfig(backend="qdrant"),
    )
    data = await build_health(None, None, config)
    assert data["vector_store"] == "qdrant"
    assert data["embedding_provider"] == "ollama"
    assert data["embedding_model"] == "nomic-embed-text"


async def test_build_health_defaults_display_values_without_config():
    data = await build_health(None, None)
    assert data["vector_store"] == "unknown"
    assert data["embedding_provider"] == "unknown"
