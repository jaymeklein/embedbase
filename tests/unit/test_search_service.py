"""Unit tests for search_collection and multi_collection_search."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.models.search import SearchFilters, SearchMode, SearchRequest, SearchResult
from api.services.search import (
    _apply_provenance,
    _merge_collections_rrf,
    _rank_by_bm25,
    _update_top_k_stats,
    multi_collection_search,
    search_collection,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(chunk_id: str, score: float = 1.0, **metadata: object) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text="text", score=score, metadata=dict(metadata))


class FakeRedis:
    def __init__(self, store: dict | None = None) -> None:
        self.store: dict = store or {}

    def get(self, key: str) -> object:
        return self.store.get(key)

    def set(self, key: str, value: object, ex: int | None = None) -> None:
        self.store[key] = value


def _corpus_redis(collection_id: str, entries: list[list[str]], version: int = 1) -> FakeRedis:
    """Build a FakeRedis with the given corpus triples [chunk_id, doc_id, text]."""
    return FakeRedis({
        f"bm25:{collection_id}:corpus": json.dumps(entries),
        f"bm25:{collection_id}:version": str(version),
    })


class FakeVectorStore:
    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = results or []
        self.last_top_k: int = 0

    def search(
        self,
        collection_id: str,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        self.last_top_k = top_k
        return self._results[:top_k]

    def upsert(self, *args: object, **kwargs: object) -> None: ...
    def delete_document(self, *args: object) -> None: ...
    def delete_collection(self, *args: object) -> None: ...
    def list_documents(self, *args: object) -> list: ...


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]] * len(texts)

    @property
    def dimensions(self) -> int:
        return 3


# ---------------------------------------------------------------------------
# _rank_by_bm25
# ---------------------------------------------------------------------------


def test_rank_by_bm25_uses_chunk_id():
    results = [_result("c1"), _result("c2")]
    scores = {"c1": 0.9, "c2": 0.2}
    ranked = _rank_by_bm25(results, scores)
    assert ranked[0].chunk_id == "c1"
    assert ranked[1].chunk_id == "c2"


def test_rank_by_bm25_missing_chunk_id_scores_zero():
    results = [_result("c1"), _result("c2")]
    scores = {"c2": 0.5}
    ranked = _rank_by_bm25(results, scores)
    assert ranked[0].chunk_id == "c2"


# ---------------------------------------------------------------------------
# _merge_collections_rrf (second-level RRF)
# ---------------------------------------------------------------------------


def test_merge_collections_rrf_assigns_sequential_ranks():
    # One collection, already rank-ordered → fused order follows input order.
    per_collection = [[_result("a"), _result("b"), _result("c")]]
    merged = _merge_collections_rrf(per_collection)
    assert [r.rank for r in merged] == [1, 2, 3]
    assert merged[0].chunk_id == "a"


def test_merge_collections_rrf_empty():
    assert _merge_collections_rrf([]) == []


def test_merge_collections_rrf_interleaves_by_rank():
    # Each collection's top hit must outrank either collection's second hit.
    per_collection = [[_result("a1"), _result("a2")], [_result("b1"), _result("b2")]]
    merged = _merge_collections_rrf(per_collection)
    assert {merged[0].chunk_id, merged[1].chunk_id} == {"a1", "b1"}


def test_merge_collections_rrf_does_not_mutate_originals():
    originals = [_result("a"), _result("b")]
    original_ranks = [r.rank for r in originals]
    _merge_collections_rrf([originals])
    assert [r.rank for r in originals] == original_ranks


# ---------------------------------------------------------------------------
# _update_top_k_stats
# ---------------------------------------------------------------------------


def test_update_top_k_stats_increments_counts():
    from api.models.search import CollectionStat, SourceProvenance

    results = [
        SearchResult(
            chunk_id="a", text="t", score=1.0,
            source=SourceProvenance(
                collection_id="col1", collection_name="c", workspace_id="ws1", workspace_name="w"
            ),
        ),
        SearchResult(
            chunk_id="b", text="t", score=0.9,
            source=SourceProvenance(
                collection_id="col1", collection_name="c", workspace_id="ws1", workspace_name="w"
            ),
        ),
    ]
    stats = {"col1": CollectionStat(name="c", workspace_name="w")}
    _update_top_k_stats(results, stats)
    assert stats["col1"].contributed_to_top_k == 2


def test_update_top_k_stats_ignores_missing_source():
    from api.models.search import CollectionStat

    results = [_result("a")]
    stats = {"col1": CollectionStat(name="c", workspace_name="w")}
    _update_top_k_stats(results, stats)
    assert stats["col1"].contributed_to_top_k == 0


# ---------------------------------------------------------------------------
# _apply_provenance
# ---------------------------------------------------------------------------


def test_apply_provenance_sets_source_on_all_results():
    results = [_result("a", document_id="d1"), _result("b", document_id="d2")]
    info = {"collection_name": "col", "workspace_id": "ws1", "workspace_name": "my-ws"}
    _apply_provenance(results, "col1", info)
    for r in results:
        assert r.source is not None
        assert r.source.collection_id == "col1"
        assert r.source.workspace_id == "ws1"


def test_apply_provenance_propagates_document_id():
    results = [_result("a", document_id="doc42")]
    info = {"collection_name": "col", "workspace_id": "ws1", "workspace_name": "ws"}
    _apply_provenance(results, "col1", info)
    assert results[0].source is not None
    assert results[0].source.document_id == "doc42"


# ---------------------------------------------------------------------------
# search_collection — semantic mode
# ---------------------------------------------------------------------------


def test_search_collection_semantic_mode_returns_results():
    candidates = [_result("a", score=0.9), _result("b", score=0.5)]
    vs = FakeVectorStore(candidates)
    results, mode, retrieved, returned = search_collection(
        "col1", [0.1], "query", top_k=5,
        mode=SearchMode.SEMANTIC, vector_store=vs, redis_client=FakeRedis(),
    )
    assert mode == SearchMode.SEMANTIC
    assert {r.chunk_id for r in results} == {"a", "b"}


def test_search_collection_respects_top_k():
    candidates = [_result(f"c{i}") for i in range(10)]
    vs = FakeVectorStore(candidates)
    results, _, _, _ = search_collection(
        "col1", [0.1], "q", top_k=3,
        mode=SearchMode.SEMANTIC, vector_store=vs, redis_client=FakeRedis(),
    )
    assert len(results) <= 3


def test_search_collection_fan_out_multiplies_candidate_request():
    candidates = [_result(f"c{i}") for i in range(40)]
    vs = FakeVectorStore(candidates)
    search_collection(
        "col1", [0.1], "q", top_k=5, fan_out=4,
        mode=SearchMode.SEMANTIC, vector_store=vs, redis_client=FakeRedis(),
    )
    assert vs.last_top_k == 20  # 5 * 4


def test_search_collection_fan_out_clamped_to_10():
    candidates = [_result(f"c{i}") for i in range(100)]
    vs = FakeVectorStore(candidates)
    search_collection(
        "col1", [0.1], "q", top_k=5, fan_out=99,
        mode=SearchMode.SEMANTIC, vector_store=vs, redis_client=FakeRedis(),
    )
    assert vs.last_top_k == 50  # 5 * 10


# ---------------------------------------------------------------------------
# search_collection — hybrid mode
# ---------------------------------------------------------------------------


def test_search_collection_hybrid_mode_returns_hybrid():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    candidates = [
        _result("c1"),
        _result("c2"),
        _result("c3"),
    ]
    rds = _corpus_redis("col1", [
        ["c1", "doc1", "machine learning python"],
        ["c2", "doc2", "cooking recipes dinner"],
        ["c3", "doc3", "gardening flowers plants"],
    ])
    vs = FakeVectorStore(candidates)
    _, mode, _, _ = search_collection(
        "col1", [0.1], "machine learning", top_k=5,
        mode=SearchMode.HYBRID, vector_store=vs, redis_client=rds,
    )
    assert mode == SearchMode.HYBRID


def test_search_collection_bm25_mode_ranks_by_keyword():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    # Vector order puts the keyword match last; BM25 must pull it to the top.
    candidates = [
        _result("c2", score=0.9),
        _result("c3", score=0.8),
        _result("c1", score=0.1),
    ]
    rds = _corpus_redis("col1", [
        ["c1", "doc1", "machine learning python"],
        ["c2", "doc2", "cooking recipes dinner"],
        ["c3", "doc3", "gardening flowers plants"],
    ])
    vs = FakeVectorStore(candidates)
    results, mode, _, _ = search_collection(
        "col1", [0.1], "machine learning", top_k=5,
        mode=SearchMode.BM25, vector_store=vs, redis_client=rds,
    )
    assert mode == SearchMode.BM25
    assert results[0].chunk_id == "c1"
    assert results[0].rank == 1


def test_search_collection_bm25_falls_back_to_semantic_when_no_corpus():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    vs = FakeVectorStore([_result("c1")])
    _, mode, _, _ = search_collection(
        "col_empty", [0.1], "q", top_k=5,
        mode=SearchMode.BM25, vector_store=vs, redis_client=FakeRedis(),
    )
    assert mode == SearchMode.SEMANTIC_ONLY


def test_search_collection_falls_back_to_semantic_when_no_bm25():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    candidates = [_result("c1")]
    vs = FakeVectorStore(candidates)
    _, mode, _, _ = search_collection(
        "col_empty", [0.1], "q", top_k=5,
        mode=SearchMode.HYBRID, vector_store=vs, redis_client=FakeRedis(),
    )
    assert mode == SearchMode.SEMANTIC_ONLY


def test_search_collection_filters_applied_after_ranking():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    candidates = [
        _result("c1", language="python"),
        _result("c2", language="go"),
    ]
    vs = FakeVectorStore(candidates)
    results, _, retrieved, returned = search_collection(
        "col1", [0.1], "q", top_k=5,
        mode=SearchMode.SEMANTIC, filters=SearchFilters(language="python"),
        vector_store=vs, redis_client=FakeRedis(),
    )
    assert retrieved == 2
    assert returned == 1
    assert results[0].chunk_id == "c1"


# ---------------------------------------------------------------------------
# multi_collection_search
# ---------------------------------------------------------------------------


def _make_db_mock(collection_name: str = "my-col", workspace_id: str = "ws1",
                  workspace_name: str = "my-ws", col_id: str = "col1") -> AsyncMock:
    row = MagicMock()
    row.id = col_id
    row.name = collection_name
    row.workspace_id = workspace_id
    row.workspace_name = workspace_name

    execute_result = MagicMock()
    execute_result.fetchall.return_value = [row]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)
    return db


@pytest.mark.asyncio
async def test_multi_collection_search_single_collection():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    candidates = [_result("c1", score=0.9), _result("c2", score=0.5)]
    vs = FakeVectorStore(candidates)
    request = SearchRequest(query="hello", collection_ids=["col1"])
    response = await multi_collection_search(
        request,
        db=_make_db_mock(),
        embedder=FakeEmbedder(),
        vector_store=vs,
        redis_client=FakeRedis(),
    )
    assert len(response.results) <= request.top_k
    assert response.search_mode in {SearchMode.HYBRID, SearchMode.SEMANTIC_ONLY}


@pytest.mark.asyncio
async def test_multi_collection_search_under_delivered_when_few_results():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    candidates = [_result("only_one")]
    vs = FakeVectorStore(candidates)
    request = SearchRequest(query="q", collection_ids=["col1"], top_k=10)
    response = await multi_collection_search(
        request,
        db=_make_db_mock(),
        embedder=FakeEmbedder(),
        vector_store=vs,
        redis_client=FakeRedis(),
    )
    assert response.under_delivered is True


@pytest.mark.asyncio
async def test_multi_collection_search_skips_unknown_collection():
    from api.services import search as search_module

    search_module._bm25_cache.clear()

    execute_result = MagicMock()
    execute_result.fetchall.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)

    vs = FakeVectorStore([_result("c1")])
    request = SearchRequest(query="q", collection_ids=["no-such-col"])
    response = await multi_collection_search(
        request,
        db=db,
        embedder=FakeEmbedder(),
        vector_store=vs,
        redis_client=FakeRedis(),
    )
    assert response.results == []
    assert response.collection_stats == {}


@pytest.mark.asyncio
async def test_multi_collection_search_populates_stats():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    candidates = [_result("c1"), _result("c2")]
    vs = FakeVectorStore(candidates)
    request = SearchRequest(query="q", collection_ids=["col1"], top_k=5)
    response = await multi_collection_search(
        request,
        db=_make_db_mock(),
        embedder=FakeEmbedder(),
        vector_store=vs,
        redis_client=FakeRedis(),
    )
    assert "col1" in response.collection_stats
    stat = response.collection_stats["col1"]
    assert stat.name == "my-col"
    assert stat.workspace_name == "my-ws"


@pytest.mark.asyncio
async def test_multi_collection_search_uses_default_fan_out_when_not_set():
    """Verify _DEFAULT_FAN_OUT (4) is used when request.fan_out is None."""
    from api.services import search as search_module
    from api.services.search import _DEFAULT_FAN_OUT

    search_module._bm25_cache.clear()
    candidates = [_result(f"c{i}") for i in range(100)]
    vs = FakeVectorStore(candidates)
    request = SearchRequest(query="q", collection_ids=["col1"], top_k=5, fan_out=None)
    assert request.fan_out is None
    await multi_collection_search(
        request,
        db=_make_db_mock(),
        embedder=FakeEmbedder(),
        vector_store=vs,
        redis_client=FakeRedis(),
    )
    assert vs.last_top_k == 5 * _DEFAULT_FAN_OUT


@pytest.mark.asyncio
async def test_multi_collection_search_results_sorted_descending():
    from api.services import search as search_module

    search_module._bm25_cache.clear()
    candidates = [_result("a", score=0.3), _result("b", score=0.9), _result("c", score=0.5)]
    vs = FakeVectorStore(candidates)
    request = SearchRequest(query="q", collection_ids=["col1"])
    response = await multi_collection_search(
        request,
        db=_make_db_mock(),
        embedder=FakeEmbedder(),
        vector_store=vs,
        redis_client=FakeRedis(),
    )
    scores = [r.score for r in response.results]
    assert scores == sorted(scores, reverse=True)
