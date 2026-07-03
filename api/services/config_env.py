"""Overlay environment variables onto raw config before validation.

``docker-compose.yml`` passes the Postgres connection details as environment
variables, and the secret ``POSTGRES_PASSWORD`` is kept out of ``config.yaml``
on purpose. This helper lets those env vars win over the ``vector_store``
section without restructuring the config model, and is shared by the API and
worker config loaders.
"""

from __future__ import annotations

import os
from typing import Any

# (env var name, nested path within the vector_store section).
_VECTOR_STORE_ENV: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("POSTGRES_HOST", ("host",)),
    ("POSTGRES_PORT", ("port",)),
    ("POSTGRES_DB", ("database",)),
    ("POSTGRES_USER", ("user",)),
    ("POSTGRES_PASSWORD", ("password",)),
)

_INT_KEYS = frozenset({"port"})

# (env var name, nested path within the parsers section).
_PARSER_ENV: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DOCLING_ARTIFACTS_PATH", ("docling_artifacts_path",)),
)

# (env var suffix after ``S3__<NAME>__``, backend config key). Secrets live here
# so they stay out of config.yaml; the rest let a single .env override an entry.
_S3_BACKEND_KEYS: tuple[tuple[str, str], ...] = (
    ("ACCESS_KEY_ID", "access_key_id"),
    ("SECRET_ACCESS_KEY", "secret_access_key"),
    ("ENDPOINT_URL", "endpoint_url"),
    ("PUBLIC_ENDPOINT_URL", "public_endpoint_url"),
    ("BUCKET", "bucket"),
)


def overlay_vector_store_env(data: dict[str, Any]) -> dict[str, Any]:
    """Overlay vector-store env vars onto the ``vector_store`` section of ``data``.

    Only env vars that are actually set (and non-empty) take effect; every other
    key keeps its config.yaml or model-default value. ``*_PORT`` values are
    coerced to ``int`` so Pydantic validation accepts them.

    Args:
        data: Raw config mapping (e.g. parsed from config.yaml), possibly empty.

    Returns:
        The same mapping, mutated in place with the overrides applied.
    """
    vector_store = data.setdefault("vector_store", {})
    if not isinstance(vector_store, dict):
        return data
    for env_name, path in _VECTOR_STORE_ENV:
        value = os.environ.get(env_name)
        if value:
            _set_nested(vector_store, path, value)
    return data


def overlay_parser_env(data: dict[str, Any]) -> dict[str, Any]:
    """Overlay parser env vars onto the ``parsers`` section of ``data``.

    Mirrors :func:`overlay_vector_store_env`: only env vars that are set (and
    non-empty) take effect, so the docling models/artifacts directory can be
    pinned from ``.env`` without editing ``config.yaml``.

    Args:
        data: Raw config mapping (e.g. parsed from config.yaml), possibly empty.

    Returns:
        The same mapping, mutated in place with the overrides applied.
    """
    parsers = data.setdefault("parsers", {})
    if not isinstance(parsers, dict):
        return data
    for env_name, path in _PARSER_ENV:
        value = os.environ.get(env_name)
        if value:
            _set_nested(parsers, path, value)
    return data


def overlay_storage_env(data: dict[str, Any]) -> dict[str, Any]:
    """Overlay storage env vars onto the ``storage`` section of ``data``.

    ``STORAGE_DEFAULT`` selects the active backend. For each ``type: s3`` entry
    already declared under ``storage.backends`` (named ``<NAME>``), the
    per-instance vars ``S3__<UPPERNAME>__{ACCESS_KEY_ID,SECRET_ACCESS_KEY,
    ENDPOINT_URL,PUBLIC_ENDPOINT_URL,BUCKET}`` overlay that entry — keeping S3
    secrets out of config.yaml. Only env vars that are set (and non-empty) win.

    Name a backend with env-safe characters (``[A-Za-z0-9_]``): a ``-``/``.`` in
    the name yields an override var like ``S3__MY-S3__…`` that POSIX shells and
    docker-compose can't set, so the overlay silently wouldn't apply.

    Args:
        data: Raw config mapping (e.g. parsed from config.yaml), possibly empty.

    Returns:
        The same mapping, mutated in place with the overrides applied.
    """
    storage = data.setdefault("storage", {})
    if not isinstance(storage, dict):
        return data

    default = os.environ.get("STORAGE_DEFAULT")
    if default:
        storage["default"] = default

    backends = storage.get("backends")
    if isinstance(backends, dict):
        for name, backend in backends.items():
            if not (isinstance(backend, dict) and backend.get("type") == "s3"):
                continue
            prefix = f"S3__{name.upper()}__"
            for suffix, key in _S3_BACKEND_KEYS:
                value = os.environ.get(prefix + suffix)
                if value:
                    backend[key] = value
    return data


def _set_nested(target: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    """Set ``value`` at the nested ``path`` in ``target``, coercing ports to int."""
    cursor = target
    for key in path[:-1]:
        child = cursor.setdefault(key, {})
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    leaf = path[-1]
    cursor[leaf] = int(value) if leaf in _INT_KEYS else value
