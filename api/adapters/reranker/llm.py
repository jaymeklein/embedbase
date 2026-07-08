"""LLM-as-reranker: pointwise relevance scoring over a chat model."""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from api.adapters.llm_chat import chat_complete
from api.adapters.reranker.reorder import rerank_by_scores
from api.models.search import SearchResult

# Pointwise prompt: score ONE passage's relevance to the query. A listwise prompt won't fit
# ``top_n`` full pages, so we score each candidate independently and reorder by score. The reply is
# constrained to ``{"score": <int>}`` by provider-native structured output (see _SCORE_SCHEMA), so
# we read the field instead of parsing prose.
_SCORE_PROMPT = (
    "Rate the passage's relevance to the search query on an integer scale from 0 (irrelevant) to "
    "10 (directly and fully answers the query). Respond with a JSON object of the form "
    '{{"score": <integer 0-10>}}.\n\nQUERY: {query}\n\nPASSAGE:\n{passage}'
)
# One integer field, enforced provider-side by structured output. Kept minimal (no min/max) so it
# stays within every provider's structured-output subset; the 0–10 bound is applied by _clamp_score.
_SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"score": {"type": "integer"}},
    "required": ["score"],
}
# Candidates can be whole pages; truncate so each prompt stays small and cheap.
_SNIPPET_CHARS = 1500
# Score candidates concurrently — pointwise scoring is embarrassingly parallel, and each call
# is a full round-trip to a (hopefully fast) remote LLM.
_CONCURRENCY = 8
_SCORE_MIN, _SCORE_MAX = 0.0, 10.0


def _clamp_score(value: float) -> float:
    return max(_SCORE_MIN, min(_SCORE_MAX, value))


def _load_json(reply: str) -> Any:
    """Parse ``reply`` as JSON; on failure retry the outermost ``{...}`` slice.

    Provider-native structured output returns a clean JSON document, but a provider that only
    *partially* honours the request (or ignores it while still obeying the prompt) often wraps the
    object in markdown fences or stray prose (```` ```json\\n{"score": 8}\\n``` ````). Loading the
    outermost brace-slice recovers those without a prose parser — a reply that carries no JSON object
    at all (e.g. ``"8/10"``) stays unrecovered and the caller sinks it. Returns ``None`` when nothing
    parses.
    """
    try:
        return json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = reply.find("{"), reply.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(reply[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _extract_score(reply: str) -> float:
    """Read the relevance score from a structured-output JSON reply; lowest score when malformed.

    Structured output constrains the reply to ``{"score": <int>}``, so we read the field instead of
    scraping prose (tolerating a fenced/prose-wrapped object via ``_load_json``); a bare JSON number
    is accepted too. Anything that doesn't yield a finite number — a provider that ignored the schema,
    a missing/non-numeric/boolean ``score`` — sinks that one candidate to the lowest score rather than
    failing the whole rerank (a transport error degrades the whole batch instead — see reorder).
    """
    data = _load_json(reply)
    if isinstance(data, dict):
        data = data.get("score")
    # Reject non-numeric JSON types up front: bool is an int subclass (``float(True)`` is ``1.0``, a
    # misleading "score"), and null/array/object are not scores — only a real number or a numeric
    # string should reach ``float()``.
    if isinstance(data, bool) or not isinstance(data, int | float | str):
        return _SCORE_MIN
    try:
        score = float(data)
    except (TypeError, ValueError):  # a non-numeric string like "high"
        return _SCORE_MIN
    # json.loads accepts the non-standard NaN/Infinity tokens; a non-finite score would clamp to
    # 10.0 (max) and rank a garbage reply at the TOP, so treat it as malformed and sink it instead.
    return _clamp_score(score) if math.isfinite(score) else _SCORE_MIN


class LLMReranker:
    """Reorders candidates by LLM-scored relevance (Reranker Protocol).

    Scores each candidate independently (pointwise) with a temperature-0 chat call over the
    shared transport (Ollama / OpenAI-compatible / native Gemini), then reorders by score.
    Pointwise + truncated snippets keep each prompt small; calls run concurrently. Any transport
    error degrades to the pre-rerank order (see reorder). Heavier than a cross-encoder —
    practical only against a fast remote LLM, not local CPU.
    """

    def __init__(
        self,
        llm_provider: str,
        model: str,
        base_url: str | None,
        api_key: str | None,
        top_n: int = 50,
        timeout: float = 60.0,
    ) -> None:
        if llm_provider != "ollama" and not (api_key or "").strip():
            raise ValueError(f"llm reranker provider {llm_provider!r} requires an api_key")
        self._llm_provider = llm_provider
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._top_n = max(1, top_n)
        self._timeout = timeout

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        return rerank_by_scores(query, results, self._top_n, self._score, provider="llm")

    def _score(self, query: str, texts: list[str]) -> list[float]:
        with ThreadPoolExecutor(max_workers=_CONCURRENCY) as pool:
            return list(pool.map(lambda text: self._score_one(query, text), texts))

    def _score_one(self, query: str, text: str) -> float:
        reply = chat_complete(
            provider=self._llm_provider,
            model=self._model,
            base_url=self._base_url,
            api_key=self._api_key,
            prompt=_SCORE_PROMPT.format(query=query, passage=text[:_SNIPPET_CHARS]),
            temperature=0.0,
            response_schema=_SCORE_SCHEMA,
            timeout=self._timeout,
        )
        return _extract_score(reply)
