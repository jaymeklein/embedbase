"""Unit tests for the AI tag suggestion service (api/services/tag_suggest.py)."""

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import insert

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import workspaces as ws_t
from api.models.config import TaggingConfig
from api.services import tag_suggest
from api.services import tags as tag_svc


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


async def test_suggest_document_tags_from_corpus(db_session):
    ws, col, doc = await _seed(db_session)
    redis = _corpus(col, [("c1", doc, "kubernetes kubernetes scaling deployment")])
    out = await tag_suggest.suggest_document_tags(
        ws, col, doc, db=db_session, redis=redis, tagging=TaggingConfig()
    )
    names = {s["name"] for s in out["suggestions"]}
    assert "kubernetes" in names


async def test_suggest_document_excludes_existing(db_session):
    ws, col, doc = await _seed(db_session)
    tag = await tag_svc.create_tag(ws, "kubernetes", None, db_session)
    await tag_svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    redis = _corpus(col, [("c1", doc, "kubernetes kubernetes scaling")])
    out = await tag_suggest.suggest_document_tags(
        ws, col, doc, db=db_session, redis=redis, tagging=TaggingConfig()
    )
    assert all(s["name"] != "kubernetes" for s in out["suggestions"])


async def test_suggest_collection_aggregates_documents(db_session):
    ws, col, doc = await _seed(db_session)
    redis = _corpus(col, [("c1", doc, "alpha alpha"), ("c2", "doc_2", "beta beta")])
    out = await tag_suggest.suggest_collection_tags(
        ws, col, db=db_session, redis=redis, tagging=TaggingConfig()
    )
    names = {s["name"] for s in out["suggestions"]}
    assert {"alpha", "beta"}.issubset(names)


async def test_suggest_document_filters_to_that_document(db_session):
    ws, col, doc = await _seed(db_session)
    redis = _corpus(col, [("c1", doc, "alpha alpha"), ("c2", "doc_other", "beta beta")])
    out = await tag_suggest.suggest_document_tags(
        ws, col, doc, db=db_session, redis=redis, tagging=TaggingConfig()
    )
    names = {s["name"] for s in out["suggestions"]}
    assert "alpha" in names
    assert "beta" not in names


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
