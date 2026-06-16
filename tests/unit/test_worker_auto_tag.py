"""Unit tests for ingestion-time AI auto-tagging in the worker."""

from types import SimpleNamespace

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.models.chunk import Chunk, ChunkMetadata
from api.models.config import TaggingConfig, TagSuggesterConfig
from api.models.tagging import TagSuggestion
from api.tables import collections, document_tags, documents, metadata, tags, workspaces
from worker.tasks import _auto_tag_document

_TS = "2026-01-01T00:00:00"


def _factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'autotag.db'}", future=True, poolclass=NullPool
    )
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed(factory) -> None:
    with factory() as s:
        s.execute(insert(workspaces).values(id="ws_1", name="WS", created_at=_TS, updated_at=_TS))
        s.execute(insert(collections).values(
            id="col_1", workspace_id="ws_1", name="C", created_at=_TS, updated_at=_TS))
        s.execute(insert(documents).values(
            id="doc_1", collection_id="col_1", filename="f.md", file_type="md",
            created_at=_TS, updated_at=_TS))
        s.commit()


def _chunks(text="connascence of name is the weakest static coupling"):
    meta = ChunkMetadata(
        source_file="f.md", filename="f.md", parser="markdown",
        document_id="doc_1", chunk_index=0,
    )
    return [Chunk(id="c1", text=text, metadata=meta)]


def _config(*, enabled=True, min_confidence=0.8):
    tagging = TaggingConfig(
        suggester=TagSuggesterConfig(backend="llm", min_confidence=min_confidence),
        auto_tag_on_ingest=enabled,
    )
    return SimpleNamespace(tagging=tagging)


class _FakeSuggester:
    def __init__(self, suggestions):
        self._suggestions = suggestions

    def suggest(self, text, existing):
        return self._suggestions


def _doc_tag_names(factory) -> list[str]:
    with factory() as s:
        rows = s.execute(
            select(tags.c.name)
            .select_from(document_tags.join(tags, tags.c.id == document_tags.c.tag_id))
            .where(document_tags.c.document_id == "doc_1")
            .order_by(tags.c.name)
        ).fetchall()
    return [r[0] for r in rows]


def _patch_suggester(monkeypatch, suggester):
    monkeypatch.setattr("api.adapters.tagging.get_tag_suggester", lambda cfg: suggester)


def test_applies_only_high_confidence(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed(factory)
    _patch_suggester(monkeypatch, _FakeSuggester([
        TagSuggestion(name="connascence", confidence=0.95),
        TagSuggestion(name="coupling", confidence=0.82),
        TagSuggestion(name="weak idea", confidence=0.40),  # below threshold
    ]))

    _auto_tag_document(factory, "col_1", "doc_1", _chunks(), _config())

    assert _doc_tag_names(factory) == ["connascence", "coupling"]


def test_disabled_is_noop(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed(factory)
    _patch_suggester(monkeypatch, _FakeSuggester([TagSuggestion(name="x", confidence=1.0)]))

    _auto_tag_document(factory, "col_1", "doc_1", _chunks(), _config(enabled=False))

    assert _doc_tag_names(factory) == []


def test_empty_text_is_noop(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed(factory)
    _patch_suggester(monkeypatch, _FakeSuggester([TagSuggestion(name="x", confidence=1.0)]))

    _auto_tag_document(factory, "col_1", "doc_1", _chunks("   "), _config())

    assert _doc_tag_names(factory) == []


def test_suggester_failure_does_not_raise(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed(factory)

    def _boom(cfg):
        raise RuntimeError("LLM unreachable")

    monkeypatch.setattr("api.adapters.tagging.get_tag_suggester", _boom)

    _auto_tag_document(factory, "col_1", "doc_1", _chunks(), _config())  # must not raise

    assert _doc_tag_names(factory) == []


def test_reuses_existing_tag_without_duplicating(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed(factory)
    with factory() as s:  # pre-existing workspace tag with the same name
        s.execute(insert(tags).values(
            id="tag_pre", workspace_id="ws_1", name="connascence", created_at=_TS))
        s.commit()
    _patch_suggester(monkeypatch, _FakeSuggester([TagSuggestion(name="Connascence", confidence=0.9)]))

    _auto_tag_document(factory, "col_1", "doc_1", _chunks(), _config())

    with factory() as s:
        count = s.execute(
            select(func.count()).select_from(tags).where(tags.c.name == "connascence")
        ).scalar()
    assert count == 1  # no duplicate tag row created
    assert _doc_tag_names(factory) == ["connascence"]
