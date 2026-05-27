from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, VectorStoreAdapter
from api.db import AsyncSessionLocal

# ---------------------------------------------------------------------------
# Adapter singletons — set once in lifespan(), read everywhere via Depends()
# ---------------------------------------------------------------------------

_embedding_adapter: EmbeddingAdapter | None = None
_vector_store: VectorStoreAdapter | None = None


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
