"""Unit tests for the pluggable storage registry (local disk + S3/MinIO).

The S3 backend is exercised entirely against ``moto`` (in-memory S3), so these
run with no container, network, or credentials — same idea as the in-memory
SQLite/fakeredis fixtures elsewhere.
"""

import io
from urllib.parse import urlparse

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException
from moto import mock_aws

from api.models.config import LocalBackendConfig, S3BackendConfig, StorageConfig
from api.services.storage import LocalStorage, S3Storage, get_storage


class FakeUpload:
    """Minimal stand-in for starlette's UploadFile (``.size`` + async ``.read``)."""

    def __init__(self, data: bytes, size: int | None = None):
        self._buf = io.BytesIO(data)
        self.size = size

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def _s3_cfg(**overrides) -> S3BackendConfig:
    # endpoint_url is left unset so the client targets the AWS default endpoint,
    # which moto intercepts — moto does NOT mock arbitrary custom hosts (a real
    # MinIO endpoint is exercised against a live container in PR 3, not moto).
    # public_endpoint_url still drives presigned URLs, which sign locally (no
    # network), so the presign-host assertion works regardless.
    base = dict(
        public_endpoint_url="http://localhost:9000",
        bucket="embedbase",
        access_key_id="test",
        secret_access_key="test",
    )
    base.update(overrides)
    return S3BackendConfig(**base)


def _raw_s3():
    """A plain boto3 client (path-style) for asserting on moto's backend state."""
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(s3={"addressing_style": "path"}),
    )


def _client_error(code: str, op: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code}}, op)


# ── Registry resolution ───────────────────────────────────────────────────────


def test_get_storage_returns_default_backend(monkeypatch, tmp_path):
    monkeypatch.setattr("api.services.storage.settings.upload_dir", str(tmp_path))
    assert isinstance(get_storage(StorageConfig()), LocalStorage)


def test_get_storage_resolves_named_backend():
    cfg = StorageConfig(
        default="local",
        backends={"local": LocalBackendConfig(), "aws": _s3_cfg()},
    )
    with mock_aws():
        assert isinstance(get_storage(cfg, "aws"), S3Storage)


def test_get_storage_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown storage backend 'nope'"):
        get_storage(StorageConfig(), "nope")


# ── Local backend ─────────────────────────────────────────────────────────────


async def test_local_put_path_fetch_delete_round_trip(tmp_path):
    store = LocalStorage(tmp_path)
    src = tmp_path / "src.txt"
    src.write_bytes(b"hello")

    store.put_path(src, "col/doc.txt")
    fetched = store.fetch_to_temp("col/doc.txt")
    assert fetched.read_bytes() == b"hello"

    store.cleanup_temp(fetched)  # no-op for local: the real file stays put
    assert fetched.exists()

    store.delete("col/doc.txt")
    assert not (tmp_path / "col" / "doc.txt").exists()


