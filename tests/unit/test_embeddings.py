"""Unit tests for the additional embedding adapters (Delivery 4).

Both adapters are driven against a fake HTTP layer — no Ollama or
OpenAI-compatible server is contacted. The Ollama adapter calls ``asyncio.run``
internally, so its tests are *synchronous* (calling it from inside a running
event loop would raise).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from api.adapters.embeddings import get_embedding_adapter
from api.adapters.embeddings.gemini import GeminiAdapter
from api.adapters.embeddings.ollama import OllamaAdapter
from api.adapters.embeddings.openai_compat import OpenAICompatAdapter
from api.models.config import EmbeddingConfig


class _FakeResp:
    is_error = False  # Gemini adapter checks this before reading the body

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

    async def __aenter__(self) -> _FakeOllamaClient:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None) -> _FakeResp:
        type(self).calls += 1
        await asyncio.sleep(0)  # yield so a contended Semaphore actually blocks (binds to the loop)
        return _FakeResp({"embedding": [0.1, 0.2, 0.3]})


def test_ollama_embed_batch_returns_one_vector_per_text(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeOllamaClient)
    adapter = OllamaAdapter(base_url="http://ollama:11434", model="nomic-embed-text", concurrency=4)

    vectors = adapter.embed_batch(["alpha", "beta"])

    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert adapter.embed("solo") == [0.1, 0.2, 0.3]


def test_ollama_concurrency_is_applied():
    adapter = OllamaAdapter(base_url="http://ollama:11434", model="m", concurrency=3)
    assert adapter._concurrency == 3


def test_ollama_embed_batch_twice_uses_a_fresh_loop(monkeypatch):
    # Regression: the Semaphore used to be created in __init__, binding to the first
    # asyncio.run() loop the moment it blocked; a second contended embed_batch ran a
    # new loop and raised "is bound to a different event loop". Must be per call.
    # concurrency=1 with 2 texts forces the block (and thus the loop binding).
    monkeypatch.setattr(httpx, "AsyncClient", _FakeOllamaClient)
    adapter = OllamaAdapter(base_url="http://ollama:11434", model="m", concurrency=1)

    assert adapter.embed_batch(["a", "b"]) == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    # second call on the SAME adapter — new event loop; must not raise.
    assert adapter.embed_batch(["c", "d"]) == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]


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


# ── Gemini (POST /v1beta/models/{model}:batchEmbedContents) ───────────────────


def test_gemini_batches_and_sends_api_key_header(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_post(url: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float | None = None) -> _FakeResp:
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["body"] = json or {}
        n = len((json or {}).get("requests", []))
        return _FakeResp({"embeddings": [{"values": [0.5, 0.6]} for _ in range(n)]})

    monkeypatch.setattr(httpx, "post", _fake_post)
    adapter = GeminiAdapter(model="gemini-embedding-2", api_key="g-key")

    vectors = adapter.embed_batch(["a", "b", "c"])

    assert vectors == [[0.5, 0.6], [0.5, 0.6], [0.5, 0.6]]
    assert captured["url"].endswith("/v1beta/models/gemini-embedding-2:batchEmbedContents")
    assert captured["headers"]["x-goog-api-key"] == "g-key"
    # one request per text, with the bare text in content.parts
    assert captured["body"]["requests"][0]["content"]["parts"][0]["text"] == "a"
    assert "output_dimensionality" not in captured["body"]["requests"][0]


def test_gemini_passes_output_dimensionality_when_set(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_post(url, json=None, headers=None, timeout=None) -> _FakeResp:
        captured["body"] = json or {}
        return _FakeResp({"embeddings": [{"values": [0.0] * 768}]})

    monkeypatch.setattr(httpx, "post", _fake_post)
    adapter = GeminiAdapter(model="gemini-embedding-2", api_key="g", output_dimensionality=768)

    assert adapter.dimensions == 768
    assert captured["body"]["requests"][0]["output_dimensionality"] == 768


# ── Registry wiring ───────────────────────────────────────────────────────────


def test_registry_resolves_ollama_and_openai_compat():
    ollama = get_embedding_adapter(EmbeddingConfig(provider="ollama", model="nomic-embed-text"))
    assert isinstance(ollama, OllamaAdapter)

    openai_compat = get_embedding_adapter(
        EmbeddingConfig(provider="openai_compat", model="text-embed", api_key="sk")
    )
    assert isinstance(openai_compat, OpenAICompatAdapter)

    gemini = get_embedding_adapter(
        EmbeddingConfig(provider="gemini", model="gemini-embedding-2", api_key="g")
    )
    assert isinstance(gemini, GeminiAdapter)


def test_registry_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        get_embedding_adapter(EmbeddingConfig(provider="does-not-exist", model="x"))
