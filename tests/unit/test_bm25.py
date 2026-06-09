"""Unit tests for the BM25 write path (JSON corpus + version counter)."""

import json

from api.models.chunk import Chunk, ChunkMetadata, make_chunk_id
from worker.tasks import BM25_TTL_SECONDS, _update_bm25_index


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def incr(self, key):
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])


def _chunk(doc_id, idx, text):
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            source_file="/f.txt", filename="f.txt", parser="txt",
            document_id=doc_id, chunk_index=idx,
        ),
    )


def test_empty_chunks_is_noop():
    rds = FakeRedis()
    _update_bm25_index(rds, "col1", [])
    assert rds.store == {}


def test_writes_corpus_triples_as_json_with_ttl():
    rds = FakeRedis()
    chunks = [_chunk("doc1", 0, "hello"), _chunk("doc1", 1, "world")]
    _update_bm25_index(rds, "col1", chunks)

    corpus = json.loads(rds.store["bm25:col1:corpus"])
    assert corpus == [
        [make_chunk_id("doc1", 0), "doc1", "hello"],
        [make_chunk_id("doc1", 1), "doc1", "world"],
    ]
    assert rds.ttls["bm25:col1:corpus"] == BM25_TTL_SECONDS


def test_increments_version():
    rds = FakeRedis()
    _update_bm25_index(rds, "col1", [_chunk("doc1", 0, "a")])
    assert rds.store["bm25:col1:version"] == "1"
    _update_bm25_index(rds, "col1", [_chunk("doc2", 0, "b")])
    assert rds.store["bm25:col1:version"] == "2"


def test_appends_to_existing_corpus():
    rds = FakeRedis()
    _update_bm25_index(rds, "col1", [_chunk("doc1", 0, "first")])
    _update_bm25_index(rds, "col1", [_chunk("doc2", 0, "second")])

    corpus = json.loads(rds.store["bm25:col1:corpus"])
    assert corpus == [
        [make_chunk_id("doc1", 0), "doc1", "first"],
        [make_chunk_id("doc2", 0), "doc2", "second"],
    ]


def test_corpus_is_json_not_pickle():
    rds = FakeRedis()
    _update_bm25_index(rds, "col1", [_chunk("doc1", 0, "x")])
    raw = rds.store["bm25:col1:corpus"]
    assert isinstance(raw, str)
    assert json.loads(raw) == [[make_chunk_id("doc1", 0), "doc1", "x"]]


def test_multi_chunk_document_stores_unique_chunk_ids():
    """Each chunk gets its own corpus entry — no document_id key collision."""
    rds = FakeRedis()
    chunks = [_chunk("doc1", i, f"text chunk {i}") for i in range(5)]
    _update_bm25_index(rds, "col1", chunks)

    corpus = json.loads(rds.store["bm25:col1:corpus"])
    chunk_ids = [entry[0] for entry in corpus]
    assert len(chunk_ids) == len(set(chunk_ids)), "chunk_ids must be unique"
    assert all(entry[1] == "doc1" for entry in corpus), "document_id preserved"
