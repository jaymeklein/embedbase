from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, Reranker
from api.adapters.vector_store.pgvector import PgvectorAdapter
from api.db import AsyncSessionLocal
from api.models.config import AppConfig, MCPConfig, SearchConfig

# ---------------------------------------------------------------------------
# Adapter singletons — set once in lifespan(), read everywhere via Depends()
# ---------------------------------------------------------------------------

_embedding_adapter: EmbeddingAdapter | None = None
_vector_store: PgvectorAdapter | None = None
_reranker: Reranker | None = None
_redis_client: Any = None
_app_config: AppConfig | None = None


def set_app_config(config: AppConfig) -> None:
    """Register the live application config (set in lifespan, swapped on reload)."""
    global _app_config
    _app_config = config


def get_app_config() -> AppConfig | None:
    """Return the live application config (None before lifespan completes)."""
    return _app_config


def get_search_config() -> SearchConfig:
    """FastAPI dependency: the live search config, or defaults before lifespan."""
    return (_app_config or AppConfig()).search


def get_mcp_config() -> MCPConfig:
    """The live MCP config, or defaults before lifespan (mirrors ``get_search_config``).

    Read per request by the MCP rate limiter so a ``rate_limit_rpm`` change applies to
    the next request — ``apply_config`` swaps this singleton, and the mounted MCP app
    is never rebuilt.
    """
    return (_app_config or AppConfig()).mcp


def set_embedding_adapter(adapter: EmbeddingAdapter) -> None:
    global _embedding_adapter
    _embedding_adapter = adapter


def set_vector_store(store: PgvectorAdapter) -> None:
    global _vector_store
    _vector_store = store


def get_embedding_adapter() -> EmbeddingAdapter | None:
    return _embedding_adapter


def get_vector_store() -> PgvectorAdapter | None:
    return _vector_store


def set_reranker(reranker: Reranker | None) -> None:
    global _reranker
    _reranker = reranker


def get_reranker() -> Reranker | None:
    """Return the reranker singleton, or ``None`` when disabled/not loaded.

    Optional by design — there is no ``require_*`` variant: search degrades to
    RRF-only ranking when the reranker is absent, so callers pass it straight
    through (a ``None`` simply skips the rerank stage).
    """
    return _reranker


def set_redis_client(client: Any) -> None:
    """Register the Redis client singleton built during lifespan."""
    global _redis_client
    _redis_client = client


def get_redis_client() -> Any:
    """Return the Redis client singleton (None before lifespan completes)."""
    return _redis_client


# ---------------------------------------------------------------------------
# FastAPI Depends wrappers — raise 503 when a backend is not ready
# ---------------------------------------------------------------------------


def require_embedding_adapter() -> EmbeddingAdapter:
    """FastAPI dependency: return the embedding adapter or raise 503.

    Returns:
        The active EmbeddingAdapter singleton.

    Raises:
        HTTPException: 503 if the adapter has not been initialised.
    """
    adapter = _embedding_adapter
    if adapter is None:
        raise HTTPException(503, "Embedding backend not ready")
    return adapter


def require_vector_store() -> PgvectorAdapter:
    """FastAPI dependency: return the vector store adapter or raise 503.

    Returns:
        The active PgvectorAdapter singleton.

    Raises:
        HTTPException: 503 if the adapter has not been initialised.
    """
    store = _vector_store
    if store is None:
        raise HTTPException(503, "Vector store backend not ready")
    return store


def require_redis_client() -> Any:
    """FastAPI dependency: return the Redis client or raise 503.

    Returns:
        The active Redis client singleton.

    Raises:
        HTTPException: 503 if the client has not been initialised.
    """
    client = _redis_client
    if client is None:
        raise HTTPException(503, "Redis backend not ready")
    return client


# ---------------------------------------------------------------------------
# Database session — yields a transactional AsyncSession per request
# ---------------------------------------------------------------------------

async def get_db() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency that opens an AsyncSession, yields it to the route
    handler, and closes it when the response is sent.  Commit explicitly
    inside the route; any unhandled exception triggers an implicit rollback.
    """
    async with AsyncSessionLocal() as session:
        yield session
