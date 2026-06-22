"""Unit tests for the standalone REST API reference route filter (api/main.py)."""

from api.main import _is_reference_route


def test_reference_includes_integration_endpoints():
    assert _is_reference_route("/search")
    assert _is_reference_route("/workspaces")
    assert _is_reference_route("/workspaces/ws_1/collections")
    assert _is_reference_route("/workspaces/ws_1/collections/col_1/documents")
    assert _is_reference_route("/documents/doc_1")


def test_reference_excludes_internal_endpoints():
    # The reference must not list the app's other endpoints.
    assert not _is_reference_route("/config")
    assert not _is_reference_route("/healthz")
    assert not _is_reference_route("/reference.json")
    assert not _is_reference_route("/")


def test_reference_spec_lists_integration_paths_only():
    """End-to-end: the served spec must carry the integration paths and nothing
    else — guards the Starlette-1.3 regression where filtering app.routes by
    top-level path yielded an empty spec."""
    from fastapi.testclient import TestClient

    from api.main import create_app

    with TestClient(create_app()) as client:
        spec = client.get("/reference.json").json()

    paths = spec["paths"]
    assert paths, "reference spec has no paths"
    assert "/workspaces" in paths
    assert "/search" in paths
    assert all(_is_reference_route(p) for p in paths)
    assert "/config" not in paths
    assert "/healthz" not in paths
