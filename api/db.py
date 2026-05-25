import aiosqlite
from api.settings import settings

_MIGRATIONS = [
    # Migration 0001 — initial schema
    """
    CREATE TABLE IF NOT EXISTS workspaces (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        color       TEXT NOT NULL DEFAULT '#6366f1',
        icon        TEXT NOT NULL DEFAULT 'folder',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS collections (
        id           TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name         TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        color        TEXT NOT NULL DEFAULT '#8b5cf6',
        icon         TEXT NOT NULL DEFAULT 'book',
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS collections_workspace_idx ON collections(workspace_id);

    CREATE UNIQUE INDEX IF NOT EXISTS collections_name_workspace_unique
        ON collections(workspace_id, name);

    CREATE TABLE IF NOT EXISTS api_keys (
        id             TEXT PRIMARY KEY,
        collection_id  TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        key_prefix     TEXT NOT NULL,
        key_hash       TEXT NOT NULL,
        label          TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL,
        last_used_at   TEXT
    );

    CREATE INDEX IF NOT EXISTS api_keys_prefix_idx ON api_keys(key_prefix);

    CREATE TABLE IF NOT EXISTS documents (
        id            TEXT PRIMARY KEY,
        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        filename      TEXT NOT NULL,
        file_type     TEXT NOT NULL,
        file_size     INTEGER,
        chunk_count   INTEGER,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS documents_collection_idx ON documents(collection_id);

    CREATE TABLE IF NOT EXISTS job_records (
        job_id         TEXT PRIMARY KEY,
        document_id    TEXT NOT NULL,
        collection_id  TEXT NOT NULL,
        filename       TEXT NOT NULL,
        file_type      TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'pending',
        chunk_count    INTEGER,
        error          TEXT,
        celery_task_id TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    """,
]


async def get_connection() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def run_migrations(db: aiosqlite.Connection) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    await db.commit()

    row = await (await db.execute("SELECT MAX(version) FROM schema_migrations")).fetchone()
    current_version = row[0] or 0

    for i, sql in enumerate(_MIGRATIONS, start=1):
        if i <= current_version:
            continue
        await db.executescript(sql)
        await db.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
            (i,),
        )
        await db.commit()