async def test_local_put_upload_enforces_size_guard(tmp_path):
    store = LocalStorage(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await store.put_upload(FakeUpload(b"A" * 500), "c/d.txt", max_bytes=100)
    assert exc.value.status_code == 413


def test_local_presigned_get_is_none(tmp_path):
    assert LocalStorage(tmp_path).presigned_get("c/d.txt", "d.txt") is None


def test_local_local_path_points_under_base(tmp_path):
    assert LocalStorage(tmp_path).local_path("c/d.txt") == tmp_path / "c" / "d.txt"


# ── S3 backend (moto) ─────────────────────────────────────────────────────────


async def test_s3_put_upload_then_fetch_round_trips():
    with mock_aws():
        store = S3Storage(_s3_cfg())
        written = await store.put_upload(FakeUpload(b"payload"), "c/d.txt", max_bytes=1000)
        assert written == 7

        fetched = store.fetch_to_temp("c/d.txt")
        try:
            assert fetched.read_bytes() == b"payload"
        finally:
            store.cleanup_temp(fetched)
        assert not fetched.exists()  # temp copy removed


async def test_s3_put_upload_rejects_oversize_before_touching_s3():
    with mock_aws():
        store = S3Storage(_s3_cfg())
        with pytest.raises(HTTPException) as exc:
            await store.put_upload(FakeUpload(b"A" * 500), "c/big.txt", max_bytes=100)
        assert exc.value.status_code == 413
        # Nothing reached S3 — the bucket was never even created.
        with pytest.raises(ClientError):
            _raw_s3().head_bucket(Bucket="embedbase")


def test_s3_presigned_get_uses_public_endpoint_host():
    with mock_aws():
        url = S3Storage(_s3_cfg()).presigned_get("c/d.txt", "d.txt")
        assert url is not None
        assert urlparse(url).netloc == "localhost:9000"


async def test_s3_delete_removes_object():
    with mock_aws():
        store = S3Storage(_s3_cfg())
        await store.put_upload(FakeUpload(b"x"), "c/d.txt", max_bytes=1000)
        store.delete("c/d.txt")
        with pytest.raises(ClientError):
            store.fetch_to_temp("c/d.txt")  # object gone → download raises


async def test_s3_bucket_auto_created_on_first_use():
    with mock_aws():
        raw = _raw_s3()
        with pytest.raises(ClientError):
            raw.head_bucket(Bucket="embedbase")  # absent before any use

        store = S3Storage(_s3_cfg())
        await store.put_upload(FakeUpload(b"x"), "c/d.txt", max_bytes=1000)

        raw.head_bucket(Bucket="embedbase")  # created on first put — no raise


async def test_two_s3_instances_route_independently():
    cfg = StorageConfig(
        default="minio",
        backends={
            "minio": _s3_cfg(bucket="embedbase"),
            "aws": _s3_cfg(bucket="prod-bucket"),
        },
    )
    with mock_aws():
        minio = get_storage(cfg)  # default → minio
        aws = get_storage(cfg, "aws")
        await minio.put_upload(FakeUpload(b"m"), "k.txt", max_bytes=1000)
        await aws.put_upload(FakeUpload(b"a"), "k.txt", max_bytes=1000)

        mf, af = minio.fetch_to_temp("k.txt"), aws.fetch_to_temp("k.txt")
        try:
            # Same key, different buckets → each instance sees only its own bytes.
            assert mf.read_bytes() == b"m"
            assert af.read_bytes() == b"a"
        finally:
            minio.cleanup_temp(mf)
            aws.cleanup_temp(af)


# ── S3 backend: credentials, bucket bootstrap, presign wiring (no network) ─────


def test_s3_static_creds_passed_only_when_both_present(monkeypatch):
    # Capture what S3Storage hands to boto3.client without building a real client.
    calls: list[dict] = []
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: calls.append(kw) or object())

    S3Storage(_s3_cfg(endpoint_url=None, public_endpoint_url=None, access_key_id="ak", secret_access_key="sk"))
    assert calls and all(kw.get("aws_access_key_id") == "ak" for kw in calls)

    calls.clear()
    # id set but secret blank → omit static creds entirely (fall back to the chain),
    # never send aws_secret_access_key="".
    S3Storage(_s3_cfg(endpoint_url=None, public_endpoint_url=None, access_key_id="ak", secret_access_key=""))
    assert all("aws_secret_access_key" not in kw for kw in calls)
    assert all("aws_access_key_id" not in kw for kw in calls)


def test_ensure_bucket_treats_head_403_as_existing(monkeypatch):
    store = S3Storage(_s3_cfg())

    def head_403(**kw):
        raise _client_error("403", "HeadBucket")

    def must_not_create(**kw):
        raise AssertionError("create_bucket must not run when the bucket exists (403)")

    monkeypatch.setattr(store._s3, "head_bucket", head_403)
    monkeypatch.setattr(store._s3, "create_bucket", must_not_create)
    store._ensure_bucket()
    assert store._bucket_ready is True


def test_ensure_bucket_swallows_concurrent_create_race(monkeypatch):
    store = S3Storage(_s3_cfg())
    monkeypatch.setattr(store._s3, "head_bucket", lambda **kw: (_ for _ in ()).throw(_client_error("404")))
    monkeypatch.setattr(
        store._s3, "create_bucket",
        lambda **kw: (_ for _ in ()).throw(_client_error("BucketAlreadyOwnedByYou")),
    )
    store._ensure_bucket()  # a lost create race is benign — must not raise
    assert store._bucket_ready is True


def test_s3_presign_reuses_main_client_without_distinct_public_endpoint():
    store = S3Storage(_s3_cfg(endpoint_url="http://minio:9000", public_endpoint_url=None))
    assert store._presign_s3 is store._s3
    assert store._s3.meta.endpoint_url == "http://minio:9000"


def test_s3_presign_binds_to_public_endpoint_when_distinct():
    store = S3Storage(_s3_cfg(endpoint_url="http://minio:9000", public_endpoint_url="http://localhost:9000"))
    assert store._presign_s3 is not store._s3
    assert store._s3.meta.endpoint_url == "http://minio:9000"  # in-cluster ops
    assert store._presign_s3.meta.endpoint_url == "http://localhost:9000"  # browser-facing


def test_s3_presigned_get_sanitizes_filename():
    from urllib.parse import parse_qs, urlparse

    with mock_aws():
        url = S3Storage(_s3_cfg()).presigned_get("c/d.txt", 'a"b\r\nc.txt')
        assert url is not None
        disp = parse_qs(urlparse(url).query)["response-content-disposition"][0]
        assert disp == 'attachment; filename="abc.txt"'  # quote/CR/LF stripped


async def test_s3_fetch_to_temp_cleans_up_on_download_error(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # temp files land here
    with mock_aws():
        store = S3Storage(_s3_cfg())
        await store.put_upload(FakeUpload(b"x"), "exists.txt", max_bytes=1000)
        before = set(tmp_path.iterdir())  # put_upload cleaned up after itself
        with pytest.raises(ClientError):
            store.fetch_to_temp("missing.txt")  # 404 → must not orphan a temp file
        assert set(tmp_path.iterdir()) == before
