"""Unit tests for the cross-encoder reranker and its registry."""

import pytest

from api.adapters.reranker import get_reranker
from api.adapters.reranker.cross_encoder import CrossEncoderReranker
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
