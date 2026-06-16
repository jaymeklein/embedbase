"""Unit tests for the tag suggesters and the suggester registry."""

import pytest

from api.adapters.tagging import get_tag_suggester
from api.adapters.tagging.keyword import KeywordTagSuggester
from api.adapters.tagging.llm import LLMTagSuggester, _parse_suggestions
from api.models.config import TaggingConfig, TagSuggesterConfig

# ── KeywordTagSuggester ───────────────────────────────────────────────────────

def test_keyword_ranks_by_frequency_with_confidence():
    out = KeywordTagSuggester(max_tags=5).suggest(
        "kubernetes kubernetes kubernetes scaling scaling deployment", []
    )
    assert out[0].confidence == 1.0
    assert all(s.name == s.name.lower() for s in out)


def test_keyword_excludes_existing_and_respects_max():
    text = "alpha alpha beta beta gamma gamma delta delta epsilon epsilon"
    out = KeywordTagSuggester(max_tags=2).suggest(text, ["alpha"])
    assert len(out) == 2
    assert all(s.name != "alpha" for s in out)


def test_keyword_empty_text_returns_empty():
    assert KeywordTagSuggester().suggest("   ", []) == []


def test_keyword_filters_stopwords_and_short_tokens():
    out = KeywordTagSuggester().suggest("the of an to is be", [])
    assert out == []


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

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    assert LLMTagSuggester._post("http://x", {}, {}) == {"ok": 1}


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_keyword():
    cfg = TaggingConfig(suggester=TagSuggesterConfig(backend="keyword"))
    assert isinstance(get_tag_suggester(cfg), KeywordTagSuggester)


def test_registry_llm():
    cfg = TaggingConfig(suggester=TagSuggesterConfig(backend="llm"))
    assert isinstance(get_tag_suggester(cfg), LLMTagSuggester)


def test_registry_unknown_backend_raises():
    cfg = TaggingConfig(suggester=TagSuggesterConfig(backend="nope"))
    with pytest.raises(ValueError, match="Unknown tag suggester backend"):
        get_tag_suggester(cfg)
