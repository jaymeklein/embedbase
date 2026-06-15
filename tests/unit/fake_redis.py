"""In-memory Redis double for config hot-reload tests (no real server).

Implements just the surface the reload code touches: ``publish`` (returning a
configurable subscriber count and optionally injecting worker acks to simulate a
worker that reloaded), hash ops, ``expire``, and a scripted ``pubsub`` for the
worker listener loop.
"""

from __future__ import annotations

import json
from typing import Any


class FakePubSub:
    """A pubsub that yields a subscribe confirmation then the scripted messages."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.channels: tuple[str, ...] = ()

    def subscribe(self, *channels: str) -> None:
        self.channels = channels

    def listen(self) -> Any:
        yield {"type": "subscribe", "data": 1}
        for data in self._messages:
            yield {"type": "message", "data": data}


class FakeRedis:
    """Minimal in-memory Redis stand-in for the reload primitives."""

    def __init__(
        self,
        *,
        subscribers: int = 0,
        worker_acks: dict[str, str] | None = None,
        messages: list[str] | None = None,
    ) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.published: list[tuple[str, str]] = []
        self.expires: dict[str, int] = {}
        self._subscribers = subscribers
        self._worker_acks = worker_acks or {}
        self._messages = messages or []

    def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, data))
        message = json.loads(data)
        if not message.get("rollback") and self._worker_acks:
            key = f"config:reload:status:{message['version_id']}"
            self.hashes.setdefault(key, {}).update(self._worker_acks)
        return self._subscribers

    def hset(
        self,
        key: str,
        field: str | None = None,
        value: str | None = None,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        bucket = self.hashes.setdefault(key, {})
        if mapping is not None:
            bucket.update({k: str(v) for k, v in mapping.items()})
            return len(mapping)
        bucket[str(field)] = str(value)
        return 1

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self._messages)
