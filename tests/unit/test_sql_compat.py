"""Unit tests for dialect-aware INSERT selection.

``dialect_insert`` picks the pg or sqlite ``insert()`` construct off any bind
exposing ``.dialect.name`` — a fake namespace is enough, no engine needed.
"""

from types import SimpleNamespace

from api.sql_compat import dialect_insert
from api.tables import documents


def _bind(name: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=name))


def test_dialect_insert_returns_postgres_construct():
    stmt = dialect_insert(_bind("postgresql"), documents)
    assert "postgresql" in type(stmt).__module__
    assert hasattr(stmt, "on_conflict_do_nothing")


def test_dialect_insert_returns_sqlite_construct():
    stmt = dialect_insert(_bind("sqlite"), documents)
    assert "sqlite" in type(stmt).__module__
    assert hasattr(stmt, "on_conflict_do_nothing")
