"""Health snapshot assembly for the ``/healthz`` endpoint.

Kept out of the router (Section 5: routers are routing-only) because it owns the
process-uptime clock and performs a real vector-store liveness probe.
"""

import asyncio
import os
import socket
import time
from functools import lru_cache
from typing import Any

from api.adapters.base import EmbeddingAdapter
from api.adapters.vector_store.pgvector import PgvectorAdapter
from api.models.config import AppConfig
from api.settings import settings

_VERSION = "1.0.0"
_START_TIME = time.time()


def _in_container() -> bool:
    """True when running inside a Docker/OCI container (marker file present)."""
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


@lru_cache(maxsize=1)
def lan_ip() -> str:
    """Return the host's LAN address, so the console can offer a reachable
    address instead of telling the user to find one.

    Resolution order:

    1. ``LAN_HOST`` — the host's LAN IP, passed in via ``.env``. A bridge-networked
       container only ever sees its own bridge IP, so it can't discover the host's
       LAN IP itself; set this to advertise a specific address to other devices.
    2. Inside a container with no ``LAN_HOST`` — loopback. The socket probe below
       would only resolve the container's *bridge* interface (e.g. ``172.19.0.x``),
       which no LAN client can reach; advertising it just hands out a dead address.
       Loopback makes the console fall back to ``localhost`` and show its
       "set LAN_HOST" hint instead of a bogus IP.
    3. Bare-metal / host networking — a socket probe: a UDP socket "connected" to a
       public address sends no packets; the OS resolves which local interface would
       route there and we read its address. Loopback when offline.

    Cached: the address is stable for the process lifetime.
    """
    if settings.lan_host:
        return settings.lan_host
    if _in_container():
        return "127.0.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def build_health(
    store: PgvectorAdapter | None,
    embedding_adapter: EmbeddingAdapter | None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Assemble the ``/healthz`` payload.

    The vector-store probe is a real round-trip (``store.ping()``) run off the
    event loop, so ``vector_store_connected`` reflects actual reachability rather
    than merely whether an adapter object was constructed. The embedding
    provider/model come from the live :class:`AppConfig` (the editable config),
    not from ``.env``.

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
        "vector_store": "postgres",
        "vector_store_connected": connected,
        "embedding_provider": config.embedding.provider if config else "unknown",
        "embedding_model": config.embedding.model if config else "unknown",
        "embedding_model_loaded": embedding_adapter is not None,
        "uptime_seconds": int(time.time() - _START_TIME),
        "lan_ip": lan_ip(),
    }
