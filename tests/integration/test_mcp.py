"""Integration tests for the Delivery 4 MCP server.

Covers the D4 verification checklist:

- ``list_workspaces`` returns the workspace tree with collection + document counts,
- ``search_documents`` across two collections returns merged, source-tagged results,
- ``ingest_document`` (local path) + ``list_documents`` + ``delete_document`` round-trip,
- rate limiting: the 61st request in a minute is rejected with HTTP 429,
- auth: a missing/invalid API key is rejected with HTTP 401.

The tool *logic* is exercised directly against a seeded in-memory database (no
SSE transport required); the auth + rate-limit behaviour is exercised at the
ASGI layer through the middleware that guards the mounted MCP app.
"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from api.models.search import SearchResult
from api.services.mcp import tools
from api.services.mcp.middleware import MCPAuthRateLimitMiddleware
from api.services.mcp.rate_limit import TokenBucketRateLimiter
from api.tables import collections as collections_t
from api.tables import documents as documents_t
from api.tables import metadata
from api.tables import workspaces as workspaces_t


def _result(chunk_id: str, score: float, **meta: object) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text=f"text-{chunk_id}", score=score, metadata=dict(meta))


class _FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]] * len(texts)

    @property
    def dimensions(self) -> int:
        return 3


class _FakeRedis:
    """No BM25 corpus -> search falls back to semantic_only."""

    def get(self, key: str) -> object:
        return None

    def set(self, key: str, value: object, ex: int | None = None) -> None: ...


class _SeededVectorStore:
    def __init__(self, by_collection: dict[str, list[SearchResult]]) -> None:
        self._by = by_collection

    def search(
        self, collection_id: str, vector: list[float], top_k: int, filters: dict | None = None
    ) -> list[SearchResult]:
        return [r.model_copy() for r in self._by.get(collection_id, [])][:top_k]

    def delete_document(self, collection_id: str, document_id: str) -> None:
        self._by[collection_id] = [
            r for r in self._by.get(collection_id, []) if r.metadata.get("document_id") != document_id
        ]

    def upsert(self, *args: object, **kwargs: object) -> None: ...
    def delete_collection(self, *args: object) -> None: ...
    def list_documents(self, *args: object) -> list:
        return []


@pytest.fixture
async def seeded():
    """Yield (session_factory, vector_store) seeded with a workspace + 2 collections."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    now = datetime.now(UTC).isoformat()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(workspaces_t).values(id="ws1", name="Research", created_at=now, updated_at=now)
        )
        await conn.execute(
            insert(collections_t).values(
                id="colA", workspace_id="ws1", name="papers", created_at=now, updated_at=now
            )
        )
        await conn.execute(
            insert(collections_t).values(
                id="colB", workspace_id="ws1", name="notes", created_at=now, updated_at=now
            )
        )
        for doc_id, col_id in (("docA", "colA"), ("docA2", "colA"), ("docB", "colB")):
            await conn.execute(
                insert(documents_t).values(
                    id=doc_id, collection_id=col_id, filename=f"{doc_id}.txt",
                    file_type=".txt", created_at=now, updated_at=now,
                )
            )

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = _SeededVectorStore(
        {
            "colA": [
                _result("a1", 0.9, document_id="docA", filename="a.pdf", language="python"),
                _result("a2", 0.5, document_id="docA2", filename="a2.pdf", language="rust"),
            ],
            "colB": [_result("b1", 0.8, document_id="docB", filename="b.md", language="go")],
        }
    )
    yield factory, store
    await engine.dispose()


# ── Tool logic ────────────────────────────────────────────────────────────────


async def test_list_workspaces_returns_tree_with_counts(seeded):
    factory, _ = seeded
    async with factory() as db:
        out = await tools.list_workspaces(db=db)

    workspaces = out["workspaces"]
    assert len(workspaces) == 1
    ws = workspaces[0]
    assert (ws["id"], ws["name"]) == ("ws1", "Research")
    assert ws["collection_count"] == 2
    assert ws["document_count"] == 3
    by_name = {c["name"]: c for c in ws["collections"]}
    assert by_name["papers"]["document_count"] == 2
    assert by_name["notes"]["document_count"] == 1


async def test_search_documents_merges_two_collections(seeded):
    factory, store = seeded
    async with factory() as db:
        out = await tools.search_documents(
            query="vectors",
            collection_ids=["colA", "colB"],
            top_k=10,
            db=db,
            embedder=_FakeEmbedder(),
            vector_store=store,
            redis_client=_FakeRedis(),
        )

    results = out["results"]
    assert len(results) == 3
    assert {r["source"]["collection_id"] for r in results} == {"colA", "colB"}
    assert set(out["collection_stats"].keys()) == {"colA", "colB"}


async def test_search_documents_clamps_top_k_to_max_results(seeded):
    factory, store = seeded
    async with factory() as db:
        out = await tools.search_documents(
            query="vectors",
            collection_ids=["colA"],
            top_k=999,  # far above max_results; must not raise a validation error
            max_results=20,
            db=db,
            embedder=_FakeEmbedder(),
            vector_store=store,
            redis_client=_FakeRedis(),
        )
    assert len(out["results"]) <= 20


async def test_ingest_list_delete_roundtrip(seeded, tmp_path, monkeypatch):
    factory, _ = seeded
    monkeypatch.setattr(
        "api.services.documents.task_producer.enqueue_ingest", lambda *a, **k: "task-ingest"
    )
    monkeypatch.setattr(
        "api.services.documents.task_producer.enqueue_delete", lambda *a, **k: "task-delete"
    )
    src = tmp_path / "note.txt"
    src.write_text("hello world", encoding="utf-8")

    async with factory() as db:
        created = await tools.ingest_document(collection_id="colA", file_path=str(src), db=db)
        assert created["status"] == "pending"
        assert created["collection_id"] == "colA"
        doc_id = created["document_id"]

        listed = await tools.list_documents(collection_id="colA", db=db)
        assert doc_id in {d["document_id"] for d in listed["documents"]}

        deleted = await tools.delete_document(document_id=doc_id, db=db)
        assert deleted == {"document_id": doc_id, "collection_id": "colA", "status": "deleting"}


async def test_ingest_document_rejects_unknown_extension(seeded, tmp_path):
    from fastapi import HTTPException

    factory, _ = seeded
    bad = tmp_path / "data.xyz"
    bad.write_text("nope", encoding="utf-8")
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.ingest_document(collection_id="colA", file_path=str(bad), db=db)
    assert exc.value.status_code == 415


# ── Auth + rate limiting (ASGI middleware) ────────────────────────────────────


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"x", b"y")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _guarded(rpm: int) -> MCPAuthRateLimitMiddleware:
    return MCPAuthRateLimitMiddleware(
        _ok_app, authenticate=lambda k: k == "secret", rate_limiter=TokenBucketRateLimiter(rpm)
    )


async def test_missing_key_is_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=_guarded(60)), base_url="http://mcp") as ac:
        r = await ac.get("/sse")
    assert r.status_code == 401


async def test_invalid_key_is_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=_guarded(60)), base_url="http://mcp") as ac:
        r = await ac.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_x_api_key_header_is_accepted():
    async with AsyncClient(transport=ASGITransport(app=_guarded(60)), base_url="http://mcp") as ac:
        r = await ac.get("/sse", headers={"X-API-Key": "secret"})
    assert r.status_code == 200


async def test_rate_limit_rejects_61st_request_with_429():
    app = _guarded(60)
    headers = {"Authorization": "Bearer secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://mcp") as ac:
        statuses = [(await ac.get("/sse", headers=headers)).status_code for _ in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429
