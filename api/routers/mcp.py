"""MCP transport mounting (routing-only).

The MCP server, tools, auth, rate limiting, and the enablement/mount decision all
live in :mod:`api.services.mcp`. This module only delegates the mount — no
business logic, conditionals, or schema declarations here.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.models.config import MCPConfig
from api.services.mcp.server import mount_app


def mount_mcp(app: FastAPI, mcp_config: MCPConfig) -> None:
    """Mount the MCP SSE app onto ``app`` (delegated to the service layer)."""
    mount_app(app, mcp_config)
