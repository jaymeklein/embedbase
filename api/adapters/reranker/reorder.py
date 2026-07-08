"""Shared reorder step for every reranker provider.

A reranker takes a query and a candidate pool and reorders it by joint relevance. Only *how*
the scores are produced differs per provider (local cross-encoder, hosted ``/rerank``, LLM);
the "score the head, sort, keep the tail, renumber" bookkeeping — and the graceful-degradation
contract — are identical, so they live here once instead of in each adapter.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import structlog

from api.models.search import SearchResult

logger = structlog.get_logger()

# ``(query, candidate_texts) -> one score per text, in the same order``.
ScoreFn = Callable[[str, list[str]], Sequence[float]]


def _finite_scores(raw: Sequence[float]) -> list[float]:
    """Coerce each score to a finite float; NaN/inf sink to the batch's lowest finite score.

    ``float()`` raises on a non-numeric score (a misbehaving provider) — the caller catches it and
    degrades. A NaN would otherwise silently break ``sorted()`` (a NaN sort key leaves items
    unmoved, so the reranker would ship a *mis-ordered* list without degrading) and is not
    JSON-serialisable; replacing any non-finite value with the lowest finite score (0.0 if none)
    keeps the result both correctly ordered and serialisable.
    """
    coerced = [float(score) for score in raw]
    finite = [score for score in coerced if math.isfinite(score)]
    floor = min(finite) if finite else 0.0
    return [score if math.isfinite(score) else floor for score in coerced]


def rerank_by_scores(
    query: str,
    results: list[SearchResult],
    top_n: int,
    score_fn: ScoreFn,
    *,
    provider: str,
) -> list[SearchResult]:
    """Score the first ``top_n`` candidates with ``score_fn`` and reorder them by score.

    Candidates beyond ``top_n`` keep their incoming order and trail the reranked head (this
    bounds the per-query rerank cost). Only the head's ``score`` — and every result's ``rank`` —
    is rewritten; the raw score becomes the surfaced relevance (overwritten later only by a
    genuine cross-collection RRF merge).

    If ``score_fn`` raises (e.g. a remote timeout), returns the wrong number of scores, or returns
    a non-numeric score, the **incoming order is returned unchanged**. That is the reranker's
    graceful-degradation contract: an optional second stage must never turn a search into a 500 —
    it degrades to the pre-rerank (RRF) order, exactly as a disabled reranker (``get_reranker`` →
    ``None``) does.

    Args:
        query: The search query.
        results: The candidate pool to reorder (already rank-ordered by the first stage).
        top_n: How many leading candidates to score (the rest trail unchanged).
        score_fn: Produces one relevance score per candidate text, in input order.
        provider: Provider name, for degradation log lines only.

    Returns:
        The reordered results (or ``results`` unchanged on degradation / fewer than 2 hits).
    """
    if len(results) < 2:
        return results
    head, tail = results[:top_n], results[top_n:]
    try:
        raw_scores = list(score_fn(query, [result.text for result in head]))
    except Exception as exc:  # any provider failure degrades to prior order, never a 500
        logger.warning(
            "reranker scoring failed; keeping pre-rerank order", provider=provider, error=str(exc)
        )
        return results
    if len(raw_scores) != len(head):
        logger.warning(
            "reranker returned a mismatched score count; keeping pre-rerank order",
            provider=provider,
            expected=len(head),
            got=len(raw_scores),
        )
        return results
    try:
        # Coerce/sanitise OUTSIDE-safe: a non-numeric score would otherwise raise here — after the
        # score_fn guard — and 500 the search (search.py does not wrap the rerank call).
        scores = _finite_scores(raw_scores)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "reranker returned non-numeric scores; keeping pre-rerank order",
            provider=provider,
            error=str(exc),
        )
        return results
    ranked_head = sorted(zip(head, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    for result, score in ranked_head:
        result.score = score  # already a finite float — surface it as the result relevance
    ordered = [result for result, _ in ranked_head] + tail
    for rank, result in enumerate(ordered, start=1):
        result.rank = rank
    return ordered
