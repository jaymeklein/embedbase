"""Unit tests for the MCP transport-security posture.

EmbedBase authenticates every MCP request with an API key, so authorization never
depends on network position. The streamable-HTTP transport therefore disables the
SDK's DNS-rebinding Host allowlist, which otherwise rejects every non-loopback Host
with ``421 Invalid Host header`` — leaving the endpoint reachable from any machine on
the network by any address or name.
"""

import asyncio

from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.requests import Request

from api.services.mcp import server as mcp_server


def _request(host: str, *, method: str = "POST") -> Request:
    """A minimal connection carrying ``host`` (POST + JSON, as real tool calls send)."""
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", host.encode()), (b"content-type", b"application/json")],
        }
    )


def test_build_mcp_server_disables_host_validation():
    # The whole change: the built server carries DNS-rebinding protection OFF, so the
    # transport refuses no Host. This is the setting FastMCP feeds to the live middleware.
    ts = mcp_server.build_mcp_server().settings.transport_security
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is False


def test_disabled_setting_accepts_a_host_the_loopback_default_would_reject():
    # Non-tautological: the SAME LAN Host is *rejected* (421) under the SDK's loopback-only
    # default but *accepted* under our settings — so the flag, not luck, is what opens the
    # endpoint to the network. Exercised over POST (Content-Type checked first), the path
    # real MCP tool calls use.
    lan_host = "192.168.32.5:3636"

    ours = TransportSecurityMiddleware(mcp_server._transport_security())
    assert asyncio.run(ours.validate_request(_request(lan_host), is_post=True)) is None

    loopback_only = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=["localhost:*"]
    )
    guarded = TransportSecurityMiddleware(loopback_only)
    rejected = asyncio.run(guarded.validate_request(_request(lan_host), is_post=True))
    assert rejected is not None and rejected.status_code == 421


def test_streamable_http_app_wires_disabled_protection_into_the_session_manager():
    # The wiring, not just the setting: build_mcp_server() only *stores* the setting on
    # FastMCP.settings — it's streamable_http_app() that constructs the
    # StreamableHTTPSessionManager whose TransportSecurityMiddleware enforces the Host allowlist on
    # every real request. Assert the disabled setting reaches that live session manager, so an SDK
    # change that stopped forwarding it (or a wiring regression) fails here instead of silently
    # 421'ing LAN clients in production — the exact gap a settings-only assertion leaves open.
    server = mcp_server.build_mcp_server()
    server.streamable_http_app()  # side effect: constructs server.session_manager
    settings = server.session_manager.security_settings
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is False
