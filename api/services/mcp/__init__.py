"""MCP (Model Context Protocol) server package.

Houses the Delivery 4 MCP surface so that ``api/routers/mcp.py`` stays
routing-only (it merely mounts the ASGI app built here):

* :mod:`api.services.mcp.rate_limit` — per-API-key token-bucket throttling.
* :mod:`api.services.mcp.tools` — the tool implementations (delegate to the
  existing workspace/document/search services).
* :mod:`api.services.mcp.middleware` — API-key auth + rate limiting for the
  mounted transport.
* :mod:`api.services.mcp.server` — wires the tools into a ``FastMCP`` instance
  and exposes the SSE ASGI app.
"""
