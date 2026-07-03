"""Unit tests for the worker's storage-backed delete path (PR 2).

``_delete_stored_object`` removes a document's original from whatever backend holds
it, resolved from the row before it's deleted. Infra-free: SessionLocal points at a
throwaway SQLite DB and get_storage is patched to a spy.
"""

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import worker.tasks as wt
from api.models.config import AppConfig
from api.tables import documents, metadata


class _SpyStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'del.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed_doc(factory, *, doc_id, col_id, ext, backend):
    with factory() as s:
        s.execute(
            insert(documents).values(
                id=doc_id, collection_id=col_id, filename=f"f{ext}", file_type=ext,
                storage_backend=backend, created_at="t", updated_at="t",
            )
        )
        s.commit()


def test_delete_stored_object_uses_recorded_backend_and_key(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed_doc(factory, doc_id="doc_x", col_id="col_x", ext=".pdf", backend="s3x")
    spy = _SpyStorage()
    seen = {}

    def fake_get_storage(cfg, name=None):
        seen["name"] = name
        return spy

    monkeypatch.setattr(wt, "SessionLocal", factory)
    monkeypatch.setattr(wt, "get_config", lambda: AppConfig())
    monkeypatch.setattr("api.services.storage.get_storage", fake_get_storage)

    wt._delete_stored_object("doc_x", "col_x")

    assert seen["name"] == "s3x"
    assert spy.deleted == ["col_x/doc_x.pdf"]


def test_delete_stored_object_defaults_null_backend_to_local(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed_doc(factory, doc_id="doc_y", col_id="col_y", ext=".txt", backend=None)
    spy = _SpyStorage()
    seen = {}

    def fake_get_storage(cfg, name=None):
        seen["name"] = name
        return spy

    monkeypatch.setattr(wt, "SessionLocal", factory)
    monkeypatch.setattr(wt, "get_config", lambda: AppConfig())
    monkeypatch.setattr("api.services.storage.get_storage", fake_get_storage)

    wt._delete_stored_object("doc_y", "col_y")

    assert seen["name"] == "local"  # NULL backend resolves to local
    assert spy.deleted == ["col_y/doc_y.txt"]


def test_delete_stored_object_swallows_backend_error(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed_doc(factory, doc_id="doc_z", col_id="col_z", ext=".txt", backend="s3x")

    class _Boom:
        def delete(self, key: str) -> None:
            raise RuntimeError("s3 unreachable")

    monkeypatch.setattr(wt, "SessionLocal", factory)
    monkeypatch.setattr(wt, "get_config", lambda: AppConfig())
    monkeypatch.setattr("api.services.storage.get_storage", lambda cfg, name=None: _Boom())

    # Best-effort: a backend failure must not propagate (row/vector cleanup proceeds).
    wt._delete_stored_object("doc_z", "col_z")


def test_delete_stored_object_missing_row_resolves_no_backend(tmp_path, monkeypatch):
    factory = _factory(tmp_path)  # schema present, no document row for this id
    resolved = {"called": False}

    def fake_get_storage(cfg, name=None):
        resolved["called"] = True
        return _SpyStorage()

    monkeypatch.setattr(wt, "SessionLocal", factory)
    monkeypatch.setattr(wt, "get_config", lambda: AppConfig())
    monkeypatch.setattr("api.services.storage.get_storage", fake_get_storage)

    wt._delete_stored_object("ghost", "col_x")  # row absent → returns before storage

    assert resolved["called"] is False  # nothing to key off → no backend built
