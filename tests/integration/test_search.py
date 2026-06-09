"""Integration tests for POST /search."""

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
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
from api.tables import metadata

MASTER = "test-master-key-for-testing-only"
AUTH = {"X-API-Key": MASTER}


# ---------------------------------------------------------------------------
# Fake adapters for overriding in the success-path fixture
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]] * len(texts)

    @property
    def dimensions(self) -> int:
        return 3


class _FakeVectorStore:
    def search(
        self, collection_id: str, vector: list[float], top_k: int
    ) -> list[SearchResult]:
        return []

    def upsert(self, *args: object, **kwargs: object) -> None: ...
    def delete_document(self, *args: object) -> None: ...
    def delete_collection(self, *args: object) -> None: ...
    def list_documents(self, *args: object) -> list: ...


class _FakeRedis:
    def get(self, key: str) -> object:
        return None

    def set(self, key: str, value: object, ex: int | None = None) -> None: ...


@asynccontextmanager
async def _noop_lifespan(app):  # type: ignore[no-untyped-def]
    yield


def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
async def search_client():
    """AsyncClient with all adapters stubbed so search returns 200."""
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_embedding_adapter] = lambda: _FakeEmbedder()
    app.dependency_overrides[require_vector_store] = lambda: _FakeVectorStore()
    app.dependency_overrides[require_redis_client] = lambda: _FakeRedis()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    await engine.dispose()


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_search_requires_auth(client):
    r = await client.post("/search", json={"query": "hello", "collection_ids": ["col1"]})
    assert r.status_code == 401


async def test_search_rejects_bad_key(client):
    r = await client.post(
        "/search",
        json={"query": "hello", "collection_ids": ["col1"]},
        headers={"X-API-Key": "eb_not_a_real_key"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 503 when backends not ready (no adapters in default client fixture)
# ---------------------------------------------------------------------------


async def test_search_returns_503_when_embedding_adapter_not_ready(client):
    r = await client.post(
        "/search",
        json={"query": "hello", "collection_ids": ["col1"]},
        headers=AUTH,
    )
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Success path with stubbed adapters
# ---------------------------------------------------------------------------


async def test_search_returns_200_with_valid_request(search_client):
    r = await search_client.post(
        "/search",
        json={"query": "machine learning", "collection_ids": ["col1"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert "search_mode" in body
    assert "total_ms" in body


async def test_search_response_shape(search_client):
    r = await search_client.post(
        "/search",
        json={"query": "q", "collection_ids": ["col1"], "top_k": 5},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["results"], list)
    assert isinstance(body["collection_stats"], dict)
    assert isinstance(body["under_delivered"], bool)
    assert body["query_embedding_ms"] >= 0
    assert body["search_ms"] >= 0
