# Pluggable object storage (local / S3 / MinIO) for uploaded files

## Context

Today every uploaded file is written to a shared Docker volume at
`/data/{collection_id}/{doc_id}{ext}` and read back by path:

- Write: `stream_upload_with_size_guard` → `ingest()` (`api/services/upload.py:29`, `api/services/documents.py:95`)
- Worker read: `parser.parse(file_path, ...)` (`worker/tasks.py:357`)
- Download: `FileResponse(path)` via `get_document_file` (`api/services/documents.py:239`, `api/routers/documents.py:100`)

This couples api + worker to a shared filesystem and leaves nowhere to plug
external storage. Goal: make storage **pluggable and scalable** — keep local
disk as the default, allow one *or several* S3-compatible targets (a
self-hosted MinIO, AWS S3, any S3 API), all selected through config.

**Decisions (confirmed with user):**
- **`local` disk is the default backend.** Out of the box nothing changes —
  no S3, no MinIO. (`api/services/upload.py` behavior preserved.)
- **Pluggable registry of named backends.** Config defines any number of
  targets; one is the active default for new uploads. It must be possible to
  configure **multiple S3 instances** at once.
- Each document records **which backend holds it**, so adding/switching targets
  never orphans existing files and several S3 instances can coexist.
- **MinIO is opt-in only** — shipped as a separate compose override
  (`docker-compose.minio.yml`), never built or run by the default stack
  (mirrors `docker-compose.postgres.yml` / `docker-compose.qdrant.yml`).
- Downloads from an S3 backend use a **presigned-URL redirect**; local stays `FileResponse`.
- **No migration** of files already on `/data` (they resolve to the `local` backend).
- `boto3` is the client — one client type covers AWS S3 and MinIO via `endpoint_url`.

### Config shape (named registry)

```yaml
storage:
  default: local            # named backend new uploads go to (default = disk)
  backends:
    local:
      type: local           # uses settings.upload_dir; always available
    # ---- everything below is opt-in; absent by default ----
    minio:
      type: s3
      endpoint_url: http://minio:9000
      public_endpoint_url: http://localhost:9000   # host the browser reaches, used to sign presigned URLs
      bucket: embedbase
      region: us-east-1
      use_path_style: true
      # access_key_id / secret_access_key come from .env (see env overlay)
    aws:
      type: s3
      bucket: my-prod-bucket
      region: eu-west-1
```

To use MinIO/S3 the user adds an entry under `backends:` and sets
`default:` (or leaves `default: local` and switches later). Defining several
`type: s3` entries satisfies "plug multiple S3 instances."

## Delivery model

Three independently shippable PRs. Each is mergeable on its own — the default
backend stays `local`, so behavior is unchanged until a user opts in. **Every PR
is TDD** (write the failing `test_*.py` first) and ends with the gating sequence
below + `/code-review` + `/helga-review`.

### Definition of done — applies to every PR
1. Tests written **before** code (TDD), new code ≥85% line coverage (`pytest --cov`).
2. `ruff check api/ worker/ && mypy api/ worker/ --ignore-missing-imports --explicit-package-bases`
3. `pytest tests/unit/`
4. `ruff check --select=C901 api/ worker/` (warnings = errors)
5. If a Dockerfile changed → `docker build` api + worker.
6. `./scripts/ci-local.sh` passes.
7. Run **`/code-review`** on the branch diff; resolve findings.
8. Run **`/helga-review`** (CLAUDE.md §6) as the final step; resolve defects.
9. Context7 (CLAUDE.md §4): verify every boto3 symbol used against the pinned
   version's docs; if Context7 is down, add `# verified against boto3==<v>`.

---

## PR 1 — Storage registry + config (no wiring)

**Outcome:** a tested `storage` abstraction resolving any number of named
backends (local + N×s3), selectable via config. Nothing calls it yet. Safe
no-op merge; default `local`.

**TDD — write first:**
- `tests/unit/test_storage.py`
  - Registry: `get_storage()` returns the `default` backend; `get_storage("aws")`
    returns the named one; unknown name → clear error.
  - Local backend: `put_path`/`fetch_to_temp`/`delete` round-trip; `put_upload`
    rejects oversize (size guard); `presigned_get` → `None`.
  - S3 backend under `moto`: oversize rejected before reaching S3; put→fetch
    bytes match; `presigned_get` host == `public_endpoint_url`; `delete` removes
    the object; bucket auto-created on first use.
  - **Two s3 instances**: configure `minio` + `aws`, assert each writes/reads
    against its own bucket/endpoint independently.
- `tests/unit/test_config_env.py`: per-instance secret overlay (below) maps env
  onto the right named backend; unset keys keep defaults.

