"""Dialect-aware ``INSERT`` — SQLite in dev, Postgres in prod.

Both dialects' ``insert()`` construct exposes the same ``on_conflict_do_*``
methods; only the import differs. Callers pass whatever they already have
bound to a connection (an ``AsyncSession``, a sync ``Session``, or an
``Engine``) — anything exposing ``.dialect.name``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Table


def dialect_insert(bind: Any, table: Table):
    """Return an ``insert()`` construct matching ``bind``'s SQL dialect."""
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)
