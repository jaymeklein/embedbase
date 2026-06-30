"""AI-assisted tag suggestion service.

Gathers an entity's indexed text from the BM25 corpus (Redis) and runs the
configured :class:`~api.adapters.base.TagSuggester` over it. Suggestions are
ephemeral — nothing is persisted; the client applies chosen tags via the
assign endpoints. Workspaces are intentionally excluded (manual tagging only).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.tagging import get_tag_suggester
from api.models.config import TaggingConfig
from api.models.redis import CorpusConfig
from api.models.tagging import TagSuggestion
from api.services.redis.redis import get_corpus
from api.services.tags import require_collection, require_document, tags_by_entity


def _collection_text(redis: Any, col_id: str) -> str:
    """Join all indexed chunk text for a collection from the BM25 corpus."""
    corpus = get_corpus(redis, CorpusConfig(col_id))
    return "\n".join(text for _, _, text in corpus.data)


def _document_text(redis: Any, col_id: str, doc_id: str) -> str:
    """Join indexed chunk text for one document from the collection's corpus."""
    corpus = get_corpus(redis, CorpusConfig(col_id))
    return "\n".join(text for _, did, text in corpus.data if did == doc_id)


async def _effective_existing(
    db: AsyncSession, *, ws_id: str, col_id: str | None = None, doc_id: str | None = None
) -> list[str]:
    """Names of every tag already effectively on the entity (own + inherited).

    A document inherits its collection's and workspace's tags, so none of those
    should be re-suggested. Returns the sorted union across the given levels.
    """
    names: set[str] = set()
    for kind, entity_id in (("workspace", ws_id), ("collection", col_id), ("document", doc_id)):
        if entity_id is None:
            continue
        mapping = await tags_by_entity(kind, [entity_id], db)
        names.update(tag["name"] for tag in mapping.get(entity_id, []))
    return sorted(names)


def _normalize(name: str) -> str:
    """Lowercase, trim, and collapse whitespace — the canonical tag-name form."""
    return " ".join(name.strip().lower().split())


def _rank(
    suggestions: list[TagSuggestion], existing: list[str], min_confidence: float
) -> list[TagSuggestion]:
    """Order by confidence desc, drop those below the threshold, dedupe, and
    exclude any tag already on the entity (case-insensitive)."""
    blocked = {_normalize(name) for name in existing}
    seen: set[str] = set()
    ranked: list[TagSuggestion] = []
    for s in sorted(suggestions, key=lambda x: x.confidence, reverse=True):
        key = _normalize(s.name)
        if not key or s.confidence < min_confidence or key in blocked or key in seen:
            continue
        seen.add(key)
        ranked.append(s)
    return ranked


async def _suggest(text: str, existing: list[str], tagging: TaggingConfig) -> dict[str, Any]:
    """Run the configured LLM suggester off the event loop and shape the response.

    Tag suggestion is LLM-only (there is no local/keyword fallback). An
    unconfigured/unknown backend or an unreachable LLM both surface as a clear
    503 the UI can show, rather than an opaque 500.

    Raises:
        HTTPException: 503 when the LLM integration is missing or the call fails.
    """
    try:
        suggester = get_tag_suggester(tagging)
        suggestions = await asyncio.to_thread(suggester.suggest, text, existing)
    except Exception as exc:  # unknown backend or network/LLM failure
        raise HTTPException(
            status_code=503,
            detail="AI tag suggestion needs a working LLM integration "
            f"(Settings → AI tag suggester): {exc}",
        ) from exc
    ranked = _rank(suggestions, existing, tagging.suggester.min_confidence)
    return {"suggestions": [s.model_dump() for s in ranked]}


async def suggest_collection_tags(
    ws_id: str, col_id: str, *, db: AsyncSession, redis: Any, tagging: TaggingConfig
) -> dict[str, Any]:
    """Suggest tags for a collection from its indexed content (ephemeral).

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    await require_collection(ws_id, col_id, db)
    text = _collection_text(redis, col_id)
    existing = await _effective_existing(db, ws_id=ws_id, col_id=col_id)
    return await _suggest(text, existing, tagging)


async def suggest_document_tags(
    ws_id: str, col_id: str, doc_id: str, *, db: AsyncSession, redis: Any, tagging: TaggingConfig
) -> dict[str, Any]:
    """Suggest tags for a document from its indexed content (ephemeral).

    Raises:
        HTTPException: 404 when the collection or document is absent.
    """
    await require_collection(ws_id, col_id, db)
    await require_document(col_id, doc_id, db)
    text = _document_text(redis, col_id, doc_id)
    existing = await _effective_existing(db, ws_id=ws_id, col_id=col_id, doc_id=doc_id)
    return await _suggest(text, existing, tagging)
