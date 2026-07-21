"""Per-request principal for MCP tool calls.

The auth middleware (:mod:`api.services.mcp.middleware`) resolves the caller's
:class:`~api.services.auth.Principal` and stashes it here; the tool wrappers in
:mod:`api.services.mcp.server` read it back and pass it to the framework-agnostic
tool implementations, which enforce the caller's grants.

A ``ContextVar`` carries the principal across the ``await`` from the middleware
into the tool. The MCP transport is **stateless** streamable HTTP, so each request
is handled in its own task tree (spawned after the middleware sets the value), and
child tasks inherit the context at creation — there is no cross-request leakage.
The middleware resets the value in a ``finally`` regardless.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from api.services.auth import Principal

_principal_ctx: ContextVar[Principal | None] = ContextVar("mcp_principal", default=None)


def set_current_principal(principal: Principal) -> Token[Principal | None]:
    """Bind the authenticated principal for the current MCP request."""
    return _principal_ctx.set(principal)


def reset_current_principal(token: Token[Principal | None]) -> None:
    """Clear the principal bound by :func:`set_current_principal`."""
    _principal_ctx.reset(token)


def current_principal() -> Principal:
    """Return the current request's principal, or raise if the middleware skipped auth."""
    principal = _principal_ctx.get()
    if principal is None:
        raise RuntimeError("No authenticated principal for this MCP request")
    return principal
