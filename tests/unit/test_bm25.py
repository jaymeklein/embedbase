"""Unit tests for the BM25 write path (JSON corpus + version counter)."""

import json

from api.models.chunk import Chunk, ChunkMetadata
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


def test_writes_corpus_pairs_as_json_with_ttl():
    rds = FakeRedis()
    chunks = [_chunk("doc1", 0, "hello"), _chunk("doc1", 1, "world")]
    _update_bm25_index(rds, "col1", chunks)

    corpus = json.loads(rds.store["bm25:col1:corpus"])
    assert corpus == [["doc1", "hello"], ["doc1", "world"]]
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
    assert corpus == [["doc1", "first"], ["doc2", "second"]]


def test_corpus_is_json_not_pickle():
    rds = FakeRedis()
    _update_bm25_index(rds, "col1", [_chunk("doc1", 0, "x")])
    raw = rds.store["bm25:col1:corpus"]
    # Round-trips through JSON cleanly (never a pickle blob).
    assert isinstance(raw, str)
    assert json.loads(raw) == [["doc1", "x"]]
