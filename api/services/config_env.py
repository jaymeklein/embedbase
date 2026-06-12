"""Overlay environment variables onto raw config before validation.

The docker-compose overrides (``docker-compose.postgres.yml`` /
``docker-compose.qdrant.yml``) pass the vector-store selection and connection
details as environment variables, and the secret ``POSTGRES_PASSWORD`` is kept
out of ``config.yaml`` on purpose. This helper lets those env vars win over the
(chroma-defaulted) ``vector_store`` section without restructuring the config
model, and is shared by the API and worker config loaders.
"""

from __future__ import annotations

import os
from typing import Any

# (env var name, nested path within the vector_store section).
_VECTOR_STORE_ENV: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("VECTOR_STORE", ("backend",)),
    ("CHROMA_HOST", ("chroma", "host")),
    ("CHROMA_PORT", ("chroma", "port")),
    ("CHROMA_AUTH_TOKEN", ("chroma", "auth_token")),
    ("POSTGRES_HOST", ("pgvector", "host")),
    ("POSTGRES_PORT", ("pgvector", "port")),
    ("POSTGRES_DB", ("pgvector", "database")),
    ("POSTGRES_USER", ("pgvector", "user")),
    ("POSTGRES_PASSWORD", ("pgvector", "password")),
    ("QDRANT_HOST", ("qdrant", "host")),
    ("QDRANT_PORT", ("qdrant", "port")),
)

_INT_KEYS = frozenset({"port"})

# (env var name, nested path within the parsers section).
_PARSER_ENV: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DOCLING_ARTIFACTS_PATH", ("docling_artifacts_path",)),
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
