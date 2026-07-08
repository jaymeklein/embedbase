"""Unit tests for the shared LLM chat transport (``api.adapters.llm_chat``).

Covers the per-provider wire formats (Ollama / OpenAI-compatible / native Gemini), the
opt-in ``temperature`` (omitted by default so existing callers keep their provider default),
and the ``post_json`` HTTP primitive. No network — ``post_json`` / ``httpx.post`` are patched.
"""

import pytest

from api.adapters import llm_chat
from api.adapters.llm_chat import chat_complete, post_json


def _capture_post(monkeypatch, reply):
    """Patch ``llm_chat.post_json`` to record its args and return ``reply``."""
    calls: dict = {}

    def fake(url, payload, headers, timeout):
        calls.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return reply

    monkeypatch.setattr(llm_chat, "post_json", fake)
    return calls


def test_chat_complete_ollama_endpoint(monkeypatch):
    calls = _capture_post(monkeypatch, {"message": {"content": "ok"}})
    out = chat_complete(
        provider="ollama", model="llama3", base_url="http://h:11434", api_key=None, prompt="p"
    )
    assert out == "ok"
    assert calls["url"].endswith("/api/chat")
    assert calls["headers"] == {}  # ollama is unauthenticated
    assert calls["payload"]["stream"] is False
    assert "options" not in calls["payload"]  # temperature omitted → no options block
    assert "format" not in calls["payload"]  # no schema → no structured-output field
    assert calls["timeout"] == llm_chat.CHAT_TIMEOUT_SECONDS  # generous default for local CPU


def test_chat_complete_openai_endpoint(monkeypatch):
    calls = _capture_post(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    out = chat_complete(
        provider="openai_compat", model="gpt", base_url="http://h:1234", api_key="secret", prompt="p"
    )
    assert out == "ok"
    assert calls["url"].endswith("/v1/chat/completions")
    assert calls["headers"]["Authorization"] == "Bearer secret"
    assert "temperature" not in calls["payload"]
    assert "response_format" not in calls["payload"]  # no schema → no structured-output field


def test_chat_complete_gemini_endpoint(monkeypatch):
    calls = _capture_post(monkeypatch, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    out = chat_complete(
        provider="gemini", model="gemini-2.5-flash", base_url=None, api_key="k",
        prompt="p", temperature=0.0,
    )
    assert out == "ok"
    assert calls["url"].endswith("/v1beta/models/gemini-2.5-flash:generateContent")
    assert calls["headers"]["x-goog-api-key"] == "k"
    assert calls["payload"]["generationConfig"] == {"temperature": 0.0}


def test_chat_complete_temperature_included_for_ollama(monkeypatch):
    calls = _capture_post(monkeypatch, {"message": {"content": "x"}})
    chat_complete(
        provider="ollama", model="m", base_url=None, api_key=None, prompt="p", temperature=0.0
    )
    assert calls["payload"]["options"] == {"temperature": 0.0}


def test_chat_complete_gemini_empty_candidates_returns_blank(monkeypatch):
    _capture_post(monkeypatch, {"candidates": []})
    out = chat_complete(provider="gemini", model="m", base_url=None, api_key="k", prompt="p")
    assert out == ""


def test_chat_complete_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        chat_complete(provider="nope", model="m", base_url=None, api_key=None, prompt="p")


# ---------------------------------------------------------------------------
# response_schema → provider-native structured output
# ---------------------------------------------------------------------------

_SCHEMA = {"type": "object", "properties": {"score": {"type": "integer"}}, "required": ["score"]}


def test_chat_complete_openai_structured_output(monkeypatch):
    calls = _capture_post(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
    chat_complete(
        provider="openai_compat", model="m", base_url=None, api_key="k", prompt="p",
        response_schema=_SCHEMA,
    )
    fmt = calls["payload"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    # strict mode needs a closed object — the transport adds additionalProperties:false itself.
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    assert fmt["json_schema"]["schema"]["properties"] == {"score": {"type": "integer"}}


def test_chat_complete_ollama_structured_output(monkeypatch):
    calls = _capture_post(monkeypatch, {"message": {"content": "{}"}})
    chat_complete(
        provider="ollama", model="m", base_url=None, api_key=None, prompt="p",
        response_schema=_SCHEMA,
    )
    assert calls["payload"]["format"] == _SCHEMA  # Ollama takes the JSON Schema verbatim


def test_chat_complete_gemini_structured_output(monkeypatch):
    calls = _capture_post(monkeypatch, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    chat_complete(
        provider="gemini", model="m", base_url=None, api_key="k", prompt="p",
        temperature=0.0, response_schema=_SCHEMA,
    )
    gen = calls["payload"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["responseSchema"] == _SCHEMA
    assert gen["temperature"] == 0.0  # temperature still rides alongside the schema


def test_post_json_invokes_httpx(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": 1}

    captured: dict = {}

    def _post(*_a, **kwargs):
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr("httpx.post", _post)
    assert post_json("http://x", {"a": 1}, {"H": "v"}, 30.0) == {"ok": 1}
    assert captured["timeout"] == 30.0
    assert captured["json"] == {"a": 1}
    assert captured["headers"] == {"H": "v"}
