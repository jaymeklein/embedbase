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


async def test_build_health_includes_lan_ip(monkeypatch):
    from api.services import health

    health.lan_ip.cache_clear()
    monkeypatch.setattr(health, "lan_ip", lambda: "192.168.1.50")
    data = await build_health(None, None)
    assert data["lan_ip"] == "192.168.1.50"


def test_lan_ip_falls_back_to_loopback_when_offline(monkeypatch):
    import socket as socket_mod

    from api.services import health

    monkeypatch.setattr(health.settings, "lan_host", "")
    health.lan_ip.cache_clear()

    class _DeadSocket:
        def connect(self, _addr):
            raise OSError("network unreachable")

        def getsockname(self):  # pragma: no cover - never reached
            return ("0.0.0.0", 0)

        def close(self):
            pass

    monkeypatch.setattr(socket_mod, "socket", lambda *a, **k: _DeadSocket())
    health.lan_ip.cache_clear()
    assert health.lan_ip() == "127.0.0.1"
    health.lan_ip.cache_clear()


def test_lan_ip_prefers_lan_host_env(monkeypatch):
    from api.services import health

    monkeypatch.setattr(health.settings, "lan_host", "192.168.3.33")
    health.lan_ip.cache_clear()
    assert health.lan_ip() == "192.168.3.33"
    health.lan_ip.cache_clear()
