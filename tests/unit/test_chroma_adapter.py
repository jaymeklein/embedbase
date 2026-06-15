"""Unit tests for ChromaAdapter mapping logic (no real Chroma server).

A fake client is injected via ``adapter._client`` so ``_get_client`` never
imports ``chromadb`` — these test the request/response translation only.
"""

from api.adapters.vector_store.chroma import ChromaAdapter
from api.models.chunk import Chunk, ChunkMetadata


def _chunk(doc_id, idx, text):
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            source_file="/f.txt", filename="f.txt", parser="txt",
            document_id=doc_id, chunk_index=idx,
        ),
    )


class FakeCollection:
    def __init__(self):
        self.upsert_kwargs = None
        self.deleted_where = None
        self._query_result = None
        self._get_result = {"metadatas": []}

    def upsert(self, **kwargs):
        self.upsert_kwargs = kwargs

    def query(self, **kwargs):
        return self._query_result

    def delete(self, where=None):
        self.deleted_where = where

    def get(self, include=None):
        return self._get_result


class FakeClient:
    def __init__(self, collection=None, raise_on_get=False, raise_on_heartbeat=False):
        self._collection = collection or FakeCollection()
        self._raise_on_get = raise_on_get
        self._raise_on_heartbeat = raise_on_heartbeat
        self.deleted_collection = None

    def heartbeat(self):
        if self._raise_on_heartbeat:
            raise RuntimeError("server down")
        return 1

    def get_or_create_collection(self, name, metadata=None):
        return self._collection

    def get_collection(self, name):
        if self._raise_on_get:
            raise RuntimeError("missing")
        return self._collection

    def delete_collection(self, name):
        self.deleted_collection = name


def _adapter(client):
    a = ChromaAdapter(host="h", port=1)
    a._client = client
    return a


def test_encode_metadata_drops_none_and_encodes_lists():
    encoded = ChromaAdapter._encode_metadata(
        {"document_id": "d1", "page_number": None, "tags": ["a", "b"], "chunk_index": 0}
    )
    assert "page_number" not in encoded  # None dropped
    assert encoded["document_id"] == "d1"
    assert encoded["chunk_index"] == 0
    assert encoded["tags"] == '["a", "b"]'  # list JSON-encoded to a scalar string


def test_decode_metadata_restores_lists():
    decoded = ChromaAdapter._decode_metadata({"tags": '["a", "b"]', "filename": "f.txt"})
    assert decoded["tags"] == ["a", "b"]  # round-trips back to a list
    assert decoded["filename"] == "f.txt"  # plain string untouched


def test_encode_decode_round_trip_preserves_tags():
    original = {"document_id": "d1", "tags": ["x", "y"]}
    assert ChromaAdapter._decode_metadata(ChromaAdapter._encode_metadata(original)) == original


def test_ping_true_when_heartbeat_succeeds():
    assert _adapter(FakeClient()).ping() is True


def test_ping_false_when_heartbeat_errors():
    assert _adapter(FakeClient(raise_on_heartbeat=True)).ping() is False


def test_upsert_passes_ids_embeddings_documents_metadatas():
    col = FakeCollection()
    adapter = _adapter(FakeClient(col))
    chunks = [_chunk("doc1", 0, "alpha"), _chunk("doc1", 1, "beta")]
    vectors = [[0.1, 0.2], [0.3, 0.4]]

    adapter.upsert("col1", chunks, vectors)

    kw = col.upsert_kwargs
    assert kw["ids"] == [c.id for c in chunks]
    assert kw["embeddings"] == vectors
    assert kw["documents"] == ["alpha", "beta"]
    assert kw["metadatas"][0]["document_id"] == "doc1"


def test_search_maps_distance_to_similarity():
    col = FakeCollection()
    col._query_result = {
        "ids": [["a", "b"]],
        "documents": [["da", "db"]],
        "metadatas": [[{"x": 1}, {"x": 2}]],
        "distances": [[0.1, 0.4]],
    }
    adapter = _adapter(FakeClient(col))

    results = adapter.search("col1", [0.0, 0.0], top_k=2)
    assert [r.chunk_id for r in results] == ["a", "b"]
    assert results[0].score == 1.0 - 0.1
    assert results[0].rank == 0
    assert results[1].rank == 1


def test_search_missing_collection_returns_empty():
    adapter = _adapter(FakeClient(raise_on_get=True))
    assert adapter.search("nope", [0.0], top_k=5) == []


def test_delete_document_filters_by_document_id():
    col = FakeCollection()
    adapter = _adapter(FakeClient(col))
    adapter.delete_document("col1", "doc1")
    assert col.deleted_where == {"document_id": "doc1"}


def test_delete_collection():
    client = FakeClient()
    adapter = _adapter(client)
    adapter.delete_collection("col1")
    assert client.deleted_collection == "col1"


def test_list_documents_aggregates_by_document():
    col = FakeCollection()
    col._get_result = {
        "metadatas": [
            {"document_id": "doc1", "filename": "a.txt", "parser": "txt"},
            {"document_id": "doc1", "filename": "a.txt", "parser": "txt"},
            {"document_id": "doc2", "filename": "b.pdf", "parser": "pdf"},
        ]
    }
    adapter = _adapter(FakeClient(col))

    summaries = {s.document_id: s for s in adapter.list_documents("col1")}
    assert summaries["doc1"].chunk_count == 2
    assert summaries["doc2"].chunk_count == 1
    assert summaries["doc2"].file_type == "pdf"
