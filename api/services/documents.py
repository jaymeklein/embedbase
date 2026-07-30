"""Business logic for document ingestion and management.

Encapsulates all data-access and domain operations so that
api/routers/documents.py remains routing-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from fastapi import HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.parsers import DOCLING_EXTENSIONS, supported_extensions
from api.constants import MAX_RETENTION_DAYS, MIN_RETENTION_DAYS
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.dependencies import get_app_config
from api.models.config import ParserConfig, StorageConfig
from api.models.document import DocumentListQuery
from api.services import permissions
from api.services import tasks as task_producer
from api.services.auth import Principal
from api.services.document_filters import build_specs, latest_status_subquery
from api.services.filters import to_conditions
from api.services.storage import PRESIGN_EXPIRY, Storage, get_storage
from api.services.upload import (
    HEAD_SNIFF_BYTES,
    ContentTypeMismatchError,
    EmptyFileError,
    FileTooLargeError,
    resolve_max_bytes,
    validate_content,
)

logger = structlog.get_logger()

# Status marker for a document reserved by ``create_upload`` whose bytes have not yet been
# PUT to the presigned URL. Excluded from listings (which filter ``status IS NULL``) until
# ``confirm_upload`` flips it active; distinct from the ``deleting`` tombstone.
_AWAITING_UPLOAD = "awaiting_upload"

# get_checksum: the digest algorithm — one source of truth, used both to build the hasher
# (``hashlib.new``) and as the label returned to the caller, so they can never disagree — and the
# streaming read size, so a large object is hashed a block at a time rather than loaded whole.
_CHECKSUM_ALGORITHM = "sha256"
_CHECKSUM_READ_CHUNK = 1 << 20  # 1 MiB


def _storage_config() -> StorageConfig:
    """The active storage registry, or an all-local default when config is unset."""
    config = get_app_config()
    return config.storage if config else StorageConfig()


def _reject_unsupported(ext: str, parsers: ParserConfig | None) -> None:
    """Raise 415 when ``ext`` is not ingestible under the active parser config.

    Office formats (``.docx``/``.pptx``) require the docling backend, so when they are rejected
    only because docling is off, the message points at the switch that enables them — the upload
    fails here, at the API boundary, instead of later in the worker on a missing dependency.
    """
    if ext in supported_extensions(parsers):
        return
    # Reaching here for an office format means docling is not configured (else it would be
    # supported), so point at the switch that enables it.
    base = f"Unsupported file type: {ext!r}"
    if ext in DOCLING_EXTENSIONS:
        raise HTTPException(
            415,
            f"{base}. Office formats (.docx/.pptx) need the docling parser backend — "
            "set parsers.pdf_backend to 'docling' to enable them.",
        )
    raise HTTPException(415, base)


def document_key(col_id: str, doc_id: str, ext: str) -> str:
    """The storage key for a document's bytes — the single source of truth for the key
    layout, shared by the API upload/delete paths and the worker's resume/purge sweeps.
    Import it rather than re-encoding ``{col}/{doc}{ext}`` so the scheme lives in one place.
    """
    return f"{col_id}/{doc_id}{ext}"


def original_key(col_id: str, doc_id: str, ext: str) -> str:
    """The storage key for a document's *original source file* — the second object kept
    alongside the parse, shared by the attach/download/delete paths.

    The ``.orig`` marker keeps it distinct from :func:`document_key` even when the original
    shares the parse's extension (``{col}/{doc}.orig{ext}`` vs ``{col}/{doc}{ext}``). Like
    ``document_key`` it is the single source of truth — import it, never re-encode.
    """
    return f"{col_id}/{doc_id}.orig{ext}"


def _require_presigned_put(storage: Storage, key: str) -> str:
    """Presign a PUT for ``key``, or 400 — presigned upload needs an S3/MinIO backend.

    Shared by the parse upload (:func:`create_upload`) and the original-source attach
    (:func:`create_original_upload`); local-disk backends can't presign and upload via REST.
    """
    url = storage.presigned_put(key)
    if url is None:
        raise HTTPException(
            400,
            "Presigned upload requires an S3/MinIO storage backend; on local-disk storage "
            "upload the file through the REST endpoint instead.",
        )
    return url


def _verify_uploaded_object(storage: Storage, key: str, ext: str) -> int:
    """Confirm a presigned PUT landed, re-enforce the size cap + content type; return the size.

    The presigned PUT lands bytes straight in storage, bypassing the REST stream guard, so both the
    size cap and the declared type are re-checked here — the client can't reserve one type and PUT
    another (e.g. a ``.png`` under a ``.pdf`` reservation). 409 if no bytes were PUT yet;
    ``FileTooLargeError`` (413) if over the cap; ``ContentTypeMismatchError`` (415) if the bytes
    aren't the declared ``ext``. A rejected object is purged, not kept. Shared by
    :func:`confirm_upload` and :func:`confirm_original_upload`.

    Like the size cap, this is a **confirm-time** check: the presigned PUT URL stays valid for
    ``PRESIGN_EXPIRY``, so a caller could re-PUT mismatched bytes after confirming. That only ever
    yields a failed/garbage parse of the caller's *own* document (the worker parses strictly by the
    declared type) — hardening against accidental/lazy mislabeling, not a permanent guarantee.
    """
    size = storage.object_head(key)
    if size is None:
        raise HTTPException(409, "No uploaded bytes found — PUT the file to the upload_url first")
    config = get_app_config()
    limit = resolve_max_bytes(config.max_file_size_bytes if config else None)
    if size > limit:
        storage.delete(key)
        raise FileTooLargeError(limit)
    if size > 0:
        head = storage.read_head(key, HEAD_SNIFF_BYTES)
        if head is None:  # object vanished between the size check and the read
            raise HTTPException(
                409, "No uploaded bytes found — PUT the file to the upload_url first"
            )
    else:
        head = b""
    try:
        validate_content(ext, head)
    except ContentTypeMismatchError:
        storage.delete(key)  # purge the mismatched object, like the oversize path
        raise
    return size


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _expiry(retention_days: int | None) -> datetime | None:
    """Absolute purge time for a temporary upload, or ``None`` (permanent).

    ``retention_days`` keeps the document that many days from now; the worker sweep
    (``purge_expired_documents``) deletes it once ``expires_at`` passes. ``None`` means
    permanent. A value outside ``[MIN_RETENTION_DAYS, MAX_RETENTION_DAYS]`` is rejected at
    this single chokepoint, so every upload path (REST + MCP) validates identically. Naive
    UTC to match the stored column (see api/tables/documents.py).
    """
    if retention_days is None:
        return None
    if not MIN_RETENTION_DAYS <= retention_days <= MAX_RETENTION_DAYS:
        raise HTTPException(
            422,
            f"retention_days must be between {MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS}",
        )
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(days=retention_days)


async def resolve_collection(
    db: AsyncSession, col_id: str, ws_id: str | None = None
) -> str:
    """Return the workspace id for ``col_id``, validating ``ws_id`` if given."""
    row = (
        await db.execute(
            select(col_t.c.id, col_t.c.workspace_id).where(col_t.c.id == col_id)
        )
    ).fetchone()
    if not row or (ws_id is not None and row.workspace_id != ws_id):
        raise HTTPException(404, f"Collection {col_id!r} not found")
    return row.workspace_id


async def _persist_and_enqueue(
    db: AsyncSession,
    *,
    col_id: str,
    doc_id: str,
    job_id: str,
    filename: str,
    ext: str,
    size: int,
    file_path: str,
    storage_backend: str,
    expires_at: datetime | None = None,
) -> dict:
    """Insert the document + job rows and enqueue the ingest task.

    Shared by the HTTP upload path (:func:`ingest`) and the MCP local-path path
    (:func:`ingest_local_path`). ``file_path`` is the storage key the worker
    resolves via the recorded ``storage_backend``. ``expires_at`` (NULL = permanent)
    stamps a temporary document for the worker purge sweep. Returns a dict suitable
    for a 202 response body.
    """
    now = _now()
    await db.execute(
        insert(doc_t).values(
            id=doc_id, collection_id=col_id, filename=filename, file_type=ext,
            file_size=size, chunk_count=None, storage_backend=storage_backend,
            expires_at=expires_at, created_at=now, updated_at=now,
        )
    )
    await _create_pending_job_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=filename, ext=ext, file_path=file_path,
    )
    return {
        "job_id": job_id, "document_id": doc_id, "collection_id": col_id,
        "filename": filename, "file_type": ext, "file_size": size, "status": "pending",
    }


async def _create_pending_job_and_enqueue(
    db: AsyncSession,
    *,
    col_id: str,
    doc_id: str,
    job_id: str,
    filename: str,
    ext: str,
    file_path: str,
) -> None:
    """Insert a ``pending`` job row for ``doc_id`` and dispatch the ingest task, recording the
    celery task id back onto the row. Shared by the initial upload (:func:`_persist_and_enqueue`)
    and the reprocess-after-failure path (:func:`reprocess_document`). Commits the session, so any
    caller's still-uncommitted document insert lands with it.
    """
    now = _now()
    await db.execute(
        insert(job_t).values(
            job_id=job_id, document_id=doc_id, collection_id=col_id, filename=filename,
            file_type=ext, status="pending", created_at=now, updated_at=now,
        )
    )
    await db.commit()
    task_id = task_producer.enqueue_ingest(job_id, file_path, col_id, doc_id, ext)
    if task_id:
        await db.execute(
            update(job_t).where(job_t.c.job_id == job_id).values(celery_task_id=task_id)
        )
        await db.commit()


async def reprocess_document(db: AsyncSession, col_id: str, doc_id: str) -> dict:
    """Re-enqueue a document's ingestion — the manual retry for a failed (or otherwise stuck) file.

    A fresh ``pending`` job row is created (the prior failed attempt stays in the queue history) and
    an ingest task dispatched; the resume-aware pipeline re-embeds from where a prior run stopped, or
    from scratch. The document's stored bytes are reused — nothing is re-uploaded — so a file whose
    bytes are genuinely gone simply fails again with a clear error rather than being lost.
    """
    row = (
        await db.execute(
            select(doc_t.c.filename, doc_t.c.file_type, doc_t.c.status).where(
                doc_t.c.id == doc_id, doc_t.c.collection_id == col_id
            )
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if row.status == "deleting":
        raise HTTPException(409, "Document is being deleted; cannot reprocess")
    if row.status == _AWAITING_UPLOAD:
        raise HTTPException(409, "Document upload not confirmed yet; cannot reprocess")
    # Don't pile a duplicate onto a document that's already queued or running — that attempt will
    # finish, or the beat sweep resumes it. Minting a second job_id here would defeat _claim_job's
    # dedup (it keys on job_id) and, under a multi-process worker, embed the document twice. Return
    # the in-flight job instead (idempotent against a double-click or a stale "failed" button).
    latest = (
        await db.execute(
            select(job_t.c.job_id, job_t.c.status)
            .where(job_t.c.document_id == doc_id)
            .order_by(job_t.c.created_at.desc())
            .limit(1)
        )
    ).fetchone()
    if latest is not None and latest.status in ("pending", "processing", "rate_limited"):
        return {
            "job_id": latest.job_id, "document_id": doc_id, "collection_id": col_id,
            "filename": row.filename, "file_type": row.file_type, "status": latest.status,
        }
    job_id = f"job_{uuid4().hex[:12]}"
    await _create_pending_job_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=row.filename, ext=row.file_type,
        file_path=document_key(col_id, doc_id, row.file_type),
    )
    return {
        "job_id": job_id, "document_id": doc_id, "collection_id": col_id,
        "filename": row.filename, "file_type": row.file_type, "status": "pending",
    }


async def ingest(
    db: AsyncSession, col_id: str, file: UploadFile, principal: Principal,
    *, retention_days: int | None = None,
) -> dict:
    """Validate, stream, record, and enqueue an uploaded document for ingestion.

    ``retention_days`` (1-30) stamps the document with an ``expires_at`` so the worker
    sweep auto-purges it after that many days; ``None`` means permanent. An out-of-range
    value is rejected (422) before any bytes are streamed.
    """
    await permissions.authorize_collection(db, principal, col_id, "write")
    # Validate + resolve the purge time up front, so a bad retention_days 422s before an
    # oversize/slow upload streams any bytes into storage.
    expires_at = _expiry(retention_days)

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    config = get_app_config()
    _reject_unsupported(ext, config.parsers if config else None)

    doc_id = f"doc_{uuid4().hex[:12]}"
    job_id = f"job_{uuid4().hex[:12]}"
    max_bytes = config.max_file_size_bytes if config else None
    storage_cfg = _storage_config()
    key = document_key(col_id, doc_id, ext)
    # put_upload streams through the same size guard before any bytes reach the
    # backend, so an oversize upload never lands remotely.
    size = await get_storage(storage_cfg).put_upload(file, key, max_bytes=max_bytes)
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=filename, ext=ext, size=size, file_path=key,
        storage_backend=storage_cfg.default,
        expires_at=expires_at,
    )


async def ingest_local_path(
    db: AsyncSession, col_id: str, file_path: str, principal: Principal,
    *, retention_days: int | None = None,
) -> dict:
    """Record + enqueue a container-local file for ingestion (MCP ingest tool).

    Unlike :func:`ingest`, the bytes are already on disk at ``file_path`` (a path
    the MCP client can see inside the container), so nothing is streamed.
    ``retention_days`` behaves as in :func:`ingest` (1-30 stamps ``expires_at``; ``None``
    is permanent).

    **Master-only.** Referencing an arbitrary container-local path can copy any file
    the server can reach (another tenant's document store, server files) into the
    target collection, so this operator/bootstrap capability requires the master key.
    Scoped users add documents by uploading bytes through :func:`ingest`, never by
    server-side path.

    Raises:
        HTTPException: 403 if the principal is not the master key, 415 for an
            unsupported extension, 422 for an out-of-range ``retention_days``, 404 if
            ``file_path`` does not exist, or 422 if the file is empty.
    """
    if not principal.is_master:
        raise HTTPException(403, "Ingesting a container-local file path requires the master key")

    expires_at = _expiry(retention_days)  # validate 1-30 before touching storage
    path = Path(file_path)
    ext = path.suffix.lower()
    config = get_app_config()
    _reject_unsupported(ext, config.parsers if config else None)
    if not path.is_file():
        raise HTTPException(404, f"File not found: {file_path!r}")
    if path.stat().st_size == 0:
        raise EmptyFileError()

    doc_id = f"doc_{uuid4().hex[:12]}"
    job_id = f"job_{uuid4().hex[:12]}"
    storage_cfg = _storage_config()
    key = document_key(col_id, doc_id, ext)
    # Copy the on-disk file into the active backend so the worker reads it the
    # same way as an HTTP upload (local: copy under upload_dir; s3: upload).
    get_storage(storage_cfg).put_path(path, key)
    return await _persist_and_enqueue(
        db, col_id=col_id, doc_id=doc_id, job_id=job_id,
        filename=path.name, ext=ext, size=path.stat().st_size, file_path=key,
        storage_backend=storage_cfg.default,
        expires_at=expires_at,
    )


async def create_upload(
    db: AsyncSession, col_id: str, filename: str, principal: Principal,
    *, retention_days: int | None = None,
) -> dict:
    """Reserve a document and return a **presigned PUT URL** for a direct-to-storage upload.

    Step one of the two-step MCP upload (there is no shared filesystem over the stateless MCP
    transport, so bytes never ride the tool call): the caller ``PUT``s the file to ``upload_url``,
    then calls :func:`confirm_upload` to verify the object landed and enqueue ingestion. The
    document row is inserted now with status ``awaiting_upload`` (hidden from listings) so its id
    is durable across the two calls; ``retention_days`` (1-30) stamps ``expires_at`` up front.

    Requires ``write`` on ``col_id``. Only backends that can presign (S3/MinIO) support this — on
    local-disk storage it raises 400 and the caller uploads via the REST endpoint instead.
    """
    await permissions.authorize_collection(db, principal, col_id, "write")
    expires_at = _expiry(retention_days)  # validate 1-30 before reserving anything
    ext = os.path.splitext(filename or "upload")[1].lower()
    config = get_app_config()
    _reject_unsupported(ext, config.parsers if config else None)

    doc_id = f"doc_{uuid4().hex[:12]}"
    storage_cfg = _storage_config()
    key = document_key(col_id, doc_id, ext)
    upload_url = _require_presigned_put(get_storage(storage_cfg), key)
    now = _now()
    await db.execute(
        insert(doc_t).values(
            id=doc_id, collection_id=col_id, filename=filename or "upload", file_type=ext,
            file_size=None, chunk_count=None, storage_backend=storage_cfg.default,
            status=_AWAITING_UPLOAD, expires_at=expires_at, created_at=now, updated_at=now,
        )
    )
    await db.commit()
    return {
        "document_id": doc_id, "collection_id": col_id, "filename": filename or "upload",
        "upload_url": upload_url, "method": "PUT", "url_expires_in": PRESIGN_EXPIRY,
        "status": _AWAITING_UPLOAD,
    }


async def confirm_upload(db: AsyncSession, doc_id: str, principal: Principal) -> dict:
    """Finalize a presigned upload: verify the bytes landed, then enqueue ingestion.

    Step two of the two-step MCP upload. Requires ``write`` on the document. The document must be
    in ``awaiting_upload`` (else 409) and its object must exist in storage (else 409 — the caller
    hasn't ``PUT`` the file yet). Records the real object size, flips the row active, and enqueues
    a fresh ingest job via the shared pipeline. Idempotency is via the ``awaiting_upload`` guard —
    a second call after activation gets a 409.
    """
    await permissions.authorize_document(db, principal, doc_id, "write")
    row = (
        await db.execute(
            select(
                doc_t.c.collection_id, doc_t.c.filename, doc_t.c.file_type, doc_t.c.status,
                doc_t.c.storage_backend,
            ).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if row.status != _AWAITING_UPLOAD:
        raise HTTPException(409, "Document is not awaiting an upload (already confirmed?)")

    key = document_key(row.collection_id, doc_id, row.file_type)
    storage = get_storage(_storage_config(), row.storage_backend or "local")
    size = _verify_uploaded_object(storage, key, row.file_type)

    # Status-guarded flip is the idempotency point: a concurrent second confirm matches 0 rows
    # (the first already flipped it active) and must NOT go on to enqueue a duplicate ingest job.
    # A blocked loser waits on the winner's row lock, then re-evaluates the WHERE → 0 rows. The
    # flip + job insert commit together in _create_pending_job_and_enqueue (atomic, so a crash
    # can't leave the doc active with no job).
    result: Any = await db.execute(
        update(doc_t)
        .where(doc_t.c.id == doc_id, doc_t.c.status == _AWAITING_UPLOAD)
        .values(file_size=size, status=None, updated_at=_now())
    )
    if result.rowcount == 0:
        raise HTTPException(409, "Document is not awaiting an upload (already confirmed?)")
    job_id = f"job_{uuid4().hex[:12]}"
    await _create_pending_job_and_enqueue(
        db, col_id=row.collection_id, doc_id=doc_id, job_id=job_id,
        filename=row.filename, ext=row.file_type, file_path=key,
    )
    return {
        "job_id": job_id, "document_id": doc_id, "collection_id": row.collection_id,
        "filename": row.filename, "file_type": row.file_type, "file_size": size,
        "status": "pending",
    }


async def create_original_upload(
    db: AsyncSession, doc_id: str, filename: str, principal: Principal
) -> dict:
    """Reserve a presigned PUT URL to attach an *original source file* to ``doc_id``.

    Step one of the two-step original attach (mirrors :func:`create_upload`): the caller ``PUT``s
    the original bytes to ``upload_url``, then calls :func:`confirm_original_upload`. The original
    is stored as a *second* object under the same document (:func:`original_key`) and is **never
    embedded** — it's a downloadable copy of the source the parse was derived from. Requires
    ``write`` on the document, which must be active (not ``awaiting_upload`` or ``deleting``). Only
    backends that can presign (S3/MinIO) support this; local-disk raises 400.

    ``original_file_type`` is recorded now so an abandoned upload's object is still cleaned up when
    the document is deleted; ``original_file_size`` stays NULL until :func:`confirm_original_upload`
    so the original counts as present only once its bytes have landed.
    """
    await permissions.authorize_document(db, principal, doc_id, "write")
    row = (
        await db.execute(
            select(
                doc_t.c.collection_id, doc_t.c.status,
                doc_t.c.storage_backend, doc_t.c.original_file_type,
            ).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if row.status == _AWAITING_UPLOAD:
        raise HTTPException(409, "Document is still awaiting its own upload — confirm that first")
    if row.status == "deleting":
        raise HTTPException(409, "Document is being deleted")

    # The original is stored for download only — never parsed — so it is NOT gated on parser
    # support (keep a .docx source even with the docling backend off). Require only a simple
    # extension so the storage key stays clean; splitext never yields a path separator, so the
    # caller-supplied filename can't inject one.
    ext = os.path.splitext(filename or "upload")[1].lower()
    if not ext or not ext[1:].isalnum():
        raise HTTPException(415, "The original file needs a simple extension, e.g. .pdf or .docx")

    storage = get_storage(_storage_config(), row.storage_backend or "local")
    key = original_key(row.collection_id, doc_id, ext)
    upload_url = _require_presigned_put(storage, key)
    # Re-attaching a *different* type would orphan the previous object (cleanup only knows the
    # current type), so remove it now — best-effort, mirroring the worker's storage cleanup.
    if row.original_file_type and row.original_file_type != ext:
        try:
            storage.delete(original_key(row.collection_id, doc_id, row.original_file_type))
        except Exception as exc:  # best-effort: a leaked object beats a failed re-attach
            logger.warning("previous original delete skipped", document_id=doc_id, error=str(exc))
    await db.execute(
        update(doc_t)
        .where(doc_t.c.id == doc_id)
        .values(
            original_filename=filename or "upload",
            original_file_type=ext,
            original_file_size=None,
            updated_at=_now(),
        )
    )
    await db.commit()
    return {
        "document_id": doc_id, "original_filename": filename or "upload",
        "upload_url": upload_url, "method": "PUT", "url_expires_in": PRESIGN_EXPIRY,
    }


async def confirm_original_upload(db: AsyncSession, doc_id: str, principal: Principal) -> dict:
    """Finalize an original-source-file attach: verify the bytes landed, record their size.

    Step two of the two-step original attach (mirrors :func:`confirm_upload`). Requires ``write``
    on the document. :func:`create_original_upload` must have run first (else 409 — no
    ``original_file_type`` recorded), and the original object must exist in storage (else 409 — the
    caller hasn't ``PUT`` it yet). Re-enforces the size cap (the presigned PUT bypasses the REST
    stream guard) and stamps ``original_file_size``, which flips the original present.

    The stamped size is confirm-time: unlike the parse (which the worker re-checks before ingest),
    the original is never re-fetched server-side, so a writer who re-``PUT``s to the still-valid URL
    after confirming can leave the recorded size stale — an accepted limit of the presigned model
    (the caller already holds ``write`` and the object is only ever served via a presigned GET).
    """
    await permissions.authorize_document(db, principal, doc_id, "write")
    row = (
        await db.execute(
            select(
                doc_t.c.collection_id, doc_t.c.original_filename,
                doc_t.c.original_file_type, doc_t.c.storage_backend,
            ).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if not row.original_file_type:
        raise HTTPException(
            409, "No original upload in progress — call request_original_upload first"
        )

    key = original_key(row.collection_id, doc_id, row.original_file_type)
    storage = get_storage(_storage_config(), row.storage_backend or "local")
    size = _verify_uploaded_object(storage, key, row.original_file_type)
    await db.execute(
        update(doc_t)
        .where(doc_t.c.id == doc_id)
        .values(original_file_size=size, updated_at=_now())
    )
    await db.commit()
    return {
        "document_id": doc_id, "original_filename": row.original_filename,
        "original_file_size": size, "status": "attached",
    }


async def list_documents(db: AsyncSession, col_id: str, query: DocumentListQuery) -> dict:
    """Return one filtered, paginated page of active documents in ``col_id``.

    Each document appears once, carrying its *latest* job ``status`` (a scalar-subquery pick of
    the most recent ``job_records`` row — a document accrues several as it re-ingests/retries),
    its tags, and an ``indexed`` flag (true once chunks are stored). Newest-first. Pagination and
    every (optional, AND-combined) filter come from ``query`` — see :class:`DocumentListQuery`.

    Returns:
        ``{"items": [...], "total": N, "limit": L, "offset": O}`` — ``total`` is the full match
        count (for the pager); ``items`` is the requested page, each a mapping with ``status``,
        ``tags``, and ``indexed``.
    """
    from api.services.tags import attach_tags

    latest_status = latest_status_subquery()

    # Base scope (collection + not soft-deleted), then each active filter as a spec. A new filter
    # is a new FilterSpec in api/services/document_filters.py, not another branch here.
    conds = [
        doc_t.c.collection_id == col_id,
        doc_t.c.status.is_(None),
        *await to_conditions(build_specs(query), db),
    ]
    where = and_(*conds)

    total = (await db.execute(select(func.count()).select_from(doc_t).where(where))).scalar_one()
    rows = (
        await db.execute(
            select(
                doc_t.c.id.label("document_id"),
                doc_t.c.filename,
                doc_t.c.file_type,
                doc_t.c.file_size,
                doc_t.c.chunk_count,
                doc_t.c.embedding_model,
                doc_t.c.storage_backend,
                doc_t.c.original_filename,
                doc_t.c.original_file_size,
                doc_t.c.created_at,
                doc_t.c.updated_at,
                latest_status.label("status"),
            )
            .where(where)
            # id as a stable tiebreaker so rows sharing a created_at can't shuffle across pages.
            .order_by(doc_t.c.created_at.desc(), doc_t.c.id.desc())
            .limit(query.limit)
            .offset(query.offset)
        )
    ).fetchall()
    items = [dict(r._mapping) for r in rows]
    items = await attach_tags("document", items, "document_id", db)
    for row in items:
        # FTS-indexed once chunks are stored; chunk_count is set when ingestion finishes.
        row["indexed"] = bool(row.get("chunk_count"))
        # An original source file counts as attached only once its size is stamped at confirm —
        # a requested-but-not-yet-PUT original (filename set, size NULL) is not surfaced.
        row["has_original"] = row.get("original_file_size") is not None
    return {"items": items, "total": total, "limit": query.limit, "offset": query.offset}


async def get_document_status(
    db: AsyncSession, col_id: str, doc_id: str
) -> dict:
    """Return current status for ``doc_id``, including soft-delete state."""
    doc_row = (
        await db.execute(
            select(doc_t.c.status).where(
                doc_t.c.id == doc_id, doc_t.c.collection_id == col_id
            )
        )
    ).fetchone()
    # A soft-status row (deleting tombstone, or awaiting_upload before confirm) has no job yet —
    # report the lifecycle state directly rather than 404ing on the missing job.
    if doc_row and doc_row.status in ("deleting", _AWAITING_UPLOAD):
        return {"status": doc_row.status, "document_id": doc_id}
    row = (
        await db.execute(
            select(job_t)
            .where(job_t.c.document_id == doc_id, job_t.c.collection_id == col_id)
            .order_by(job_t.c.created_at.desc())
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"No job found for document {doc_id!r}")
    return dict(row._mapping)


async def delete_document(db: AsyncSession, col_id: str, doc_id: str) -> None:
    """Soft-delete a document and enqueue async vector / BM25 / row cleanup.

    Marks the document row as ``status='deleting'`` instead of removing it so
    the worker has a durable tombstone to retry against if the first cleanup
    attempt fails. The worker hard-deletes the row after all stores are clean.
    An ``awaiting_upload`` reservation is also deletable here, so a caller can cancel a
    presigned upload it never completed.
    """
    result: Any = await db.execute(
        update(doc_t)
        .where(
            doc_t.c.id == doc_id,
            doc_t.c.collection_id == col_id,
            or_(doc_t.c.status.is_(None), doc_t.c.status == _AWAITING_UPLOAD),
        )
        .values(status="deleting", updated_at=_now())
    )
    if result.rowcount == 0:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    await db.execute(delete(job_t).where(job_t.c.document_id == doc_id))
    await db.commit()
    try:
        task_id = task_producer.enqueue_delete(doc_id, col_id)
        if task_id:
            logger.info("delete task enqueued", document_id=doc_id, celery_task_id=task_id)
    except Exception:
        await db.execute(
            update(doc_t).where(doc_t.c.id == doc_id).values(status=None, updated_at=_now())
        )
        await db.commit()
        raise HTTPException(503, "Cleanup queue unavailable, please retry") from None


async def resolve_document_download(
    db: AsyncSession, doc_id: str, principal: Principal, *, original: bool = False
) -> Response:
    """Build the download Response for a document's bytes.

    Resolves the backend that holds the document (``storage_backend``; NULL =
    legacy/local) and returns the right Response for it: a 302 redirect to a
    short-lived presigned URL for S3-backed documents, or an inline
    ``FileResponse`` for local ones. Building the Response here (rather than in
    the router) keeps the router routing-only.

    Args:
        db: Active async database session.
        doc_id: Document to open.
        principal: Caller; must be able to access the owning collection.
        original: Serve the attached *original source file* instead of the parse
            (404 if none is attached).

    Returns:
        A ``RedirectResponse`` (S3) or ``FileResponse`` (local) for the bytes.

    Raises:
        HTTPException: 404 if the document or its file is gone, 403 if the
            principal cannot access the owning collection.
    """
    storage, key, filename = await _resolve_storage_and_key(db, doc_id, principal, original=original)
    url = storage.presigned_get(key, filename)
    if url is not None:
        return RedirectResponse(url, status_code=302)

    path = storage.local_path(key)
    if path is None or not path.is_file():
        raise HTTPException(404, "Original file is no longer available")
    return FileResponse(path, filename=filename, content_disposition_type="inline")


async def _resolve_storage_and_key(
    db: AsyncSession, doc_id: str, principal: Principal, *, original: bool = False
) -> tuple[Storage, str, str]:
    """Authorize read on ``doc_id`` then resolve its ``(storage, key, filename)``.

    Shared by the REST download (:func:`resolve_document_download`, → ``Response``) and the MCP
    download (:func:`resolve_download_url`, → presigned-URL dict). Authorizes *before* fetching so
    an unpermitted caller can't tell "forbidden" (403) from "nonexistent" (404) — both are 403.

    ``original=True`` resolves the *original source file* attached to the document instead of the
    parse — 404 if none is attached (``original_file_size`` still NULL).
    """
    await permissions.authorize_document(db, principal, doc_id, "read")
    row = (
        await db.execute(
            select(
                doc_t.c.collection_id, doc_t.c.filename, doc_t.c.file_type,
                doc_t.c.storage_backend, doc_t.c.original_filename,
                doc_t.c.original_file_type, doc_t.c.original_file_size,
            ).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    # NULL storage_backend = ingested before the column existed → bytes on local disk.
    storage = get_storage(_storage_config(), row.storage_backend or "local")
    if original:
        if row.original_file_size is None:
            raise HTTPException(404, "No original source file is attached to this document")
        return (
            storage,
            original_key(row.collection_id, doc_id, row.original_file_type),
            row.original_filename,
        )
    return storage, document_key(row.collection_id, doc_id, row.file_type), row.filename


async def resolve_download_url(
    db: AsyncSession, doc_id: str, principal: Principal, *, original: bool = False
) -> dict:
    """Return a presigned **GET** URL for a document's bytes (the MCP download tool).

    Requires ``read`` on the document. For an S3/MinIO backend returns ``{document_id, filename,
    url, url_expires_in}``; a local-disk backend can't presign, so it returns a ``notice`` pointing
    at the REST byte endpoint instead (``url`` is ``None``). ``original=True`` targets the attached
    *original source file* instead of the parse (404 if none is attached).
    """
    storage, key, filename = await _resolve_storage_and_key(db, doc_id, principal, original=original)
    url = storage.presigned_get(key, filename)
    if url is not None:
        return {
            "document_id": doc_id, "filename": filename,
            "url": url, "url_expires_in": PRESIGN_EXPIRY,
        }
    if storage.object_head(key) is None:
        raise HTTPException(404, "File is no longer available")
    raw_path = f"GET /documents/{doc_id}/raw" + ("?original=1" if original else "")
    return {
        "document_id": doc_id, "filename": filename, "url": None,
        "notice": (
            "This document is on local-disk storage, which can't presign. Fetch its bytes from "
            f"the REST endpoint {raw_path}."
        ),
    }


def _hash_object(storage: Storage, key: str) -> tuple[str, int] | None:
    """Stream ``key``'s bytes through the checksum hash; return ``(hex digest, byte count)``.

    Returns ``None`` when the object does not exist (the caller turns that into a 404). Fetches the
    object and reads it a block at a time, so a large file is never loaded into memory whole; the
    digest and the byte count come from the *same* single pass, so the reported size is exactly the
    bytes that were hashed. Blocking (a remote backend downloads a temp copy first), so call it via
    :func:`asyncio.to_thread`; the temp copy is always released, even if the read raises.
    """
    if storage.object_head(key) is None:
        return None
    path = storage.fetch_to_temp(key)
    try:
        digest = hashlib.new(_CHECKSUM_ALGORITHM)
        size = 0
        with path.open("rb") as fh:
            while chunk := fh.read(_CHECKSUM_READ_CHUNK):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size
    finally:
        storage.cleanup_temp(path)


async def compute_checksum(
    db: AsyncSession, doc_id: str, principal: Principal, *, original: bool = False
) -> dict:
    """Compute a fresh checksum over a document's stored bytes (the MCP ``get_checksum`` tool).

    Streams the object straight from storage through the hash on **every call** — nothing is
    cached — so the returned digest always reflects the bytes exactly as they are stored right now.
    A caller compares it against the hash of their local copy (e.g. ``sha256sum``) to prove the
    file was stored intact and hasn't drifted. ``ingested_at`` (the document's ingestion timestamp)
    lets them also tell whether the stored copy predates a newer local file.

    Requires ``read`` on the document (authorized before any bytes are touched). ``original=True``
    hashes the attached *original source file* instead of the parse (404 if none is attached).

    Raises:
        HTTPException: 403 if the caller can't read the document, 404 if the document is missing,
            no original is attached, or its bytes are unavailable (upload never completed / purged).
    """
    storage, key, filename = await _resolve_storage_and_key(db, doc_id, principal, original=original)
    result = await asyncio.to_thread(_hash_object, storage, key)
    if result is None:
        raise HTTPException(404, "The document's stored bytes are not available")
    checksum, size = result
    # _resolve_storage_and_key already confirmed the row exists (else it 404s), so scalar_one holds.
    ingested_at = (
        await db.execute(select(doc_t.c.created_at).where(doc_t.c.id == doc_id))
    ).scalar_one()
    return {
        "document_id": doc_id,
        "filename": filename,
        "original": original,
        "algorithm": _CHECKSUM_ALGORITHM,
        "checksum": checksum,
        "file_size": size,
        "ingested_at": ingested_at,
    }


async def resolve_document_collection(db: AsyncSession, doc_id: str) -> str:
    """Return the collection_id that owns ``doc_id``, raising 404 if absent."""
    row = (
        await db.execute(
            select(doc_t.c.collection_id).where(doc_t.c.id == doc_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    return row.collection_id
