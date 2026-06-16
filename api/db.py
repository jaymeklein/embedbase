"""
Database layer — SQLAlchemy 2.0 async + Alembic migrations.

Table definitions live in api/tables/. This module owns the engine,
session factory, and migration runner. It re-exports all table objects
and metadata so existing `from api.db import X` imports stay valid.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.settings import settings
from api.tables import (
    api_keys,
    collection_tags,
    collections,
    document_tags,
    documents,
    job_records,
    metadata,
    tags,
    workspace_tags,
    workspaces,
)

__all__ = [
    "metadata",
    "workspaces",
    "collections",
    "api_keys",
    "documents",
    "job_records",
    "tags",
    "workspace_tags",
    "collection_tags",
    "document_tags",
    "engine",
    "AsyncSessionLocal",
    "init_db",
]


# ---------------------------------------------------------------------------
# Engine + session factory
# ---------------------------------------------------------------------------

def _async_db_url() -> str:
    return f"sqlite+aiosqlite:///{settings.database_path}"


engine = create_async_engine(_async_db_url(), echo=False)

# Pragmas must be set on the underlying sync connection at creation time.
# The "connect" event on the sync engine fires before the async wrapper sees
# the connection, making this the correct place for SQLite pragmas.
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Migration runner (called once at startup)
# ---------------------------------------------------------------------------

def _run_migrations_sync() -> None:
    """
    Run Alembic migrations synchronously.

    Called from a thread executor so Alembic's internal asyncio.run()
    has a clean event loop to work with (no existing loop in the thread).
    """
    from alembic import command
    from alembic.config import Config

    # alembic.ini lives next to this file
    ini_path = Path(__file__).parent / "alembic.ini"
    cfg = Config(str(ini_path))
    # Override URL so env.py always uses settings.database_path,
    # not the fallback hardcoded in alembic.ini
    cfg.set_main_option("sqlalchemy.url", _async_db_url())

    command.upgrade(cfg, "head")


async def init_db() -> None:
    """Run Alembic migrations at startup. Safe to call on every boot."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_migrations_sync)
