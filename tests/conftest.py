import os
import sys
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MASTER_API_KEY", "test-master-key-for-testing-only")
os.environ.setdefault("DATABASE_PATH", ":memory:")

from api.dependencies import get_db
from api.main import create_app
from api.tables import metadata


@asynccontextmanager
async def _noop_lifespan(app):
    """Replaces the real lifespan in tests — skips Alembic and adapter loading."""
    yield


@pytest.fixture
async def client():
    """
    Provides an AsyncClient backed by a fresh in-memory SQLite database.
    Tables are created via metadata.create_all() (no Alembic needed).
    Each test gets an isolated database — no state leaks between tests.
    """
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

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    await engine.dispose()
