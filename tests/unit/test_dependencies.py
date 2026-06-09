"""Unit tests for FastAPI dependency guards (require_* 503 paths)."""

import pytest
from fastapi import HTTPException

from api import dependencies as deps


def _reset() -> None:
    """Reset all adapter singletons to None between tests."""
    deps._embedding_adapter = None
    deps._vector_store = None
    deps._redis_client = None


@pytest.fixture(autouse=True)
def _isolated(monkeypatch) -> None:
    """Ensure each test starts with all singletons cleared."""
    monkeypatch.setattr(deps, "_embedding_adapter", None)
    monkeypatch.setattr(deps, "_vector_store", None)
    monkeypatch.setattr(deps, "_redis_client", None)


# ---------------------------------------------------------------------------
# require_embedding_adapter
# ---------------------------------------------------------------------------


def test_require_embedding_adapter_raises_503_when_none():
    with pytest.raises(HTTPException) as exc:
        deps.require_embedding_adapter()
    assert exc.value.status_code == 503


def test_require_embedding_adapter_returns_adapter_when_set(monkeypatch):
    fake = object()
    monkeypatch.setattr(deps, "_embedding_adapter", fake)
    assert deps.require_embedding_adapter() is fake


# ---------------------------------------------------------------------------
# require_vector_store
# ---------------------------------------------------------------------------


def test_require_vector_store_raises_503_when_none():
    with pytest.raises(HTTPException) as exc:
        deps.require_vector_store()
    assert exc.value.status_code == 503


def test_require_vector_store_returns_store_when_set(monkeypatch):
    fake = object()
    monkeypatch.setattr(deps, "_vector_store", fake)
    assert deps.require_vector_store() is fake


# ---------------------------------------------------------------------------
# require_redis_client
# ---------------------------------------------------------------------------


def test_require_redis_client_raises_503_when_none():
    with pytest.raises(HTTPException) as exc:
        deps.require_redis_client()
    assert exc.value.status_code == 503


def test_require_redis_client_returns_client_when_set(monkeypatch):
    fake = object()
    monkeypatch.setattr(deps, "_redis_client", fake)
    assert deps.require_redis_client() is fake
