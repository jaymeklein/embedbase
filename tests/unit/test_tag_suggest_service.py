"""Unit tests for the AI tag suggestion service (api/services/tag_suggest.py)."""

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import insert

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import workspaces as ws_t
from api.models.config import TaggingConfig, TagSuggesterConfig
from api.models.tagging import TagSuggestion
from api.services import tag_suggest
from api.services import tags as tag_svc


def _sug(name: str, conf: float) -> TagSuggestion:
    return TagSuggestion(name=name, confidence=conf)


def _llm() -> TaggingConfig:
    """An LLM-backed tagging config (keep all suggestions — no min-confidence floor)."""
    return TaggingConfig(suggester=TagSuggesterConfig(backend="llm", min_confidence=0.0))


class _FakeSuggester:
    """Records what it was asked and returns canned suggestions (no LLM call)."""

    def __init__(self, suggestions: list[TagSuggestion] | None = None) -> None:
        self._suggestions = suggestions or []
        self.seen_text: str | None = None
        self.seen_existing: list[str] | None = None

    def suggest(self, text: str, existing: list[str]) -> list[TagSuggestion]:
        self.seen_text = text
        self.seen_existing = existing
        return self._suggestions


def _patch_suggester(monkeypatch, suggester: _FakeSuggester) -> None:
    monkeypatch.setattr(tag_suggest, "get_tag_suggester", lambda cfg: suggester)


def test_rank_orders_by_confidence_desc():
    out = tag_suggest._rank([_sug("a", 0.5), _sug("b", 0.9), _sug("c", 0.7)], [], 0.0)
    assert [s.name for s in out] == ["b", "c", "a"]


def test_rank_drops_below_min_confidence():
    out = tag_suggest._rank([_sug("a", 0.9), _sug("b", 0.79)], [], 0.8)
    assert [s.name for s in out] == ["a"]  # 0.79 < 0.8 dropped; 0.8 boundary kept elsewhere


def test_rank_keeps_confidence_equal_to_min():
    out = tag_suggest._rank([_sug("a", 0.8)], [], 0.8)
    assert [s.name for s in out] == ["a"]


def test_rank_dedupes_case_insensitively_keeping_highest():
    out = tag_suggest._rank([_sug("Kube", 0.6), _sug("kube", 0.9)], [], 0.0)
    assert [(s.name, s.confidence) for s in out] == [("kube", 0.9)]


def test_rank_excludes_existing_tags():
    out = tag_suggest._rank([_sug("python", 0.9), _sug("rust", 0.8)], ["Python"], 0.0)
    assert [s.name for s in out] == ["rust"]


class FakeRedis:
    """Minimal Redis double exposing only the ``get`` used by get_corpus."""

    def __init__(self, mapping: dict[str, str]):
        self._m = mapping

    def get(self, key: str):
        return self._m.get(key)


def _corpus(col_id: str, entries: list[tuple[str, str, str]]) -> FakeRedis:
    return FakeRedis({f"bm25:{col_id}:corpus": json.dumps([list(e) for e in entries])})


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _seed(db, ws="ws_1", col="col_1", doc="doc_1"):
    await db.execute(insert(ws_t).values(id=ws, name="WS", created_at=_now(), updated_at=_now()))
    await db.execute(
        insert(col_t).values(
            id=col, workspace_id=ws, name="C", created_at=_now(), updated_at=_now()
        )
    )
    await db.execute(
        insert(doc_t).values(
            id=doc, collection_id=col, filename="f.txt", file_type=".txt",
            created_at=_now(), updated_at=_now(),
        )
    )
    await db.commit()
    return ws, col, doc


async def test_suggest_document_tags_from_corpus(db_session, monkeypatch):
    ws, col, doc = await _seed(db_session)
    suggester = _FakeSuggester([_sug("kubernetes", 0.9)])
    _patch_suggester(monkeypatch, suggester)
    redis = _corpus(col, [("c1", doc, "kubernetes kubernetes scaling deployment")])
    out = await tag_suggest.suggest_document_tags(
        ws, col, doc, db=db_session, redis=redis, tagging=_llm()
    )
    names = {s["name"] for s in out["suggestions"]}
    assert "kubernetes" in names
    assert "scaling deployment" in (suggester.seen_text or "")  # corpus text reached the LLM


