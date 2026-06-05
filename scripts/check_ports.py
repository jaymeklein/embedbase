#!/usr/bin/env python3
"""Detect host port conflicts before running docker compose up.

Reads API_PORT and UI_PORT from .env (falling back to the coded defaults in
api/constants.py) and checks whether those ports are available on the host.

Usage::

    python scripts/check_ports.py          # report conflicts
    python scripts/check_ports.py --fix    # write free alternatives to .env

Exit codes: 0 = all ports available (or fixed), 1 = conflicts remain.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Make the repo root importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.constants import API_HOST_PORT, UI_HOST_PORT
from api.port_utils import find_free_port, is_port_free

logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger(__name__)

# env-var name → default port (from the single source of truth)
_HOST_PORTS: dict[str, int] = {
    "API_PORT": API_HOST_PORT,
    "UI_PORT": UI_HOST_PORT,
}


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _write_env_updates(path: Path, updates: dict[str, int]) -> None:
    lines: list[str] = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(fix: bool = False) -> int:
    """Run port conflict detection and optionally write fixes to .env.

    Args:
        fix: When True, write free port assignments back to .env.

    Returns:
        0 when all ports are free (or successfully reassigned); 1 otherwise.
    """
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    existing = _load_env(env_file)

    conflicts: dict[str, tuple[int, int]] = {}
    for var, default in _HOST_PORTS.items():
        configured = int(existing.get(var, default))
        if not is_port_free(configured):
            conflicts[var] = (configured, find_free_port(configured))

    if not conflicts:
        _log.info("All configured ports are available.")
        return 0

    _log.info("Port conflicts detected:")
    for var, (used, free) in conflicts.items():
        _log.info("  %s=%d is in use  →  suggested free port: %d", var, used, free)

    if fix:
        updates = {var: free for var, (_, free) in conflicts.items()}
        _write_env_updates(env_file, updates)
        _log.info("Updated %s with free port assignments.", env_file)
        return 0

    _log.info("\nRun with --fix to auto-assign free ports in .env")
    return 1


if __name__ == "__main__":
    sys.exit(main(fix="--fix" in sys.argv))
