"""Integration tests for the realtime WebSocket bridge (api/routers/ws.py).

Starlette's sync TestClient is used because httpx's AsyncClient (the `client`
fixture) can't speak WebSocket. A fake ``redis.asyncio`` stands in for Redis, so
no server is needed: the test asserts the per-topic snapshot is replayed first, a
published message is then forwarded, and that auth is enforced.
"""

import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.routers.ws as ws_module
from api.dependencies import get_db
from api.main import create_app
from api.tables import metadata

MASTER = "test-master-key-for-testing-only"  # set by tests/conftest.py


class _FakePubSub:
    def __init__(self, messages):
        self._messages = messages

    async def subscribe(self, channel):
        pass

    async def unsubscribe(self, channel):
        pass

    async def aclose(self):
        pass

    async def listen(self):
        yield {"type": "subscribe", "data": 1}
        for data in self._messages:
            yield {"type": "message", "data": data}


class _FakeAsyncRedis:
    def __init__(self, snapshot, messages):
        self._snapshot = snapshot
        self._messages = messages

    def pubsub(self):
        return _FakePubSub(self._messages)

    async def hgetall(self, key):
        return dict(self._snapshot)

    async def aclose(self):
        pass


@pytest.fixture
def ws_client(monkeypatch):
    """TestClient over the app with an in-memory DB and a stubbed async redis."""
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

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    @asynccontextmanager
    async def _lifespan(_app):
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        yield

    app = create_app()
    app.router.lifespan_context = _lifespan
    app.dependency_overrides[get_db] = _override_get_db

    snapshot = {
        "doc_1": json.dumps(
            {"document_id": "doc_1", "phase": "parsing", "pct": 10, "status": "processing"}
        )
    }
    messages = [
        json.dumps(
            {"document_id": "doc_1", "phase": "embedding", "pct": 50, "status": "processing"}
        )
    ]
    monkeypatch.setattr(
        ws_module.aioredis, "from_url", lambda *a, **k: _FakeAsyncRedis(snapshot, messages)
    )

    with TestClient(app) as client:
        yield client


def test_master_receives_snapshot_then_message(ws_client):
    with ws_client.websocket_connect(f"/ws?topic=ingestion:col_1&key={MASTER}") as ws:
        snap = json.loads(ws.receive_text())  # snapshot replayed first
        assert snap["document_id"] == "doc_1"
        assert snap["phase"] == "parsing"
        msg = json.loads(ws.receive_text())  # then the live published event
        assert msg["phase"] == "embedding"
        assert msg["pct"] == 50


def test_bad_key_is_rejected(ws_client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/ws?topic=ingestion:col_1&key=wrong-key") as ws:
            ws.receive_text()
    assert exc.value.code == 4401
