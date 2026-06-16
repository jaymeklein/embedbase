"""AI-assisted tag suggestion service.

Gathers an entity's indexed text from the BM25 corpus (Redis) and runs the
configured :class:`~api.adapters.base.TagSuggester` over it. Suggestions are
ephemeral — nothing is persisted; the client applies chosen tags via the
assign endpoints. Workspaces are intentionally excluded (manual tagging only).
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.tagging import get_tag_suggester
from api.models.config import TaggingConfig
from api.models.redis import CorpusConfig
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


async def _existing_names(kind: str, entity_id: str, db: AsyncSession) -> list[str]:
    """Return the names of tags already assigned to an entity."""
    mapping = await tags_by_entity(kind, [entity_id], db)
    return [tag["name"] for tag in mapping.get(entity_id, [])]


async def _suggest(text: str, existing: list[str], tagging: TaggingConfig) -> dict[str, Any]:
    """Run the configured suggester off the event loop and shape the response."""
    suggester = get_tag_suggester(tagging)
    suggestions = await asyncio.to_thread(suggester.suggest, text, existing)
    return {"suggestions": [s.model_dump() for s in suggestions]}


async def suggest_collection_tags(
    ws_id: str, col_id: str, *, db: AsyncSession, redis: Any, tagging: TaggingConfig
) -> dict[str, Any]:
    """Suggest tags for a collection from its indexed content (ephemeral).

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    await require_collection(ws_id, col_id, db)
    text = _collection_text(redis, col_id)
    existing = await _existing_names("collection", col_id, db)
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
    existing = await _existing_names("document", doc_id, db)
    return await _suggest(text, existing, tagging)
