"""Unit tests for the rerankers (cross-encoder / hosted API / LLM) and the registry."""

import pytest

from api.adapters.reranker import get_reranker
from api.adapters.reranker.cross_encoder import CrossEncoderReranker
from api.adapters.reranker.llm import LLMReranker, _extract_score
from api.adapters.reranker.reorder import rerank_by_scores
from api.adapters.reranker.rerank_api import RerankApiReranker
from api.models.config import RerankerConfig
from api.models.search import SearchResult


def _result(chunk_id: str, text: str) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text=text, score=1.0)


class _FakeModel:
    """Stand-in for sentence_transformers.CrossEncoder.

    Scores each (query, text) pair by the score table, so the test controls the
    reranked order without loading torch.
    """

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [self._scores[text] for _query, text in pairs]


def _reranker(scores: dict[str, float], top_n: int = 50) -> CrossEncoderReranker:
    """Build a reranker without invoking __init__ (skips the heavy model load)."""
    r = object.__new__(CrossEncoderReranker)
    r._model = _FakeModel(scores)
    r._top_n = top_n
    return r


# ---------------------------------------------------------------------------
# CrossEncoderReranker.rerank
# ---------------------------------------------------------------------------


def test_rerank_reorders_by_cross_encoder_score():
    reranker = _reranker({"a": 0.1, "b": 0.9, "c": 0.5})
    results = [_result("c1", "a"), _result("c2", "b"), _result("c3", "c")]

    ranked = reranker.rerank("q", results)

    assert [r.chunk_id for r in ranked] == ["c2", "c3", "c1"]
    assert [r.rank for r in ranked] == [1, 2, 3]


def test_rerank_short_circuits_below_two_results():
    reranker = _reranker({"only": 0.9})
    single = [_result("c1", "only")]
    assert reranker.rerank("q", single) is single  # untouched, no predict call
    assert reranker.rerank("q", []) == []


def test_rerank_only_scores_top_n_then_appends_tail():
    # top_n=2: only the first two are scored/reordered; the rest trail in order.
    reranker = _reranker({"a": 0.1, "b": 0.9, "c": 0.5}, top_n=2)
    results = [_result("c1", "a"), _result("c2", "b"), _result("c3", "c")]

    ranked = reranker.rerank("q", results)

    assert [r.chunk_id for r in ranked] == ["c2", "c1", "c3"]  # b,a reranked; c untouched tail
    assert [r.rank for r in ranked] == [1, 2, 3]


# ---------------------------------------------------------------------------
# get_reranker registry
# ---------------------------------------------------------------------------


def test_registry_returns_none_when_disabled():
    assert get_reranker(RerankerConfig(enabled=False)) is None


def test_registry_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown reranker provider"):
        get_reranker(RerankerConfig(enabled=True, provider="nope"))


def test_registry_rerank_api_dispatch():
    reranker = get_reranker(
        RerankerConfig(
            provider="rerank_api", model="rerank-v3.5",
            api_key="k", base_url="https://api.cohere.com/v2/rerank",
        )
    )
    assert isinstance(reranker, RerankApiReranker)


def test_registry_rerank_api_requires_key():
    with pytest.raises(ValueError, match="requires an api_key"):
        get_reranker(RerankerConfig(provider="rerank_api", base_url="https://x/rerank"))


def test_registry_rerank_api_requires_base_url():
    with pytest.raises(ValueError, match="requires a base_url"):
        get_reranker(RerankerConfig(provider="rerank_api", api_key="k"))


def test_registry_llm_dispatch():
    reranker = get_reranker(
        RerankerConfig(provider="llm", llm_provider="gemini", model="gemini-2.5-flash", api_key="k")
    )
    assert isinstance(reranker, LLMReranker)


def test_registry_llm_remote_requires_key():
    # A remote LLM flavour (gemini/openai) with no key is a config error → surfaced at save.
    with pytest.raises(ValueError, match="requires an api_key"):
        get_reranker(RerankerConfig(provider="llm", llm_provider="gemini"))


def test_registry_llm_ollama_needs_no_key():
    # Local Ollama is unauthenticated, so no key is required.
    reranker = get_reranker(RerankerConfig(provider="llm", llm_provider="ollama", model="llama3"))
    assert isinstance(reranker, LLMReranker)


# ---------------------------------------------------------------------------
# rerank_by_scores — shared reorder + graceful degradation
# ---------------------------------------------------------------------------


def test_rerank_by_scores_degrades_on_scoring_error():
    results = [_result("c1", "a"), _result("c2", "b")]

    def boom(_query, _texts):
        raise RuntimeError("remote timeout")

    out = rerank_by_scores("q", results, 50, boom, provider="test")
    assert out is results  # unchanged order/identity — never a 500


def test_rerank_by_scores_degrades_on_length_mismatch():
    results = [_result("c1", "a"), _result("c2", "b")]
    out = rerank_by_scores("q", results, 50, lambda _q, _t: [0.5], provider="test")
    assert out is results  # a mismatched score count → keep prior order


