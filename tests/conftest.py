import os
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Force-set test values so VS Code's Python extension loading .env doesn't
# cause settings.master_api_key to diverge from the hardcoded test headers.
os.environ["MASTER_API_KEY"] = "test-master-key-for-testing-only"
os.environ["DATABASE_PATH"] = ":memory:"

from api.dependencies import get_db
from api.main import create_app
from api.tables import metadata


@asynccontextmanager
async def _noop_lifespan(app):
    """Replaces the real lifespan in tests — skips Alembic and adapter loading."""
    yield


@pytest.fixture
def redis_client():
    """Register an in-memory Redis for the test — the same idea as in-memory SQLite.

    ``fakeredis`` is a real implementation of the Redis command set running in
    process (get/set/incr all behave as production), so the BM25 corpus path is
    exercised for real with no container, network, or ``REDIS_URL`` — identical
    in CI and locally.
    """
    import fakeredis

    from api.dependencies import set_redis_client

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    set_redis_client(client)
    try:
        yield client
    finally:
        set_redis_client(None)
        client.close()


@pytest.fixture(autouse=True)
def _neutralize_broker(monkeypatch):
    """Stop any test from reaching a real Celery broker.

    Tag assignment/rename/merge/delete now enqueue a per-document vector-store
    sync via api.services.tag_bridge. Patching ``send_task`` (rather than the
    named ``enqueue_*`` helpers) keeps those helpers real — tests that assert on
    the producer or the bridge re-patch the layer they need, overriding this.
    """
    from api.services import tasks as task_producer

    monkeypatch.setattr(task_producer._producer, "send_task", lambda *a, **k: None)


@pytest.fixture
async def db_session():
    """A bare in-memory AsyncSession with the full schema created.

    For unit-testing async DB code (e.g. the auth service) without spinning up
    the whole ASGI app.
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

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


MASTER_KEY = os.environ.get("MASTER_API_KEY", "test-master-key-for-testing-only")


@pytest.fixture
def master_client(client):
    """AsyncClient pre-configured with the master API key header."""
    client.headers.update({"Authorization": f"Bearer {MASTER_KEY}"})
    return client


@pytest.fixture
def make_user_key(client):
    """Async helper: create a user, apply grants, and mint their API key.

    ``grants`` is an iterable of ``(resource_type, resource_id, level)`` tuples.
    Returns ``(user_id, raw_key)``. Uses explicit master headers per call so it
    never mutates the shared client's default headers — unauthenticated ``client``
    requests stay unauthenticated.
    """
    master = {"Authorization": f"Bearer {MASTER_KEY}"}

    async def _make(grants=()):
        suffix = uuid4().hex[:8]
        user_id = (
            await client.post(
                "/users",
                json={"username": f"user_{suffix}", "email": f"user_{suffix}@example.com"},
                headers=master,
            )
        ).json()["id"]
        for resource_type, resource_id, level in grants:
            r = await client.post(
                f"/users/{user_id}/permissions",
                json={"resource_type": resource_type, "resource_id": resource_id, "level": level},
                headers=master,
            )
            assert r.status_code == 201, r.text
        raw = (
            await client.post(f"/users/{user_id}/key", json={}, headers=master)
        ).json()["raw_key"]
        return user_id, raw

    return _make


@pytest.fixture
def make_operator(client):
    """Async helper: create a console user and return a live login session.

    Creates the user (``is_admin`` optional), applies ``grants`` (as in
    :func:`make_user_key`), then completes first login + the forced password change.
    Returns a dict with ``user_id``, ``username``, ``temp_password``,
    ``must_change_token`` (the first-login token), ``token`` (a full session token),
    and ``password`` — enough to drive any point of the login/change flow.
    """
    master = {"Authorization": f"Bearer {MASTER_KEY}"}
    new_password = "changed-password-123456"

    async def _make(*, is_admin=False, grants=()):
        suffix = uuid4().hex[:8]
        username = f"op_{suffix}"
        created = (
            await client.post(
                "/users",
                json={
                    "username": username,
                    "email": f"{username}@example.com",
                    "is_admin": is_admin,
                },
                headers=master,
            )
        ).json()
        user_id, temp_password = created["id"], created["temp_password"]
        for resource_type, resource_id, level in grants:
            r = await client.post(
                f"/users/{user_id}/permissions",
                json={"resource_type": resource_type, "resource_id": resource_id, "level": level},
                headers=master,
            )
            assert r.status_code == 201, r.text
        first = (
            await client.post(
                "/auth/login", json={"username": username, "password": temp_password}
            )
        ).json()
        changed = (
            await client.post(
                "/auth/change-password",
                json={"current_password": temp_password, "new_password": new_password},
                headers={"Authorization": f"Bearer {first['access_token']}"},
            )
        ).json()
        return {
            "user_id": user_id,
            "username": username,
            "temp_password": temp_password,
            "must_change_token": first["access_token"],
            "token": changed["access_token"],
            "password": new_password,
        }

    return _make


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
        # Expose the app's session factory so tests can seed DB state the API has
        # no endpoint for (e.g. a document's chunk_count, set by the worker).
        ac.session_factory = session_factory
        yield ac

    await engine.dispose()
