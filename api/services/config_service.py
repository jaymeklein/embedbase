"""Config read/apply service backing the editable config page.

``GET`` returns the live :class:`AppConfig` with write-only secrets masked.
``PUT`` validates the incoming config, *builds the new adapters* (the dry run —
for sentence-transformers this loads the model, so a bad model name or an
unreachable backend fails here, before anything is persisted), atomically
rewrites ``config.yaml``, then swaps the API's live adapters. Build-then-commit:
``config.yaml`` is only touched once the new adapters construct successfully.

Worker propagation (Phase 3): after the API swaps its own adapters it publishes
the new version on a Redis channel, waits briefly for each worker process to
rebuild and ack, and *rolls back* (restores the ``.bak``, reverts the API
adapters, republishes the previous file) if a worker rejects the config. When no
Redis client is configured (e.g. single-process/dev), it falls back to an
API-local status record.
"""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from api.adapters.embeddings import get_embedding_adapter as resolve_embedding
from api.adapters.vector_store import get_vector_store as resolve_store
from api.dependencies import (
    get_app_config,
    get_embedding_adapter,
    get_redis_client,
    get_vector_store,
    set_app_config,
    set_embedding_adapter,
    set_vector_store,
)
from api.models.config import AppConfig
from api.services.config_reload import (
    init_status,
    mark_rolled_back,
    publish_reload,
    read_status,
)

# A point-in-time snapshot of the API's live adapters + config, captured before a
# PUT so a worker rejection can be rolled back.
_Snapshot = tuple[Any, Any, AppConfig]

# Write-only secret fields: never returned by GET, and preserved on PUT when the
# client echoes the mask sentinel back unchanged.
SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("embedding", "api_key"),
    ("vector_store", "chroma", "auth_token"),
    ("vector_store", "pgvector", "password"),
    ("tagging", "suggester", "api_key"),
)
SECRET_MASK = "__SECRET_SET__"

_CONFIG_PATHS = (Path("/app/config.yaml"), Path("config.yaml"))

# Fallback reload records when no Redis client is configured (single-process/dev).
# With Redis, status lives in the version's status hash (see config_reload).
_reload_status: dict[str, dict[str, Any]] = {}

# How long ``apply_config`` waits for worker acks before returning ``pending`` and
# letting the client poll. Kept short so the PUT stays responsive: a worker that
# *rejects* the config fails fast (caught here -> rollback), while a worker still
# loading a model simply shows as a missing ack and is reported via reload-status.
_ACK_WAIT_SECONDS = 10.0
_ACK_POLL_SECONDS = 0.2


def _config_path() -> Path:
    """Return the config.yaml path to read/write (matches main._load_app_config)."""
    for path in _CONFIG_PATHS:
        if path.exists():
            return path
    return _CONFIG_PATHS[-1]


