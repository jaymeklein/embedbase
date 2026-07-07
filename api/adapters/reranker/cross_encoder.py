"""Local cross-encoder reranker (sentence-transformers ``CrossEncoder``)."""

from __future__ import annotations

import os

from api.models.search import SearchResult

# Directory where the image bakes vendored reranker models (see api/Dockerfile).
# The files are fetched at *build* time via plain GET rather than huggingface_hub,
# because some networks block the HEAD requests the Hub uses for cache metadata —
# so a runtime Hub download fails even when the model is otherwise reachable.
# Loading a baked copy from disk sidesteps the Hub entirely and is offline-safe.
_MODELS_DIR = os.environ.get("EMBEDBASE_MODELS_DIR", "/opt/models")


def _resolve_model(model_name: str) -> str:
    """Return a baked local dir for ``model_name`` if present, else the name itself.

    A repo id like ``cross-encoder/ms-marco-MiniLM-L6-v2`` maps to
    ``{_MODELS_DIR}/ms-marco-MiniLM-L6-v2``; a value that is already a directory is
    returned unchanged. Falling back to the name preserves the normal Hub load for
    any model that was not vendored into the image.
    """
    if os.path.isdir(model_name):
        return model_name
    local = os.path.join(_MODELS_DIR, model_name.split("/")[-1])
    return local if os.path.isdir(local) else model_name


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

        self._model = CrossEncoder(_resolve_model(model_name))
        self._top_n = max(1, top_n)

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        if len(results) < 2:
            return results
        head, tail = results[: self._top_n], results[self._top_n :]
        scores = self._model.predict([(query, r.text) for r in head])
        scored = sorted(zip(head, scores, strict=True), key=lambda p: p[1], reverse=True)
        ordered = []
        for r, s in scored:
            r.score = float(s)  # surface the cross-encoder relevance as the result score
            ordered.append(r)
        ranked = ordered + tail
        for rank, result in enumerate(ranked, start=1):
            result.rank = rank
        return ranked
