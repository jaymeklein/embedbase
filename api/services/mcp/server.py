"""Build the EmbedBase ``FastMCP`` server and its streamable-HTTP ASGI app.

The tool wrappers here resolve runtime dependencies (DB session + adapter
singletons) and delegate to :mod:`api.services.mcp.tools`, which holds the
testable logic. The streamable-HTTP app is mounted at ``/mcp`` and — because the
API carries ``root_path="/api"`` — is reached externally at ``/api/mcp/``. It is
wrapped with the auth + rate-limit middleware, and its session manager is run for
the app's lifetime by the lifespan (see :mod:`api.main`).

The transport is stateless streamable HTTP (``stateless_http`` + ``json_response``):
each tool call is a self-contained request/response with no long-lived stream to
drop across an ``uvicorn --reload`` — the failure mode of the previous SSE
transport. API surface verified against ``mcp==1.28.1`` via runtime introspection.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from api.db import AsyncSessionLocal
from api.dependencies import (
    get_embedding_adapter,
    get_reranker,
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
        """Hybrid semantic + keyword search across one or more collections.

        The response carries a ``more_available`` flag; when relevant chunks fell below the
        ``top_k`` cut it also includes a ``notice`` string. If a ``notice`` is present and the
        answer looks incomplete, re-run with a higher ``top_k`` before responding.
        """
        embedder = _require(get_embedding_adapter(), "Embedding")
        vector_store = _require(get_vector_store(), "Vector store")
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
                reranker=get_reranker(),  # optional — None skips the rerank stage
            )

    @server.tool()
    async def ingest_document(
        collection_id: str, file_path: str, temporary: bool = False
    ) -> dict[str, Any]:
        """Ingest a container-local file (by path) into a collection.

        Set ``temporary`` to auto-purge the document after
        ``storage.temp_retention_hours`` (a no-op when retention is 0).
        """
        async with AsyncSessionLocal() as db:
            return await tools.ingest_document(
                collection_id=collection_id, file_path=file_path,
                temporary=temporary, db=db,
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

    Uses the **streamable-HTTP** transport in ``stateless_http`` + ``json_response``
    mode: each tool call is a self-contained HTTP request/response, with no
    long-lived SSE stream and no server-side session to expire. That survives
    ``uvicorn --reload`` and Docker Desktop's port-forwarding, unlike the deprecated
    SSE transport (whose single persistent stream silently died on any reload and
    could not be re-established without a full client reconnect).

    Args:
        max_results: Upper bound applied to ``search_documents`` ``top_k``.

    Returns:
        A ready-to-serve :class:`FastMCP` instance.
    """
    server = FastMCP("embedbase", stateless_http=True, json_response=True)
    # Serve at the mount root so the external endpoint is a clean ``/api/mcp``
    # (this app is mounted at ``/mcp`` and the app carries root_path ``/api``).
    server.settings.streamable_http_path = "/"
    _register_tools(server, max_results=max_results)
    return server


def build_mcp_asgi_app(config: MCPConfig) -> tuple[ASGIApp, FastMCP]:
    """Build the streamable-HTTP ASGI app for ``/mcp``, guarded by auth + limits.

    Args:
        config: The resolved ``mcp`` config section (rate limit + result cap).

    Returns:
        ``(asgi_app, server)`` — the auth/rate-limit-wrapped streamable-HTTP app,
        and the :class:`FastMCP` server whose session manager the caller must run
        for the app's lifetime (see :func:`mount_app` + the app lifespan).
    """
    server = build_mcp_server(max_results=config.max_results)
    http_app = server.streamable_http_app()
    guarded = build_mcp_middleware(http_app, rate_limit_rpm=config.rate_limit_rpm)
    return guarded, server


def mount_app(app: FastAPI, config: MCPConfig) -> None:
    """Mount the MCP streamable-HTTP app at ``/mcp`` (→ ``/api/mcp``) when enabled.

    Owns the enablement decision so the router stays a pure delegation. Stashes the
    server on ``app.state.mcp_server`` so the app lifespan can run its streamable
    session manager — required before the endpoint can serve requests.

    Args:
        app: The FastAPI application to mount onto.
        config: The resolved ``mcp`` config; mounting is skipped when disabled.
    """
    if not config.enabled:
        return
    asgi_app, server = build_mcp_asgi_app(config)
    app.mount("/mcp", asgi_app)
    app.state.mcp_server = server
