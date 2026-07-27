"""Authentication + rate limiting for the mounted MCP transport.

Implemented as a *raw* ASGI middleware (not Starlette's ``BaseHTTPMiddleware``)
so it never buffers the response stream. Every HTTP request to ``/mcp`` must:

1. carry a valid API key (``Authorization: Bearer`` or ``X-API-Key``) — the master
   key or an active user's key — else 401/403;
2. stay within the per-key token-bucket budget — else 429.

The resolved :class:`~api.services.auth.Principal` is bound for the request (see
:mod:`api.services.mcp.context`) so the tool wrappers can enforce the user's
grants. Non-HTTP scopes (lifespan) pass straight through.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.db import AsyncSessionLocal
from api.services.auth import Principal, authenticate_api_key, record_key_use
from api.services.mcp.context import (
    reset_current_principal,
    reset_current_rate_limit,
    set_current_principal,
    set_current_rate_limit,
)
from api.services.mcp.rate_limit import TokenBucketRateLimiter

PrincipalResolver = Callable[[str], Awaitable[Principal]]


def _raw_key_from_scope(scope: Scope) -> str | None:
    """Extract the API key from ``Authorization``/``X-API-Key`` request headers."""
    # ASGI header bytes are latin-1 per HTTP; decoding as UTF-8 would raise on a
    # malformed byte and surface a 500 instead of a clean 401.
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
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


async def _resolve_principal(raw_key: str) -> Principal:
    """Default resolver: authenticate against the DB and record key usage."""
    async with AsyncSessionLocal() as db:
        principal = await authenticate_api_key(raw_key, db)
        if principal.api_key_id is not None:
            await record_key_use(principal.api_key_id, db)
        return principal


class MCPAuthRateLimitMiddleware:
    """Gate the wrapped MCP ASGI app behind API-key auth + token-bucket limiting."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        resolve_principal: PrincipalResolver,
        rate_limiter: TokenBucketRateLimiter,
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The downstream MCP ASGI app.
            resolve_principal: Async resolver mapping a raw key to a
                :class:`Principal`, raising ``HTTPException`` (401/403) on failure.
            rate_limiter: Per-key token bucket; a denied key yields HTTP 429.
        """
        self._app = app
        self._resolve_principal = resolve_principal
        self._limiter = rate_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entrypoint: authenticate + throttle, bind the principal, delegate."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        raw_key = _raw_key_from_scope(scope)
        if not raw_key:
            await _send_json(send, 401, {"detail": "Missing or invalid API key"})
            return
        # Coarse pre-auth guard on the raw key: gates the DB-backed, bcrypt-on-hit auth
        # below so a single key can't flood it. Sized to the caller's own rate — the global
        # default until their override is learned post-auth, then their configured limit.
        if not self._limiter.allow(raw_key):
            await _send_json(send, 429, {"detail": "Rate limit exceeded"}, [(b"retry-after", b"1")])
            return
        try:
            principal = await self._resolve_principal(raw_key)
        except HTTPException as exc:
            await _send_json(send, exc.status_code, {"detail": str(exc.detail)})
            return
        # Enforce the caller's configured rate limit (0/None → the global default). Exact
        # enforcement is a *post-auth* token bucket keyed by user id: sized to the limit
        # before its first allow(), so a concurrent cold-start burst can't slip past at the
        # global rate before the override is known, and keyed by user id (not the raw key)
        # so rotating the key can't reset the quota. The same limit is pushed onto the
        # pre-auth guard too, so an above-global override isn't capped by it on later
        # requests. The master key has no user id → only the coarse guard above applies.
        budget_key = raw_key
        if principal.user_id is not None:
            limit = principal.rate_limit_rpm or None
            budget_key = f"user:{principal.user_id}"
            self._limiter.set_key_limit(raw_key, limit)
            self._limiter.set_key_limit(budget_key, limit)
            if not self._limiter.allow(budget_key):
                await _send_json(
                    send, 429, {"detail": "Rate limit exceeded"}, [(b"retry-after", b"1")]
                )
                return
        # Snapshot the caller's binding budget (their per-user quota, or the coarse guard
        # for the master key) for the get_rate_limit tool. Non-consuming.
        rate_limit = self._limiter.snapshot(budget_key)
        principal_token = set_current_principal(principal)
        rate_limit_token = set_current_rate_limit(rate_limit)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_current_rate_limit(rate_limit_token)
            reset_current_principal(principal_token)


def build_mcp_middleware(
    app: ASGIApp,
    *,
    rate_limit_rpm: Callable[[], int],
    resolve_principal: PrincipalResolver | None = None,
) -> MCPAuthRateLimitMiddleware:
    """Wire the MCP middleware from config, with a DB-backed principal resolver.

    Args:
        app: The MCP ASGI app to protect.
        rate_limit_rpm: Returns the requests-per-minute ceiling per API key. Resolved
            per request (not captured here), so a config change applies to the next
            request — this middleware is built once at mount and never rebuilt.
        resolve_principal: Override the resolver (tests inject a fake); defaults to
            authenticating against the metadata DB.

    Returns:
        A configured :class:`MCPAuthRateLimitMiddleware`.
    """
    return MCPAuthRateLimitMiddleware(
        app,
        resolve_principal=resolve_principal or _resolve_principal,
        rate_limiter=TokenBucketRateLimiter(rate_limit_rpm),
    )
