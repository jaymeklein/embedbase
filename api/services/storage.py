"""Pluggable object storage for uploaded files (local disk / S3 / MinIO).

A small key-addressed abstraction over *where* a document's bytes live. A ``key``
is the same ``{collection_id}/{doc_id}{ext}`` path used on disk today.
:func:`get_storage` resolves a named backend from the config registry:

* ``local`` — files under ``settings.upload_dir``; always available, the default.
* ``s3``    — any S3-compatible bucket (AWS S3, self-hosted MinIO) via boto3.

Nothing wires this into the upload / worker / download paths yet — that's the
next PR. With the default ``local`` backend, ``boto3`` is never imported (the S3
backend imports it lazily), so a plain disk deployment pays nothing for it.
"""

from __future__ import annotations

import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from api.services.upload import stream_upload_with_size_guard
from api.settings import settings

if TYPE_CHECKING:
    from fastapi import UploadFile

    from api.models.config import S3BackendConfig, StorageConfig

_PRESIGN_EXPIRY = 3600  # seconds a presigned download URL stays valid


class Storage(ABC):
    """Key-addressed blob storage. Callers use only the methods below."""

    @abstractmethod
    async def put_upload(self, file: UploadFile, key: str, *, max_bytes: int | None) -> int:
        """Stream an upload to ``key`` (size-guarded); return bytes written."""

    @abstractmethod
    def put_path(self, src: Path, key: str) -> None:
        """Store an already-on-disk file at ``key``."""

    @abstractmethod
    def fetch_to_temp(self, key: str) -> Path:
        """Return a local path to ``key``'s bytes (a temp copy for remote backends)."""

    @abstractmethod
    def cleanup_temp(self, path: Path) -> None:
        """Release whatever :meth:`fetch_to_temp` returned (no-op for local)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key`` (idempotent)."""

    @abstractmethod
    def presigned_get(self, key: str, filename: str) -> str | None:
        """A short-lived download URL, or ``None`` for local (served via FileResponse)."""

    @abstractmethod
    def local_path(self, key: str) -> Path | None:
        """The on-disk path for a ``FileResponse``, or ``None`` for remote backends."""


