"""Unit tests for QdrantAdapter mapping logic (no real Qdrant server).

A fake ``qdrant_client`` module (with a minimal ``models`` namespace) is injected
into ``sys.modules`` so the adapter's lazy ``from qdrant_client import models``
resolves to recorder stubs, and a fake client is injected via ``adapter._client``.
These tests verify the point-id mapping, payload shape, and response translation.
"""

import sys
import types
import uuid

import pytest

from api.adapters.vector_store.qdrant import QdrantAdapter
from api.models.chunk import Chunk, ChunkMetadata


def _chunk(doc_id, idx, text):
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            source_file="/f.txt", filename="f.txt", parser="txt",
            document_id=doc_id, chunk_index=idx,
        ),
    )


class _PointStruct:
    def __init__(self, id, vector, payload):
        self.id = id
        self.vector = vector
        self.payload = payload


class _Rec:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _make_fake_qdrant():
    models = types.SimpleNamespace(
        PointStruct=_PointStruct,
        VectorParams=lambda **kw: _Rec(**kw),
        Distance=types.SimpleNamespace(COSINE="Cosine"),
        Filter=lambda **kw: _Rec(**kw),
        FieldCondition=lambda **kw: _Rec(**kw),
        MatchValue=lambda **kw: _Rec(**kw),
        FilterSelector=lambda **kw: _Rec(**kw),
    )
    module = types.ModuleType("qdrant_client")
    module.models = models
    module.QdrantClient = object
    return module


@pytest.fixture(autouse=True)
def fake_qdrant(monkeypatch):
    monkeypatch.setitem(sys.modules, "qdrant_client", _make_fake_qdrant())


class _Point:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class _QueryResult:
    def __init__(self, points):
        self.points = points


class _Record:
    def __init__(self, payload):
        self.payload = payload


class FakeClient:
    def __init__(self, exists=True, query_result=None, scroll_pages=None,
                 raise_on_list=False):
        self._exists = exists
        self.created = None
        self.upserted = None
        self.deleted_selector = None
        self.deleted_collection = None
        self._query_result = query_result
        self._scroll_pages = scroll_pages or [([], None)]
        self._scroll_idx = 0
        self._raise_on_list = raise_on_list
        self.set_payload_call = None

    def get_collections(self):
        if self._raise_on_list:
            raise RuntimeError("server down")
        return _Rec(collections=[])

    def collection_exists(self, name):
        return self._exists

    def create_collection(self, collection_name, vectors_config):
        self.created = (collection_name, vectors_config)
        self._exists = True

    def upsert(self, collection_name, points):
        self.upserted = (collection_name, points)

    def query_points(self, collection_name, query, limit, with_payload):
        return self._query_result

    def set_payload(self, collection_name, payload, points):
        self.set_payload_call = (collection_name, payload, points)

    def delete(self, collection_name, points_selector):
        self.deleted_selector = (collection_name, points_selector)

    def delete_collection(self, collection_name):
        self.deleted_collection = collection_name

    def scroll(self, collection_name, limit, offset, with_payload, with_vectors):
        page = self._scroll_pages[self._scroll_idx]
        self._scroll_idx += 1
        return page


def _adapter(client):
    a = QdrantAdapter(host="h", port=6333, dimensions=3)
    a._client = client
    return a


# --- ping -------------------------------------------------------------------


def test_ping_true_when_collections_listed():
    assert _adapter(FakeClient()).ping() is True


def test_ping_false_when_client_errors():
    assert _adapter(FakeClient(raise_on_list=True)).ping() is False


# --- upsert -----------------------------------------------------------------


def test_upsert_ensures_collection_and_builds_points():
    client = FakeClient(exists=False)
    adapter = _adapter(client)
    chunks = [_chunk("d1", 0, "alpha")]

    adapter.upsert("col1", chunks, [[0.1, 0.2, 0.3]])

    assert client.created[0] == "col1"
    name, points = client.upserted
    assert name == "col1"
    point = points[0]
    assert point.payload["text"] == "alpha"
    assert point.payload["chunk_id"] == chunks[0].id
    assert point.payload["document_id"] == "d1"
    # sha256 chunk id is mapped to a deterministic UUID, not used raw.
    assert point.id == str(uuid.uuid5(uuid.NAMESPACE_OID, chunks[0].id))
    assert point.id != chunks[0].id


