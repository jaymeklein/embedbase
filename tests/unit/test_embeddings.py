"""Unit tests for the additional embedding adapters (Delivery 4).

Both adapters are driven against a fake HTTP layer — no Ollama or
OpenAI-compatible server is contacted. The Ollama adapter calls ``asyncio.run``
internally, so its tests are *synchronous* (calling it from inside a running
event loop would raise).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from api.adapters.embeddings import get_embedding_adapter
from api.adapters.embeddings.ollama import OllamaAdapter
from api.adapters.embeddings.openai_compat import OpenAICompatAdapter
from api.models.config import EmbeddingConfig


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


# ── Ollama (POST /api/embeddings, one request per text) ───────────────────────


class _FakeOllamaClient:
    calls = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeOllamaClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None) -> _FakeResp:
        type(self).calls += 1
        return _FakeResp({"embedding": [0.1, 0.2, 0.3]})


def test_ollama_embed_batch_returns_one_vector_per_text(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeOllamaClient)
    adapter = OllamaAdapter(base_url="http://ollama:11434", model="nomic-embed-text", concurrency=4)

    vectors = adapter.embed_batch(["alpha", "beta"])

    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert adapter.embed("solo") == [0.1, 0.2, 0.3]


def test_ollama_concurrency_is_applied():
    adapter = OllamaAdapter(base_url="http://ollama:11434", model="m", concurrency=3)
    assert adapter._semaphore._value == 3


def test_ollama_dimensions_probe_is_cached(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeOllamaClient)
    _FakeOllamaClient.calls = 0
    adapter = OllamaAdapter(base_url="http://ollama:11434", model="m")

    assert adapter.dimensions == 3
    calls_after_first = _FakeOllamaClient.calls
    assert adapter.dimensions == 3  # cached — no extra HTTP call
    assert _FakeOllamaClient.calls == calls_after_first


# ── OpenAI-compatible (POST /v1/embeddings, batched input) ────────────────────


def test_openai_compat_batches_and_sends_bearer_token(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_post(url: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float | None = None) -> _FakeResp:
        captured["url"] = url
        captured["headers"] = headers or {}
        n = len((json or {}).get("input", []))
        return _FakeResp({"data": [{"embedding": [0.5, 0.6]} for _ in range(n)]})

    monkeypatch.setattr(httpx, "post", _fake_post)
    adapter = OpenAICompatAdapter(base_url="http://lmstudio:1234/", model="text-embed", api_key="sk-test")

    vectors = adapter.embed_batch(["a", "b", "c"])

    assert vectors == [[0.5, 0.6], [0.5, 0.6], [0.5, 0.6]]
    assert captured["url"] == "http://lmstudio:1234/v1/embeddings"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_openai_compat_dimensions(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResp({"data": [{"embedding": [0.0, 1.0, 2.0, 3.0]}]})
    )
    adapter = OpenAICompatAdapter(base_url="http://lmstudio:1234", model="m")
    assert adapter.dimensions == 4


# ── Registry wiring ───────────────────────────────────────────────────────────


def test_registry_resolves_ollama_and_openai_compat():
    ollama = get_embedding_adapter(EmbeddingConfig(provider="ollama", model="nomic-embed-text"))
    assert isinstance(ollama, OllamaAdapter)

    openai_compat = get_embedding_adapter(
        EmbeddingConfig(provider="openai_compat", model="text-embed", api_key="sk")
    )
    assert isinstance(openai_compat, OpenAICompatAdapter)


def test_registry_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        get_embedding_adapter(EmbeddingConfig(provider="does-not-exist", model="x"))
