"""Canonical default values — single source of truth for infrastructure ports.

Import these in ``settings.py`` and ``models/config.py`` so a default change
requires one edit.  Do not repeat these literals elsewhere in the codebase.
"""

# Internal Docker-network ports (container-to-container; no host conflict risk)
CHROMA_PORT: int = 8000
POSTGRES_PORT: int = 5432
QDRANT_PORT: int = 6333
REDIS_PORT: int = 6379

# Derived full Redis URL (avoids repeating the port literal in URL strings)
REDIS_URL: str = f"redis://redis:{REDIS_PORT}/0"

# Host-mapped ports (docker-compose ``ports:`` — can conflict with host processes)
# Correspond to the API_PORT and UI_PORT env vars consumed by docker-compose.yml.
API_HOST_PORT: int = 8000
UI_HOST_PORT: int = 3000
