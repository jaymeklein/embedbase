"""
Alembic async migration environment.

Uses async_engine_from_config so migrations run against whichever driver
``DATABASE_URL`` selects (aiosqlite by default, asyncpg/psycopg for
Postgres). run_sync() bridges the async connection into Alembic's
synchronous migration context.

render_as_batch is only enabled for SQLite, which does not support
ALTER TABLE ... DROP COLUMN or ALTER TABLE ... ADD CONSTRAINT — Alembic
emulates those by rebuilding the table. Postgres supports direct ALTER,
so batch mode there would just be a slower, unnecessary table rebuild.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic config object — provides access to values within alembic.ini
config = context.config

# Wire Python logging from alembic.ini if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import our metadata so autogenerate can diff the schema
from api.tables import metadata  # noqa: E402

target_metadata = metadata


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout without connecting to the DB
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch="sqlite" in url,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect to the DB and run migrations
# ---------------------------------------------------------------------------

def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # no pooling — each alembic run gets one connection
    )

    async with connectable.connect() as connection:
        # Bridge async connection into Alembic's sync migration context
        await connection.run_sync(do_run_migrations)
        # Persist explicitly. ``.connect()`` autobegins a transaction; Alembic's
        # own commit (over the run_sync-bridged sync connection) does not always
        # propagate to this outer async connection, so SQLite rolls the work back
        # on close and the migrations are silently lost. The runtime engine in
        # api/db.py applies the WAL/foreign-key pragmas for normal operation.
        await connection.commit()

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # asyncio.run() works here because _run_migrations_sync() in db.py
    # calls this via a ThreadPoolExecutor — the thread has no event loop,
    # so asyncio.run() can create a fresh one safely.
    asyncio.run(run_migrations_online())
