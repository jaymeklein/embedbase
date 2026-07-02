"""Unit tests for search service filters.

Vector + BM25 fusion moved into a single SQL round-trip
(``PgvectorAdapter.hybrid_search``, Phase 4 item 2), which the SQLite suite can't
exercise — the old Python RRF (``_reciprocal_rank_fusion`` + ``services.bm25``) was
removed with it. HYBRID routing/fallback is covered via the fake in
``test_search_service.py``; the SQL itself is verified against live ParadeDB.
"""

from api.models.search import SearchFilters, SearchResult
from api.services.search import apply_filters

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
