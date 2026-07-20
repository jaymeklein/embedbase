---
paths:
  - "api/models/config.py"
  - "api/services/config_service.py"
  - "api/services/config_env.py"
  - "api/services/config_reload.py"
  - "worker/config.py"
  - "worker/config_reload.py"
  - "config.yaml"
  - "config.example.yaml"
  - ".env"
  - ".env.example"
---

# Configuration

Config is **pydantic models** — `AppConfig` and its sub-configs in `api/models/config.py`
(`embedding`, `vector_store`, `reranker`, `parsers`, `chunking`, `storage`, `mcp`, `search`, …).
`config.yaml` and `.env` are **gitignored — never commit secrets.** The read/apply service is
`api/services/config_service.py`; it backs the editable settings page.

## Secrets — mask on GET, preserve on PUT
- Write-only secret fields are listed in **`SECRET_PATHS`** in `config_service.py` and masked with
  **`SECRET_MASK` (`"__SECRET_SET__"`)** by `get_masked_config()`. Currently: `embedding.api_key`,
  `vector_store.password`, `reranker.api_key`.
- **Adding a new secret field?** Add its path to `SECRET_PATHS` (or, for user-named backends, extend
  `_storage_secret_paths()` — S3 `access_key_id` / `secret_access_key` are computed per-config because
  backend names are dynamic). Otherwise a whole-config save from the UI round-trips it in the clear.
- On PUT, `_merge_secrets()` restores any field the client echoed back as `SECRET_MASK` from the live config.

## The save flow — build-then-commit, then fan out
`apply_config()`:
1. **Merge secrets** → **validate + dry-run build the adapters** (`_validate_and_build`). Building is the
   dry run: a bad embedding model / unreachable backend fails **here (422)** before anything is persisted.
   A rate-limited dimension probe → **503** (retry), not a rejection; the reranker degrades to `None` if
   its model can't load (an unknown *provider* still raises).
2. **Persist** `config.yaml` **in place** (not rename-swap — a rename splits Docker/WSL bind-mount views),
   fsynced, with a `.bak` taken only when the current file is non-empty **and** parses (so a crash-truncated
   file can't clobber the last-good backup — the boot recovery source in `config_env.load_config_data`).
3. **Hot-swap** the API's live singletons (`_swap_live`), then **propagate to workers** over Redis and wait
   for acks; a worker rejection triggers **rollback** (restore `.bak`, revert adapters, republish) and **409**.
   No Redis (single-process/dev) → an API-local status record.

## Gotchas
- The config load path is `api/main.py::_load_app_config` with `.bak` crash recovery + env overlays
  (`api/services/config_env.py`, e.g. `S3__<NAME>__*`). Startup sets `app.state.config` + `set_app_config`.
- Worker-side reload lives in `worker/config_reload.py` / `worker/tasks.py::reload_adapters` (rebuilds the
  worker's embedder/vector-store singletons on a new version).
- Adapters are built from config via the `get_*()` factories — see [`architecture.md`](architecture.md).
