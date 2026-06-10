"""Migration parity tests — Alembic vs metadata.create_all.

Ensures the Alembic migration script and the SQLAlchemy table metadata
stay in sync. A column added to api/tables/ without a matching migration
revision will cause test_migration_schema_matches_metadata to fail.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool

from api.tables import metadata

# Ephemeral per-test SQLite files: NullPool closes each connection on return so
# no sqlite3.Connection lingers in a pool to be GC'd unclosed (ResourceWarning).

_API_DIR = Path(__file__).parent.parent.parent / "api"
_VERSIONS_DIR = _API_DIR / "alembic" / "versions"


def _apply_migrations(db_path: str) -> None:
    """Run all Alembic upgrades via a sync SQLite engine (test-only path).

    Loads each revision file in filename order (0001_, 0002_, ...) and calls
    its upgrade() function under an Alembic Operations context. Avoids the
    async env.py path to keep tests hermetic and fast.
    """
    engine = create_engine(f"sqlite:///{db_path}", poolclass=NullPool)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        for migration_file in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
            spec = importlib.util.spec_from_file_location(migration_file.stem, migration_file)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            with Operations.context(ctx):
                mod.upgrade()
        conn.commit()
    engine.dispose()


def test_migration_runs_without_error(tmp_path):
    """All upgrade() functions must complete without exception."""
    _apply_migrations(str(tmp_path / "test.db"))


def test_migration_schema_matches_metadata(tmp_path):
    """Tables and columns produced by migrations must match api/tables/ metadata.

    Fails if a column is added to a Table() definition in api/tables/ without
    a corresponding Alembic revision (or vice-versa).
    """
    db_a = str(tmp_path / "alembic.db")
    _apply_migrations(db_a)
    insp_a = inspect(create_engine(f"sqlite:///{db_a}", poolclass=NullPool))

    db_b = str(tmp_path / "metadata.db")
    engine_b = create_engine(f"sqlite:///{db_b}", poolclass=NullPool)
    metadata.create_all(engine_b)
    insp_b = inspect(engine_b)

    tables_a = set(insp_a.get_table_names())
    tables_b = set(insp_b.get_table_names())
    assert tables_a == tables_b, f"table mismatch — alembic={tables_a} metadata={tables_b}"

    for table in sorted(tables_b):
        cols_a = {c["name"] for c in insp_a.get_columns(table)}
        cols_b = {c["name"] for c in insp_b.get_columns(table)}
        assert cols_a == cols_b, (
            f"column mismatch in {table!r} — "
            f"alembic={sorted(cols_a)} metadata={sorted(cols_b)}"
        )


def test_migration_indexes_exist(tmp_path):
    """Expected indexes must be present after all migrations run."""
    db_path = str(tmp_path / "idx.db")
    _apply_migrations(db_path)
    insp = inspect(create_engine(f"sqlite:///{db_path}", poolclass=NullPool))

    idx_names = {
        idx["name"]
        for table in insp.get_table_names()
        for idx in insp.get_indexes(table)
    }

    assert "collections_workspace_idx" in idx_names
    assert "api_keys_prefix_idx" in idx_names
    assert "documents_collection_idx" in idx_names
