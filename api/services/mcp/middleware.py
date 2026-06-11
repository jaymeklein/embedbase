"""Authentication + rate limiting for the mounted MCP transport.

Implemented as a *raw* ASGI middleware (not Starlette's ``BaseHTTPMiddleware``)
so it never buffers the SSE response stream. Every HTTP request to ``/mcp`` must:

1. carry the master API key (``Authorization: Bearer`` or ``X-API-Key``) — else 401;
2. stay within the per-key token-bucket budget — else 429.

Non-HTTP scopes (lifespan) pass straight through.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.services.mcp.rate_limit import TokenBucketRateLimiter
from api.settings import settings


def _raw_key_from_scope(scope: Scope) -> str | None:
    """Extract the API key from ``Authorization``/``X-API-Key`` request headers."""
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    x_api_key = headers.get("x-api-key")
    if x_api_key:
        return x_api_key.strip()
    authorization = headers.get("authorization")
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return value
    return None


async def _send_json(
    send: Send, status: int, payload: dict[str, str], extra: list[tuple[bytes, bytes]] | None = None
) -> None:
    """Write a small JSON error response over the ASGI ``send`` channel."""
    body = json.dumps(payload).encode()
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if extra:
        headers.extend(extra)
    start: Message = {"type": "http.response.start", "status": status, "headers": headers}
    await send(start)
    await send({"type": "http.response.body", "body": body})


class MCPAuthRateLimitMiddleware:
    """Gate the wrapped MCP ASGI app behind API-key auth + token-bucket limiting."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        authenticate: Callable[[str], bool],
        rate_limiter: TokenBucketRateLimiter,
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The downstream MCP ASGI app (e.g. ``FastMCP.sse_app()``).
            authenticate: Returns ``True`` when the raw key is accepted.
            rate_limiter: Per-key token bucket; a denied key yields HTTP 429.
        """
        self._app = app
        self._authenticate = authenticate
        self._limiter = rate_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entrypoint: authenticate + throttle, then delegate."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        raw_key = _raw_key_from_scope(scope)
        if not raw_key or not self._authenticate(raw_key):
            await _send_json(send, 401, {"detail": "Missing or invalid API key"})
            return
        if not self._limiter.allow(raw_key):
            await _send_json(send, 429, {"detail": "Rate limit exceeded"}, [(b"retry-after", b"1")])
            return
        await self._app(scope, receive, send)


def build_mcp_middleware(
    app: ASGIApp, *, rate_limit_rpm: int, master_key: str | None = None
) -> MCPAuthRateLimitMiddleware:
    """Wire the MCP middleware from config + the configured master key.

    Args:
        app: The MCP ASGI app to protect.
        rate_limit_rpm: Requests-per-minute ceiling per API key.
        master_key: Override the accepted key (defaults to ``settings.master_api_key``).

    Returns:
        A configured :class:`MCPAuthRateLimitMiddleware`.
    """
    expected = master_key if master_key is not None else settings.master_api_key

    def _authenticate(raw_key: str) -> bool:
        return secrets.compare_digest(raw_key, expected)

    return MCPAuthRateLimitMiddleware(
        app, authenticate=_authenticate, rate_limiter=TokenBucketRateLimiter(rate_limit_rpm)
    )
