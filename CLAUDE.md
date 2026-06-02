# EmbedBase — contributor guide

Local-first document embedding/retrieval platform (RAG) exposing a REST API and
an MCP server. Stack: FastAPI (async), Pydantic v2, SQLAlchemy 2.0 async +
Alembic, Celery + Redis, Chroma vector store. The `api` package is shared with
the `worker` package; the API dispatches Celery tasks by name and never imports
the worker.

## Before opening a PR — run the local CI gate

Always run the full local CI pipeline and make sure it passes **before** pushing
a branch or opening a PR. It mirrors `.github/workflows/ci.yml` (lint + type
check, unit tests, Docker build smoke test) so you catch failures locally
instead of on the runner:

```powershell
# Windows / PowerShell
./scripts/ci-local.ps1
```

```bash
# macOS / Linux / Git Bash
./scripts/ci-local.sh
```

- The first run is slow (it builds the Docker images and downloads deps); after
  that, layers and pip downloads are cached locally under `.buildcache/`.
- Fast inner loop while iterating: `./scripts/ci-local.ps1 -SkipDocker`
  (or `--skip-docker` for the bash script) runs only lint + tests.
- Static lint/mypy run on your machine; tests and image builds run inside
  `python:3.12` / buildx so the environment matches CI exactly. Do not rely on
  bare local mypy alone — local Python and missing deps can hide failures that
  CI's 3.12 environment surfaces.

Only open the PR once `ci-local` reports **"All CI stages passed."**

## CI jobs (what the gate reproduces)

1. **Lint + type check** — `ruff check api/ worker/` and
   `mypy api/ worker/ --ignore-missing-imports --explicit-package-bases`.
   Ruff rules: `E, F, I, UP, B, SIM`; mypy has `warn_unused_ignores = true`, so
   prefer dynamic access (e.g. `getattr`) over `# type: ignore` when a stub gap
   only exists in one environment.
2. **Unit tests** — `pytest tests/unit/`.
3. **Docker build smoke test** — builds the `api` (context `./api`) and `worker`
   (context repo root, `-f worker/Dockerfile`) images. CI caches layers via
   `type=gha`; the local script uses `type=local` under `.buildcache/`.
