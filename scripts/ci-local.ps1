#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Run the GitHub Actions CI pipeline locally before opening a PR.

.DESCRIPTION
  Mirrors .github/workflows/ci.yml in three stages:
    1. Lint + type check  (ruff + mypy, run on your machine - fast)
    2. Unit tests         (inside python:3.12 so deps/version match CI)
    3. Docker build smoke  (buildx build of the api + worker images)

  Static checks run locally for speed; tests and image builds run inside
  python:3.12 / buildx so the environment matches CI exactly. Docker layers
  and pip downloads are cached locally, so the slow first run is paid once.

.PARAMETER SkipDocker
  Skip the Docker image builds (the slowest stage).

.PARAMETER SkipTests
  Skip the unit-test stage.

.EXAMPLE
  ./scripts/ci-local.ps1
  ./scripts/ci-local.ps1 -SkipDocker
#>
param(
    [switch]$SkipDocker,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Section($name) { Write-Host "`n=== $name ===" -ForegroundColor Cyan }

# 1. Lint + type check (mirrors the lint-and-type-check job) -----------------
Section "Ruff lint"
python -m ruff check api/ worker/
if ($LASTEXITCODE -ne 0) { throw "ruff failed" }

Section "Mypy type check"
Write-Host "(note: local Python may differ from CI 3.12; the container stages below are authoritative)" -ForegroundColor DarkGray
python -m mypy api/ worker/ --ignore-missing-imports --explicit-package-bases
if ($LASTEXITCODE -ne 0) { throw "mypy failed" }

# 2. Unit tests (mirrors the unit-tests job) --------------------------------
if (-not $SkipTests) {
    Section "Unit tests (python:3.12 container)"
    docker run --rm -v "${repo}:/app" -w /app `
        -v embedbase-pipcache:/root/.cache/pip `
        python:3.12-slim `
        sh -c "pip install -q -r api/requirements.txt pytest pytest-asyncio && pytest tests/unit/ -q"
    if ($LASTEXITCODE -ne 0) { throw "unit tests failed" }
}

# 3. Docker build smoke test (mirrors the docker-build job) ------------------
if (-not $SkipDocker) {
    if (-not (Test-Path config.yaml)) { Copy-Item config.example.yaml config.yaml }

    Section "Docker build: api"
    docker buildx build --load -t embedbase/api:ci `
        --cache-to   "type=local,dest=.buildcache/api,mode=max" `
        --cache-from "type=local,src=.buildcache/api" ./api
    if ($LASTEXITCODE -ne 0) { throw "api image build failed" }

    Section "Docker build: worker"
    docker buildx build --load -t embedbase/worker:ci -f worker/Dockerfile `
        --cache-to   "type=local,dest=.buildcache/worker,mode=max" `
        --cache-from "type=local,src=.buildcache/worker" .
    if ($LASTEXITCODE -ne 0) { throw "worker image build failed" }
}

Section "All CI stages passed."