def test_rerank_by_scores_degrades_on_non_numeric_score():
    # float() coercion is inside the guard, so a non-numeric score degrades instead of 500-ing.
    results = [_result("c1", "a"), _result("c2", "b")]
    out = rerank_by_scores("q", results, 50, lambda _q, _t: [None, None], provider="test")
    assert out is results


def test_rerank_by_scores_sinks_nan_without_corrupting_order():
    # A NaN score would freeze sorted() if unguarded; it must sink and the rest still sort.
    results = [_result("c1", "a"), _result("c2", "b"), _result("c3", "c")]
    out = rerank_by_scores(
        "q", results, 50, lambda _q, _t: [float("nan"), 1.0, 9.0], provider="test"
    )
    assert out[0].chunk_id == "c3"  # the 9.0 candidate rose to the top despite the NaN
    assert {r.chunk_id for r in out} == {"c1", "c2", "c3"}  # nothing dropped


# ---------------------------------------------------------------------------
# RerankApiReranker
# ---------------------------------------------------------------------------


def _rerank_api() -> RerankApiReranker:
    return RerankApiReranker(model="m", api_key="k", base_url="https://x/rerank", top_n=50)


def test_rerank_api_maps_scores_by_index(monkeypatch):
    # The API returns results out of order, keyed by the original document index.
    reply = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}
    monkeypatch.setattr("api.adapters.reranker.rerank_api.post_json", lambda *a, **k: reply)
    ranked = _rerank_api().rerank("q", [_result("c1", "a"), _result("c2", "b")])
    assert [r.chunk_id for r in ranked] == ["c2", "c1"]  # index 1 (0.9) ranks above index 0 (0.1)
    assert ranked[0].score == 0.9


def test_rerank_api_reads_data_envelope(monkeypatch):
    # Voyage-style ``data`` envelope instead of ``results``.
    reply = {"data": [{"index": 0, "relevance_score": 0.8}, {"index": 1, "relevance_score": 0.2}]}
    monkeypatch.setattr("api.adapters.reranker.rerank_api.post_json", lambda *a, **k: reply)
    ranked = _rerank_api().rerank("q", [_result("c1", "a"), _result("c2", "b")])
    assert [r.chunk_id for r in ranked] == ["c1", "c2"]


def test_rerank_api_degrades_on_http_error(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr("api.adapters.reranker.rerank_api.post_json", boom)
    results = [_result("c1", "a"), _result("c2", "b")]
    assert _rerank_api().rerank("q", results) is results  # falls back to prior order


# ---------------------------------------------------------------------------
# LLMReranker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"score": 8}', 8.0),  # the structured-output shape
        ('{"score": 0}', 0.0),
        ('{"score": 12}', 10.0),  # clamped to the max
        ('{"score": -3}', 0.0),  # clamped to the min
        ('{"score": 7.5}', 7.5),  # a numeric score is taken as-is (be lenient on int vs float)
        ("9", 9.0),  # a bare JSON number is tolerated
        ('```json\n{"score": 6}\n```', 6.0),  # markdown-fenced JSON (provider ignored the schema)
        ('The score is {"score": 4}.', 4.0),  # object wrapped in prose is still recovered
        ('{"rating": 9}', 0.0),  # missing "score" field → lowest
        ('{"score": "high"}', 0.0),  # non-numeric score → lowest
        ('{"score": true}', 0.0),  # JSON bool is not a numeric score → lowest (not 1.0)
        ('{"score": [9]}', 0.0),  # non-scalar score → lowest
        ("null", 0.0),  # JSON null → lowest
        ("", 0.0),  # empty reply → lowest
        ('{"score": NaN}', 0.0),  # json.loads accepts NaN; non-finite must sink, not clamp to max
        ('{"score": Infinity}', 0.0),  # ditto for Infinity (would otherwise clamp to 10.0 = top)
        ("8/10", 0.0),  # bare prose with no JSON object stays unrecovered (no regex fallback)
        ("not json", 0.0),  # unparseable → lowest
    ],
)
def test_extract_score(reply, expected):
    assert _extract_score(reply) == expected


def test_llm_reranker_scores_and_reorders(monkeypatch):
    # chat_complete returns a structured-output JSON object per candidate; reorder by its score.
    scores = {"a": '{"score": 2}', "b": '{"score": 9}', "c": '{"score": 5}'}
    monkeypatch.setattr(
        "api.adapters.reranker.llm.chat_complete",
        lambda *, prompt, **_k: scores[prompt.rsplit("\n", 1)[-1]],
    )
    reranker = LLMReranker("ollama", "llama3", None, None, top_n=50)
    ranked = reranker.rerank("q", [_result("c1", "a"), _result("c2", "b"), _result("c3", "c")])
    assert [r.chunk_id for r in ranked] == ["c2", "c3", "c1"]


def test_llm_reranker_degrades_on_transport_error(monkeypatch):
    def boom(**_k):
        raise RuntimeError("remote down")

    monkeypatch.setattr("api.adapters.reranker.llm.chat_complete", boom)
    results = [_result("c1", "a"), _result("c2", "b")]
    assert LLMReranker("gemini", "m", None, "k").rerank("q", results) is results
