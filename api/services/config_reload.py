"""Redis-backed coordination for live config hot-reload across processes.

After the API atomically rewrites ``config.yaml`` it publishes the new
``version_id`` to :data:`RELOAD_CHANNEL`. Each worker process subscribes to that
channel (see :mod:`worker.config_reload`), rebuilds its adapters from the new
file, and records a per-process ack in the version's status hash. The API folds
those acks into an overall reload status that the config page polls.

CAP tradeoff: this favours Availability + Partition tolerance over strong
Consistency. ``config.yaml`` (written atomically) is the source of truth; the
pub/sub fan-out is best-effort, so a worker that is down or slow shows up as a
missing ack rather than blocking the apply. Workers converge on the next message
or on restart.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from typing import Any

RELOAD_CHANNEL = "config:reload"
_STATUS_PREFIX = "config:reload:status:"
_STATUS_TTL_SECONDS = 60 * 60  # status records stay pollable for 1h, then expire
_WORKER_FIELD_PREFIX = "worker:"


def status_key(version_id: str) -> str:
    """Return the Redis hash key holding the reload status for ``version_id``."""
    return f"{_STATUS_PREFIX}{version_id}"


def worker_id() -> str:
    """Return a stable per-process worker identity (``hostname:pid``)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def publish_reload(redis_client: Any, version_id: str, *, rollback: bool = False) -> int:
    """Publish a reload notice for ``version_id``; return the subscriber count.

    The count is how many worker processes received the message, i.e. the number
    of acks to expect. ``rollback`` marks a revert republish so workers reload the
    restored file without acking the failed version.
    """
    payload = json.dumps({"version_id": version_id, "rollback": rollback})
    return int(redis_client.publish(RELOAD_CHANNEL, payload))


def init_status(redis_client: Any, version_id: str, expected_workers: int) -> None:
    """Seed the status hash for a freshly published reload (API node = ok)."""
    key = status_key(version_id)
    redis_client.hset(
        key,
        mapping={
            "version_id": version_id,
            "api": "ok",
            "expected_workers": str(expected_workers),
            "applied_at": datetime.now(UTC).isoformat(),
        },
    )
    redis_client.expire(key, _STATUS_TTL_SECONDS)


def record_worker_ack(redis_client: Any, version_id: str, result: str) -> None:
    """Record this worker process's reload result in the version's status hash."""
    field = f"{_WORKER_FIELD_PREFIX}{worker_id()}"
    redis_client.hset(status_key(version_id), field, result)


def mark_rolled_back(redis_client: Any, version_id: str) -> None:
    """Pin ``version_id``'s status to ``rolled_back`` for any later poll."""
    redis_client.hset(status_key(version_id), "final", "rolled_back")


def read_status(redis_client: Any, version_id: str) -> dict[str, Any] | None:
    """Return the aggregated reload status for ``version_id``, or None if absent."""
    raw = redis_client.hgetall(status_key(version_id))
    return _aggregate(raw) if raw else None


def _worker_results(raw: dict[str, str]) -> dict[str, str]:
    """Extract the per-worker ack fields from a raw status hash."""
    return {k: v for k, v in raw.items() if k.startswith(_WORKER_FIELD_PREFIX)}


def _aggregate(raw: dict[str, str]) -> dict[str, Any]:
    """Fold a raw status hash into a per-node view plus an overall status."""
    workers = _worker_results(raw)
    expected = int(raw.get("expected_workers", "0"))
    ok_workers = sum(1 for value in workers.values() if value == "ok")
    has_errors = any(value != "ok" for value in workers.values())
    status = raw.get("final") or _overall(raw.get("api", "ok"), ok_workers, expected, has_errors)
    return {
        "version_id": raw.get("version_id", ""),
        "status": status,
        "api": raw.get("api", "ok"),
        "expected_workers": expected,
        "acked_workers": len(workers),
        "workers": workers,
        "applied_at": raw.get("applied_at", ""),
    }


def _overall(api: str, ok_workers: int, expected: int, has_errors: bool) -> str:
    """Derive the overall reload status from the API node + worker acks."""
    if api != "ok" or has_errors:
        return "error"
    if ok_workers >= expected:
        return "applied"
    return "pending"
