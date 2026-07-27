"""Integration tests for the Delivery 4 MCP server.

Covers the D4 verification checklist:

- ``list_workspaces`` returns the workspace tree with collection + document counts,
- ``search_documents`` across two collections returns merged, source-tagged results,
- ``ingest_document`` (local path) + ``list_documents`` + ``delete_document`` round-trip,
- rate limiting: the 61st request in a minute is rejected with HTTP 429,
- auth: a missing/invalid API key is rejected with HTTP 401.

The tool *logic* is exercised directly against a seeded in-memory database (no
SSE transport required); the auth + rate-limit behaviour is exercised at the
ASGI layer through the middleware that guards the mounted MCP app.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from api.models.search import SearchResult
from api.services.auth import Principal
from api.services.mcp import tools
from api.services.mcp.middleware import MCPAuthRateLimitMiddleware
from api.services.mcp.rate_limit import TokenBucketRateLimiter
from api.tables import collections as collections_t
from api.tables import documents as documents_t
from api.tables import job_records as job_records_t
from api.tables import metadata
from api.tables import permissions as permissions_t
from api.tables import users as users_t
from api.tables import workspaces as workspaces_t

# The MCP transport authenticates the caller; tools receive the resolved principal.
MASTER = Principal(is_master=True)
USER = Principal(is_master=False, user_id="usr1")


async def _seed_user_with_grants(db, grants, *, user_id="usr1"):
    """Insert a user (FK target) plus their permission grants for enforcement tests."""
    await db.execute(
        insert(users_t).values(
            id=user_id, email=f"{user_id}@example.com", name="",
            is_active=True, created_at="t", updated_at="t",
        )
    )
    for resource_type, resource_id, level in grants:
        await db.execute(
            insert(permissions_t).values(
                id=uuid4().hex, user_id=user_id, resource_type=resource_type,
                resource_id=resource_id, level=level, created_at="t",
            )
        )
    await db.commit()


def _result(chunk_id: str, score: float, **meta: object) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text=f"text-{chunk_id}", score=score, metadata=dict(meta))


class _FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]] * len(texts)

    @property
    def dimensions(self) -> int:
        return 3


class _SeededVectorStore:
    def __init__(self, by_collection: dict[str, list[SearchResult]]) -> None:
        self._by = by_collection

    def search(
        self, collection_id: str, vector: list[float], top_k: int, filters: dict | None = None
    ) -> list[SearchResult]:
        return [r.model_copy() for r in self._by.get(collection_id, [])][:top_k]

    def bm25_scores(
        self, collection_id: str, query: str, chunk_ids: list[str]
    ) -> dict[str, float]:
        # No FTS matches -> search falls back to semantic_only.
        return {}

    def hybrid_search(
        self, collection_id: str, vector: list[float], query: str, top_k: int,
        *, alpha: float = 0.7, k: int = 60, filters: dict | None = None,
    ) -> tuple[list[SearchResult], bool]:
        # No lexical matches -> HYBRID degrades to SEMANTIC_ONLY (real cosine scores).
        return self.search(collection_id, vector, top_k, filters), False

    def delete_document(self, collection_id: str, document_id: str) -> None:
        self._by[collection_id] = [
            r for r in self._by.get(collection_id, []) if r.metadata.get("document_id") != document_id
        ]

    def upsert(self, *args: object, **kwargs: object) -> None: ...
    def delete_collection(self, *args: object) -> None: ...
    def list_documents(self, *args: object) -> list:
        return []

    def _doc_chunks_sorted(self, collection_id: str, document_id: str) -> list[SearchResult]:
        items = [
            r for r in self._by.get(collection_id, [])
            if r.metadata.get("document_id") == document_id
        ]
        return sorted(items, key=lambda r: r.metadata.get("chunk_index", 0))

    def document_chunks(
        self, collection_id: str, document_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[SearchResult], int]:
        ordered = self._doc_chunks_sorted(collection_id, document_id)
        return ordered[offset : offset + limit], len(ordered)

    def chunks_by_ids(self, collection_id: str, chunk_ids: list[str]) -> list[SearchResult]:
        wanted = set(chunk_ids)
        return [r for r in self._by.get(collection_id, []) if r.chunk_id in wanted]


class _FakePresignStorage:
    """Storage double for the presigned two-step upload: hands out fake PUT/GET URLs and records
    the 'uploaded' object bytes so object_head + read_head reflect the client's PUT. For tests that
    only care about size (the oversize path, rejected before the content read), pass ``size=``
    instead of ``data=`` to register a size without materialising bytes."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._sizes: dict[str, int] = {}

    def presigned_put(self, key: str) -> str:
        return f"https://minio.test/put/{key}"

    def presigned_get(self, key: str, filename: str) -> str:
        return f"https://minio.test/get/{key}?f={filename}"

    def object_head(self, key: str) -> int | None:
        if key in self._objects:
            return len(self._objects[key])
        return self._sizes.get(key)

    def read_head(self, key: str, n: int) -> bytes | None:
        data = self._objects.get(key)
        return None if data is None else data[:n]

    def receive(self, key: str, data: bytes | None = None, size: int | None = None) -> None:
        """Simulate the client PUTting bytes. Pass real ``data`` (content-validated at confirm), or
        a ``size`` for size-only tests where the bytes are never read (the oversize path)."""
        if data is not None:
            self._objects[key] = data
        else:
            self._sizes[key] = size if size is not None else 42

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)
        self._sizes.pop(key, None)


