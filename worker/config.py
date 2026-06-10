"""Lazily-loaded application config for the worker process."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from api.models.config import AppConfig
from api.services.config_env import overlay_vector_store_env


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Load ``config.yaml`` once (``/app/config.yaml`` then CWD), else defaults.

    Vector-store environment variables (e.g. from ``docker-compose.postgres.yml``)
    are overlaid on top of the file so the backend and secrets can be selected
    without editing ``config.yaml``.
    """
    data: dict = {}
    for candidate in (Path("/app/config.yaml"), Path("config.yaml")):
        if candidate.exists():
            with open(candidate) as fh:
                data = yaml.safe_load(fh) or {}
            break
    return AppConfig.model_validate(overlay_vector_store_env(data))
