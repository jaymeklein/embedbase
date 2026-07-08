"""Unit tests for A2 adjacency expansion + span merge (``api.services.expansion``)."""

from api.models.chunk import make_chunk_id
from api.models.search import SearchResult, SourceProvenance
from api.services.expansion import expand_spans


class _Store:
    """Fake chunk store for expansion tests.

    ``add`` registers a document's chunks; ``hit`` hands out a ranked SearchResult for one of them
    (with provenance, as search would); ``fetch`` serves neighbour lookups by id — the injected
    ``fetch`` callable ``expand_spans`` depends on.
    """

    def __init__(self) -> None:
        self._chunks: dict[tuple[str, str], SearchResult] = {}  # (collection_id, chunk_id) -> chunk

    def add(
        self, document_id: str, index: int, *, text: str | None = None,
        page: int | None = None, collection_id: str = "c1",
    ) -> "_Store":
        cid = make_chunk_id(document_id, index)
        self._chunks[(collection_id, cid)] = SearchResult(
            chunk_id=cid, text=text if text is not None else f"{document_id}-{index}", score=0.0,
            metadata={"document_id": document_id, "chunk_index": index, "page_number": page},
        )
        return self

    def hit(
        self, document_id: str, index: int, *, score: float = 1.0, collection_id: str = "c1",
    ) -> SearchResult:
        base = self._chunks[(collection_id, make_chunk_id(document_id, index))]
        return SearchResult(
            chunk_id=base.chunk_id, text=base.text, score=score, rank=0,
            source=SourceProvenance(
                collection_id=collection_id, collection_name="C",
                workspace_id="w", workspace_name="W",
            ),
            metadata=dict(base.metadata),
        )

    def fetch(self, collection_id: str, chunk_ids: list[str]) -> list[SearchResult]:
        ids = set(chunk_ids)
        return [
            chunk for (col, cid), chunk in self._chunks.items()
            if col == collection_id and cid in ids
        ]


def _doc(store: _Store, n: int, *, text=None, doc: str = "d") -> _Store:
    for i in range(n):
        store.add(doc, i, text=(text(i) if text else f"p{i}"), page=i + 1)
    return store


def test_expands_window_around_hit():
    store = _doc(_Store(), 5)
    out = expand_spans([store.hit("d", 2)], neighbors=1, char_budget=10_000, fetch=store.fetch)
    assert len(out) == 1
    assert out[0].text == "p1\n\np2\n\np3"  # chunks 1,2,3 assembled in order
    assert out[0].source.page_range == "2-4"  # pages 2,3,4
    assert out[0].rank == 1


def test_boundary_at_start_no_negative_index():
    store = _doc(_Store(), 5)
    out = expand_spans([store.hit("d", 0)], neighbors=1, char_budget=10_000, fetch=store.fetch)
    assert out[0].text == "p0\n\np1"  # no chunk -1; window clamps to [0,1]


def test_boundary_at_end_missing_neighbor_absent():
    store = _doc(_Store(), 5)
    out = expand_spans([store.hit("d", 4)], neighbors=1, char_budget=10_000, fetch=store.fetch)
    assert out[0].text == "p3\n\np4"  # chunk 5 does not exist → simply absent


def test_gap_in_document_is_skipped():
    store = _Store()
    for i in (0, 1, 3, 4):  # chunk 2 missing
        store.add("d", i, text=f"p{i}", page=i + 1)
    out = expand_spans([store.hit("d", 3)], neighbors=1, char_budget=10_000, fetch=store.fetch)
    assert out[0].text == "p3\n\np4"  # window [2,4]; 2 is absent, 3+4 present


def test_overlapping_hits_coalesce_into_one_span():
    store = _doc(_Store(), 6)
    hits = [store.hit("d", 2, score=0.9), store.hit("d", 3, score=0.8)]
    out = expand_spans(hits, neighbors=1, char_budget=10_000, fetch=store.fetch)
    assert len(out) == 1  # windows [1,3] and [2,4] overlap → one span [1,4]
    assert out[0].text == "p1\n\np2\n\np3\n\np4"
    assert out[0].score == 0.9  # the best-ranked hit represents the span


