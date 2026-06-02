"""Synchronous database access for Celery tasks.

Celery tasks are plain sync functions, so the worker uses a synchronous
SQLAlchemy engine against the same SQLite file the API serves. Table objects are
imported from ``api.tables`` (Core ``Table`` definitions, no engine/settings
coupling) so both processes share one schema definition.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from api.tables import api_keys, collections, documents, job_records, metadata, workspaces

__all__ = [
    "metadata",
    "workspaces",
    "collections",
    "api_keys",
    "documents",
    "job_records",
    "engine",
    "SessionLocal",
    "session_scope",
]

_DB_PATH = os.environ.get("DATABASE_PATH", "/store/embedbase.db")

engine = create_engine(f"sqlite:///{_DB_PATH}", future=True)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(engine, class_=Session, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
