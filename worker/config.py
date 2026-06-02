"""Lazily-loaded application config for the worker process."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from api.models.config import AppConfig


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Load ``config.yaml`` once (``/app/config.yaml`` then CWD), else defaults."""
    for candidate in (Path("/app/config.yaml"), Path("config.yaml")):
        if candidate.exists():
            with open(candidate) as fh:
                data = yaml.safe_load(fh) or {}
            return AppConfig.model_validate(data)
    return AppConfig()
