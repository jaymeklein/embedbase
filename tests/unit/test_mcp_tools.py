"""Unit tests for MCP tool helpers (infra-free).

Covers the A3 saturation ``notice`` the ``search_documents`` tool adds so an MCP caller is
told when relevant chunks fell below the ``top_k`` cut.
"""

from api.models.search import (
    CollectionStat,
    DocumentCoverage,
    SearchResponse,
    SearchResult,
)
from api.services.mcp.tools import _NOTICE_DOC_LIMIT, _saturation_notice


def _response(
    *,
    more_available: bool,
    ranked_pool: int,
    shown: int,
    coverage: list[DocumentCoverage] | None = None,
) -> SearchResponse:
    return SearchResponse(
        results=[SearchResult(chunk_id=f"c{i}", text="t", score=1.0) for i in range(shown)],
        collection_stats={
            "col1": CollectionStat(
                name="c", workspace_name="w", returned_after_filter=ranked_pool
            )
        },
        more_available=more_available,
        coverage=coverage or [],
    )


def test_saturation_notice_none_when_results_complete():
    # more_available is false → no warning, so the model doesn't learn to ignore it.
    assert _saturation_notice(_response(more_available=False, ranked_pool=3, shown=3), 20, 3) is None


def test_saturation_notice_reports_counts_and_action_when_more_available():
    notice = _saturation_notice(_response(more_available=True, ranked_pool=12, shown=3), 20, 3)
    assert notice is not None
    assert "12 chunks matched" in notice  # the ranked pool
    assert "top 3" in notice  # what was shown
    assert "9 more" in notice  # 12 - 3 fell below the cut
    assert "top_k" in notice and "20" in notice  # the actionable instruction + the cap


def test_saturation_notice_uses_ranked_count_not_coalesced_span_count():
    # A2 expansion may coalesce the top_k hits into FEWER spans; the notice must still report the
    # pre-expansion ranked count (the scale more_available was measured on), not len(results).
    resp = _response(more_available=True, ranked_pool=8, shown=4)  # 4 spans after coalescing
    notice = _saturation_notice(resp, 20, 5)  # but 5 ranked hits were shown before coalescing
    assert notice is not None
    assert "top 5" in notice  # the ranked count, not the 4 coalesced spans
    assert "3 more" in notice  # 8 - 5, not 8 - 4


def test_saturation_notice_names_documents_with_hidden_matches():
    # The notice enumerates each under-delivered document so the caller knows where to look.
    coverage = [
        DocumentCoverage(document_id="doc1", filename="report.pdf", returned=3, matched=8),
        DocumentCoverage(document_id="doc2", filename="notes.md", returned=1, matched=2),
    ]
    notice = _saturation_notice(
        _response(more_available=True, ranked_pool=12, shown=4, coverage=coverage), 20, 4
    )
    assert notice is not None
    assert "report.pdf: showing 3 of 8 matched (5 more below the cut)" in notice
    assert "notes.md: showing 1 of 2 matched (1 more below the cut)" in notice


def test_saturation_notice_falls_back_to_document_id_when_no_filename():
    coverage = [DocumentCoverage(document_id="doc1", filename=None, returned=1, matched=4)]
    notice = _saturation_notice(
        _response(more_available=True, ranked_pool=6, shown=1, coverage=coverage), 20, 1
    )
    assert notice is not None
    assert "doc1: showing 1 of 4 matched" in notice  # the id stands in for a missing filename


def test_saturation_notice_caps_enumerated_documents():
    # More documents than the inline cap → enumerate the first few, summarise the rest (the full
    # list is always in the structured ``coverage`` field, so nothing is silently dropped).
    extra = 3
    coverage = [
        DocumentCoverage(document_id=f"doc{i}", filename=f"f{i}.pdf", returned=1, matched=5)
        for i in range(_NOTICE_DOC_LIMIT + extra)
    ]
    notice = _saturation_notice(
        _response(more_available=True, ranked_pool=40, shown=8, coverage=coverage), 20, 8
    )
    assert notice is not None
    assert notice.count("showing 1 of 5 matched") == _NOTICE_DOC_LIMIT  # only the cap is enumerated
    assert f"and {extra} more document(s) with matches below the cut" in notice  # remainder summarised


def test_saturation_notice_omits_topk_suggestion_at_ceiling():
    # At the ceiling (ranked_shown == max_results) "raise top_k" is a dead-end — the request is
    # already clamped to max_results — so the notice reports what's hidden but drops the suggestion.
    coverage = [DocumentCoverage(document_id="doc1", filename="a.pdf", returned=20, matched=30)]
    notice = _saturation_notice(
        _response(more_available=True, ranked_pool=30, shown=20, coverage=coverage), 20, 20
    )
    assert notice is not None
    assert "a.pdf: showing 20 of 30 matched" in notice  # still reports the hidden matches
    assert "re-run" not in notice and "higher top_k" not in notice  # no dead-end suggestion
