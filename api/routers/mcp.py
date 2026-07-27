"""MCP transport mounting (routing-only).

The MCP server, tools, auth, rate limiting, and the enablement/mount decision all
live in :mod:`api.services.mcp`. This module only delegates the mount — no
business logic, conditionals, or schema declarations here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI

from api.models.config import MCPConfig
from api.services.auth import Principal, require_auth
from api.services.mcp.server import build_tool_catalog, mount_app

# Not under ``/mcp`` — that path is the mounted MCP ASGI sub-app, which would shadow a REST route.
router = APIRouter(tags=["mcp"])


@router.get("/mcp-tools")
async def mcp_tools(_: Principal = Depends(require_auth)) -> dict[str, Any]:
    """The MCP tool catalogue, grouped by domain, introspected live from the registered tools.

    Powers the Settings → MCP page + its generated SKILL.md so both track the real tool surface
    without a hand-kept list. Non-sensitive (the public integration surface); any valid key may read.
    """
    return {"groups": await build_tool_catalog()}


def mount_mcp(app: FastAPI, mcp_config: MCPConfig) -> None:
    """Mount the MCP streamable-HTTP app onto ``app`` (delegated to the service layer)."""
    mount_app(app, mcp_config)