def _get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Return the value at ``path`` in ``data``, or None if any key is absent."""
    cursor: Any = data
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return None
        cursor = cursor[key]
    return cursor


def _set_nested(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Set ``value`` at the nested ``path`` in ``data``, creating dicts as needed."""
    cursor = data
    for key in path[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[path[-1]] = value


def _require_config() -> AppConfig:
    """Return the live config or raise 503 if startup has not completed."""
    config = get_app_config()
    if config is None:
        raise HTTPException(503, "Config not loaded yet")
    return config


def get_masked_config() -> dict[str, Any]:
    """Return the live config with every secret masked (``SECRET_MASK`` / "")."""
    data = _require_config().model_dump()
    for path in SECRET_PATHS:
        _set_nested(data, path, SECRET_MASK if _get_nested(data, path) else "")
    return data


def _merge_secrets(incoming: dict[str, Any], current: AppConfig) -> dict[str, Any]:
    """Restore each secret the client left masked from the current live config."""
    current_data = current.model_dump()
    merged = deepcopy(incoming)
    for path in SECRET_PATHS:
        if _get_nested(merged, path) == SECRET_MASK:
            _set_nested(merged, path, _get_nested(current_data, path))
    return merged


def _build_adapters(config: AppConfig) -> tuple[Any, Any]:
    """Build the embedding + vector-store adapters (the PUT dry run).

    Eager construction means a bad model name or unreachable backend raises here,
    before ``config.yaml`` is touched.
    """
    embed = resolve_embedding(config.embedding)
    store = resolve_store(config.vector_store, embed.dimensions)
    return embed, store


def _atomic_write(data: dict[str, Any], path: Path) -> None:
    """Persist ``data`` as YAML atomically (.bak backup -> .tmp write -> rename).

    Falls back to an in-place write when the rename fails — a bind-mounted single
    file (Docker on Windows) is a mount point that cannot be renamed over (EBUSY),
    but can still be written through. The ``.bak`` backup is taken first either way.
    """
    content = yaml.safe_dump(data, sort_keys=False)
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        # ponytail: bind-mounted file can't be renamed over; write in place instead.
        path.write_text(content, encoding="utf-8")
        tmp.unlink(missing_ok=True)


def _record_applied(version_id: str) -> dict[str, Any]:
    """Record and return the status for a successfully applied config version."""
    status = {
        "version_id": version_id,
        "status": "applied",
        "api": "ok",
        "applied_at": datetime.now(UTC).isoformat(),
    }
    _reload_status[version_id] = status
    return status


def _validate_and_build(merged: dict[str, Any]) -> tuple[Any, Any, AppConfig]:
    """Validate the merged config and build its adapters; raise 422 on failure."""
    try:
        new_config = AppConfig.model_validate(merged)
        embed, store = _build_adapters(new_config)
    except HTTPException:
        raise
    except Exception as exc:  # invalid config or unbuildable adapter
        raise HTTPException(422, f"Invalid configuration: {exc}") from exc
    return embed, store, new_config


def _swap_live(embed: Any, store: Any, config: AppConfig) -> None:
    """Atomically point the API's live singletons at a new adapter set + config."""
    set_embedding_adapter(embed)
    set_vector_store(store)
    set_app_config(config)


def _restore_backup(path: Path) -> None:
    """Restore ``config.yaml`` from its ``.bak`` sibling, if one exists."""
    backup = path.with_suffix(path.suffix + ".bak")
    if backup.exists():
        path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")


def apply_config(payload: AppConfig) -> dict[str, Any]:
    """Validate, build adapters, persist, hot-swap the API, and fan out to workers.

    Args:
        payload: The desired config; secret fields left as ``SECRET_MASK`` keep
            their current values.

    Returns:
        The reload status (``version_id`` for polling; ``status`` is ``applied``,
        ``pending`` while workers finish, or the single-process record).

    Raises:
        HTTPException: 503 if the live config is unavailable; 422 if the merged
            config is invalid or its adapters cannot be built; 409 if a worker
            rejects the config (the change is rolled back).
    """
    current = _require_config()
    previous: _Snapshot = (get_embedding_adapter(), get_vector_store(), current)
    merged = _merge_secrets(payload.model_dump(), current)
    embed, store, new_config = _validate_and_build(merged)
    path = _config_path()
    _atomic_write(merged, path)
    _swap_live(embed, store, new_config)
    return _propagate(uuid.uuid4().hex[:12], previous, path)


def _propagate(version_id: str, previous: _Snapshot, path: Path) -> dict[str, Any]:
    """Notify workers of the new config and wait/rollback; record the status."""
    redis_client = get_redis_client()
    if redis_client is None:
        return _record_applied(version_id)  # single-process fallback
    expected = publish_reload(redis_client, version_id)
    init_status(redis_client, version_id, expected)
    if expected:
        _await_or_rollback(redis_client, version_id, expected, previous, path)
    status = read_status(redis_client, version_id)
    return status if status is not None else _record_applied(version_id)


def _await_or_rollback(
    redis_client: Any, version_id: str, expected: int, previous: _Snapshot, path: Path
) -> None:
    """Wait up to the ack window; roll back on a worker error, else return.

    Returns once every worker acks ``ok`` (applied) or the window elapses with
    acks still missing (``pending`` — the client polls reload-status). A worker
    error ack triggers an immediate rollback and a 409.
    """
    deadline = time.monotonic() + _ACK_WAIT_SECONDS
    while True:
        status = read_status(redis_client, version_id) or {}
        if status.get("status") == "error":
            _rollback(redis_client, version_id, previous, path)
            raise HTTPException(409, f"Config rejected by a worker; rolled back: {status['workers']}")
        if status.get("status") == "applied" or time.monotonic() >= deadline:
            return
        time.sleep(_ACK_POLL_SECONDS)


def _rollback(redis_client: Any, version_id: str, previous: _Snapshot, path: Path) -> None:
    """Restore the previous config file + API adapters and tell workers to revert."""
    prev_embed, prev_store, prev_config = previous
    _restore_backup(path)
    _swap_live(prev_embed, prev_store, prev_config)
    publish_reload(redis_client, version_id, rollback=True)
    mark_rolled_back(redis_client, version_id)


def list_ollama_models(base_url: str | None = None) -> list[str]:
    """List models installed on the Ollama server for the config UI's model picker.

    Uses ``base_url`` when given (the value the user is editing), else the live
    config's suggester base URL. Raises 502 if Ollama cannot be reached.
    """
    from api.adapters.tagging.llm import list_ollama_models as _list

    resolved = base_url or _require_config().tagging.suggester.base_url
    try:
        return _list(resolved)
    except Exception as exc:  # unreachable server / bad response
        raise HTTPException(502, f"Could not reach Ollama: {exc}") from exc


def get_reload_status(version_id: str) -> dict[str, Any]:
    """Return the reload status for ``version_id`` (Redis first), or raise 404."""
    redis_client = get_redis_client()
    if redis_client is not None:
        status = read_status(redis_client, version_id)
        if status is not None:
            return status
    status = _reload_status.get(version_id)
    if status is None:
        raise HTTPException(404, f"Unknown config version {version_id!r}")
    return status
