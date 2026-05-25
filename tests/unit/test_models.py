"""Smoke tests for core data models — Delivery 1 unit test suite."""
import hashlib
from api.models.chunk import Chunk, ChunkMetadata, make_chunk_id
from api.models.collection import Workspace, Collection, APIKey
from api.models.search import SearchRequest, SearchResponse
from api.models.config import AppConfig


def test_chunk_id_deterministic():
    doc_id = "doc_abc123"
    idx = 0
    expected = hashlib.sha256(f"{doc_id}:{idx}".encode()).hexdigest()
    assert make_chunk_id(doc_id, idx) == expected


def test_chunk_id_unique_per_index():
    doc_id = "doc_abc123"
    assert make_chunk_id(doc_id, 0) != make_chunk_id(doc_id, 1)


def test_chunk_auto_id():
    chunk = Chunk(
        text="hello world",
        metadata=ChunkMetadata(
            source_file="/data/test.txt",
            filename="test.txt",
            parser="txt",
            document_id="doc_abc",
            chunk_index=5,
        ),
    )
    assert chunk.id == make_chunk_id("doc_abc", 5)
    assert len(chunk.id) == 64  # sha256 hex


def test_workspace_id_prefix():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ws = Workspace(name="Test", created_at=now, updated_at=now)
    assert ws.id.startswith("ws_")
    assert len(ws.id) == 15  # "ws_" + 12 hex chars


def test_collection_id_prefix():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    col = Collection(name="Test", workspace_id="ws_abc", created_at=now, updated_at=now)
    assert col.id.startswith("col_")
    assert len(col.id) == 16  # "col_" + 12 hex chars


def test_search_request_requires_collection_ids():
    import pytest
    with pytest.raises(Exception):
        SearchRequest(query="test", collection_ids=[])


def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.embedding.provider == "sentence_transformers"
    assert cfg.vector_store.backend == "chroma"
    assert cfg.search.retrieval_fan_out == 4
    assert cfg.search.max_fan_out == 10
