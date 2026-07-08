"""Adjacency expansion + span merge (plan A2).

A context that runs across consecutive pages is stored as consecutive chunks, but its tail chunks
rank low in isolation and fall past ``top_k`` — so the caller gets it half-complete. Because every
chunk carries a contiguous ``chunk_index`` and ``make_chunk_id`` is deterministic, "the chunk next
to this hit" is a *computed key*, not a search: we fetch a small window of neighbours around each
hit by id, coalesce contiguous runs into one span, and return the span's text in place of the orphan
chunk. Purely mechanical — no embeddings, no LLM.

Graceful degradation is the caller's job: it wraps ``expand_spans`` so any fetch failure falls back
to the un-expanded hits (search must never 500).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

from api.models.chunk import make_chunk_id
from api.models.search import SearchResult

# ``(collection_id, chunk_ids) -> the stored chunks for those ids`` (missing ids simply absent).
FetchFn = Callable[[str, list[str]], Sequence[SearchResult]]


def _hit_key(result: SearchResult) -> tuple[str, str, int] | None:
    """The ``(collection_id, document_id, chunk_index)`` a hit expands on, or ``None``.

    A hit is expandable only when it carries all three — its collection (from provenance) and its
    document + ordinal (from metadata). Anything missing (older data, a non-chunked source) makes
    the hit pass through unexpanded.
    """
    source = result.source
    document_id = result.metadata.get("document_id")
    chunk_index = result.metadata.get("chunk_index")
    if source is None or document_id is None or chunk_index is None:
        return None
    try:
        return source.collection_id, str(document_id), int(chunk_index)
    except (TypeError, ValueError):
        return None


def _page_range(chunks: list[SearchResult]) -> str | None:
    """Inclusive ``"lo-hi"`` page label for a span; ``None`` when single-page or pages unknown."""
    pages = [
        p for p in (c.metadata.get("page_number") for c in chunks) if isinstance(p, int)
    ]
    if not pages:
        return None
    lo, hi = min(pages), max(pages)
    return f"{lo}-{hi}" if lo != hi else None


def _text_len(chunk: SearchResult) -> int:
    return len(chunk.text)


def _fit_budget(
    ordered: list[tuple[int, SearchResult]], hit_indexes: set[int], char_budget: int
) -> list[SearchResult]:
    """Trim a contiguous, index-ordered span to ``char_budget`` by dropping its outermost
    NON-hit chunks first (the ones farthest from any hit), so the hit and its nearest context
    survive and the span stays contiguous. A hit chunk is never dropped — the budget caps *added*
    neighbours, not the matched chunk itself.
    """
    lo, hi = 0, len(ordered) - 1

    def size() -> int:
        # +2 per gap approximates the ``\n\n`` joiners used when the text is assembled.
        return sum(_text_len(ordered[j][1]) for j in range(lo, hi + 1)) + 2 * (hi - lo)

    while lo < hi and size() > char_budget:
        left_droppable = ordered[lo][0] not in hit_indexes
        right_droppable = ordered[hi][0] not in hit_indexes
        if not left_droppable and not right_droppable:
            break  # only hit chunks remain; keep them even if over budget
        if left_droppable and (
            not right_droppable or _text_len(ordered[lo][1]) >= _text_len(ordered[hi][1])
        ):
            lo += 1
        else:
            hi -= 1
    return [chunk for _idx, chunk in ordered[lo : hi + 1]]


def _merge_intervals(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent ``[start, end]`` windows into contiguous spans.

    Adjacent (``next.start <= cur.end + 1``) counts as one run — chunk 6 and chunk 7 are a
    contiguous context, so their windows fuse rather than returning two touching spans.
    """
    spans: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if spans and start <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def expand_spans(
    results: list[SearchResult], *, neighbors: int, char_budget: int, fetch: FetchFn
) -> list[SearchResult]:
    """Grow each hit into its adjacency span and coalesce overlapping spans (see module docstring).

    Args:
        results: The ranked top-k hits (already cut); their order is preserved.
        neighbors: Chunks to pull on EACH side of a hit (window half-width). ``<= 0`` returns
            ``results`` unchanged.
        char_budget: Soft cap on an assembled span's text; the farthest neighbours are dropped
            first and a hit's own text is never dropped.
        fetch: Pulls stored chunks by id within a collection (injected for testability, and so a
            failure can be caught by the caller and degraded).

    Returns:
        One SearchResult per span, in the hits' original best-rank order and renumbered 1..N: the
        best-ranked hit of the span with its text replaced by the assembled span text and
        ``source.page_range`` set. Non-expandable hits pass through unchanged.
    """
    if neighbors <= 0 or not results:
        return results

    # 1. Expandable hits + every neighbour id to fetch (grouped by collection → one fetch each).
    keys: dict[int, tuple[str, str, int]] = {}
    need: dict[str, set[str]] = defaultdict(set)
    for i, result in enumerate(results):
        key = _hit_key(result)
        if key is None:
            continue
        keys[i] = key
        collection_id, document_id, index = key
        for idx in range(max(0, index - neighbors), index + neighbors + 1):
            need[collection_id].add(make_chunk_id(document_id, idx))
    if not keys:
        return results

    # 2. Fetch neighbours once per collection; index by (collection, chunk id). Keying by chunk_id
    #    alone would let two collections that share a document_id + chunk_index clobber each other
    #    (make_chunk_id is collection-agnostic), so the per-collection fetch stays per-collection.
    fetched: dict[tuple[str, str], SearchResult] = {}
    for collection_id, ids in need.items():
        for chunk in fetch(collection_id, sorted(ids)):
            fetched[(collection_id, chunk.chunk_id)] = chunk

    # 3. Per (collection, document): merge hit windows into spans and record hit indexes.
    windows: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    hit_indexes: dict[tuple[str, str], set[int]] = defaultdict(set)
    for collection_id, document_id, index in keys.values():
        doc = (collection_id, document_id)
        windows[doc].append((max(0, index - neighbors), index + neighbors))
        hit_indexes[doc].add(index)
    spans: dict[tuple[str, str], list[tuple[int, int]]] = {
        doc: _merge_intervals(w) for doc, w in windows.items()
    }

    def span_for(doc: tuple[str, str], index: int) -> tuple[int, int]:
        for start, end in spans[doc]:
            if start <= index <= end:
                return start, end
        return index, index  # unreachable: every hit index lies in one of its own windows

    # 4. Walk results in order; emit each span once, at its best-ranked hit's position.
    emitted: set[tuple[str, str, int, int]] = set()
    out: list[SearchResult] = []
    for i, result in enumerate(results):
        if i not in keys:
            out.append(result)  # non-expandable — untouched
            continue
        collection_id, document_id, index = keys[i]
        doc = (collection_id, document_id)
        start, end = span_for(doc, index)
        marker = (collection_id, document_id, start, end)
        if marker in emitted:
            continue  # a better-ranked hit already represents this span
        emitted.add(marker)
        out.append(_build_span(result, doc, start, end, hit_indexes[doc], fetched, char_budget))

    for rank, result in enumerate(out, start=1):
        result.rank = rank
    return out


def _build_span(
    hit: SearchResult,
    doc: tuple[str, str],
    start: int,
    end: int,
    hit_indexes: set[int],
    fetched: dict[tuple[str, str], SearchResult],
    char_budget: int,
) -> SearchResult:
    """Assemble the ``[start, end]`` span for ``doc`` into a copy of the representative ``hit``."""
    collection_id, document_id = doc
    ordered = [
        (idx, fetched[key])
        for idx in range(start, end + 1)
        if (key := (collection_id, make_chunk_id(document_id, idx))) in fetched
    ]
    if not ordered:
        return hit  # nothing fetched (e.g. a transient miss) — keep the orphan hit
    kept = _fit_budget(ordered, hit_indexes, char_budget)
    span = hit.model_copy(deep=True)
    span.text = "\n\n".join(chunk.text for chunk in kept)
    if span.source is not None:
        span.source.page_range = _page_range(kept)
    return span
