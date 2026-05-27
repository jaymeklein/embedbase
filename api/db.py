"""
Database layer — SQLAlchemy 2.0 async + Alembic migrations.

Tables are defined here as Core Table objects so that:
  - Alembic autogenerate can diff them against the live schema
  - Routers can import them for type-safe Core queries (select/insert/update/delete)
  - No ORM / no inheritance — plain Core expressions throughout

Pragmas (WAL, synchronous=NORMAL, foreign_keys=ON) are applied via a sync
engine event that fires on every new connection, before the async layer wraps it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.settings import settings

# ---------------------------------------------------------------------------
# Metadata — single registry for all tables; Alembic reads this for autogenerate
# ---------------------------------------------------------------------------

metadata = MetaData()

workspaces = Table(
    "workspaces",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("color", String, nullable=False, server_default="#6366f1"),
    Column("icon", String, nullable=False, server_default="folder"),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

collections = Table(
    "collections",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "workspace_id",
        String,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("color", String, nullable=False, server_default="#8b5cf6"),
    Column("icon", String, nullable=False, server_default="book"),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("workspace_id", "name", name="collections_name_workspace_unique"),
    Index("collections_workspace_idx", "workspace_id"),
)

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "collection_id",
        String,
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("key_prefix", String, nullable=False),
    Column("key_hash", String, nullable=False),
    Column("label", String, nullable=False, server_default=""),
    Column("created_at", String, nullable=False),
    Column("last_used_at", String, nullable=True),
    Index("api_keys_prefix_idx", "key_prefix"),
)

documents = Table(
    "documents",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "collection_id",
        String,
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("filename", String, nullable=False),
    Column("file_type", String, nullable=False),
    Column("file_size", Integer, nullable=True),
    Column("chunk_count", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("documents_collection_idx", "collection_id"),
)

job_records = Table(
    "job_records",
    metadata,
    Column("job_id", String, primary_key=True),
    Column("document_id", String, nullable=False),
    Column("collection_id", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("file_type", String, nullable=False),
    Column("status", String, nullable=False, server_default="pending"),
    Column("chunk_count", Integer, nullable=True),
    Column("error", Text, nullable=True),
    Column("celery_task_id", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)


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
