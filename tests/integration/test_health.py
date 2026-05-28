"""Integration tests for the health endpoints."""


async def test_healthz_returns_200(client):
    r = await client.get("/healthz")
    assert r.status_code == 200


async def test_healthz_status_ok(client):
    data = (await client.get("/healthz")).json()
    assert data["status"] == "ok"
    assert data["service"] == "api"
    assert data["version"] == "1.0.0"


async def test_healthz_required_fields_present(client):
    data = (await client.get("/healthz")).json()
    for field in ("status", "service", "version", "uptime_seconds", "embedding_model_loaded"):
        assert field in data, f"Missing field: {field}"


async def test_healthz_uptime_non_negative(client):
    data = (await client.get("/healthz")).json()
    assert data["uptime_seconds"] >= 0


async def test_healthz_embedding_not_loaded_without_adapter(client):
    # No adapters are initialised in the test lifespan
    data = (await client.get("/healthz")).json()
    assert data["embedding_model_loaded"] is False
