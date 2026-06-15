"""Unit tests for PgvectorAdapter SQL translation (no real Postgres).

A fake asyncpg pool/connection is injected via ``adapter._pool`` and a direct
runner (runs coroutines on a throwaway loop) via ``adapter._runner``, so these
tests never import asyncpg or touch a database — they verify the request/response
mapping and the lazy-index threshold only.
"""

import asyncio

from api.adapters.vector_store.pgvector import PgvectorAdapter
from api.models.chunk import Chunk, ChunkMetadata


def _chunk(doc_id, idx, text):
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            source_file="/f.txt", filename="f.txt", parser="txt",
            document_id=doc_id, chunk_index=idx,
        ),
    )


class _DirectRunner:
    """Runs a coroutine to completion on a fresh, short-lived event loop."""

    def run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class FakeConn:
    def __init__(self, fetch_rows=None, fetchval_value=0):
        self.executemany_calls = []
        self.execute_calls = []
        self.fetch_calls = []
        self._fetch_rows = fetch_rows or []
        self._fetchval_value = fetchval_value

    async def executemany(self, sql, rows):
        self.executemany_calls.append((sql, rows))

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self._fetch_rows

    async def fetchval(self, sql, *args):
        return self._fetchval_value


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


def _adapter(conn, index_min_rows=100):
    a = PgvectorAdapter(host="h", port=5432, database="db", user="u",
                        password="p", dimensions=3, index_min_rows=index_min_rows)
    a._pool = FakePool(conn)
    a._runner = _DirectRunner()
    return a


# --- ping -------------------------------------------------------------------


def test_ping_true_when_query_succeeds():
    conn = FakeConn()
    adapter = _adapter(conn)
    assert adapter.ping() is True
    assert any("SELECT 1" in sql for sql, _ in conn.execute_calls)


def test_ping_false_when_query_errors():
    class _BoomConn(FakeConn):
        async def execute(self, sql, *args):
            raise RuntimeError("db down")

    assert _adapter(_BoomConn()).ping() is False


# --- upsert -----------------------------------------------------------------


def test_upsert_issues_insert_on_conflict():
    conn = FakeConn(fetchval_value=0)
    adapter = _adapter(conn)
    chunks = [_chunk("d1", 0, "alpha"), _chunk("d1", 1, "beta")]
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    adapter.upsert("col_abcd1234", chunks, vectors)

    sql, rows = conn.executemany_calls[0]
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert rows[0][0] == chunks[0].id
    assert rows[0][1] == "col_abcd1234"
    assert rows[0][4] == [0.1, 0.2, 0.3]


def test_upsert_empty_is_noop():
    conn = FakeConn()
    adapter = _adapter(conn)
    adapter.upsert("col1", [], [])
    assert conn.executemany_calls == []


# --- lazy HNSW index --------------------------------------------------------


def test_hnsw_index_created_when_rows_reach_threshold():
    conn = FakeConn(fetchval_value=100)
    adapter = _adapter(conn, index_min_rows=100)

    adapter.upsert("col_abcd1234", [_chunk("d1", 0, "x")], [[0.1, 0.2, 0.3]])

    created = [sql for sql, _ in conn.execute_calls if "CREATE INDEX CONCURRENTLY" in sql]
    assert created
    assert '"chunks_embedding_hnsw_col_abcd"' in created[0]  # collection_id[:8]
    assert "hnsw (embedding vector_cosine_ops)" in created[0]


def test_hnsw_index_skipped_below_threshold():
    conn = FakeConn(fetchval_value=99)
    adapter = _adapter(conn, index_min_rows=100)

    adapter.upsert("col1", [_chunk("d1", 0, "x")], [[0.1, 0.2, 0.3]])

    assert not any("CREATE INDEX CONCURRENTLY" in sql for sql, _ in conn.execute_calls)


# --- search -----------------------------------------------------------------


def test_search_maps_rows_to_results():
    rows = [
        {"id": "c1", "text": "alpha", "score": 0.91, "metadata": '{"document_id": "d1"}'},
        {"id": "c2", "text": "beta", "score": 0.42, "metadata": '{"document_id": "d2"}'},
    ]
    conn = FakeConn(fetch_rows=rows)
    adapter = _adapter(conn)

    results = adapter.search("col1", [0.1, 0.2, 0.3], top_k=2)

    assert [r.chunk_id for r in results] == ["c1", "c2"]
    assert results[0].score == 0.91
    assert results[0].rank == 0
    assert results[0].metadata["document_id"] == "d1"
    _, args = conn.fetch_calls[0]
    assert args == ([0.1, 0.2, 0.3], "col1", 2)


# --- delete -----------------------------------------------------------------


def test_delete_document_filters_by_document_id():
    conn = FakeConn()
    adapter = _adapter(conn)
    adapter.delete_document("col1", "d1")
    sql, args = conn.execute_calls[0]
    assert "DELETE FROM chunks" in sql
    assert "metadata->>'document_id'" in sql
    assert args == ("col1", "d1")


def test_delete_collection_removes_rows_and_index():
    conn = FakeConn()
    adapter = _adapter(conn)
    adapter.delete_collection("col_abcd1234")
    sqls = [sql for sql, _ in conn.execute_calls]
    assert any("DELETE FROM chunks WHERE collection_id" in s for s in sqls)
    assert any('DROP INDEX IF EXISTS "chunks_embedding_hnsw_col_abcd"' in s for s in sqls)


# --- list_documents ---------------------------------------------------------


def test_list_documents_builds_summaries():
    rows = [
        {"document_id": "d1", "filename": "a.txt", "parser": "txt", "chunk_count": 3},
        {"document_id": "d2", "filename": "b.pdf", "parser": "pdf", "chunk_count": 1},
    ]
    conn = FakeConn(fetch_rows=rows)
    adapter = _adapter(conn)

    summaries = {s.document_id: s for s in adapter.list_documents("col1")}
    assert summaries["d1"].chunk_count == 3
    assert summaries["d2"].file_type == "pdf"


# --- helpers ----------------------------------------------------------------


def test_as_dict_decodes_json_and_passthrough():
    assert PgvectorAdapter._as_dict('{"a": 1}') == {"a": 1}
    assert PgvectorAdapter._as_dict({"b": 2}) == {"b": 2}
    assert PgvectorAdapter._as_dict(None) == {}
