"""Unit tests for the tag suggesters and the suggester registry."""

import pytest

from api.adapters.tagging import get_tag_suggester
from api.adapters.tagging.llm import LLMTagSuggester, _parse_suggestions, list_ollama_models
from api.models.config import TaggingConfig, TagSuggesterConfig

# ── _parse_suggestions ────────────────────────────────────────────────────────

def test_parse_suggestions_objects_use_real_confidence():
    raw = '[{"name": "Python", "confidence": 0.9}, {"name": "redis", "confidence": 0.4}]'
    assert _parse_suggestions(raw, 8) == [("python", 0.9), ("redis", 0.4)]


def test_parse_suggestions_clamps_and_defaults_confidence():
    raw = '[{"name": "a", "confidence": 1.5}, {"name": "b"}, {"name": "c", "confidence": "x"}]'
    assert _parse_suggestions(raw, 8) == [("a", 1.0), ("b", 0.0), ("c", 0.0)]


def test_parse_suggestions_dedupes_keeping_first():
    raw = '[{"name": "python", "confidence": 0.9}, {"name": "Python", "confidence": 0.2}]'
    assert _parse_suggestions(raw, 8) == [("python", 0.9)]


def test_parse_suggestions_name_only_array_falls_back_to_rank():
    out = _parse_suggestions('["alpha", "beta"]', 8)
    assert [n for n, _ in out] == ["alpha", "beta"]
    assert out[0][1] > out[1][1]  # rank-derived, declining


def test_parse_suggestions_comma_newline_fallback():
    out = _parse_suggestions("python, fastapi\ntesting", 8)
    assert [n for n, _ in out] == ["python", "fastapi", "testing"]


def test_parse_suggestions_respects_limit():
    assert [n for n, _ in _parse_suggestions("a, b, c, d", 2)] == ["a", "b"]


def test_parse_suggestions_extracts_array_from_prose():
    out = _parse_suggestions('Sure! ["alpha", "beta"] hope this helps', 8)
    assert [n for n, _ in out] == ["alpha", "beta"]


# ── LLMTagSuggester ───────────────────────────────────────────────────────────

def test_llm_suggest_uses_real_confidence_and_excludes_existing(monkeypatch):
    sug = LLMTagSuggester("ollama", "llama3", None, None, max_tags=8)
    reply = '[{"name":"python","confidence":0.95},{"name":"redis","confidence":0.6}]'
    monkeypatch.setattr(sug, "_complete", lambda prompt: reply)
    out = sug.suggest("some text", ["redis"])
    assert [(s.name, s.confidence) for s in out] == [("python", 0.95)]


def test_llm_suggest_blank_text_returns_empty_without_prompting(monkeypatch):
    sug = LLMTagSuggester("ollama", "llama3", None, None, max_tags=8)
    # _complete must never run for blank text — empty text makes the model reply
    # with prose that the parser would mistake for tags (the "Suggest while still
    # ingesting" bug). Blow up if it's called.
    monkeypatch.setattr(
        sug, "_complete", lambda prompt: (_ for _ in ()).throw(AssertionError("called"))
    )
    assert sug.suggest("   \n\t ", []) == []


def test_llm_suggest_name_only_reply_falls_back_to_rank(monkeypatch):
    sug = LLMTagSuggester("ollama", "llama3", None, None, max_tags=8)
    monkeypatch.setattr(sug, "_complete", lambda prompt: '["python", "fastapi"]')
    out = sug.suggest("some text", [])
    assert [s.name for s in out] == ["python", "fastapi"]
    assert out[0].confidence > out[1].confidence


def test_llm_complete_ollama_endpoint(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers):
        captured["url"] = url
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(LLMTagSuggester, "_post", staticmethod(fake_post))
    sug = LLMTagSuggester("ollama", "llama3", "http://h:11434", None)
    assert sug._complete("p") == "ok"
    assert captured["url"].endswith("/api/chat")


def test_llm_complete_openai_endpoint(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers):
        captured["url"] = url
        captured["headers"] = headers
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(LLMTagSuggester, "_post", staticmethod(fake_post))
    sug = LLMTagSuggester("openai_compat", "gpt", "http://h:1234", "secret")
    assert sug._complete("p") == "ok"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_post_invokes_httpx(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": 1}

    captured = {}

    def _post(*_a, **kwargs):
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr("httpx.post", _post)
    assert LLMTagSuggester._post("http://x", {}, {}) == {"ok": 1}
    # Local CPU inference is slow, so the timeout must be generous (not the old 60s).
    assert captured["timeout"] >= 120.0


# ── list_ollama_models ──────────────────────────────────────────────────────────


def test_list_ollama_models_sorts_names_and_uses_base_url(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "llama3.1"}, {"name": "gemma2"}, {"bad": 1}]}

    def _get(url, **_kwargs):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr("httpx.get", _get)
    assert list_ollama_models("http://ollama:11434") == ["gemma2", "llama3.1"]
    assert captured["url"] == "http://ollama:11434/api/tags"


def test_list_ollama_models_blank_url_uses_default(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": []}

    captured = {}

    def _get(url, **_kwargs):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr("httpx.get", _get)
    assert list_ollama_models(None) == []
    assert captured["url"].endswith("/api/tags")


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_llm():
    cfg = TaggingConfig(suggester=TagSuggesterConfig(backend="llm"))
    assert isinstance(get_tag_suggester(cfg), LLMTagSuggester)


def test_registry_default_is_llm():
    # Tag suggestion is LLM-only; the default backend must resolve to the LLM suggester.
    assert isinstance(get_tag_suggester(TaggingConfig()), LLMTagSuggester)


def test_registry_keyword_backend_no_longer_exists():
    # The local keyword backend was removed — any non-LLM value is unknown.
    cfg = TaggingConfig(suggester=TagSuggesterConfig(backend="keyword"))
    with pytest.raises(ValueError, match="Unknown tag suggester backend"):
        get_tag_suggester(cfg)


def test_registry_unknown_backend_raises():
    cfg = TaggingConfig(suggester=TagSuggesterConfig(backend="nope"))
    with pytest.raises(ValueError, match="Unknown tag suggester backend"):
        get_tag_suggester(cfg)
