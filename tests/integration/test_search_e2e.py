"""End-to-end search tests for the Delivery 3 verification scenarios.

Drives ``POST /search`` against a seeded in-memory database (a workspace with two
collections) and a fake vector store that returns real results, covering the D3
"Verification" checklist items that the thinner ``test_search.py`` fixture (which
returns no candidates) does not exercise:

- single + multi-collection search with correct ``source`` provenance tags,
- per-collection ``collection_stats``,
- ``under_delivered: true`` on a highly selective metadata filter,
- delete -> a document's chunks no longer appear in results.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from api.dependencies import (
    get_db,
    require_embedding_adapter,
    require_redis_client,
    require_vector_store,
)
from api.main import create_app
from api.models.search import SearchResult
from api.tables import collections as collections_t
from api.tables import metadata
from api.tables import workspaces as workspaces_t

MASTER = "test-master-key-for-testing-only"
AUTH = {"X-API-Key": MASTER}


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


@asynccontextmanager
async def _noop_lifespan(app):  # type: ignore[no-untyped-def]
    yield


@pytest.fixture
async def search_app():
    """Yield (client, vector_store) with a seeded workspace + two collections."""
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

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    store = _SeededVectorStore(
        {
            "colA": [
                _result("a1", 0.9, document_id="docA", filename="a.pdf", language="python"),
                _result("a2", 0.5, document_id="docA2", filename="a2.pdf", language="rust"),
            ],
            "colB": [_result("b1", 0.8, document_id="docB", filename="b.md", language="go")],
        }
    )

    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_embedding_adapter] = lambda: _FakeEmbedder()
    app.dependency_overrides[require_vector_store] = lambda: store
    app.dependency_overrides[require_redis_client] = lambda: _FakeRedis()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, store

    await engine.dispose()


async def test_multi_collection_search_tags_source_and_stats(search_app):
    ac, _ = search_app
    r = await ac.post(
        "/search",
        json={"query": "vectors", "collection_ids": ["colA", "colB"], "top_k": 10},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()

    assert len(body["results"]) == 3
    by_chunk = {res["chunk_id"]: res for res in body["results"]}
    assert {res["source"]["collection_id"] for res in body["results"]} == {"colA", "colB"}
    assert by_chunk["a1"]["source"]["collection_name"] == "papers"
    assert by_chunk["a1"]["source"]["workspace_name"] == "Research"
    assert by_chunk["a1"]["source"]["document_id"] == "docA"
    assert by_chunk["b1"]["source"]["collection_name"] == "notes"
    assert set(body["collection_stats"].keys()) == {"colA", "colB"}


async def test_under_delivered_on_selective_filter(search_app):
    ac, _ = search_app
    r = await ac.post(
        "/search",
        json={
            "query": "vectors",
            "collection_ids": ["colA", "colB"],
            "top_k": 10,
            "filters": {"language": "rust"},
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    # Only a2 is tagged language=rust; the rest are filtered out.
    assert [res["chunk_id"] for res in body["results"]] == ["a2"]
    assert body["under_delivered"] is True


async def test_delete_removes_document_from_results(search_app):
    ac, store = search_app

    first = await ac.post(
        "/search", json={"query": "q", "collection_ids": ["colA"], "top_k": 10}, headers=AUTH
    )
    assert "a1" in {res["chunk_id"] for res in first.json()["results"]}

    # Simulate the worker delete_document task pruning docA from the vector store.
    store.delete_document("colA", "docA")

    second = await ac.post(
        "/search", json={"query": "q", "collection_ids": ["colA"], "top_k": 10}, headers=AUTH
    )
    chunks = {res["chunk_id"] for res in second.json()["results"]}
    assert "a1" not in chunks
    assert "a2" in chunks
