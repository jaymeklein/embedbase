"""Unit tests for S3 bucket bootstrap side effects (CORS), under ``moto``.

PR 3: on first use an S3 backend applies a bucket CORS rule so the browser can
fetch a presigned GET URL cross-origin (the download flow redirects the browser
to the backend's own origin). Presign-host binding to ``public_endpoint_url`` is
covered in ``test_storage.py``; here we assert the CORS bootstrap.
"""

import io

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from moto import mock_aws

from api.models.config import S3BackendConfig
from api.services.storage import S3Storage


def _s3_cfg(**overrides) -> S3BackendConfig:
    base = dict(
        public_endpoint_url="http://localhost:9000",
        bucket="embedbase",
        access_key_id="test",
        secret_access_key="test",
    )
    base.update(overrides)
    return S3BackendConfig(**base)


class _FakeUpload:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self.size = len(data)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def _raw_cors(bucket: str) -> list[dict]:
    raw = boto3.client(
        "s3", region_name="us-east-1",
        aws_access_key_id="test", aws_secret_access_key="test",
        config=Config(s3={"addressing_style": "path"}),
    )
    return raw.get_bucket_cors(Bucket=bucket)["CORSRules"]


async def test_bucket_cors_applied_on_bootstrap(monkeypatch):
    from api.settings import settings

    monkeypatch.setattr(settings, "cors_origins", "http://localhost:3000,https://app.example.com")
    with mock_aws():
        store = S3Storage(_s3_cfg())
        await store.put_upload(_FakeUpload(b"x"), "c/d.txt", max_bytes=1000)  # first use → bootstrap

        rules = _raw_cors("embedbase")

    assert rules[0]["AllowedMethods"] == ["GET"]  # read-only from the browser
    assert rules[0]["AllowedOrigins"] == ["http://localhost:3000", "https://app.example.com"]


async def test_cors_failure_does_not_block_upload(monkeypatch):
    with mock_aws():
        store = S3Storage(_s3_cfg())

        def _boom(**kw):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutBucketCORS")

        monkeypatch.setattr(store._s3, "put_bucket_cors", _boom)
        # Best-effort: a locked-down IAM role forbidding PutBucketCORS must not fail
        # the upload — the object still lands, only cross-origin browser fetch is lost.
        written = await store.put_upload(_FakeUpload(b"payload"), "c/d.txt", max_bytes=1000)
        assert written == 7
