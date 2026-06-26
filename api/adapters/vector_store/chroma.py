import contextlib
import json
from typing import Any

from api.models.chunk import Chunk
from api.models.document import DocumentSummary
from api.models.search import SearchResult


class ChromaAdapter:
    def __init__(self, host: str, port: int, auth_token: str = "embedbase-internal") -> None:
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._client: Any = None

    def _get_client(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings

            # verified against chromadb==0.5.3 via github.com/chroma-core/chroma/blob/0.5.3/chromadb/config.py
            self._client = chromadb.HttpClient(
                host=self._host,
                port=self._port,
                settings=Settings(
                    chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
                    chroma_client_auth_credentials=self._auth_token,
                    chroma_auth_token_transport_header="Authorization",
                ),
            )
        return self._client

    def ping(self) -> bool:
        """Return True if the Chroma server answers a heartbeat."""
        try:
            self._get_client().heartbeat()
            return True
        except Exception:
            return False

    @staticmethod
    def _encode_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """Make metadata Chroma-safe: drop None, JSON-encode list/dict values.

        Chroma only accepts scalar (str/int/float/bool) metadata values, so a
        ``None`` raises and a list (e.g. ``tags``) is rejected. ``_decode_metadata``
        reverses the JSON encoding on read so filters still see real lists.
        """
        out: dict[str, Any] = {}
        for key, value in meta.items():
            if value is None:
                continue
            out[key] = json.dumps(value) if isinstance(value, (list, dict)) else value
        return out

    @staticmethod
    def _decode_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """Reverse ``_encode_metadata``: parse JSON-encoded list/dict strings back."""
        out: dict[str, Any] = {}
        for key, value in meta.items():
            if isinstance(value, str) and value[:1] in "[{":
                try:
                    out[key] = json.loads(value)
                except json.JSONDecodeError:
                    out[key] = value
            else:
                out[key] = value
        return out

    def upsert(self, collection_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        client = self._get_client()
        col = client.get_or_create_collection(
            name=collection_id,
            metadata={"hnsw:space": "cosine"},
        )
        col.upsert(
            ids=[c.id for c in chunks],
            embeddings=vectors,
            documents=[c.text for c in chunks],
            metadatas=[self._encode_metadata(c.metadata.model_dump()) for c in chunks],
        )

    def search(
        self,
        collection_id: str,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        client = self._get_client()
        try:
            col = client.get_collection(name=collection_id)
        except Exception:
            return []

        where = filters or None
        results = col.query(
            query_embeddings=[vector],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        out = []
        ids = results["ids"][0]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]
        for rank, (cid, doc, meta, dist) in enumerate(zip(ids, docs, metas, dists, strict=False)):
            out.append(SearchResult(
                chunk_id=cid,
                text=doc,
                score=1.0 - dist,  # cosine distance → similarity
                rank=rank,
                metadata=self._decode_metadata(meta or {}),
            ))
        return out

    def iter_document_chunks(
        self, collection_id: str, document_id: str
    ) -> list[tuple[str, str, str]]:
        """Return ``(chunk_id, document_id, text)`` triples for a document's chunks."""
        client = self._get_client()
        try:
            col = client.get_collection(name=collection_id)
        except Exception:
            return []
        data = col.get(where={"document_id": document_id}, include=["documents"])
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        return [
            (cid, document_id, text or "")
            for cid, text in zip(ids, docs, strict=False)
        ]

    def set_document_tags(
        self, collection_id: str, document_id: str, tags: list[str]
    ) -> None:
        """Rewrite the ``tags`` metadata on every chunk of a document.

        Fetches each chunk's existing metadata, overwrites only the ``tags``
        key, and updates in place so other metadata (document_id, filename, …)
        is preserved — Chroma's ``update`` replaces, not merges, per-id metadata.

        # verified against chromadb==0.5.3 via official docs (collection.get /
        # collection.update) — github.com/chroma-core/chroma docs/collections
        """
        client = self._get_client()
        with contextlib.suppress(Exception):
            col = client.get_collection(name=collection_id)
            existing = col.get(where={"document_id": document_id}, include=["metadatas"])
            ids = existing["ids"]
            if not ids:
                return
            metas = existing["metadatas"] or [{} for _ in ids]
            updated = [self._encode_metadata({**(m or {}), "tags": tags}) for m in metas]
            col.update(ids=ids, metadatas=updated)

    def delete_document(self, collection_id: str, document_id: str) -> None:
        client = self._get_client()
        with contextlib.suppress(Exception):
            col = client.get_collection(name=collection_id)
            col.delete(where={"document_id": document_id})

    def delete_collection(self, collection_id: str) -> None:
        client = self._get_client()
        with contextlib.suppress(Exception):
            client.delete_collection(name=collection_id)

    def list_documents(self, collection_id: str) -> list[DocumentSummary]:
        """Aggregate stored chunks into per-document summaries.

        Timestamps are not tracked in the vector store; the authoritative
        document listing (with created/updated times) comes from SQLite. This is
        a convenience view over what is actually indexed.
        """
        from datetime import UTC, datetime

        client = self._get_client()
        try:
            col = client.get_collection(name=collection_id)
        except Exception:
            return []

        data = col.get(include=["metadatas"])
        metas = data.get("metadatas") or []
        agg: dict[str, dict] = {}
        for meta in metas:
            doc_id = (meta or {}).get("document_id")
            if not doc_id:
                continue
            entry = agg.setdefault(
                doc_id,
                {
                    "filename": meta.get("filename", ""),
                    "file_type": meta.get("parser", ""),
                    "count": 0,
                },
            )
            entry["count"] += 1

        now = datetime.now(UTC)
        return [
            DocumentSummary(
                document_id=doc_id,
                filename=entry["filename"],
                file_type=entry["file_type"],
                chunk_count=entry["count"],
                created_at=now,
                updated_at=now,
            )
            for doc_id, entry in agg.items()
        ]