**Implement:**
- `api/services/storage.py` (new). Key-addressed interface, only what callers need:
  ```python
  def put_upload(file: UploadFile, key: str, *, max_bytes: int | None) -> int
  def put_path(src: Path, key: str) -> None
  def fetch_to_temp(key: str) -> Path
  def cleanup_temp(path: Path) -> None      # no-op for local
  def delete(key: str) -> None
  def presigned_get(key: str, filename: str) -> str | None   # None for local
  def local_path(key: str) -> Path | None                    # FileResponse source
  ```
  `get_storage(name: str | None = None)` = factory resolving `name` (or
  `storage.default`) from `AppConfig.storage.backends`, `lru_cache`'d per name
  (mirrors `get_vector_store`). S3 backend on first use: `head_bucket` →
  `create_bucket` on 404. S3 `put_upload` streams to a `NamedTemporaryFile` via
  the existing `stream_upload_with_size_guard` (keeps the proven size guard),
  then `upload_file`, then unlink. boto3 client built per backend with
  `Config(s3={"addressing_style": "path"})` when `use_path_style`.
- `api/models/config.py`: add a discriminated-union registry, wire `storage:`
  into `AppConfig` (default = local-only):
  ```python
  class LocalBackendConfig(BaseModel):
      type: Literal["local"] = "local"
  class S3BackendConfig(BaseModel):
      type: Literal["s3"] = "s3"
      endpoint_url: str | None = None
      public_endpoint_url: str | None = None
      region: str = "us-east-1"
      bucket: str = "embedbase"
      access_key_id: str = ""
      secret_access_key: str = ""
      use_path_style: bool = True
  Backend = Annotated[LocalBackendConfig | S3BackendConfig, Field(discriminator="type")]
  class StorageConfig(BaseModel):
      default: str = "local"
      backends: dict[str, Backend] = {"local": LocalBackendConfig()}
  # AppConfig: storage: StorageConfig = StorageConfig()
  ```
  Note: `_warn_extra_keys` won't recurse into the `backends` dict values — fine,
  but unknown keys *inside* a backend entry won't warn. Acceptable.
- `api/services/config_env.py`: add `overlay_storage_env(data)`. Walks
  `storage.backends`; for each `type: s3` entry named `<NAME>`, overlays
  per-instance env (secrets out of `config.yaml`):
  `S3__<UPPERNAME>__ACCESS_KEY_ID`, `S3__<UPPERNAME>__SECRET_ACCESS_KEY`, and
  optionally `S3__<UPPERNAME>__ENDPOINT_URL` / `__BUCKET` / `__PUBLIC_ENDPOINT_URL`.
  Also `STORAGE_DEFAULT → storage.default`.
- `api/main.py:101` + `worker/config.py:28`: extend both overlay chains to
  `overlay_storage_env(overlay_parser_env(overlay_vector_store_env(data)))`.
- Deps: `boto3>=1.34` → `pyproject.toml`, `api/requirements.txt`,
  `worker/requirements.txt`; `moto[s3]>=5.0` → dev deps.

---

## PR 2 — Track per-document backend + wire upload, worker, download

**Outcome:** real code paths go through `storage`, and each document records
which backend holds it. With `default: local` (unchanged default) behavior is
byte-identical to today; opting into an s3 backend works end-to-end, and
multiple s3 instances resolve correctly per document.

**TDD — write/adjust first:**
- Migration test / model assert: `documents.storage_backend` column exists, nullable.
- `test_documents*`: upload records `storage_backend = storage.default`;
  `/documents/{id}/raw` → 302 `RedirectResponse` when the doc's backend is s3,
  `FileResponse` when local (incl. legacy rows where the column is NULL).
- Worker ingestion tests: patch `get_storage` so `_run_ingestion` resolves the
  doc's backend, fetches a temp file, parses, cleans up — infra-free.
- MCP `ingest_local_path`: uploads the on-disk file into the default backend
  (assert `put_path` on the right backend + key).

**Implement:**
- **Schema:** new Alembic migration `api/alembic/versions/0005_add_documents_storage_backend.py`
  (`op.add_column("documents", sa.Column("storage_backend", sa.String(), nullable=True))`;
  `down_revision="0004"`), and add the column to `api/tables/documents.py`.
  NULL == legacy/local (files physically on disk), so read logic uses
  `row.storage_backend or "local"`.
- `api/services/documents.py`:
  - `ingest()`: `key = f"{col_id}/{doc_id}{ext}"`; `storage = get_storage()`;
    `storage.put_upload(file, key, max_bytes=...)`; record
    `storage_backend = config.storage.default` on the document row; pass `key` as
    `file_path`.
  - `ingest_local_path()`: after validation `get_storage().put_path(path, key)`;
    record the default backend name.
  - Replace `get_document_file` with
    `resolve_document_download(db, doc_id, principal) -> Response`: access-check,
    resolve `get_storage(row.storage_backend or "local")`, then **return** the
    Response (presigned `RedirectResponse` for s3, `FileResponse(local_path)` for
    local). Building the Response in the service keeps the router routing-only
    (CLAUDE.md §5).
