"""Contract tests for routes still stubbed for later deliveries.

These pin the documented status codes so a partial implementation doesn't
silently change the contract.
"""


async def test_config_get_returns_501(client):
    assert (await client.get("/config")).status_code == 501


async def test_config_put_returns_501(client):
    # Empty body validates to AppConfig() defaults, so we reach the handler.
    r = await client.put("/config", json={})
    assert r.status_code == 501


async def test_config_reload_status_returns_501(client):
    assert (await client.get("/config/reload-status/v1")).status_code == 501


async def test_metrics_stub_returns_200(client):
    r = await client.get("/metrics")
    assert r.status_code == 200


async def test_mcp_sse_stub_returns_200(client):
    r = await client.get("/mcp/sse")
    assert r.status_code == 200
