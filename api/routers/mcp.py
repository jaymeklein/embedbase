from fastapi import APIRouter

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/sse")
async def mcp_sse():
    # Full MCP server with SSE transport implemented in Delivery 4
    return {"status": "MCP server implemented in Delivery 4"}
