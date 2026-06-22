"""Health snapshot assembly for the ``/healthz`` endpoint.

Kept out of the router (Section 5: routers are routing-only) because it owns the
process-uptime clock and performs a real vector-store liveness probe.
"""

import asyncio
import socket
import time
from functools import lru_cache
from typing import Any

from api.adapters.base import EmbeddingAdapter, VectorStoreAdapter
from api.models.config import AppConfig
from api.settings import settings

_VERSION = "1.0.0"
_START_TIME = time.time()


@lru_cache(maxsize=1)
def lan_ip() -> str:
    """Return the host's LAN address, so the console can offer a reachable
    address instead of telling the user to find one.

    Prefers ``LAN_HOST`` (injected by the start script, which detects it on the
    host — a bridge-networked container only ever sees its own bridge IP, so it
    can't discover the host's LAN IP itself). When unset (bare-metal / host
    networking), falls back to a socket probe: a UDP socket "connected" to a
    public address sends no packets; the OS just resolves which local interface
    would route there, and we read its address. Loopback when offline. Cached:
    the address is stable for the process lifetime.
    """
    if settings.lan_host:
        return settings.lan_host
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def build_health(
    store: VectorStoreAdapter | None,
    embedding_adapter: EmbeddingAdapter | None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Assemble the ``/healthz`` payload.

    The vector-store probe is a real round-trip (``store.ping()``) run off the
    event loop, so ``vector_store_connected`` reflects actual reachability rather
    than merely whether an adapter object was constructed. The displayed backend
    and embedding provider/model come from the live :class:`AppConfig` (the
    editable config), not from ``.env``.

    Args:
        store: The active vector-store adapter, or None before startup completes.
        embedding_adapter: The active embedding adapter, or None before startup.
        config: The live application config, or None before startup completes.

    Returns:
        The health document serialised by the route handler.
    """
    connected = await asyncio.to_thread(store.ping) if store is not None else False
    return {
        "status": "ok",
        "service": "api",
        "version": _VERSION,
        "vector_store": config.vector_store.backend if config else "unknown",
        "vector_store_connected": connected,
        "embedding_provider": config.embedding.provider if config else "unknown",
        "embedding_model": config.embedding.model if config else "unknown",
        "embedding_model_loaded": embedding_adapter is not None,
        "uptime_seconds": int(time.time() - _START_TIME),
        "lan_ip": lan_ip(),
    }
