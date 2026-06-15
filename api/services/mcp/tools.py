"""MCP tool implementations.

Each function is a thin, framework-agnostic wrapper over an existing service, so
the same logic is exercised by both the ``FastMCP`` server
(:mod:`api.services.mcp.server`) and the integration tests — no SSE transport is
needed to test a tool. The server layer is responsible for resolving the
``db``/``embedder``/``vector_store``/``redis_client`` dependencies and passing
them in as keyword arguments.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, VectorStoreAdapter
from api.models.search import SearchRequest
from api.services import documents as doc_svc
from api.services import workspaces as ws_svc
from api.services.auth import Principal
from api.services.search import multi_collection_search

# MCP authenticates with the master key (see api.services.mcp.middleware), so the
# tools run with full access. The principal is still threaded through so the
# collection-scoping checks stay honest and unit-testable.
MASTER_PRINCIPAL = Principal(is_master=True)

_TOP_K_FLOOR = 1


async def list_workspaces(*, db: AsyncSession) -> dict[str, Any]:
    """Return the workspace tree with per-collection and per-workspace counts."""
    return {"workspaces": await ws_svc.list_workspace_tree(db)}


async def search_documents(
    *,
    query: str,
    collection_ids: list[str],
    top_k: int = 5,
    hybrid: bool = True,
    filters: dict[str, Any] | None = None,
    max_results: int = 20,
    db: AsyncSession,
    embedder: EmbeddingAdapter,
    vector_store: VectorStoreAdapter,
    redis_client: Any,
) -> dict[str, Any]:
    """Run a hybrid (semantic + BM25) search across one or more collections.

    Args:
        query: Natural-language search string.
        collection_ids: Collections to fan out across (at least one).
        top_k: Desired number of results; clamped to ``[1, max_results]``.
        hybrid: When ``True`` fuse BM25 with semantic scores (RRF).
        filters: Optional ``language``/``filename``/``tags`` metadata filter.
        max_results: Upper bound on ``top_k`` (from ``mcp.max_results`` config).
        db: Active async database session.
        embedder: Embedding adapter for the query vector.
        vector_store: Vector store to search.
        redis_client: Redis client backing the BM25 read path.

    Returns:
        A JSON-serialisable ``SearchResponse`` dict with results and stats.
    """
    bounded_top_k = max(_TOP_K_FLOOR, min(top_k, max_results))
    request = SearchRequest.model_validate(
        {
            "query": query,
            "collection_ids": collection_ids,
            "top_k": bounded_top_k,
            "hybrid": hybrid,
            "filters": filters,
        }
    )
    response = await multi_collection_search(
        request,
        db=db,
        embedder=embedder,
        vector_store=vector_store,
        redis_client=redis_client,
    )
    return response.model_dump(mode="json")


async def ingest_document(
    *,
    collection_id: str,
    file_path: str,
    db: AsyncSession,
    principal: Principal = MASTER_PRINCIPAL,
) -> dict[str, Any]:
    """Enqueue a container-local file for ingestion into ``collection_id``."""
    return await doc_svc.ingest_local_path(db, collection_id, file_path, principal)


async def list_documents(*, collection_id: str, db: AsyncSession) -> dict[str, Any]:
    """List active documents (with ingestion status) in ``collection_id``."""
    return {"documents": await doc_svc.list_documents(db, collection_id)}


async def delete_document(
    *,
    document_id: str,
    db: AsyncSession,
    principal: Principal = MASTER_PRINCIPAL,
) -> dict[str, Any]:
    """Soft-delete a document and enqueue async vector + BM25 cleanup."""
    collection_id = await doc_svc.resolve_document_collection(db, document_id)
    if not principal.can_access(collection_id):
        raise HTTPException(403, "API key not valid for this collection")
    await doc_svc.delete_document(db, collection_id, document_id)
    return {"document_id": document_id, "collection_id": collection_id, "status": "deleting"}
