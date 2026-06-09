from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, VectorStoreAdapter
from api.db import AsyncSessionLocal

# ---------------------------------------------------------------------------
# Adapter singletons — set once in lifespan(), read everywhere via Depends()
# ---------------------------------------------------------------------------

_embedding_adapter: EmbeddingAdapter | None = None
_vector_store: VectorStoreAdapter | None = None
_redis_client: Any = None


def set_embedding_adapter(adapter: EmbeddingAdapter) -> None:
    global _embedding_adapter
    _embedding_adapter = adapter


def set_vector_store(store: VectorStoreAdapter) -> None:
    global _vector_store
    _vector_store = store


def get_embedding_adapter() -> EmbeddingAdapter | None:
    return _embedding_adapter


def get_vector_store() -> VectorStoreAdapter | None:
    return _vector_store


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


def require_vector_store() -> VectorStoreAdapter:
    """FastAPI dependency: return the vector store adapter or raise 503.

    Returns:
        The active VectorStoreAdapter singleton.

    Raises:
        HTTPException: 503 if the adapter has not been initialised.
    """
    store = _vector_store
    if store is None:
        raise HTTPException(503, "Vector store backend not ready")
    return store


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
