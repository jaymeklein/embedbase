"""Search router: POST /search — multi-collection hybrid search."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, Reranker, VectorStoreAdapter
from api.dependencies import (
    get_db,
    get_reranker,
    require_embedding_adapter,
    require_redis_client,
    require_vector_store,
)
from api.models.search import SearchRequest, SearchResponse
from api.services.auth import require_master
from api.services.search import multi_collection_search

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    db: AsyncSession = Depends(get_db),
    _principal: object = Depends(require_master),
    embedder: EmbeddingAdapter = Depends(require_embedding_adapter),
    vector_store: VectorStoreAdapter = Depends(require_vector_store),
    redis_client: Any = Depends(require_redis_client),
    reranker: Reranker | None = Depends(get_reranker),
) -> SearchResponse:
    """Run a hybrid (semantic + BM25) search across one or more collections.

    Args:
        request: Search parameters including query, collection IDs, and filters.
        db: Injected async database session for collection metadata.
        _principal: Authenticated master principal (enforces auth, value unused).
        embedder: Embedding adapter injected via Depends.
        vector_store: Vector store adapter injected via Depends.
        redis_client: Redis client injected via Depends (raises 503 if not ready).
        reranker: Optional cross-encoder reranker (None when disabled/not loaded).

    Returns:
        SearchResponse with ranked results and per-collection stats.
    """
    return await multi_collection_search(
        request,
        db=db,
        embedder=embedder,
        vector_store=vector_store,
        redis_client=redis_client,
        reranker=reranker,
    )