def test_distant_hits_stay_separate_spans():
    store = _doc(_Store(), 10)
    out = expand_spans(
        [store.hit("d", 1), store.hit("d", 8)], neighbors=1, char_budget=10_000, fetch=store.fetch
    )
    assert len(out) == 2  # windows [0,2] and [7,9] disjoint
    assert [r.rank for r in out] == [1, 2]


def test_char_budget_drops_farthest_neighbours_keeps_hit():
    store = _doc(_Store(), 5, text=lambda i: str(i) * 100)  # each chunk 100 chars, distinct
    # window [0,4] would be ~508 chars; budget 250 keeps the hit (idx 2) + its nearest neighbour.
    out = expand_spans([store.hit("d", 2)], neighbors=2, char_budget=250, fetch=store.fetch)
    assert "2" * 100 in out[0].text  # the hit chunk is never dropped
    assert len(out[0].text) <= 260  # trimmed to ~two chunks, within budget (+ joiner)
    assert "0" * 100 not in out[0].text  # farthest neighbour dropped first


def test_neighbors_zero_is_passthrough():
    store = _doc(_Store(), 3)
    hits = [store.hit("d", 1)]
    assert expand_spans(hits, neighbors=0, char_budget=10_000, fetch=store.fetch) is hits


def test_non_expandable_hits_pass_through_without_fetching():
    def _boom(_collection_id, _ids):
        raise AssertionError("fetch must not be called when no hit is expandable")

    orphan = SearchResult(chunk_id="x", text="t", score=1.0, metadata={})  # no document_id/index
    assert expand_spans([orphan], neighbors=1, char_budget=10_000, fetch=_boom) == [orphan]


def test_mixed_expandable_and_orphan_preserve_order():
    store = _doc(_Store(), 3)
    orphan = SearchResult(chunk_id="x", text="orphan", score=0.5, metadata={})
    out = expand_spans(
        [store.hit("d", 1), orphan], neighbors=1, char_budget=10_000, fetch=store.fetch
    )
    assert len(out) == 2
    assert out[0].text == "p0\n\np1\n\np2"  # expanded hit
    assert out[1].text == "orphan"  # untouched
    assert [r.rank for r in out] == [1, 2]


def test_two_documents_expand_independently():
    store = _Store()
    for i in range(3):
        store.add("a", i, text=f"a{i}", page=i + 1)
        store.add("b", i, text=f"b{i}", page=i + 1)
    out = expand_spans(
        [store.hit("a", 1), store.hit("b", 1)], neighbors=1, char_budget=10_000, fetch=store.fetch
    )
    assert len(out) == 2
    assert out[0].text == "a0\n\na1\n\na2"
    assert out[1].text == "b0\n\nb1\n\nb2"


def test_same_document_id_across_collections_is_not_cross_contaminated():
    # Two collections hold a document with the SAME document_id + chunk_index but different text
    # (make_chunk_id is collection-agnostic). The per-collection fetch must stay per-collection —
    # one collection's chunk must not clobber the other's in the fetched index.
    store = _Store()
    for i in range(3):
        store.add("shared", i, text=f"A{i}", page=i + 1, collection_id="c1")
        store.add("shared", i, text=f"B{i}", page=i + 1, collection_id="c2")
    out = expand_spans(
        [store.hit("shared", 1, collection_id="c1"), store.hit("shared", 1, collection_id="c2")],
        neighbors=1, char_budget=10_000, fetch=store.fetch,
    )
    assert len(out) == 2
    assert {r.text for r in out} == {"A0\n\nA1\n\nA2", "B0\n\nB1\n\nB2"}  # each from its own collection
