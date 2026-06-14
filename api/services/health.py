"""Health snapshot assembly for the ``/healthz`` endpoint.

Kept out of the router (Section 5: routers are routing-only) because it owns the
process-uptime clock and performs a real vector-store liveness probe.
"""

import asyncio
import time
from typing import Any

from api.adapters.base import EmbeddingAdapter, VectorStoreAdapter
from api.settings import settings

_VERSION = "1.0.0"
_START_TIME = time.time()


async def build_health(
    store: VectorStoreAdapter | None,
    embedding_adapter: EmbeddingAdapter | None,
) -> dict[str, Any]:
    """Assemble the ``/healthz`` payload.

    The vector-store probe is a real round-trip (``store.ping()``) run off the
    event loop, so ``vector_store_connected`` reflects actual reachability rather
    than merely whether an adapter object was constructed.

    Args:
        store: The active vector-store adapter, or None before startup completes.
        embedding_adapter: The active embedding adapter, or None before startup.

    Returns:
        The health document serialised by the route handler.
    """
    connected = await asyncio.to_thread(store.ping) if store is not None else False
    return {
        "status": "ok",
        "service": "api",
        "version": _VERSION,
        "vector_store": settings.vector_store,
        "vector_store_connected": connected,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_model_loaded": embedding_adapter is not None,
        "uptime_seconds": int(time.time() - _START_TIME),
    }
