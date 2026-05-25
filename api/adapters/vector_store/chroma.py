import contextlib

from api.models.chunk import Chunk
from api.models.document import DocumentSummary
from api.models.search import SearchResult


class ChromaAdapter:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._client = None

    def _get_client(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings

            from api.settings import settings as app_settings
            self._client = chromadb.HttpClient(
                host=self._host,
                port=self._port,
                settings=Settings(
                    chroma_client_auth_provider="chromadb.auth.token.TokenAuthClientProvider",
                    chroma_client_auth_credentials=app_settings.chroma_auth_token,
                ),
            )
        return self._client

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
            metadatas=[c.metadata.model_dump() for c in chunks],
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
                metadata=meta or {},
            ))
        return out

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
        # Full implementation in Delivery 2
        return []
