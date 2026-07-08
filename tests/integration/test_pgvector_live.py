"""Live ParadeDB verification for the pgvector adapter's SQL.

The BM25 (``pdb.score`` / ``|||``) and single-round-trip hybrid (``hybrid_search``)
statements run only against a real ParadeDB instance — the SQLite-based suite can't
exercise them. These tests drive the *real* ``PgvectorAdapter`` (real asyncpg pool,
real schema bootstrap) against a running ParadeDB, covering the query-time behaviour
Phase 4 relies on:

* single round-trip hybrid fuses vector + BM25 via RRF (item 2),
* full-collection lexical recall — a keyword hit outside the vector top-k still
  surfaces (the recall ceiling Phase 3 deferred to here),
* SEMANTIC_ONLY fallback with real cosine scores when nothing matches lexically,
* metadata pre-filter pushdown into both candidate scans (item 1),

plus the ``search_collection`` service routing on top of the live adapter.

Opt in with ``EMBEDBASE_TEST_PARADEDB=1``; point at the DB with ``EMBEDBASE_TEST_PG_*``
(defaults match the bundled docker-compose ``postgres`` service). Skipped otherwise so
the default CI run stays DB-free. To run against the compose stack::

    docker compose up -d postgres
    EMBEDBASE_TEST_PARADEDB=1 EMBEDBASE_TEST_PG_HOST=localhost \
      EMBEDBASE_TEST_PG_PASSWORD=... .venv/bin/pytest tests/integration/test_pgvector_live.py -v
"""

import os
from uuid import uuid4

import pytest

from api.adapters.vector_store.pgvector import PgvectorAdapter
from api.models.chunk import Chunk, ChunkMetadata
from api.models.search import SearchFilters, SearchMode
from api.services.search import search_collection

pytestmark = pytest.mark.paradedb

_DIM = 3


def _pg_kwargs() -> dict:
    return {
        "host": os.environ.get("EMBEDBASE_TEST_PG_HOST", "localhost"),
        "port": int(os.environ.get("EMBEDBASE_TEST_PG_PORT", "5432")),
        "database": os.environ.get("EMBEDBASE_TEST_PG_DB", "embedbase"),
        "user": os.environ.get("EMBEDBASE_TEST_PG_USER", "embedbase"),
        "password": os.environ.get("EMBEDBASE_TEST_PG_PASSWORD", ""),
    }


def _chunk(doc_id: str, text: str, *,
           filename: str = "f.txt", tags: list[str] | None = None) -> Chunk:
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            source_file=f"/{filename}", filename=filename, parser="txt",
            document_id=doc_id, chunk_index=0, tags=tags or [],
        ),
    )


@pytest.fixture
def live_store():
    """A real adapter bound to an isolated collection; dropped on teardown.

    ``index_min_rows`` is set absurdly high so the tiny fixtures never trigger a
    ``CREATE INDEX CONCURRENTLY`` HNSW build — a sequential scan is correct and the
    concurrent build only adds latency and log noise for a handful of rows.
    """
    if not os.environ.get("EMBEDBASE_TEST_PARADEDB"):
        pytest.skip("set EMBEDBASE_TEST_PARADEDB=1 (+ EMBEDBASE_TEST_PG_*) to run live ParadeDB tests")
    adapter = PgvectorAdapter(dimensions=_DIM, index_min_rows=10_000, **_pg_kwargs())
    if not adapter.ping():
        pytest.skip(f"ParadeDB not reachable at {_pg_kwargs()['host']}:{_pg_kwargs()['port']}")
    collection = f"test_hybrid_{uuid4().hex[:12]}"
    try:
        yield adapter, collection
    finally:
        adapter.delete_collection(collection)


# A deliberately awkward corpus: three chunks whose vectors cluster near the query
# direction ([1,0,0]) but whose text never mentions "quantum", plus one chunk whose
# vector is orthogonal (cosine 0) but whose text *is about* quantum. Any hybrid that
# only re-ranked the vector top-k would miss the quantum chunk; a full-collection BM25
# side surfaces it.
def _seed_corpus(adapter: PgvectorAdapter, collection: str) -> None:
    chunks = [
        _chunk("d_near1", "the cat sat on the warm mat"),
        _chunk("d_near2", "a dog ran across the green field"),
        _chunk("d_near3", "birds fly under a clear blue sky", filename="fr.txt"),
        _chunk("d_quantum", "quantum entanglement links distant particles"),
    ]
    vectors = [
        [1.0, 0.05, 0.0],   # near1 — closest to the query direction
        [0.9, 0.2, 0.0],    # near2
        [0.8, 0.3, 0.0],    # near3
        [0.0, 0.0, 1.0],    # quantum — orthogonal to the query (cosine ~0)
    ]
    adapter.upsert(collection, chunks, vectors)


