"""Local cross-encoder reranker (sentence-transformers ``CrossEncoder``)."""

from __future__ import annotations

import os

from api.adapters.reranker.reorder import rerank_by_scores
from api.constants import MODELS_DIR_DEFAULT, MODELS_DIR_ENV
from api.models.search import SearchResult

# Directory where the image bakes vendored reranker models (see api/Dockerfile).
# The files are fetched at *build* time via plain GET rather than huggingface_hub,
# because some networks block the HEAD requests the Hub uses for cache metadata —
# so a runtime Hub download fails even when the model is otherwise reachable.
# Loading a baked copy from disk sidesteps the Hub entirely and is offline-safe.
_MODELS_DIR = os.environ.get(MODELS_DIR_ENV, MODELS_DIR_DEFAULT)


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

    Scores at most ``top_n`` candidates with a local cross-encoder and sorts them by that
    score; candidates beyond ``top_n`` keep their incoming order and trail the reranked head.
    The shared :func:`rerank_by_scores` owns the reorder + graceful-degradation bookkeeping,
    so a model failure degrades to the pre-rerank order rather than 500-ing the search.
    """

    def __init__(self, model_name: str, top_n: int = 50) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(_resolve_model(model_name))
        self._top_n = max(1, top_n)

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        return rerank_by_scores(
            query,
            results,
            self._top_n,
            lambda q, texts: self._model.predict([(q, text) for text in texts]),
            provider="cross_encoder",
        )
