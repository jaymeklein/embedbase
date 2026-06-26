#!/usr/bin/env bash
# Run the GitHub Actions CI pipeline locally before opening a PR.
#
# Mirrors .github/workflows/ci.yml in four stages:
#   1. Lint + type check   (ruff + mypy, run on your machine — fast)
#   2. Unit tests          (inside python:3.12 so deps/version match CI)
#   3. Integration tests   (python:3.12 + a real Redis container, mirroring
#                           CI's redis service — never a fake)
#   4. Docker build smoke   (buildx build of the api + worker images)
#
# Docker layers and pip downloads are cached locally, so the slow first run
# is paid once.
#
# Usage:
#   ./scripts/ci-local.sh              # all stages
#   ./scripts/ci-local.sh --skip-docker
#   ./scripts/ci-local.sh --skip-tests
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"

skip_docker=0
skip_tests=0
for arg in "$@"; do
    case "$arg" in
        --skip-docker) skip_docker=1 ;;
        --skip-tests)  skip_tests=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

section() { printf '\n=== %s ===\n' "$1"; }

# 1. Lint + type check (mirrors the lint-and-type-check job) -----------------
section "Ruff lint"
python -m ruff check api/ worker/

section "Mypy type check"
echo "(note: local Python may differ from CI 3.12 — the container stages below are authoritative)"
python -m mypy api/ worker/ --ignore-missing-imports --explicit-package-bases

# 2. Unit tests (mirrors the unit-tests job) --------------------------------
if [ "$skip_tests" -eq 0 ]; then
    section "Unit tests (python:3.12 container)"
    docker run --rm -v "$repo:/app" -w /app \
        -v embedbase-pipcache:/root/.cache/pip \
        python:3.12-slim \
        sh -c "pip install -q -r api/requirements.txt pytest pytest-asyncio && pytest tests/unit/ -q"
fi

# 3. Integration tests with a real Redis (mirrors the integration-tests job) -
# Spin up a Redis container and run the suite against it over a shared Docker
# network, so the BM25 corpus path is exercised for real — just like CI's redis
# service. Cleaned up on any exit.
if [ "$skip_tests" -eq 0 ]; then
    section "Integration tests (python:3.12 + real Redis)"
    ci_net="embedbase-ci-net"
    ci_redis="embedbase-ci-redis"
    cleanup_ci_redis() {
        docker rm -f "$ci_redis" >/dev/null 2>&1 || true
        docker network rm "$ci_net" >/dev/null 2>&1 || true
    }
    trap cleanup_ci_redis EXIT
    cleanup_ci_redis  # clear leftovers from a previously aborted run
    docker network create "$ci_net" >/dev/null
    docker run -d --name "$ci_redis" --network "$ci_net" redis:7.2-alpine >/dev/null
    # Wait for Redis to accept connections so the suite runs (never skips).
    for _ in $(seq 1 20); do
        docker exec "$ci_redis" redis-cli ping >/dev/null 2>&1 && break
        sleep 0.5
    done
    docker run --rm --network "$ci_net" \
        -e REDIS_URL="redis://$ci_redis:6379/0" \
        -v "$repo:/app" -w /app \
        -v embedbase-pipcache:/root/.cache/pip \
        python:3.12-slim \
        sh -c "pip install -q -r api/requirements.txt pytest pytest-asyncio && pytest tests/integration/ -q"
    cleanup_ci_redis
    trap - EXIT
fi

# 4. Docker build smoke test (mirrors the docker-build job) ------------------
if [ "$skip_docker" -eq 0 ]; then
    [ -f config.yaml ] || cp config.example.yaml config.yaml

    section "Docker build: api"
    docker buildx build --load -t embedbase/api:ci \
        --cache-to   type=local,dest=.buildcache/api,mode=max \
        --cache-from type=local,src=.buildcache/api ./api

    section "Docker build: worker"
    docker buildx build --load -t embedbase/worker:ci -f worker/Dockerfile \
        --cache-to   type=local,dest=.buildcache/worker,mode=max \
        --cache-from type=local,src=.buildcache/worker .
fi

section "All CI stages passed."