def test_upsert_empty_is_noop():
    client = FakeClient()
    adapter = _adapter(client)
    adapter.upsert("col1", [], [])
    assert client.upserted is None
    assert client.created is None


# --- search -----------------------------------------------------------------


def test_search_maps_points_to_results():
    points = [
        _Point("u1", 0.9, {"text": "alpha", "chunk_id": "c1", "document_id": "d1"}),
        _Point("u2", 0.4, {"text": "beta", "chunk_id": "c2", "document_id": "d2"}),
    ]
    client = FakeClient(exists=True, query_result=_QueryResult(points))
    adapter = _adapter(client)

    results = adapter.search("col1", [0.1, 0.2, 0.3], top_k=2)

    assert [r.chunk_id for r in results] == ["c1", "c2"]
    assert results[0].score == 0.9
    assert results[0].text == "alpha"
    assert results[0].rank == 0
    assert results[0].metadata == {"document_id": "d1"}  # text + chunk_id stripped


def test_search_missing_collection_returns_empty():
    adapter = _adapter(FakeClient(exists=False))
    assert adapter.search("nope", [0.1, 0.2, 0.3], top_k=5) == []


# --- delete -----------------------------------------------------------------


def test_delete_document_builds_document_filter():
    client = FakeClient()
    adapter = _adapter(client)
    adapter.delete_document("col1", "d1")
    name, selector = client.deleted_selector
    assert name == "col1"
    cond = selector.filter.must[0]
    assert cond.key == "document_id"
    assert cond.match.value == "d1"


def test_delete_collection_calls_client():
    client = FakeClient()
    adapter = _adapter(client)
    adapter.delete_collection("col1")
    assert client.deleted_collection == "col1"


# --- set_document_tags ------------------------------------------------------


def test_set_document_tags_sets_payload_filtered_by_document():
    client = FakeClient()
    adapter = _adapter(client)

    adapter.set_document_tags("col1", "d1", ["x", "y"])

    name, payload, points = client.set_payload_call
    assert name == "col1"
    assert payload == {"tags": ["x", "y"]}
    cond = points.must[0]
    assert cond.key == "document_id"
    assert cond.match.value == "d1"


# --- list_documents ---------------------------------------------------------


def test_list_documents_aggregates_payloads():
    pages = [
        (
            [
                _Record({"document_id": "d1", "filename": "a.txt", "parser": "txt"}),
                _Record({"document_id": "d1", "filename": "a.txt", "parser": "txt"}),
                _Record({"document_id": "d2", "filename": "b.pdf", "parser": "pdf"}),
            ],
            None,
        ),
    ]
    client = FakeClient(exists=True, scroll_pages=pages)
    adapter = _adapter(client)

    summaries = {s.document_id: s for s in adapter.list_documents("col1")}
    assert summaries["d1"].chunk_count == 2
    assert summaries["d2"].file_type == "pdf"


def test_list_documents_paginates():
    pages = [
        ([_Record({"document_id": "d1", "filename": "a", "parser": "txt"})], "next"),
        ([_Record({"document_id": "d2", "filename": "b", "parser": "pdf"})], None),
    ]
    client = FakeClient(exists=True, scroll_pages=pages)
    adapter = _adapter(client)
    summaries = {s.document_id: s for s in adapter.list_documents("col1")}
    assert set(summaries) == {"d1", "d2"}


def test_list_documents_missing_collection_empty():
    adapter = _adapter(FakeClient(exists=False))
    assert adapter.list_documents("nope") == []


# --- helpers ----------------------------------------------------------------


def test_point_id_is_deterministic_uuid():
    first = QdrantAdapter._point_id("abc")
    assert first == QdrantAdapter._point_id("abc")
    assert first != "abc"
