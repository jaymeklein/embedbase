"""Contract tests for routes still stubbed for later deliveries.

These pin the documented status codes so a partial implementation doesn't
silently change the contract.
"""


async def test_metrics_stub_returns_200(client):
    r = await client.get("/metrics")
    assert r.status_code == 200


# /config is no longer a stub — the config page (Phase 2) implements GET/PUT and
# reload-status. Covered by tests/integration/test_config.py + unit
# tests/unit/test_config_service.py.
# /mcp is no longer a stub — Delivery 4 mounts the real MCP SSE server there.
# Its auth + behaviour are covered by tests/integration/test_mcp.py.
