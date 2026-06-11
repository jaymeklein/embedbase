"""Build the EmbedBase ``FastMCP`` server and its SSE ASGI app.

The tool wrappers here resolve runtime dependencies (DB session + adapter
singletons) and delegate to :mod:`api.services.mcp.tools`, which holds the
testable logic. The SSE app is mounted at ``/mcp`` (so SSE lives at ``/mcp/sse``
and the message channel at ``/mcp/messages/``) and wrapped with the auth +
rate-limit middleware.

API surface verified against ``mcp==1.27.1`` via runtime introspection of
``FastMCP.sse_app`` / ``FastMCP.tool`` (Context7 lookup not required — the
installed package is authoritative for the pinned version).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from api.db import AsyncSessionLocal
from api.dependencies import (
    get_embedding_adapter,
    get_redis_client,
    get_vector_store,
)
from api.models.config import MCPConfig
from api.services.mcp import tools
from api.services.mcp.middleware import build_mcp_middleware


def _require[T](value: T | None, name: str) -> T:
    """Return ``value`` or raise if the backing adapter is not yet ready."""
    if value is None:
        raise RuntimeError(f"{name} backend not ready")
    return value


def _register_tools(server: FastMCP, *, max_results: int) -> None:
    """Register the five EmbedBase tools on ``server``."""

    @server.tool()
    async def list_workspaces() -> dict[str, Any]:
        """List all workspaces with their collections and document counts."""
        async with AsyncSessionLocal() as db:
            return await tools.list_workspaces(db=db)

    @server.tool()
    async def search_documents(
        query: str,
        collection_ids: list[str],
        top_k: int = 5,
        hybrid: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hybrid semantic + keyword search across one or more collections."""
        embedder = _require(get_embedding_adapter(), "Embedding")
        vector_store = _require(get_vector_store(), "Vector store")
        redis_client = _require(get_redis_client(), "Redis")
        async with AsyncSessionLocal() as db:
            return await tools.search_documents(
                query=query,
                collection_ids=collection_ids,
                top_k=top_k,
                hybrid=hybrid,
                filters=filters,
                max_results=max_results,
                db=db,
                embedder=embedder,
                vector_store=vector_store,
                redis_client=redis_client,
            )

    @server.tool()
    async def ingest_document(collection_id: str, file_path: str) -> dict[str, Any]:
        """Ingest a container-local file (by path) into a collection."""
        async with AsyncSessionLocal() as db:
            return await tools.ingest_document(
                collection_id=collection_id, file_path=file_path, db=db
            )

    @server.tool()
    async def list_documents(collection_id: str) -> dict[str, Any]:
        """List the documents in a collection with their ingestion status."""
        async with AsyncSessionLocal() as db:
            return await tools.list_documents(collection_id=collection_id, db=db)

    @server.tool()
    async def delete_document(document_id: str) -> dict[str, Any]:
        """Delete a document and enqueue async vector + BM25 cleanup."""
        async with AsyncSessionLocal() as db:
            return await tools.delete_document(document_id=document_id, db=db)


def build_mcp_server(*, max_results: int = 20) -> FastMCP:
    """Construct the ``FastMCP`` server with all EmbedBase tools registered.

    Args:
        max_results: Upper bound applied to ``search_documents`` ``top_k``.

    Returns:
        A ready-to-serve :class:`FastMCP` instance.
    """
    server = FastMCP("embedbase")
    _register_tools(server, max_results=max_results)
    return server


def build_mcp_asgi_app(config: MCPConfig) -> ASGIApp:
    """Build the SSE ASGI app for mounting at ``/mcp``, guarded by auth + limits.

    Args:
        config: The resolved ``mcp`` config section (rate limit + result cap).

    Returns:
        An ASGI app: SSE at ``/sse`` and messages at ``/messages/`` (relative to
        the ``/mcp`` mount point), wrapped in the auth + rate-limit middleware.
    """
    server = build_mcp_server(max_results=config.max_results)
    sse_app = server.sse_app()
    return build_mcp_middleware(sse_app, rate_limit_rpm=config.rate_limit_rpm)


def mount_app(app: FastAPI, config: MCPConfig) -> None:
    """Mount the MCP SSE app at ``/mcp`` (SSE at ``/mcp/sse``) when enabled.

    Owns the enablement decision so the router stays a pure delegation.

    Args:
        app: The FastAPI application to mount onto.
        config: The resolved ``mcp`` config; mounting is skipped when disabled.
    """
    if not config.enabled:
        return
    app.mount("/mcp", build_mcp_asgi_app(config))
