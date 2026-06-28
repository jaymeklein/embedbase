"""Smoke tests — does the whole system assemble and boot?

Fast, dependency-light checks: the app builds, OpenAPI generates, every router
is wired, core endpoints answer, middleware runs, and the deployment artifacts
(compose files, example config) parse.
"""

from pathlib import Path

import pytest
import yaml

from api.main import create_app

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

def test_app_builds_with_metadata():
    app = create_app()
    assert app.title == "EmbedBase"
    assert app.version == "1.0.0"


def test_openapi_schema_generates():
    schema = create_app().openapi()
    assert schema["info"]["title"] == "EmbedBase"
    assert "paths" in schema and schema["paths"]


def test_all_routers_are_wired():
    # FastAPI 0.137 / Starlette 1.3 wrap included routers in lazy `_IncludedRouter`
    # objects that have no `.path`, so iterating `app.routes` for paths breaks. Read
    # the HTTP paths from the OpenAPI schema and union in the non-schema top-level
    # routes (e.g. the `/mcp` mount) — both are stable public surfaces.
    app = create_app()
    paths = set(app.openapi()["paths"]) | {
        r.path for r in app.routes if getattr(r, "path", None)
    }
    expected = {
        "/healthz",
        "/metrics",
        "/workspaces",
        "/workspaces/{ws_id}",
        "/workspaces/{ws_id}/collections",
        "/workspaces/{ws_id}/collections/{col_id}",
        "/workspaces/{ws_id}/collections/{col_id}/keys",
        "/workspaces/{ws_id}/collections/{col_id}/documents",
        "/workspaces/{ws_id}/collections/{col_id}/documents/{doc_id}/status",
        "/documents",
        "/search",
        "/config",
        "/mcp",  # MCP SSE app mounted here (SSE served at /mcp/sse)
    }
    missing = expected - paths
    assert not missing, f"router paths missing: {missing}"


# ---------------------------------------------------------------------------
# Live endpoints (via the in-memory client fixture)
# ---------------------------------------------------------------------------

async def test_healthz_answers(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_docs_endpoint_serves(client):
    r = await client.get("/docs")
    assert r.status_code == 200


async def test_openapi_json_serves(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "EmbedBase"


async def test_request_id_middleware_sets_header(client):
    r = await client.get("/healthz")
    assert "x-request-id" in {k.lower() for k in r.headers}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_load_and_derive():
    from api.settings import settings

    assert settings.master_api_key  # provided via env in conftest
    assert settings.cors_origins_list  # non-empty list


def test_app_config_file_size_derivation():
    from api.models.config import AppConfig

    config = AppConfig(max_file_size_mb=50)
    assert config.max_file_size_bytes == 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# Deployment artifacts
# ---------------------------------------------------------------------------

def test_docker_compose_has_all_services():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    assert {"api", "worker", "redis", "chroma", "nginx"}.issubset(compose["services"])


def test_compose_overrides_parse():
    for name in (
        "docker-compose.postgres.yml",
        "docker-compose.qdrant.yml",
        "docker-compose.gpu.yml",
    ):
        data = yaml.safe_load((REPO / name).read_text())
        assert "services" in data


def test_config_example_is_valid():
    from api.models.config import AppConfig

    data = yaml.safe_load((REPO / "config.example.yaml").read_text())
    cfg = AppConfig.model_validate(data)
    assert cfg.embedding.provider


# ---------------------------------------------------------------------------
# Worker assembly
# ---------------------------------------------------------------------------

def test_worker_celery_configured():
    from worker.celery_app import celery_app

    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_time_limit == 600
    assert conf.task_soft_time_limit == 540


def test_worker_tasks_registered():
    import worker.tasks  # noqa: F401  (registers tasks)
    from worker.celery_app import celery_app

    assert "worker.tasks.ingest_document" in celery_app.tasks
    assert "worker.tasks.delete_document" in celery_app.tasks


def test_static_openapi_yaml_parses():
    """docs/openapi.yaml must exist and be valid YAML.

    Re-generate with: python scripts/export_openapi.py
    """
    spec_path = REPO / "docs" / "openapi.yaml"
    assert spec_path.exists(), "docs/openapi.yaml missing — run: python scripts/export_openapi.py"
    schema = yaml.safe_load(spec_path.read_text())
    assert "openapi" in schema
    assert "paths" in schema
