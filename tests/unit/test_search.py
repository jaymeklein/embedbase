"""Unit tests for search service functions: filters, BM25 scoring, and RRF."""

import json

import pytest

from api.models.redis import CorpusConfig
from api.models.search import SearchFilters, SearchResult
from api.services.bm25 import score_semantic, score_structured
from api.services.search import (
    _get_bm25_scores,
    _reciprocal_rank_fusion,
    apply_filters,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self, store: dict | None = None):
        self.store: dict = store or {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value, ex=None):
        self.store[key] = value


def _result(chunk_id: str, score: float = 1.0, **metadata) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text="text", score=score, metadata=metadata)


# ---------------------------------------------------------------------------
# apply_filters / _matches
# ---------------------------------------------------------------------------


def test_apply_filters_none_returns_all():
    results = [_result("a"), _result("b")]
    assert apply_filters(results, None) == results


def test_apply_filters_empty_filters_returns_all():
    results = [_result("a"), _result("b")]
    assert apply_filters(results, SearchFilters()) == results


def test_apply_filters_by_language():
    results = [
        _result("a", language="python"),
        _result("b", language="javascript"),
    ]
    filtered = apply_filters(results, SearchFilters(language="python"))
    assert [r.chunk_id for r in filtered] == ["a"]


def test_apply_filters_by_filename():
    results = [_result("a", filename="foo.py"), _result("b", filename="bar.py")]
    filtered = apply_filters(results, SearchFilters(filename="foo.py"))
    assert [r.chunk_id for r in filtered] == ["a"]


def test_apply_filters_by_tags_all_must_match():
    results = [
        _result("a", tags=["ml", "python"]),
        _result("b", tags=["ml"]),
        _result("c", tags=["python"]),
    ]
    filtered = apply_filters(results, SearchFilters(tags=["ml", "python"]))
    assert [r.chunk_id for r in filtered] == ["a"]


def test_apply_filters_combined():
    results = [
        _result("a", language="python", filename="foo.py"),
        _result("b", language="python", filename="bar.py"),
        _result("c", language="go", filename="foo.py"),
    ]
    filtered = apply_filters(results, SearchFilters(language="python", filename="foo.py"))
    assert [r.chunk_id for r in filtered] == ["a"]


def test_apply_filters_no_match_returns_empty():
    results = [_result("a", language="python")]
    assert apply_filters(results, SearchFilters(language="go")) == []


# ---------------------------------------------------------------------------
# score_semantic / score_structured
# ---------------------------------------------------------------------------


def test_score_semantic_descending_order():
    results = [_result("a"), _result("b"), _result("c")]
    scored = score_semantic(results)
    scores = [r.score for r in scored]
    assert scores == sorted(scores, reverse=True)


def test_score_semantic_first_rank_highest():
    results = [_result("first"), _result("second")]
    scored = score_semantic(results)
    assert scored[0].chunk_id == "first"


def test_score_semantic_does_not_mutate_originals():
    results = [_result("a", score=99.0), _result("b", score=99.0)]
    score_semantic(results)
    assert results[0].score == 99.0
    assert results[1].score == 99.0


def test_score_structured_descending_order():
    results = [_result("a"), _result("b"), _result("c")]
    scored = score_structured(results)
    scores = [r.score for r in scored]
    assert scores == sorted(scores, reverse=True)


def test_score_structured_uses_bm25_weight():
    alpha = 0.7
    results = [_result("a")]
    scored = score_structured(results, alpha=alpha, k=60)
    expected = (1 - alpha) * (1 / (60 + 1))
    assert abs(scored[0].score - expected) < 1e-9


def test_score_structured_does_not_mutate_originals():
    results = [_result("a", score=99.0)]
    score_structured(results)
    assert results[0].score == 99.0


# ---------------------------------------------------------------------------
# _get_bm25_scores
# ---------------------------------------------------------------------------


def _corpus_redis(collection_id: str, entries: list[list[str]], version: int = 1) -> FakeRedis:
    return FakeRedis({
        f"bm25:{collection_id}:corpus": json.dumps(entries),
        f"bm25:{collection_id}:version": str(version),
    })


def test_get_bm25_scores_returns_scores_for_matching_query():
    # Three docs needed: BM25 IDF = log((N-df+0.5)/(df+0.5)).
    # With N=2 and df=1 that's log(1)=0 for all scores.
    # A third unrelated doc pushes N to 3, making IDF positive.
    rds = _corpus_redis("col1", [
        ["doc1", "machine learning algorithms"],
        ["doc2", "cooking recipes dinner"],
        ["doc3", "gardening plants flowers"],
    ])
    config = CorpusConfig("col1")
    scores = _get_bm25_scores(rds, config, "machine learning")
    assert scores["doc1"] > scores["doc2"]
    assert scores["doc1"] > scores["doc3"]