- `api/routers/documents.py:100`: handler becomes a single delegation returning
  `Response`; drop `FileResponse`-specific logic. Re-run the §5 router grep guard.
- `worker/tasks.py`:
  - `_run_ingestion` (≈`:357`): read the document's `storage_backend` in the
    existing job/row query, `local = get_storage(name).fetch_to_temp(file_path)`;
    parse; `finally: get_storage(name).cleanup_temp(local)`. (No Celery signature
    change — backend resolved from the row, back-compatible with in-flight tasks.)
  - `delete_document` (≈`:469`): read `storage_backend`, rebuild key,
    `get_storage(name).delete(key)` — also fixes originals currently leaking on delete.

---

## PR 3 — Opt-in MinIO override, presigned/CORS bootstrap, docs

**Outcome:** users can spin up a bundled MinIO with one extra compose file and
flip `storage.default` to it; the default stack still runs on local disk with no
MinIO. External S3 needs no container at all — pure config.

**TDD — write first:**
- `tests/unit/test_storage_bootstrap.py` (moto): on S3-backend init, bucket CORS
  is applied (`get_bucket_cors` reflects `CORS_ORIGINS`); presign uses the
  backend's `public_endpoint_url` host.

**Implement:**
- `api/services/storage.py`: at S3 bucket bootstrap call `put_bucket_cors`
  (AllowedMethods `[GET]`, AllowedOrigins from `CORS_ORIGINS`) so the browser's
  cross-origin `fetch` of the presigned URL succeeds. Sign presigned URLs with a
  **client bound to the backend's `public_endpoint_url`** (SigV4 signs Host; the
  URL must match the host the browser uses — `http://localhost:9000` for bundled
  MinIO; unset for AWS).
- **`docker-compose.minio.yml`** (new, opt-in override — NOT referenced by the
  base stack):
  ```yaml
  services:
    minio:
      image: minio/minio:latest
      command: server /data --console-address ":9001"
      environment:
        - MINIO_ROOT_USER=${S3__MINIO__ACCESS_KEY_ID:-embedbase}
        - MINIO_ROOT_PASSWORD=${S3__MINIO__SECRET_ACCESS_KEY}
      volumes: [minio_data:/data]
      ports: ["${MINIO_PORT:-9000}:9000", "${MINIO_CONSOLE_PORT:-9001}:9001"]
      healthcheck: { test: ["CMD","curl","-f","http://localhost:9000/minio/health/live"] }
      networks: [embedbase_net]
    api:
      environment: [STORAGE_DEFAULT=minio, S3__MINIO__SECRET_ACCESS_KEY]
      depends_on: { minio: { condition: service_healthy } }
    worker:
      environment: [STORAGE_DEFAULT=minio, S3__MINIO__SECRET_ACCESS_KEY]
      depends_on: { minio: { condition: service_healthy } }
  volumes: { minio_data: {} }
  ```
  Run with `docker compose -f docker-compose.yml -f docker-compose.minio.yml up`.
  The `minio` backend itself is declared in `config.yaml` (see example below).
- Base `docker-compose.yml`: **unchanged** for storage (local default; keep the
  existing `embedbase_data:/data` mount).
- `config.example.yaml`: add a commented `storage:` registry — `local` default
  plus example `minio` and `aws` entries.
- `.env.example`: document `STORAGE_DEFAULT` and the per-instance secret vars
  (`S3__MINIO__SECRET_ACCESS_KEY`, etc.).

**Using external S3 (no MinIO container):** add an `aws`/custom `type: s3` entry
to `config.yaml`, set its secrets via `S3__<NAME>__*` in `.env`, set
`storage.default` (or keep per-document as desired). No override file needed.

**End-to-end verification:**
1. Default: `docker compose up -d` → no MinIO container, uploads/downloads on
   local disk exactly as before.
2. Opt-in MinIO: `docker compose -f docker-compose.yml -f docker-compose.minio.yml up -d`,
   `storage.default: minio`. Upload → object visible in MinIO console (`:9001`);
   download → browser 302 to `http://localhost:9000/...` (verifies presign host +
   bucket CORS); delete → object gone.
3. Multiple instances: declare `minio` + `aws`, upload with `default: minio`,
   switch `default: aws`, upload again; confirm each document downloads from the
   instance recorded in its `storage_backend` column.
4. External S3: point an `aws` entry at a real bucket, no MinIO container, repeat.

---

## Out of scope (ponytail)
- Per-collection / rule-based routing to a specific backend — the registry +
  per-document column make it possible later; for now new uploads use
  `storage.default`.
- Multipart / direct-to-S3 streaming — temp-file + `upload_file` is fine ≤50 MB.
- Per-object encryption, lifecycle/retention, MinIO clustering — bucket/external
  S3 config, not app code.
