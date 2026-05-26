from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from api.adapters.base import EmbeddingAdapter, VectorStoreAdapter
from api.db import get_connection

# Singletons resolved at startup via lifespan
_embedding_adapter: EmbeddingAdapter | None = None
_vector_store: VectorStoreAdapter | None = None


def set_embedding_adapter(adapter: EmbeddingAdapter) -> None:
    global _embedding_adapter
    _embedding_adapter = adapter


def set_vector_store(store: VectorStoreAdapter) -> None:
    global _vector_store
    _vector_store = store


async def get_embedding_adapter() -> EmbeddingAdapter | None:
    # Returns None in Delivery 1 — adapter deps installed in Delivery 2
    return _embedding_adapter


async def get_vector_store() -> VectorStoreAdapter | None:
    # Returns None in Delivery 1 — adapter deps installed in Delivery 2
    return _vector_store


@asynccontextmanager
async def db_context() -> AsyncIterator[aiosqlite.Connection]:
    db = await get_connection()
    try:
        yield db
    finally:
        await db.close()


async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    async with db_context() as db:
        yield db
