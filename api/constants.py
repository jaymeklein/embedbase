"""Canonical default values — single source of truth for infrastructure ports.

Import these in ``settings.py`` and ``models/config.py`` so a default change
requires one edit.  Do not repeat these literals elsewhere in the codebase.
"""

# Internal Docker-network ports (container-to-container; no host conflict risk)
POSTGRES_PORT: int = 5432
REDIS_PORT: int = 6379

# Derived full Redis URL (avoids repeating the port literal in URL strings)
REDIS_URL: str = f"redis://redis:{REDIS_PORT}/0"

# Host-mapped ports (docker-compose ``ports:`` — can conflict with host processes)
# Correspond to the API_PORT and UI_PORT env vars consumed by docker-compose.yml.
API_HOST_PORT: int = 8000
UI_HOST_PORT: int = 3000

# Cross-encoder reranker vendored into the api image at build time. The env var + its default
# locate the baked model dir (read by api/adapters/reranker/cross_encoder.py); RERANKER_MODEL is
# the default model id (RerankerConfig.model). The standalone build script
# api/scripts/fetch_reranker_model.py mirrors all three literals because it runs before the api/
# package is copied (see api/Dockerfile) and so cannot import this module — keep them in sync.
MODELS_DIR_ENV: str = "EMBEDBASE_MODELS_DIR"
MODELS_DIR_DEFAULT: str = "/opt/models"
RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L6-v2"

# Native Google Generative Language (Gemini) REST host. Shared by the embeddings Gemini adapter
# and the LLM chat transport — both hit ``{host}/v1beta/models/{model}:<verb>`` with an
# ``x-goog-api-key`` header, so the host lives in one place.
GEMINI_API_BASE_URL: str = "https://generativelanguage.googleapis.com"

# A2 adjacency expansion: soft cap on an assembled span's text (farthest neighbours dropped
# first). The SearchConfig field default and the multi_collection_search / search_documents
# parameter fallbacks all read this one value so a retune stays a single edit.
DEFAULT_EXPAND_CHAR_BUDGET: int = 8000

# Hard ceiling on results-per-search. ``SearchRequest.top_k`` is bound by this (``le``) and the MCP
# search tool clamps ``top_k``/``max_results`` to it — one source so the model bound and the clamp
# can't drift. (They did: a ``mcp.max_results`` set above the bound overflowed it into a 500.)
MAX_TOP_K: int = 20
