"""Local cross-encoder reranker (sentence-transformers ``CrossEncoder``)."""

from __future__ import annotations

from api.models.search import SearchResult


class CrossEncoderReranker:
    """Reorders candidates by joint query-document relevance (Reranker Protocol).

    Scores at most ``top_n`` candidates with a cross-encoder and sorts them by
    that score; any candidates beyond ``top_n`` keep their incoming order and
    trail the reranked head. Only ``rank`` is rewritten — the response ``score``
    is overwritten downstream by the cross-collection RRF merge, so the raw
    cross-encoder logits are never surfaced.
    """

    def __init__(self, model_name: str, top_n: int = 50) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)
        self._top_n = max(1, top_n)

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        if len(results) < 2:
            return results
        head, tail = results[: self._top_n], results[self._top_n :]
        scores = self._model.predict([(query, r.text) for r in head])
        scored = sorted(zip(head, scores, strict=True), key=lambda p: p[1], reverse=True)
        ordered = [r for r, _ in scored]
        ranked = ordered + tail
        for rank, result in enumerate(ranked, start=1):
            result.rank = rank
        return ranked