_QUERY_VEC = [1.0, 0.0, 0.0]


def test_hybrid_full_collection_lexical_recall(live_store):
    adapter, collection = live_store
    _seed_corpus(adapter, collection)

    # Pool of 2 per side: the semantic side reaches only near1/near2, so the quantum
    # chunk (orthogonal vector, cosine ~0) is outside the vector top-k. The BM25 side
    # scans the WHOLE collection and pulls it in — the full-collection lexical recall
    # Phase 3 deferred here, impossible for the old candidate-scoped bm25_scores.
    # alpha=0.3 leans on BM25 so the lexical-only hit wins its fused slot; at the
    # default 0.7 a pure-lexical, vector-orthogonal chunk correctly loses to two
    # strong semantic hits (RRF working as intended), which wouldn't show recall.
    results, matched = adapter.hybrid_search(
        collection, _QUERY_VEC, "quantum", top_k=2, alpha=0.3
    )

    assert matched is True
    quantum_id = _chunk("d_quantum", "").id
    assert quantum_id in {r.chunk_id for r in results}  # lexical-only hit surfaced
    assert results[0].chunk_id == quantum_id            # and fused to the top
    assert results[0].metadata["document_id"] == "d_quantum"


def test_hybrid_semantic_only_fallback_keeps_real_cosine(live_store):
    adapter, collection = live_store
    _seed_corpus(adapter, collection)

    # No chunk mentions this term → BM25 side is empty → pure semantic ranking.
    results, matched = adapter.hybrid_search(
        collection, _QUERY_VEC, "unmatchable_term_zzz", top_k=4
    )

    assert matched is False
    # Ordered by real cosine similarity, and the scores are real similarities (near 1.0
    # for the aligned vectors), not the rank-only RRF constant ~1/61.
    assert results[0].chunk_id == _chunk("d_near1", "").id
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].score > 0.9


def test_hybrid_pushes_filter_into_both_candidate_scans(live_store):
    adapter, collection = live_store
    _seed_corpus(adapter, collection)

    # filename=fr.txt matches only near3 (which has no "quantum" text) — so the bm25 side
    # is empty under the filter and the semantic side is filtered to that one row. Neither
    # the quantum chunk nor near1/near2 may leak through.
    results, matched = adapter.hybrid_search(
        collection, _QUERY_VEC, "quantum", top_k=4, filters=SearchFilters(filename="fr.txt")
    )

    assert {r.chunk_id for r in results} == {_chunk("d_near3", "").id}
    assert matched is False  # the only matching row has no lexical match


def test_bm25_scores_returns_real_okapi_scores(live_store):
    adapter, collection = live_store
    _seed_corpus(adapter, collection)
    quantum_id = _chunk("d_quantum", "").id

    scores = adapter.bm25_scores(
        collection, "quantum",
        [quantum_id, _chunk("d_near1", "").id, _chunk("d_near2", "").id],
    )

    # Only the quantum chunk matches; its BM25 score is a real positive relevance value.
    assert quantum_id in scores
    assert scores[quantum_id] > 0
    assert _chunk("d_near1", "").id not in scores


def test_search_pushes_metadata_filter_into_where(live_store):
    adapter, collection = live_store
    _seed_corpus(adapter, collection)

    # Item 1: the vector search itself must return only matching rows (correct top-k
    # under a restrictive filter), not filter after the fact.
    results = adapter.search(
        collection, _QUERY_VEC, top_k=10, filters=SearchFilters(filename="fr.txt")
    )

    assert {r.chunk_id for r in results} == {_chunk("d_near3", "").id}


def test_search_collection_hybrid_routes_through_single_round_trip(live_store):
    adapter, collection = live_store
    _seed_corpus(adapter, collection)

    # End-to-end through the service: HYBRID must surface the lexical hit and report
    # HYBRID mode; a no-match query degrades to SEMANTIC_ONLY. alpha=0.3 (see the
    # recall test) lets the vector-orthogonal lexical hit win its fused slot.
    hits, mode, _, _ = search_collection(
        collection, _QUERY_VEC, "quantum", top_k=2, fan_out=1, alpha=0.3,
        mode=SearchMode.HYBRID, vector_store=adapter,
    )
    assert mode == SearchMode.HYBRID
    assert hits[0].chunk_id == _chunk("d_quantum", "").id

    _, fallback_mode, _, _ = search_collection(
        collection, _QUERY_VEC, "unmatchable_term_zzz", top_k=2, fan_out=1,
        mode=SearchMode.HYBRID, vector_store=adapter,
    )
    assert fallback_mode == SearchMode.SEMANTIC_ONLY
