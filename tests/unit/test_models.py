"""Smoke tests for core data models — Delivery 1 unit test suite."""
import hashlib
from datetime import UTC

import pytest
from pydantic import ValidationError

from api.models.chunk import Chunk, ChunkMetadata, make_chunk_id
from api.models.collection import Collection, Workspace
from api.models.config import AppConfig
from api.models.search import SearchRequest, SearchResponse


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
    from datetime import datetime
    now = datetime.now(UTC)
    ws = Workspace(name="Test", created_at=now, updated_at=now)
    assert ws.id.startswith("ws_")
    assert len(ws.id) == 15  # "ws_" + 12 hex chars


def test_collection_id_prefix():
    from datetime import datetime
    now = datetime.now(UTC)
    col = Collection(name="Test", workspace_id="ws_abc", created_at=now, updated_at=now)
    assert col.id.startswith("col_")
    assert len(col.id) == 16  # "col_" + 12 hex chars


def test_search_request_requires_collection_ids():
    with pytest.raises(ValidationError):
        SearchRequest(query="test", collection_ids=[])


def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.embedding.provider == "ollama"
    assert cfg.vector_store.backend == "chroma"
    assert cfg.search.retrieval_fan_out == 4
    assert cfg.search.max_fan_out == 10


# ---------------------------------------------------------------------------
# Document models
# ---------------------------------------------------------------------------

def test_document_status_enum_values():
    from api.models.document import DocumentStatus

    assert {s.value for s in DocumentStatus} == {
        "pending", "processing", "done", "failed", "deleted",
    }


def test_job_record_roundtrip():
    from datetime import UTC, datetime

    from api.models.document import DocumentStatus, JobRecord

    now = datetime.now(UTC)
    jr = JobRecord(
        job_id="job_1", document_id="doc_1", collection_id="col_1",
        filename="a.txt", file_type=".txt", status=DocumentStatus.pending,
        created_at=now, updated_at=now,
    )
    assert jr.status == "pending"
    assert jr.chunk_count is None
    assert jr.error is None


def test_document_summary_defaults():
    from datetime import UTC, datetime

    from api.models.document import DocumentSummary

    now = datetime.now(UTC)
    ds = DocumentSummary(
        document_id="doc_1", filename="a.txt", file_type=".txt",
        chunk_count=3, created_at=now, updated_at=now,
    )
    assert ds.chunk_count == 3
    assert ds.file_size_bytes is None


# ---------------------------------------------------------------------------
# Chunk metadata
# ---------------------------------------------------------------------------

def test_chunk_metadata_optional_fields_default_none():
    md = ChunkMetadata(
        source_file="/f", filename="f", parser="pdf",
        document_id="doc_1", chunk_index=0,
    )
    assert md.page_number is None
    assert md.heading_path is None
    assert md.language is None
    assert md.tags == []


# ---------------------------------------------------------------------------
# Search request validation
# ---------------------------------------------------------------------------

def test_search_request_top_k_bounds():
    with pytest.raises(ValidationError):
        SearchRequest(query="q", collection_ids=["c"], top_k=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="q", collection_ids=["c"], top_k=21)
    ok = SearchRequest(query="q", collection_ids=["c"], top_k=20)
    assert ok.top_k == 20


def test_search_request_alpha_bounds():
    with pytest.raises(ValidationError):
        SearchRequest(query="q", collection_ids=["c"], hybrid_alpha=1.5)
    ok = SearchRequest(query="q", collection_ids=["c"], hybrid_alpha=0.0)
    assert ok.hybrid_alpha == 0.0


def test_search_request_defaults():
    from api.models.search import SearchRequest

    req = SearchRequest(query="q", collection_ids=["c"])
    assert req.top_k == 5
    assert req.hybrid is True
    assert req.hybrid_alpha == 0.7
    assert req.filters is None


def test_search_response_defaults():
    resp = SearchResponse(results=[])
    assert resp.search_mode == "hybrid"
    assert resp.under_delivered is False
    assert resp.collection_stats == {}


# ---------------------------------------------------------------------------
# API key public model never leaks the hash
# ---------------------------------------------------------------------------

def test_api_key_public_has_no_hash_field():
    from api.models.collection import APIKeyPublic

    assert "key_hash" not in APIKeyPublic.model_fields