def _typed_bytes(ext: str, size: int) -> bytes:
    """Exactly ``size`` bytes that pass content validation for ``ext`` (PDF magic or plain text)."""
    head = b"%PDF-1.4\n" if ext == ".pdf" else b""
    return (head + b"x" * size)[:size] if size >= len(head) else head


@pytest.fixture
async def seeded():
    """Yield (session_factory, vector_store) seeded with a workspace + 2 collections."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    now = datetime.now(UTC).isoformat()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(workspaces_t).values(id="ws1", name="Research", created_at=now, updated_at=now)
        )
        await conn.execute(
            insert(collections_t).values(
                id="colA", workspace_id="ws1", name="papers", created_at=now, updated_at=now
            )
        )
        await conn.execute(
            insert(collections_t).values(
                id="colB", workspace_id="ws1", name="notes", created_at=now, updated_at=now
            )
        )
        for doc_id, col_id in (("docA", "colA"), ("docA2", "colA"), ("docB", "colB")):
            await conn.execute(
                insert(documents_t).values(
                    id=doc_id, collection_id=col_id, filename=f"{doc_id}.txt",
                    file_type=".txt", created_at=now, updated_at=now,
                )
            )

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = _SeededVectorStore(
        {
            "colA": [
                _result("a1", 0.9, document_id="docA", filename="a.pdf"),
                _result("a2", 0.5, document_id="docA2", filename="a2.pdf"),
            ],
            "colB": [_result("b1", 0.8, document_id="docB", filename="b.md")],
        }
    )
    yield factory, store
    await engine.dispose()


# ── Tool logic ────────────────────────────────────────────────────────────────


async def test_list_workspaces_returns_tree_with_counts(seeded):
    factory, _ = seeded
    async with factory() as db:
        out = await tools.list_workspaces(db=db, principal=MASTER)

    workspaces = out["workspaces"]
    assert len(workspaces) == 1
    ws = workspaces[0]
    assert (ws["id"], ws["name"]) == ("ws1", "Research")
    assert ws["collection_count"] == 2
    assert ws["document_count"] == 3
    by_name = {c["name"]: c for c in ws["collections"]}
    assert by_name["papers"]["document_count"] == 2
    assert by_name["notes"]["document_count"] == 1


async def test_search_documents_merges_two_collections(seeded):
    factory, store = seeded
    async with factory() as db:
        out = await tools.search_documents(
            query="vectors",
            collection_ids=["colA", "colB"],
            top_k=10,
            db=db,
            principal=MASTER,
            embedder=_FakeEmbedder(),
            vector_store=store,
        )

    results = out["results"]
    assert len(results) == 3
    assert {r["source"]["collection_id"] for r in results} == {"colA", "colB"}
    assert set(out["collection_stats"].keys()) == {"colA", "colB"}


async def test_search_documents_clamps_top_k_to_max_results(seeded):
    factory, store = seeded
    async with factory() as db:
        out = await tools.search_documents(
            query="vectors",
            collection_ids=["colA"],
            top_k=999,  # far above max_results; must not raise a validation error
            max_results=20,
            db=db,
            principal=MASTER,
            embedder=_FakeEmbedder(),
            vector_store=store,
        )
    assert len(out["results"]) <= 20


async def test_ingest_list_delete_roundtrip(seeded, tmp_path, monkeypatch):
    factory, _ = seeded
    # ingest_local_path now copies the file into the (local) storage backend, which
    # writes under settings.upload_dir — point it at a writable temp dir.
    monkeypatch.setattr("api.services.storage.settings.upload_dir", str(tmp_path))
    monkeypatch.setattr(
        "api.services.documents.task_producer.enqueue_ingest", lambda *a, **k: "task-ingest"
    )
    monkeypatch.setattr(
        "api.services.documents.task_producer.enqueue_delete", lambda *a, **k: "task-delete"
    )
    src = tmp_path / "note.txt"
    src.write_text("hello world", encoding="utf-8")

    async with factory() as db:
        created = await tools.ingest_document(
            collection_id="colA", file_path=str(src), db=db, principal=MASTER
        )
        assert created["status"] == "pending"
        assert created["collection_id"] == "colA"
        doc_id = created["document_id"]

        listed = await tools.list_documents(collection_id="colA", db=db, principal=MASTER)
        assert doc_id in {d["document_id"] for d in listed["documents"]}

        deleted = await tools.delete_document(document_id=doc_id, db=db, principal=MASTER)
        assert deleted == {"document_id": doc_id, "collection_id": "colA", "status": "deleting"}


async def test_ingest_document_rejects_unknown_extension(seeded, tmp_path):
    from fastapi import HTTPException

    factory, _ = seeded
    bad = tmp_path / "data.xyz"
    bad.write_text("nope", encoding="utf-8")
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.ingest_document(
                collection_id="colA", file_path=str(bad), db=db, principal=MASTER
            )
    assert exc.value.status_code == 415


async def test_ingest_document_missing_file_is_404(seeded, tmp_path):
    from fastapi import HTTPException

    factory, _ = seeded
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.ingest_document(
                collection_id="colA", file_path=str(tmp_path / "ghost.txt"),
                db=db, principal=MASTER,
            )
    assert exc.value.status_code == 404


async def test_delete_document_unknown_id_is_404(seeded):
    from fastapi import HTTPException

    factory, _ = seeded
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.delete_document(document_id="doc_nope", db=db, principal=MASTER)
    assert exc.value.status_code == 404


# ── Document lifecycle: presigned upload / status / download / chunks ─────────


def _use_presign_storage(monkeypatch) -> "_FakePresignStorage":
    """Point the document service at a presigning storage double + stub the ingest broker."""
    storage = _FakePresignStorage()
    monkeypatch.setattr("api.services.documents.get_storage", lambda cfg, name=None: storage)
    monkeypatch.setattr(
        "api.services.documents.task_producer.enqueue_ingest", lambda *a, **k: "task-ingest"
    )
    return storage


async def test_presigned_upload_confirm_roundtrip(seeded, monkeypatch):
    from api.services.documents import document_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    async with factory() as db:
        reserved = await tools.request_upload(
            collection_id="colA", filename="report.txt", retention_days=7,
            db=db, principal=MASTER,
        )
        assert reserved["upload_url"].startswith("https://minio.test/put/")
        doc_id = reserved["document_id"]
        # An awaiting_upload doc stays hidden from listings until confirmed.
        listed = await tools.list_documents(collection_id="colA", db=db, principal=MASTER)
        assert doc_id not in {d["document_id"] for d in listed["documents"]}
        # Simulate the client PUT to the presigned URL, then confirm.
        storage.receive(document_key("colA", doc_id, ".txt"), data=_typed_bytes(".txt", 123))
        confirmed = await tools.confirm_upload(document_id=doc_id, db=db, principal=MASTER)
        assert confirmed["status"] == "pending"
        assert confirmed["file_size"] == 123
        status = await tools.get_document_status(document_id=doc_id, db=db, principal=MASTER)
        assert status["status"] == "pending"
        # Now confirmed → it shows up in the listing.
        listed = await tools.list_documents(collection_id="colA", db=db, principal=MASTER)
        assert doc_id in {d["document_id"] for d in listed["documents"]}


async def test_confirm_upload_twice_is_409(seeded, monkeypatch):
    from api.services.documents import document_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    async with factory() as db:
        reserved = await tools.request_upload(
            collection_id="colA", filename="x.txt", db=db, principal=MASTER
        )
        doc_id = reserved["document_id"]
        storage.receive(document_key("colA", doc_id, ".txt"), data=_typed_bytes(".txt", 20))
        first = await tools.confirm_upload(document_id=doc_id, db=db, principal=MASTER)
        assert first["status"] == "pending"
        # A second confirm on an already-active doc is a no-op 409 (no duplicate job).
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_upload(document_id=doc_id, db=db, principal=MASTER)
    assert exc.value.status_code == 409


async def test_confirm_upload_without_bytes_is_409(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        reserved = await tools.request_upload(
            collection_id="colA", filename="x.txt", db=db, principal=MASTER
        )
        # No client PUT → object_head returns None → confirm 409s.
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_upload(
                document_id=reserved["document_id"], db=db, principal=MASTER
            )
    assert exc.value.status_code == 409


async def test_confirm_upload_oversized_is_413_and_purged(seeded, monkeypatch):
    from api.models.config import AppConfig
    from api.services.documents import document_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    # 1 MiB cap so the "uploaded" object (2 MiB) exceeds it.
    monkeypatch.setattr(
        "api.services.documents.get_app_config", lambda: AppConfig(max_file_size_mb=1)
    )
    async with factory() as db:
        reserved = await tools.request_upload(
            collection_id="colA", filename="big.txt", db=db, principal=MASTER
        )
        key = document_key("colA", reserved["document_id"], ".txt")
        storage.receive(key, size=2 * 1024 * 1024)  # over the 1 MiB cap
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_upload(
                document_id=reserved["document_id"], db=db, principal=MASTER
            )
    assert exc.value.status_code == 413
    assert storage.object_head(key) is None  # oversized object was purged


async def test_confirm_upload_content_mismatch_is_415_and_purged(seeded, monkeypatch):
    from api.services.documents import document_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    async with factory() as db:
        reserved = await tools.request_upload(
            collection_id="colA", filename="report.pdf", db=db, principal=MASTER
        )
        key = document_key("colA", reserved["document_id"], ".pdf")
        storage.receive(key, data=b"\x89PNG\r\n\x1a\n\x00\x00\x00\r")  # a PNG, not a PDF
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_upload(
                document_id=reserved["document_id"], db=db, principal=MASTER
            )
    assert exc.value.status_code == 415
    assert storage.object_head(key) is None  # the mismatched object was purged


async def test_request_upload_local_backend_is_400(seeded, monkeypatch):
    factory, _ = seeded

    class _NoPresign:
        def presigned_put(self, key: str) -> None:
            return None

    monkeypatch.setattr("api.services.documents.get_storage", lambda cfg, name=None: _NoPresign())
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.request_upload(
                collection_id="colA", filename="x.txt", db=db, principal=MASTER
            )
    assert exc.value.status_code == 400


async def test_request_upload_outside_scope_403(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colB", "read")])  # no write on colA
        with pytest.raises(HTTPException) as exc:
            await tools.request_upload(
                collection_id="colA", filename="x.txt", db=db, principal=USER
            )
    assert exc.value.status_code == 403


# ── Attach an original source file (second object under the same document) ─────


async def test_attach_original_roundtrip(seeded, monkeypatch):
    from api.services.documents import original_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    async with factory() as db:
        # docA is already an active document; attach the source PDF its parse came from.
        reserved = await tools.request_original_upload(
            document_id="docA", filename="report.pdf", db=db, principal=MASTER
        )
        assert reserved["upload_url"].startswith("https://minio.test/put/")
        # Requested but not yet PUT → not surfaced as present.
        listed = await tools.list_documents(collection_id="colA", db=db, principal=MASTER)
        docA = next(d for d in listed["documents"] if d["document_id"] == "docA")
        assert docA["has_original"] is False
        # Simulate the client PUT to the presigned URL, then confirm.
        storage.receive(original_key("colA", "docA", ".pdf"), data=_typed_bytes(".pdf", 456))
        confirmed = await tools.confirm_original_upload(
            document_id="docA", db=db, principal=MASTER
        )
        assert confirmed["status"] == "attached"
        assert confirmed["original_file_size"] == 456
        # Now present in the listing under its original filename.
        listed = await tools.list_documents(collection_id="colA", db=db, principal=MASTER)
        docA = next(d for d in listed["documents"] if d["document_id"] == "docA")
        assert docA["has_original"] is True
        assert docA["original_filename"] == "report.pdf"
        # Downloadable via the original flag (distinct from the parse's own bytes).
        dl = await tools.download_document(
            document_id="docA", original=True, db=db, principal=MASTER
        )
        assert dl["filename"] == "report.pdf"
        assert original_key("colA", "docA", ".pdf") in dl["url"]


async def test_download_original_without_attach_is_404(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.download_document(
                document_id="docA", original=True, db=db, principal=MASTER
            )
    assert exc.value.status_code == 404


async def test_confirm_original_without_request_is_409(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        # No request_original_upload → no original_file_type recorded → confirm 409s.
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_original_upload(document_id="docA", db=db, principal=MASTER)
    assert exc.value.status_code == 409


async def test_confirm_original_without_bytes_is_409(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        await tools.request_original_upload(
            document_id="docA", filename="report.pdf", db=db, principal=MASTER
        )
        # No client PUT → object_head returns None → confirm 409s.
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_original_upload(document_id="docA", db=db, principal=MASTER)
    assert exc.value.status_code == 409


async def test_attach_original_oversized_is_413_and_purged(seeded, monkeypatch):
    from api.models.config import AppConfig
    from api.services.documents import original_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    monkeypatch.setattr(
        "api.services.documents.get_app_config", lambda: AppConfig(max_file_size_mb=1)
    )
    async with factory() as db:
        await tools.request_original_upload(
            document_id="docA", filename="big.pdf", db=db, principal=MASTER
        )
        key = original_key("colA", "docA", ".pdf")
        storage.receive(key, size=2 * 1024 * 1024)  # over the 1 MiB cap
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_original_upload(document_id="docA", db=db, principal=MASTER)
    assert exc.value.status_code == 413
    assert storage.object_head(key) is None  # oversized object was purged


async def test_confirm_original_content_mismatch_is_415_and_purged(seeded, monkeypatch):
    from api.services.documents import original_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    async with factory() as db:
        await tools.request_original_upload(
            document_id="docA", filename="report.pdf", db=db, principal=MASTER
        )
        key = original_key("colA", "docA", ".pdf")
        storage.receive(key, data=b"\x89PNG\r\n\x1a\n\x00\x00\x00\r")  # a PNG, not a PDF
        with pytest.raises(HTTPException) as exc:
            await tools.confirm_original_upload(document_id="docA", db=db, principal=MASTER)
    assert exc.value.status_code == 415
    assert storage.object_head(key) is None  # the mismatched object was purged


async def test_request_original_outside_scope_403(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "read")])  # read, not write
        with pytest.raises(HTTPException) as exc:
            await tools.request_original_upload(
                document_id="docA", filename="report.pdf", db=db, principal=USER
            )
    assert exc.value.status_code == 403


async def test_request_original_local_backend_is_400(seeded, monkeypatch):
    factory, _ = seeded

    class _NoPresign:
        def presigned_put(self, key: str) -> None:
            return None

    monkeypatch.setattr("api.services.documents.get_storage", lambda cfg, name=None: _NoPresign())
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.request_original_upload(
                document_id="docA", filename="report.pdf", db=db, principal=MASTER
            )
    assert exc.value.status_code == 400


async def test_attach_original_accepts_non_parseable_type(seeded, monkeypatch):
    # The original is stored for download only — never parsed — so a non-ingestible source type
    # (e.g. .docx with docling off) is accepted, unlike the parse upload.
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        reserved = await tools.request_original_upload(
            document_id="docA", filename="source.docx", db=db, principal=MASTER
        )
    assert reserved["original_filename"] == "source.docx"
    assert reserved["upload_url"].startswith("https://minio.test/put/")


async def test_attach_original_invalid_extension_is_415(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await tools.request_original_upload(
                document_id="docA", filename="badext.h@ck", db=db, principal=MASTER
            )
    assert exc.value.status_code == 415


async def test_reattach_original_different_ext_deletes_previous(seeded, monkeypatch):
    from api.services.documents import original_key

    factory, _ = seeded
    storage = _use_presign_storage(monkeypatch)
    async with factory() as db:
        await tools.request_original_upload(
            document_id="docA", filename="src.pdf", db=db, principal=MASTER
        )
        pdf_key = original_key("colA", "docA", ".pdf")
        storage.receive(pdf_key, data=_typed_bytes(".pdf", 100))
        await tools.confirm_original_upload(document_id="docA", db=db, principal=MASTER)
        assert storage.object_head(pdf_key) == 100
        # Re-attach a different type → the previous .pdf object is purged (no orphan leak).
        reserved = await tools.request_original_upload(
            document_id="docA", filename="src.docx", db=db, principal=MASTER
        )
        assert storage.object_head(pdf_key) is None
        assert original_key("colA", "docA", ".docx") in reserved["upload_url"]
        # Back to not-present until the new one is confirmed.
        listed = await tools.list_documents(collection_id="colA", db=db, principal=MASTER)
        docA = next(d for d in listed["documents"] if d["document_id"] == "docA")
        assert docA["has_original"] is False


async def test_attach_original_on_deleting_doc_is_409(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        await db.execute(
            documents_t.update().where(documents_t.c.id == "docA").values(status="deleting")
        )
        with pytest.raises(HTTPException) as exc:
            await tools.request_original_upload(
                document_id="docA", filename="src.pdf", db=db, principal=MASTER
            )
    assert exc.value.status_code == 409


async def test_download_document_returns_presigned_url(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        out = await tools.download_document(document_id="docA", db=db, principal=MASTER)
    assert out["url"].startswith("https://minio.test/get/")
    assert out["filename"] == "docA.txt"


async def test_download_document_outside_scope_403(seeded, monkeypatch):
    factory, _ = seeded
    _use_presign_storage(monkeypatch)
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colB", "read")])  # docA is in colA
        with pytest.raises(HTTPException) as exc:
            await tools.download_document(document_id="docA", db=db, principal=USER)
    assert exc.value.status_code == 403


async def test_get_document_chunks_paged_ordered_by_index(seeded):
    factory, store = seeded
    # Three out-of-order chunks for docA; the paged mode returns them by chunk_index with a total.
    store._by["colA"] = [
        _result("a-1", 0.0, document_id="docA", chunk_index=1),
        _result("a-0", 0.0, document_id="docA", chunk_index=0),
        _result("a-2", 0.0, document_id="docA", chunk_index=2),
    ]
    async with factory() as db:
        out = await tools.get_document_chunks(
            document_id="docA", db=db, principal=MASTER, vector_store=store
        )
    assert out["count"] == 3
    assert out["total"] == 3
    assert out["has_more"] is False
    assert [c["chunk_id"] for c in out["chunks"]] == ["a-0", "a-1", "a-2"]


async def test_get_document_chunks_paginates(seeded):
    factory, store = seeded
    store._by["colA"] = [
        _result(f"a-{i}", 0.0, document_id="docA", chunk_index=i) for i in range(5)
    ]
    async with factory() as db:
        page1 = await tools.get_document_chunks(
            document_id="docA", limit=2, offset=0, db=db, principal=MASTER, vector_store=store
        )
        page3 = await tools.get_document_chunks(
            document_id="docA", limit=2, offset=4, db=db, principal=MASTER, vector_store=store
        )
    assert [c["chunk_id"] for c in page1["chunks"]] == ["a-0", "a-1"]
    assert page1["total"] == 5 and page1["has_more"] is True
    # Last page: one leftover chunk, no more after it.
    assert [c["chunk_id"] for c in page3["chunks"]] == ["a-4"]
    assert page3["has_more"] is False


async def test_get_document_chunks_by_ids(seeded):
    factory, store = seeded
    store._by["colA"] = [
        _result("a-0", 0.0, document_id="docA", chunk_index=0),
        _result("a-1", 0.0, document_id="docA", chunk_index=1),
        _result("a-2", 0.0, document_id="docA", chunk_index=2),
    ]
    async with factory() as db:
        out = await tools.get_document_chunks(
            document_id="docA", chunk_ids=["a-2", "a-0"],
            db=db, principal=MASTER, vector_store=store,
        )
    # Only the requested chunks, returned in chunk_index order (not request order).
    assert out["requested"] == 2
    assert [c["chunk_id"] for c in out["chunks"]] == ["a-0", "a-2"]


async def test_get_document_chunks_by_ids_ignores_other_documents(seeded):
    factory, store = seeded
    # docA and docA2 both live in colA; a chunk_id from docA2 must not leak through a docA request.
    store._by["colA"] = [
        _result("a-0", 0.0, document_id="docA", chunk_index=0),
        _result("other", 0.0, document_id="docA2", chunk_index=0),
    ]
    async with factory() as db:
        out = await tools.get_document_chunks(
            document_id="docA", chunk_ids=["a-0", "other"],
            db=db, principal=MASTER, vector_store=store,
        )
    assert [c["chunk_id"] for c in out["chunks"]] == ["a-0"]


async def test_get_document_chunks_outside_scope_403(seeded):
    factory, store = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colB", "read")])  # docA is in colA
        with pytest.raises(HTTPException) as exc:
            await tools.get_document_chunks(
                document_id="docA", db=db, principal=USER, vector_store=store
            )
    assert exc.value.status_code == 403


async def test_reprocess_document_reenqueues(seeded, monkeypatch):
    factory, _ = seeded
    monkeypatch.setattr(
        "api.services.documents.task_producer.enqueue_ingest", lambda *a, **k: "task-x"
    )
    async with factory() as db:
        out = await tools.reprocess_document(document_id="docA", db=db, principal=MASTER)
    assert out["document_id"] == "docA"
    assert out["status"] == "pending"


# ── Workspace & collection CRUD ───────────────────────────────────────────────


async def test_workspace_crud_roundtrip(seeded):
    factory, _ = seeded
    async with factory() as db:
        created = await tools.create_workspace(name="New WS", db=db, principal=MASTER)
        ws_id = created["id"]
        assert created["name"] == "New WS"
        updated = await tools.update_workspace(
            workspace_id=ws_id, name="Renamed", db=db, principal=MASTER
        )
        assert updated["name"] == "Renamed"
        deleted = await tools.delete_workspace(
            workspace_id=ws_id, db=db, principal=MASTER
        )
        assert deleted == {"workspace_id": ws_id, "status": "deleted"}


async def test_collection_crud_roundtrip(seeded):
    factory, _ = seeded
    async with factory() as db:
        created = await tools.create_collection(
            workspace_id="ws1", name="New Col", db=db, principal=MASTER
        )
        col_id = created["id"]
        updated = await tools.update_collection(
            workspace_id="ws1", collection_id=col_id, description="desc",
            db=db, principal=MASTER,
        )
        assert updated["description"] == "desc"
        deleted = await tools.delete_collection(
            workspace_id="ws1", collection_id=col_id, db=db, principal=MASTER
        )
        assert deleted["status"] == "deleted"


async def test_create_workspace_requires_capability(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "read")])  # scoped, no capability
        with pytest.raises(HTTPException) as exc:
            await tools.create_workspace(name="X", db=db, principal=USER)
    assert exc.value.status_code == 403


async def test_create_collection_requires_workspace_write(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "read")])  # read-only in ws1
        with pytest.raises(HTTPException) as exc:
            await tools.create_collection(workspace_id="ws1", name="X", db=db, principal=USER)
    assert exc.value.status_code == 403


async def test_update_collection_outside_scope_403(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colB", "write")])  # colA out of scope
        with pytest.raises(HTTPException) as exc:
            await tools.update_collection(
                workspace_id="ws1", collection_id="colA", name="X", db=db, principal=USER
            )
    assert exc.value.status_code == 403


# ── Tags (MCP) ────────────────────────────────────────────────────────────────


async def test_tag_tools_roundtrip(seeded):
    factory, _ = seeded
    async with factory() as db:
        created = await tools.create_tag(
            workspace_id="ws1", name="python", db=db, principal=MASTER
        )
        tag_id = created["id"]
        listed = await tools.list_tags(workspace_id="ws1", db=db, principal=MASTER)
        assert tag_id in {t["id"] for t in listed["tags"]}
        a1 = await tools.assign_tag(
            workspace_id="ws1", tag_id=tag_id, target="collection",
            collection_id="colA", db=db, principal=MASTER,
        )
        assert a1["status"] == "assigned"
        a2 = await tools.assign_tag(
            workspace_id="ws1", tag_id=tag_id, target="document",
            collection_id="colA", document_id="docA", db=db, principal=MASTER,
        )
        assert a2["status"] == "assigned"
        u = await tools.unassign_tag(
            workspace_id="ws1", tag_id=tag_id, target="collection",
            collection_id="colA", db=db, principal=MASTER,
        )
        assert u["status"] == "unassigned"
        renamed = await tools.update_tag(
            workspace_id="ws1", tag_id=tag_id, name="py", db=db, principal=MASTER
        )
        assert renamed["name"] == "py"
        deleted = await tools.delete_tag(
            workspace_id="ws1", tag_id=tag_id, db=db, principal=MASTER
        )
        assert deleted["status"] == "deleted"


async def test_tag_tools_require_capability(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "write")])  # no manage_tags
        with pytest.raises(HTTPException) as exc:
            await tools.create_tag(workspace_id="ws1", name="x", db=db, principal=USER)
    assert exc.value.status_code == 403


async def test_tag_assignment_requires_target_write(seeded):
    factory, _ = seeded
    async with factory() as db:
        created = await tools.create_tag(workspace_id="ws1", name="t", db=db, principal=MASTER)
        # manage_tags but only READ on colA → can't tag it.
        await _seed_user_with_grants(
            db, [("collection", "colA", "read"), ("capability", "manage_tags", "write")]
        )
        with pytest.raises(HTTPException) as exc:
            await tools.assign_tag(
                workspace_id="ws1", tag_id=created["id"], target="collection",
                collection_id="colA", db=db, principal=USER,
            )
    assert exc.value.status_code == 403


async def test_tag_assignment_missing_collection_id_422(seeded):
    factory, _ = seeded
    async with factory() as db:
        created = await tools.create_tag(workspace_id="ws1", name="t", db=db, principal=MASTER)
        with pytest.raises(HTTPException) as exc:
            await tools.assign_tag(
                workspace_id="ws1", tag_id=created["id"], target="collection",
                db=db, principal=MASTER,
            )
    assert exc.value.status_code == 422


async def test_tag_assignment_workspace_target_rejects_resource_id_422(seeded):
    # A resource id on a workspace target is rejected, so a caller who forgot target="collection"
    # doesn't silently tag the whole workspace instead of the collection they named.
    factory, _ = seeded
    async with factory() as db:
        created = await tools.create_tag(workspace_id="ws1", name="t", db=db, principal=MASTER)
        with pytest.raises(HTTPException) as exc:
            await tools.assign_tag(
                workspace_id="ws1", tag_id=created["id"], target="workspace",
                collection_id="colA", db=db, principal=MASTER,
            )
    assert exc.value.status_code == 422


# ── Ops: ingestion queue / stats / rate limit ────────────────────────────────


async def _seed_job(db, *, job_id, doc_id, col_id, status):
    await db.execute(
        insert(job_records_t).values(
            job_id=job_id, document_id=doc_id, collection_id=col_id,
            filename=f"{doc_id}.txt", file_type=".txt", status=status,
            created_at="t", updated_at="t",
        )
    )
    await db.commit()


async def test_list_ingestion_jobs_scoped_to_grants(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_job(db, job_id="j1", doc_id="docA", col_id="colA", status="done")
        await _seed_job(db, job_id="j2", doc_id="docB", col_id="colB", status="failed")
        # Master sees every collection's jobs.
        assert (await tools.list_ingestion_jobs(db=db, principal=MASTER))["total"] == 2
        # A user scoped to colA sees only colA's job.
        await _seed_user_with_grants(db, [("collection", "colA", "read")])
        scoped = await tools.list_ingestion_jobs(db=db, principal=USER)
    assert scoped["total"] == 1
    assert {j["job_id"] for j in scoped["items"]} == {"j1"}


async def test_list_ingestion_jobs_clamps_page_size(seeded):
    factory, _ = seeded
    async with factory() as db:
        # Out-of-range page args are clamped (not a validation error), like search_documents' top_k.
        over = await tools.list_ingestion_jobs(limit=999, offset=-5, db=db, principal=MASTER)
        assert over["limit"] == 200 and over["offset"] == 0
        under = await tools.list_ingestion_jobs(limit=0, db=db, principal=MASTER)
        assert under["limit"] == 1


async def test_get_ingestion_stats_counts_and_pause(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_job(db, job_id="j1", doc_id="docA", col_id="colA", status="done")
        await _seed_job(db, job_id="j2", doc_id="docA2", col_id="colA", status="pending")
        out = await tools.get_ingestion_stats(
            embedding_pause_seconds=42, db=db, principal=MASTER
        )
    assert out["counts"].get("done") == 1
    assert out["counts"].get("pending") == 1
    assert out["embedding_paused_seconds"] == 42


async def test_get_rate_limit_assembles_budget_and_pause():
    out = await tools.get_rate_limit(
        rate_limit={"limit_rpm": 60, "remaining": 12, "reset_seconds": 0.0},
        embedding_pause_seconds=15,
    )
    assert out["mcp"]["remaining"] == 12
    assert out["ingestion"]["embedding_paused_seconds"] == 15


# ── Per-user enforcement (grants) ─────────────────────────────────────────────


async def test_list_workspaces_filtered_to_readable(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "read")])
        out = await tools.list_workspaces(db=db, principal=USER)
    workspaces = out["workspaces"]
    assert len(workspaces) == 1
    assert {c["name"] for c in workspaces[0]["collections"]} == {"papers"}  # colB hidden


async def test_search_filters_to_readable_collections(seeded):
    factory, store = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "read")])
        out = await tools.search_documents(
            query="v", collection_ids=["colA", "colB"], top_k=10,
            db=db, principal=USER, embedder=_FakeEmbedder(), vector_store=store,
        )
    assert set(out["collection_stats"].keys()) == {"colA"}  # colB dropped, not searched


async def test_workspace_grant_covers_all_its_collections(seeded):
    factory, store = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("workspace", "ws1", "read")])
        out = await tools.search_documents(
            query="v", collection_ids=["colA", "colB"], top_k=10,
            db=db, principal=USER, embedder=_FakeEmbedder(), vector_store=store,
        )
    assert set(out["collection_stats"].keys()) == {"colA", "colB"}


async def test_search_outside_scope_raises_403(seeded):
    factory, store = seeded
    async with factory() as db:
        # Scoped to colB → searching colA (out of scope) leaves no readable collection → 403.
        await _seed_user_with_grants(db, [("collection", "colB", "read")])
        with pytest.raises(HTTPException) as exc:
            await tools.search_documents(
                query="v", collection_ids=["colA"],
                db=db, principal=USER, embedder=_FakeEmbedder(), vector_store=store,
            )
    assert exc.value.status_code == 403


async def test_list_documents_outside_scope_raises_403(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colB", "read")])  # scoped elsewhere
        with pytest.raises(HTTPException) as exc:
            await tools.list_documents(collection_id="colA", db=db, principal=USER)
    assert exc.value.status_code == 403


async def test_ingest_document_requires_master(seeded, tmp_path):
    """A user key — even with a write grant — cannot ingest by container-local path.

    Referencing an arbitrary server path is master-only; a scoped write grant must
    not become an arbitrary-file-read / cross-tenant-copy primitive.
    """
    factory, _ = seeded
    src = tmp_path / "n.txt"
    src.write_text("hi", encoding="utf-8")
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "write")])
        with pytest.raises(HTTPException) as exc:
            await tools.ingest_document(
                collection_id="colA", file_path=str(src), db=db, principal=USER
            )
    assert exc.value.status_code == 403


async def test_delete_with_read_grant_only_raises_403(seeded):
    factory, _ = seeded
    async with factory() as db:
        await _seed_user_with_grants(db, [("collection", "colA", "read")])
        with pytest.raises(HTTPException) as exc:
            await tools.delete_document(document_id="docA", db=db, principal=USER)
    assert exc.value.status_code == 403


# ── Auth + rate limiting (ASGI middleware) ────────────────────────────────────


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"x", b"y")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _resolve_secret(raw_key: str) -> Principal:
    """Fake resolver: accept the literal ``secret`` as the master key, else 401."""
    if raw_key == "secret":
        return Principal(is_master=True)
    raise HTTPException(401, "Invalid API key")


def _guarded(rpm: int) -> MCPAuthRateLimitMiddleware:
    return MCPAuthRateLimitMiddleware(
        _ok_app,
        resolve_principal=_resolve_secret,
        rate_limiter=TokenBucketRateLimiter(lambda: rpm),
    )


async def test_missing_key_is_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=_guarded(60)), base_url="http://mcp") as ac:
        r = await ac.get("/sse")
    assert r.status_code == 401


async def test_invalid_key_is_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=_guarded(60)), base_url="http://mcp") as ac:
        r = await ac.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_inactive_user_key_is_forbidden():
    """A resolver that raises 403 (the inactive-user path) surfaces as HTTP 403."""

    async def _resolve_inactive(_raw_key: str) -> Principal:
        raise HTTPException(403, "User is inactive")

    mw = MCPAuthRateLimitMiddleware(
        _ok_app, resolve_principal=_resolve_inactive, rate_limiter=TokenBucketRateLimiter(lambda: 60)
    )
    async with AsyncClient(transport=ASGITransport(app=mw), base_url="http://mcp") as ac:
        r = await ac.get("/sse", headers={"X-API-Key": "some-user-key"})
    assert r.status_code == 403


async def test_x_api_key_header_is_accepted():
    async with AsyncClient(transport=ASGITransport(app=_guarded(60)), base_url="http://mcp") as ac:
        r = await ac.get("/sse", headers={"X-API-Key": "secret"})
    assert r.status_code == 200


async def test_rate_limit_rejects_61st_request_with_429():
    app = _guarded(60)
    headers = {"Authorization": "Bearer secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://mcp") as ac:
        statuses = [(await ac.get("/sse", headers=headers)).status_code for _ in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429


async def test_per_user_override_throttles_below_the_global():
    """A user's configured rate_limit_rpm caps their key well below the global default.

    The cap is enforced by a post-auth token bucket sized to the user's limit, so it is
    exact: with global=60 and the user's limit=3, only 3 requests succeed before 429 —
    far below the 60 an un-overridden key would get.
    """

    async def _resolve_capped(_raw_key: str) -> Principal:
        return Principal(is_master=False, user_id="usr_capped", rate_limit_rpm=3)

    mw = MCPAuthRateLimitMiddleware(
        _ok_app,
        resolve_principal=_resolve_capped,
        rate_limiter=TokenBucketRateLimiter(lambda: 60),
    )
    headers = {"X-API-Key": "user-key"}
    async with AsyncClient(transport=ASGITransport(app=mw), base_url="http://mcp") as ac:
        statuses = [(await ac.get("/sse", headers=headers)).status_code for _ in range(10)]
    assert statuses[:3] == [200] * 3
    assert all(s == 429 for s in statuses[3:])


async def test_per_user_override_holds_under_concurrent_cold_start():
    """Regression: N simultaneous requests must not drain the global bucket before the
    per-user cap is installed. Enforcing the cap post-auth on a user-id bucket sized
    before its first check means exactly ``limit`` succeed, even on a cold start.
    """

    async def _resolve_capped(_raw_key: str) -> Principal:
        # Yield like the real DB-backed resolver, so every request clears the pre-auth
        # guard before the first one installs the override — the race being guarded.
        await asyncio.sleep(0)
        return Principal(is_master=False, user_id="usr_burst", rate_limit_rpm=3)

    mw = MCPAuthRateLimitMiddleware(
        _ok_app,
        resolve_principal=_resolve_capped,
        rate_limiter=TokenBucketRateLimiter(lambda: 60),
    )
    headers = {"X-API-Key": "user-key"}
    async with AsyncClient(transport=ASGITransport(app=mw), base_url="http://mcp") as ac:
        results = await asyncio.gather(*(ac.get("/sse", headers=headers) for _ in range(30)))
    assert sum(r.status_code == 200 for r in results) == 3  # the cap, not the global 60


def test_malformed_header_bytes_decode_without_error():
    # A high byte (0xE9) in the auth header is valid latin-1 but invalid UTF-8;
    # the extractor must not raise (which would surface a 500 instead of a 401).
    from api.services.mcp.middleware import _raw_key_from_scope

    scope = {"type": "http", "headers": [(b"authorization", b"Bearer \xe9key")]}
    assert _raw_key_from_scope(scope) == "\xe9key"


# ── Mount wiring ──────────────────────────────────────────────────────────────


def test_mount_app_skips_when_disabled():
    from fastapi import FastAPI

    from api.models.config import MCPConfig
    from api.services.mcp.server import mount_app

    app = FastAPI()
    mount_app(app, MCPConfig(enabled=False))
    assert "/mcp" not in {getattr(r, "path", "") for r in app.routes}


def test_mount_app_mounts_when_enabled():
    from fastapi import FastAPI

    from api.models.config import MCPConfig
    from api.services.mcp.server import mount_app

    app = FastAPI()
    mount_app(app, MCPConfig(enabled=True))
    assert "/mcp" in {getattr(r, "path", "") for r in app.routes}


def test_require_returns_value_or_raises_when_backend_missing():
    from api.services.mcp.server import _require

    assert _require("adapter", "Embedding") == "adapter"
    with pytest.raises(RuntimeError, match="Embedding backend not ready"):
        _require(None, "Embedding")
