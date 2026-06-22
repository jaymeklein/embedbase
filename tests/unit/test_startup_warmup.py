"""Unit tests for the background adapter warm-up (api/main._warm_up_adapters).

The warm-up exists so the slow embedding-model import does not block the ASGI
server's startup; these guard that it sets the adapters when resolution works and
swallows failures (leaving the getters at ``None``, which /healthz reports).
"""

from types import SimpleNamespace

import pytest

import api.dependencies as deps
from api.main import _warm_up_adapters


def _fake_config() -> SimpleNamespace:
    """A stand-in AppConfig exposing only the attributes the helper reads."""
    return SimpleNamespace(
        embedding=SimpleNamespace(provider="sentence_transformers", model="m"),
        vector_store=SimpleNamespace(backend="chroma"),
    )


@pytest.fixture(autouse=True)
def _reset_adapters():
    """Clear the process-global adapters before and after each test."""
    deps.set_embedding_adapter(None)
    deps.set_vector_store(None)
    yield
    deps.set_embedding_adapter(None)
    deps.set_vector_store(None)


async def test_warm_up_sets_both_adapters(monkeypatch) -> None:
    emb = SimpleNamespace(dimensions=384)
    monkeypatch.setattr("api.adapters.embeddings.get_embedding_adapter", lambda cfg: emb)
    monkeypatch.setattr("api.adapters.vector_store.get_vector_store", lambda cfg, dims: "STORE")

    await _warm_up_adapters(_fake_config())

    assert deps.get_embedding_adapter() is emb
    assert deps.get_vector_store() == "STORE"


async def test_warm_up_swallows_embedding_failure_and_still_inits_store(monkeypatch) -> None:
    def boom(cfg):
        raise RuntimeError("torch unavailable")

    captured: dict[str, int] = {}
    monkeypatch.setattr("api.adapters.embeddings.get_embedding_adapter", boom)
    monkeypatch.setattr(
        "api.adapters.vector_store.get_vector_store",
        lambda cfg, dims: captured.setdefault("dims", dims) and "STORE" or "STORE",
    )

    await _warm_up_adapters(_fake_config())  # must not raise

    assert deps.get_embedding_adapter() is None
    # Store still initialised, with the 384 fallback dimension.
    assert deps.get_vector_store() == "STORE"
    assert captured["dims"] == 384
