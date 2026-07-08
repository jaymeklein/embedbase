"""Unit tests for MCP tool helpers (infra-free).

Covers the A3 saturation ``notice`` the ``search_documents`` tool adds so an MCP caller is
told when relevant chunks fell below the ``top_k`` cut.
"""

from api.models.search import CollectionStat, SearchResponse, SearchResult
from api.services.mcp.tools import _saturation_notice


def _response(*, more_available: bool, ranked_pool: int, shown: int) -> SearchResponse:
    return SearchResponse(
        results=[SearchResult(chunk_id=f"c{i}", text="t", score=1.0) for i in range(shown)],
        collection_stats={
            "col1": CollectionStat(
                name="c", workspace_name="w", returned_after_filter=ranked_pool
            )
        },
        more_available=more_available,
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
