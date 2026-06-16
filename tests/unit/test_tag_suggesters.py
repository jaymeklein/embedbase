"""Unit tests for the tag suggesters and the suggester registry."""

import pytest

from api.adapters.tagging import get_tag_suggester
from api.adapters.tagging.keyword import KeywordTagSuggester
from api.adapters.tagging.llm import LLMTagSuggester, _parse_tags
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


# ── _parse_tags ───────────────────────────────────────────────────────────────

def test_parse_tags_json_array():
    assert _parse_tags('["python", "FastAPI", "python"]', 8) == ["python", "fastapi"]


def test_parse_tags_comma_and_newline_fallback():
    assert _parse_tags("python, fastapi\ntesting", 8) == ["python", "fastapi", "testing"]


def test_parse_tags_respects_limit():
    assert _parse_tags("a, b, c, d", 2) == ["a", "b"]


def test_parse_tags_extracts_array_from_prose():
    assert _parse_tags('Sure! ["alpha", "beta"] hope this helps', 8) == ["alpha", "beta"]


def test_parse_tags_invalid_json_array_falls_back_to_split():
    # Bracketed but not valid JSON -> JSONDecodeError -> delimiter fallback.
    assert _parse_tags("[alpha, beta]", 8) == ["[alpha", "beta]"]


# ── LLMTagSuggester ───────────────────────────────────────────────────────────

def test_llm_suggest_parses_and_excludes_existing(monkeypatch):
    sug = LLMTagSuggester("ollama", "llama3", None, None, max_tags=8)
    monkeypatch.setattr(sug, "_complete", lambda prompt: '["python", "fastapi", "redis"]')
    out = sug.suggest("some text", ["redis"])
    assert [s.name for s in out] == ["python", "fastapi"]
    assert out[0].confidence > out[1].confidence  # declining


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
