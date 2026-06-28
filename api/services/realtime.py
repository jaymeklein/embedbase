"""Generic Redis-backed realtime channels: publish events for a topic, bridge them
to browser WebSockets (see :mod:`api.routers.ws`).

A *topic* is an opaque string (e.g. ``"ingestion:{collection_id}"``). Each event is
a JSON object published to the Redis channel ``rt:{topic}`` for live fan-out, and --
when a ``snapshot_key`` is given -- also written into a per-topic hash ``rt:{topic}``
so a client that connects late (or refreshes) is replayed the latest state per key
before the live stream resumes. The hash expires after :data:`RT_TTL_SECONDS` so
dead topics don't accumulate.

The same string ``rt:{topic}`` serves as both the pub/sub channel and the snapshot
hash key -- Redis pub/sub channel names live in a separate namespace from the
keyspace, so they never collide.

Transport-only: this module carries no domain logic. Ingestion progress is the
first consumer, but any feature can publish to its own topic and subscribe via the
generic ``/ws`` endpoint. Mirrors the sync-publish style of
:mod:`api.services.config_reload`.
"""

from __future__ import annotations

import json
from typing import Any

_CHANNEL_PREFIX = "rt:"
RT_TTL_SECONDS = 60 * 60  # snapshot hashes stay readable for 1h, then expire


def channel(topic: str) -> str:
    """Return the Redis channel (and snapshot-hash key) for ``topic``."""
    return f"{_CHANNEL_PREFIX}{topic}"


def publish(
    redis_client: Any,
    topic: str,
    payload: dict[str, Any],
    *,
    snapshot_key: str | None = None,
) -> int:
    """Publish ``payload`` to ``topic``; return the subscriber count.

    Always fans the JSON-encoded payload out on the topic's channel for live
    subscribers. When ``snapshot_key`` is given, also stores the payload in the
    topic's snapshot hash under that field (with a refreshed TTL) so late joiners
    can be replayed the latest state per key on connect.
    """
    data = json.dumps(payload)
    key = channel(topic)
    if snapshot_key is not None:
        redis_client.hset(key, snapshot_key, data)
        redis_client.expire(key, RT_TTL_SECONDS)
    return int(redis_client.publish(key, data))


def read_snapshot(redis_client: Any, topic: str) -> dict[str, str]:
    """Return the latest payload (JSON str) per snapshot_key for ``topic``.

    Empty dict when the topic has no snapshot hash (nothing published yet, or it
    expired). Used by the WebSocket bridge to prime a freshly connected client.
    """
    return redis_client.hgetall(channel(topic)) or {}
