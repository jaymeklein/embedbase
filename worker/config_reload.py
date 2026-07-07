"""Per-process config hot-reload listener for Celery workers.

Each worker child process subscribes to the config reload channel and, on a new
version, clears the cached config, rebuilds its embedding + vector-store adapters
from the freshly-written ``config.yaml`` (bind-mounted, so the API's atomic write
is visible here), and records a per-process ack the API aggregates.

CAP tradeoff: availability over consistency — a reload failure is recorded as an
error ack (which triggers the API-side rollback) rather than crashing the worker,
so already-queued ingestion keeps running on the previous adapters.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

import structlog
from celery.signals import worker_process_init

from api.constants import REDIS_URL as _REDIS_URL_DEFAULT
from api.services.config_reload import RELOAD_CHANNEL, record_worker_ack

logger = structlog.get_logger()


def _redis_client() -> Any:
    """Open a dedicated decode-on Redis connection for the subscriber loop."""
    import redis

    url = os.environ.get("REDIS_URL", _REDIS_URL_DEFAULT)
    return redis.Redis.from_url(url, decode_responses=True)


def _reload_adapters() -> bool:
    """Clear the cached config and rebuild this worker's adapters from the file.

    Returns True if the *embedding* config changed (e.g. a rotated API key or a
    raised ``max_rpm``). Such a change resets the provider quota, so the caller can
    immediately resume any ingest paused on a rate limit instead of leaving it to
    wait for its retry countdown or the periodic beat sweep.
    """
    from worker import tasks
    from worker.config import get_config

    before = get_config().embedding  # snapshot before the cache is cleared
    get_config.cache_clear()
    tasks.reload_adapters()
    return get_config().embedding != before


def _resume_rate_limited() -> None:
    """Re-enqueue rate-limited ingests now that a new key / limit reset the quota.

    Runs after the reload ack so it never delays the API's apply, and is best-effort:
    the beat sweep still backstops it. ``_claim_job`` keeps it idempotent against a
    job whose own retry countdown fires around the same time.
    """
    try:
        from worker import tasks

        # respect_pending=False: a new key / higher RPM reset the quota, so resume every
        # paused job now rather than waiting out its (now-stale) countdown marker.
        count = tasks.requeue_rate_limited(respect_pending=False)
        logger.info("embedding config changed; resumed rate-limited ingests", count=count)
    except Exception as exc:  # pragma: no cover - best-effort
        logger.error("could not resume rate-limited ingests", error=str(exc))


def _safe_reload() -> None:
    """Best-effort reload used on rollback republish (no ack, never raises)."""
    try:
        _reload_adapters()  # revert only; a rollback must not trigger a resume
    except Exception as exc:  # pragma: no cover - best-effort revert
        logger.error("config rollback reload failed", error=str(exc))


def _handle_message(redis_client: Any, data: str) -> None:
    """Apply one reload message and record this process's ack."""
    message = json.loads(data)
    version_id = message.get("version_id", "")
    if message.get("rollback"):
        _safe_reload()  # revert to the restored file; the failed version is not re-acked
        return
    try:
        embedding_changed = _reload_adapters()
        record_worker_ack(redis_client, version_id, "ok")
    except Exception as exc:
        logger.error("config reload failed", version_id=version_id, error=str(exc))
        record_worker_ack(redis_client, version_id, f"error: {exc}"[:200])
        return
    if embedding_changed:
        _resume_rate_limited()  # new key / higher RPM → continue paused ingests at once


def _listen(redis_client: Any) -> None:
    """Block on the reload channel, dispatching each message to ``_handle_message``."""
    pubsub = redis_client.pubsub()
    pubsub.subscribe(RELOAD_CHANNEL)
    logger.info("config reload listener subscribed", channel=RELOAD_CHANNEL)
    for message in pubsub.listen():
        if message.get("type") != "message":
            continue
        _handle_message(redis_client, message["data"])


def start_listener() -> threading.Thread:
    """Start the daemon subscriber thread for this worker process."""
    thread = threading.Thread(
        target=_listen, args=(_redis_client(),), name="config-reload", daemon=True
    )
    thread.start()
    return thread


def _clear_stale_heartbeats() -> None:
    """Drop leftover ingestion heartbeats on worker boot.

    A fresh worker has no task running yet, so any ``ingest:hb:*`` key is from a
    process that died (a reload/crash that skipped the task's ``finally`` cleanup).
    Clearing them lets a redelivered ingest task reclaim the job instead of seeing a
    stale 'alive' heartbeat and skipping — which would orphan the job.

    ponytail: assumes one worker (concurrency=1, the deployment). With multiple
    workers this would wipe peers' live heartbeats; gate on a worker-id then.
    """
    try:
        client = _redis_client()
        keys = list(client.scan_iter(match="ingest:hb:*"))
        if keys:
            client.delete(*keys)
            logger.info("cleared stale ingestion heartbeats", count=len(keys))
    except Exception as exc:  # pragma: no cover - best-effort
        logger.error("could not clear stale heartbeats", error=str(exc))


@worker_process_init.connect
def _on_worker_process_init(**_: Any) -> None:
    """Celery hook: per forked worker process — clear dead heartbeats, then start the
    config-reload listener."""
    _clear_stale_heartbeats()
    try:
        start_listener()
    except Exception as exc:  # pragma: no cover - never block worker startup
        logger.error("could not start config reload listener", error=str(exc))
