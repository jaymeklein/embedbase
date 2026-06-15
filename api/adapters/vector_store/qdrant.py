"""Qdrant vector-store adapter (Delivery 3).

Uses the synchronous ``QdrantClient`` (blocking HTTP, like the Chroma adapter),
so no event-loop bridging is needed.

Chunk ids are 64-char sha256 hex strings, which are *not* valid Qdrant point ids
(Qdrant accepts only unsigned integers or UUIDs). Each point id is therefore a
deterministic UUID5 of the chunk id, and the original chunk id is preserved in
the payload under ``chunk_id`` so search results carry the real id back.

# verified against qdrant-client>=1.9 via Context7 (/qdrant/qdrant-client)
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

from api.models.chunk import Chunk
from api.models.document import DocumentSummary
from api.models.search import SearchResult

_SCROLL_PAGE = 256


class QdrantAdapter:
    """Vector store backed by a Qdrant server (1:1 collection ↔ namespace)."""

    def __init__(self, host: str, port: int, dimensions: int) -> None:
        self._host = host
        self._port = port
        self._dimensions = dimensions
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(host=self._host, port=self._port)
        return self._client

    def ping(self) -> bool:
        """Return True if the Qdrant server answers a collections listing."""
        try:
            self._get_client().get_collections()
            return True
        except Exception:
            return False

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        """Map a sha256 chunk id to a deterministic, Qdrant-valid UUID string."""
        return str(uuid.uuid5(uuid.NAMESPACE_OID, chunk_id))

    def _ensure_collection(self, name: str) -> None:
        """Create the collection with cosine distance if it does not exist."""
        from qdrant_client import models

        client = self._get_client()
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=self._dimensions, distance=models.Distance.COSINE
                ),
            )

    def upsert(self, collection_id: str, chunks: list[Chunk],
               vectors: list[list[float]]) -> None:
        """Insert or update chunk embeddings for a collection.

        Args:
            collection_id: Target collection (created on first use).
            chunks: Chunks to store; the real id is kept in ``payload.chunk_id``.
            vectors: Embedding per chunk, aligned by index with ``chunks``.
        """
        if not chunks:
            return
        from qdrant_client import models

        self._ensure_collection(collection_id)
        points = [
            models.PointStruct(
                id=self._point_id(c.id),
                vector=v,
                payload={"text": c.text, "chunk_id": c.id, **c.metadata.model_dump()},
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        self._get_client().upsert(collection_name=collection_id, points=points)

    def search(self, collection_id: str, vector: list[float], top_k: int,
               filters: dict | None = None) -> list[SearchResult]:
        """Return the ``top_k`` most cosine-similar chunks in a collection.

        Args:
            collection_id: Collection to search.
            vector: Query embedding.
            top_k: Maximum results to return.
            filters: Accepted for Protocol compatibility; metadata filtering is
                applied by the search service after ranking (mirrors Chroma).

        Returns:
            Ranked results; ``score`` is the cosine similarity Qdrant returns.
        """
        client = self._get_client()
        if not client.collection_exists(collection_id):
            return []
        response = client.query_points(
            collection_name=collection_id, query=vector, limit=top_k, with_payload=True
        )
        return [self._to_result(rank, point) for rank, point in enumerate(response.points)]

    @staticmethod
    def _to_result(rank: int, point: Any) -> SearchResult:
        """Translate a Qdrant scored point into a ``SearchResult``."""
        payload = dict(point.payload or {})
        chunk_id = payload.pop("chunk_id", None) or str(point.id)
        text = payload.pop("text", "")
        return SearchResult(
            chunk_id=chunk_id, text=text, score=float(point.score),
            rank=rank, metadata=payload,
        )

    def delete_document(self, collection_id: str, document_id: str) -> None:
        """Delete every point belonging to a document in a collection.

        Args:
            collection_id: Collection to prune.
            document_id: Document whose points to remove.
        """
        from qdrant_client import models

        client = self._get_client()
        with contextlib.suppress(Exception):
            client.delete(
                collection_name=collection_id,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchValue(value=document_id),
                            )
                        ]
                    )
                ),
            )

    def delete_collection(self, collection_id: str) -> None:
        """Delete an entire collection (no-op if it does not exist).

        Args:
            collection_id: Collection to drop.
        """
        client = self._get_client()
        with contextlib.suppress(Exception):
            client.delete_collection(collection_name=collection_id)

    def list_documents(self, collection_id: str) -> list[DocumentSummary]:
        """Aggregate stored points into per-document summaries.

        Timestamps are synthetic; the authoritative listing lives in SQLite.

        Args:
            collection_id: Collection to summarise.

        Returns:
            One ``DocumentSummary`` per distinct ``document_id``.
        """
        client = self._get_client()
        if not client.collection_exists(collection_id):
            return []
        agg: dict[str, dict[str, Any]] = {}
        offset: Any = None
        while True:
            records, offset = client.scroll(
                collection_name=collection_id, limit=_SCROLL_PAGE,
                offset=offset, with_payload=True, with_vectors=False,
            )
            for record in records:
                self._accumulate(agg, record.payload or {})
            if offset is None:
                break
        now = datetime.now(UTC)
        return [
            DocumentSummary(
                document_id=doc_id, filename=entry["filename"],
                file_type=entry["file_type"], chunk_count=entry["count"],
                created_at=now, updated_at=now,
            )
            for doc_id, entry in agg.items()
        ]

    @staticmethod
    def _accumulate(agg: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
        """Fold one point's payload into the per-document aggregation dict."""
        doc_id = payload.get("document_id")
        if not doc_id:
            return
        entry = agg.setdefault(
            doc_id,
            {"filename": payload.get("filename", ""),
             "file_type": payload.get("parser", ""), "count": 0},
        )
        entry["count"] += 1