class LocalStorage(Storage):
    """Files on a local/shared disk under ``base_dir`` (the default backend)."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _path(self, key: str) -> Path:
        return self._base / key

    async def put_upload(self, file: UploadFile, key: str, *, max_bytes: int | None) -> int:
        return await stream_upload_with_size_guard(file, self._path(key), max_bytes=max_bytes)

    def put_path(self, src: Path, key: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def fetch_to_temp(self, key: str) -> Path:
        return self._path(key)  # already on disk — hand back the real path

    def cleanup_temp(self, path: Path) -> None:
        pass  # fetch_to_temp returns the live file, never a throwaway copy

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def presigned_get(self, key: str, filename: str) -> str | None:
        return None  # local downloads use FileResponse, not a redirect

    def local_path(self, key: str) -> Path | None:
        return self._path(key)


class S3Storage(Storage):
    """Any S3-compatible bucket (AWS S3, MinIO) reached through boto3."""

    def __init__(self, cfg: S3BackendConfig) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = cfg.bucket
        self._region = cfg.region
        self._bucket_ready = False

        client_cfg = Config(s3={"addressing_style": "path"}) if cfg.use_path_style else None
        # Both keys empty → let boto3's default chain resolve them (e.g. an AWS
        # instance role). Require BOTH before passing static creds: a half-filled
        # pair (id set, secret blank) would send aws_secret_access_key="" and fail
        # every request rather than falling back to the chain.
        creds: dict[str, str] = {}
        if cfg.access_key_id and cfg.secret_access_key:
            creds["aws_access_key_id"] = cfg.access_key_id
            creds["aws_secret_access_key"] = cfg.secret_access_key

        self._s3 = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint_url,
            region_name=cfg.region,
            config=client_cfg,
            **creds,
        )
        # Presigned URLs are signed for the Host the *browser* reaches, which
        # differs from the in-cluster endpoint (minio:9000 vs localhost:9000).
        public = cfg.public_endpoint_url or cfg.endpoint_url
        if public == cfg.endpoint_url:
            self._presign_s3 = self._s3
        else:
            self._presign_s3 = boto3.client(
                "s3",
                endpoint_url=public,
                region_name=cfg.region,
                config=client_cfg,
                **creds,
            )

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        from botocore.exceptions import ClientError

        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("403", "Forbidden"):
                # Bucket exists but our creds can't head it (object-level-only IAM);
                # assume it's there and let the actual object op surface any problem.
                self._bucket_ready = True
                return
            if code not in ("404", "NoSuchBucket"):
                raise
            params: dict = {"Bucket": self._bucket}
            # us-east-1 must NOT send a LocationConstraint; every other region must.
            if self._region != "us-east-1":
                params["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
            try:
                self._s3.create_bucket(**params)
            except ClientError as create_exc:
                # A concurrent creator won the race — the bucket now exists, fine.
                if create_exc.response["Error"]["Code"] not in (
                    "BucketAlreadyOwnedByYou",
                    "BucketAlreadyExists",
                ):
                    raise
        self._bucket_ready = True

    async def put_upload(self, file: UploadFile, key: str, *, max_bytes: int | None) -> int:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(key).suffix) as tf:
            tmp = Path(tf.name)
        try:
            # Reuse the proven streaming size guard, then hand the temp file to S3.
            # The guard runs first, so an oversize upload never touches the bucket.
            written = await stream_upload_with_size_guard(file, tmp, max_bytes=max_bytes)
            self._ensure_bucket()
            self._s3.upload_file(str(tmp), self._bucket, key)
            return written
        finally:
            tmp.unlink(missing_ok=True)

    def put_path(self, src: Path, key: str) -> None:
        self._ensure_bucket()
        self._s3.upload_file(str(src), self._bucket, key)

    def fetch_to_temp(self, key: str) -> Path:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(key).suffix) as tf:
            tmp = Path(tf.name)
        try:
            self._s3.download_file(self._bucket, key, str(tmp))
        except BaseException:
            # A failed download can't be cleaned up by the caller (no path returned),
            # so drop the empty temp file here rather than orphan it.
            tmp.unlink(missing_ok=True)
            raise
        return tmp

    def cleanup_temp(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def presigned_get(self, key: str, filename: str) -> str | None:
        # Strip characters that would break the Content-Disposition header value
        # (quotes / CR / LF). A download hint, not a path — keep it simple.
        # ponytail: ASCII-safe strip; add RFC 5987 filename* if non-ASCII names matter.
        safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
        return self._presign_s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe}"',
            },
            ExpiresIn=_PRESIGN_EXPIRY,
        )

    def local_path(self, key: str) -> Path | None:
        return None  # remote object — served via presigned_get, not FileResponse


def get_storage(config: StorageConfig, name: str | None = None) -> Storage:
    """Resolve a named backend (or ``config.default``) from the registry.

    Takes the config explicitly — unlike a global-reading singleton, this works
    identically in the API and the worker (each holds its own AppConfig) and
    stays trivially testable.
    """
    # ponytail: builds a fresh backend per call — no per-name cache. Nothing wires
    # this yet (PR 1); memoize in the wiring PR if boto3 client construction shows
    # up in a profile (keyed so a config reload invalidates it).
    from api.models.config import S3BackendConfig

    backend_name = name or config.default
    try:
        backend = config.backends[backend_name]
    except KeyError:
        raise ValueError(
            f"unknown storage backend {backend_name!r}; configured: {sorted(config.backends)}"
        ) from None

    if isinstance(backend, S3BackendConfig):
        return S3Storage(backend)
    return LocalStorage(Path(settings.upload_dir))
