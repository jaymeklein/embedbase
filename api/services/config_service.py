"""Config read/apply service backing the editable config page.

``GET`` returns the live :class:`AppConfig` with write-only secrets masked.
``PUT`` validates the incoming config, *builds the new adapters* (the dry run —
for sentence-transformers this loads the model, so a bad model name or an
unreachable backend fails here, before anything is persisted), atomically
rewrites ``config.yaml``, then swaps the API's live adapters. Build-then-commit:
``config.yaml`` is only touched once the new adapters construct successfully.

Worker propagation (Redis pub/sub + per-worker acks) lands in Phase 3; for now a
successful ``PUT`` reloads the API process only and records an API-local status.
"""

from __future__ import annotations

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
    set_app_config,
    set_embedding_adapter,
    set_vector_store,
)
from api.models.config import AppConfig

# Write-only secret fields: never returned by GET, and preserved on PUT when the
# client echoes the mask sentinel back unchanged.
SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("embedding", "api_key"),
    ("vector_store", "chroma", "auth_token"),
    ("vector_store", "pgvector", "password"),
)
SECRET_MASK = "__SECRET_SET__"

_CONFIG_PATHS = (Path("/app/config.yaml"), Path("config.yaml"))

# Phase 2: API-local reload records. Phase 3 moves this to Redis with worker acks.
_reload_status: dict[str, dict[str, Any]] = {}


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
    """Persist ``data`` as YAML atomically (.bak backup -> .tmp write -> rename)."""
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


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


def apply_config(payload: AppConfig) -> dict[str, Any]:
    """Validate, build adapters, persist, and hot-swap the API's live adapters.

    Args:
        payload: The desired config; secret fields left as ``SECRET_MASK`` keep
            their current values.

    Returns:
        The applied-version status (``version_id`` for reload polling).

    Raises:
        HTTPException: 503 if the live config is unavailable; 422 if the merged
            config is invalid or its adapters cannot be built.
    """
    current = _require_config()
    merged = _merge_secrets(payload.model_dump(), current)
    try:
        new_config = AppConfig.model_validate(merged)
        embed, store = _build_adapters(new_config)
    except HTTPException:
        raise
    except Exception as exc:  # invalid config or unbuildable adapter
        raise HTTPException(422, f"Invalid configuration: {exc}") from exc
    _atomic_write(merged, _config_path())
    set_embedding_adapter(embed)
    set_vector_store(store)
    set_app_config(new_config)
    return _record_applied(uuid.uuid4().hex[:12])


def get_reload_status(version_id: str) -> dict[str, Any]:
    """Return the recorded status for ``version_id`` or raise 404."""
    status = _reload_status.get(version_id)
    if status is None:
        raise HTTPException(404, f"Unknown config version {version_id!r}")
    return status