async def test_suggest_document_excludes_existing(db_session, monkeypatch):
    ws, col, doc = await _seed(db_session)
    tag = await tag_svc.create_tag(ws, "kubernetes", None, db_session)
    await tag_svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    _patch_suggester(monkeypatch, _FakeSuggester([_sug("kubernetes", 0.9), _sug("scaling", 0.9)]))
    redis = _corpus(col, [("c1", doc, "kubernetes kubernetes scaling")])
    out = await tag_suggest.suggest_document_tags(
        ws, col, doc, db=db_session, redis=redis, tagging=_llm()
    )
    assert all(s["name"] != "kubernetes" for s in out["suggestions"])


async def test_suggest_document_excludes_inherited_collection_tags(db_session, monkeypatch):
    ws, col, doc = await _seed(db_session)
    tag = await tag_svc.create_tag(ws, "kubernetes", None, db_session)
    await tag_svc.assign_collection_tag(ws, col, tag["id"], db_session)  # inherited, not own
    _patch_suggester(monkeypatch, _FakeSuggester([_sug("kubernetes", 0.9), _sug("scaling", 0.9)]))
    redis = _corpus(col, [("c1", doc, "kubernetes kubernetes scaling")])
    out = await tag_suggest.suggest_document_tags(
        ws, col, doc, db=db_session, redis=redis, tagging=_llm()
    )
    assert all(s["name"] != "kubernetes" for s in out["suggestions"])


async def test_suggest_collection_aggregates_documents(db_session, monkeypatch):
    ws, col, doc = await _seed(db_session)
    suggester = _FakeSuggester()
    _patch_suggester(monkeypatch, suggester)
    redis = _corpus(col, [("c1", doc, "alpha alpha"), ("c2", "doc_2", "beta beta")])
    await tag_suggest.suggest_collection_tags(
        ws, col, db=db_session, redis=redis, tagging=_llm()
    )
    # The whole collection's text is sent to the LLM (both documents).
    assert "alpha" in (suggester.seen_text or "")
    assert "beta" in (suggester.seen_text or "")


async def test_suggest_document_filters_to_that_document(db_session, monkeypatch):
    ws, col, doc = await _seed(db_session)
    suggester = _FakeSuggester()
    _patch_suggester(monkeypatch, suggester)
    redis = _corpus(col, [("c1", doc, "alpha alpha"), ("c2", "doc_other", "beta beta")])
    await tag_suggest.suggest_document_tags(
        ws, col, doc, db=db_session, redis=redis, tagging=_llm()
    )
    # Only the target document's text is sent — sibling-document text is excluded.
    assert "alpha" in (suggester.seen_text or "")
    assert "beta" not in (suggester.seen_text or "")


async def test_suggest_without_llm_integration_returns_503(db_session):
    ws, col, doc = await _seed(db_session)
    redis = _corpus(col, [("c1", doc, "kubernetes scaling")])
    # A non-LLM backend (e.g. a stale "keyword" value — that backend was removed)
    # has no integration to run, so the click must error rather than guess.
    stale = TaggingConfig(suggester=TagSuggesterConfig(backend="keyword"))
    with pytest.raises(HTTPException) as exc:
        await tag_suggest.suggest_document_tags(
            ws, col, doc, db=db_session, redis=redis, tagging=stale
        )
    assert exc.value.status_code == 503


async def test_suggest_llm_failure_returns_503(db_session, monkeypatch):
    ws, col, doc = await _seed(db_session)

    class _Boom:
        def suggest(self, text, existing):
            raise RuntimeError("LLM unreachable")

    monkeypatch.setattr(tag_suggest, "get_tag_suggester", lambda cfg: _Boom())
    redis = _corpus(col, [("c1", doc, "kubernetes scaling")])
    with pytest.raises(HTTPException) as exc:
        await tag_suggest.suggest_document_tags(
            ws, col, doc, db=db_session, redis=redis, tagging=_llm()
        )
    assert exc.value.status_code == 503


async def test_suggest_collection_not_found_404(db_session):
    ws, _, _ = await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await tag_suggest.suggest_collection_tags(
            ws, "col_missing", db=db_session, redis=_corpus("x", []), tagging=TaggingConfig()
        )
    assert exc.value.status_code == 404


async def test_suggest_document_not_found_404(db_session):
    ws, col, _ = await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await tag_suggest.suggest_document_tags(
            ws, col, "doc_missing", db=db_session, redis=_corpus(col, []),
            tagging=TaggingConfig(),
        )
    assert exc.value.status_code == 404