def test_get_bm25_scores_empty_corpus_returns_empty():
    rds = FakeRedis()
    config = CorpusConfig("col1")
    assert _get_bm25_scores(rds, config, "anything") == {}


def test_get_bm25_scores_returns_all_doc_ids():
    entries = [["doc1", "hello world"], ["doc2", "foo bar"]]
    rds = _corpus_redis("col2", entries)
    config = CorpusConfig("col2")
    scores = _get_bm25_scores(rds, config, "hello")
    assert set(scores.keys()) == {"doc1", "doc2"}


def test_get_bm25_scores_cache_avoids_rebuild(monkeypatch):
    from api.services import search as search_module

    rds = _corpus_redis("col3", [["doc1", "cached content"]], version=5)
    config = CorpusConfig("col3")

    build_count = 0
    original_bm25 = __import__("rank_bm25").BM25Okapi

    class CountingBM25(original_bm25):
        def __init__(self, *args, **kwargs):
            nonlocal build_count
            build_count += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("api.services.search.BM25Okapi", CountingBM25)
    search_module._bm25_cache.clear()

    _get_bm25_scores(rds, config, "cached")
    _get_bm25_scores(rds, config, "content")

    assert build_count == 1


def test_get_bm25_scores_rebuilds_on_version_change(monkeypatch):
    from api.services import search as search_module

    rds = _corpus_redis("col4", [["doc1", "text"]], version=1)
    config = CorpusConfig("col4")

    build_count = 0
    original_bm25 = __import__("rank_bm25").BM25Okapi

    class CountingBM25(original_bm25):
        def __init__(self, *args, **kwargs):
            nonlocal build_count
            build_count += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("api.services.search.BM25Okapi", CountingBM25)
    search_module._bm25_cache.clear()

    _get_bm25_scores(rds, config, "text")
    rds.store[f"bm25:col4:version"] = "2"
    _get_bm25_scores(rds, config, "text")

    assert build_count == 2


# ---------------------------------------------------------------------------
# _reciprocal_rank_fusion
# ---------------------------------------------------------------------------


def test_rrf_fused_score_higher_for_result_in_both_lists():
    shared = _result("shared")
    semantic_only = _result("semantic_only")
    bm25_only = _result("bm25_only")

    fused = _reciprocal_rank_fusion(
        vector_results=[shared, semantic_only],
        bm25_results=[shared, bm25_only],
    )
    fused_by_id = {r.chunk_id: r for r in fused}

    assert fused_by_id["shared"].score > fused_by_id["semantic_only"].score
    assert fused_by_id["shared"].score > fused_by_id["bm25_only"].score


def test_rrf_returns_all_unique_results():
    fused = _reciprocal_rank_fusion(
        vector_results=[_result("a"), _result("b")],
        bm25_results=[_result("b"), _result("c")],
    )
    assert {r.chunk_id for r in fused} == {"a", "b", "c"}


def test_rrf_ranks_are_assigned_sequentially():
    fused = _reciprocal_rank_fusion(
        vector_results=[_result("a"), _result("b")],
        bm25_results=[_result("a"), _result("c")],
    )
    ranks = [r.rank for r in fused]
    assert ranks == list(range(1, len(fused) + 1))


def test_rrf_prefers_semantic_metadata_for_shared_chunk():
    from api.models.search import SourceProvenance

    semantic_result = SearchResult(
        chunk_id="shared",
        text="from vector store",
        score=1.0,
        source=SourceProvenance(
            collection_id="col1",
            collection_name="my-col",
            workspace_id="ws1",
            workspace_name="my-ws",
        ),
    )
    bm25_result = _result("shared")

    fused = _reciprocal_rank_fusion(
        vector_results=[semantic_result],
        bm25_results=[bm25_result],
    )
    assert fused[0].source is not None
    assert fused[0].source.collection_id == "col1"


def test_rrf_sorted_descending_by_score():
    fused = _reciprocal_rank_fusion(
        vector_results=[_result("a"), _result("b"), _result("c")],
        bm25_results=[_result("a"), _result("d")],
    )
    scores = [r.score for r in fused]
    assert scores == sorted(scores, reverse=True)


def test_rrf_does_not_mutate_input_lists():
    original_score = 42.0
    vec = [_result("a", score=original_score)]
    bm = [_result("b", score=original_score)]
    _reciprocal_rank_fusion(vector_results=vec, bm25_results=bm)
    assert vec[0].score == original_score
    assert bm[0].score == original_score


def test_rrf_empty_bm25_returns_semantic_only():
    fused = _reciprocal_rank_fusion(
        vector_results=[_result("a"), _result("b")],
        bm25_results=[],
    )
    assert {r.chunk_id for r in fused} == {"a", "b"}


def test_rrf_empty_semantic_returns_bm25_only():
    fused = _reciprocal_rank_fusion(
        vector_results=[],
        bm25_results=[_result("x"), _result("y")],
    )
    assert {r.chunk_id for r in fused} == {"x", "y"}
