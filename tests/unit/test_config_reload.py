"""Unit tests for the shared config-reload Redis primitives (Phase 3)."""

from __future__ import annotations

import json
import os
import socket

from api.services import config_reload as cr
from tests.unit.fake_redis import FakeRedis


def test_status_key_uses_prefix():
    assert cr.status_key("abc123") == "config:reload:status:abc123"


def test_worker_id_is_hostname_and_pid():
    assert cr.worker_id() == f"{socket.gethostname()}:{os.getpid()}"


def test_publish_reload_returns_subscriber_count_and_payload():
    redis = FakeRedis(subscribers=3)
    assert cr.publish_reload(redis, "v1") == 3
    channel, data = redis.published[0]
    assert channel == cr.RELOAD_CHANNEL
    assert json.loads(data) == {"version_id": "v1", "rollback": False}


def test_publish_reload_marks_rollback():
    redis = FakeRedis(subscribers=1)
    cr.publish_reload(redis, "v1", rollback=True)
    assert json.loads(redis.published[0][1])["rollback"] is True


def test_init_status_seeds_fields_and_expiry():
    redis = FakeRedis()
    cr.init_status(redis, "v1", 2)
    bucket = redis.hashes[cr.status_key("v1")]
    assert bucket["api"] == "ok"
    assert bucket["expected_workers"] == "2"
    assert bucket["version_id"] == "v1"
    assert redis.expires[cr.status_key("v1")] > 0


def test_record_worker_ack_writes_worker_field():
    redis = FakeRedis()
    cr.record_worker_ack(redis, "v1", "ok")
    bucket = redis.hashes[cr.status_key("v1")]
    assert bucket[f"worker:{cr.worker_id()}"] == "ok"


def test_read_status_missing_returns_none():
    assert cr.read_status(FakeRedis(), "nope") is None


def test_read_status_pending_when_acks_outstanding():
    redis = FakeRedis()
    cr.init_status(redis, "v1", 2)
    cr.record_worker_ack(redis, "v1", "ok")
    status = cr.read_status(redis, "v1")
    assert status is not None
    assert status["status"] == "pending"
    assert status["acked_workers"] == 1
    assert status["expected_workers"] == 2


def test_read_status_applied_when_all_workers_ok():
    redis = FakeRedis()
    cr.init_status(redis, "v1", 1)
    cr.record_worker_ack(redis, "v1", "ok")
    assert cr.read_status(redis, "v1")["status"] == "applied"


def test_read_status_error_when_a_worker_fails():
    redis = FakeRedis()
    cr.init_status(redis, "v1", 1)
    redis.hset(cr.status_key("v1"), "worker:other", "error: boom")
    status = cr.read_status(redis, "v1")
    assert status["status"] == "error"
    assert status["workers"]["worker:other"].startswith("error")


def test_read_status_final_rolled_back_is_honored():
    redis = FakeRedis()
    cr.init_status(redis, "v1", 1)
    cr.record_worker_ack(redis, "v1", "ok")  # would otherwise read "applied"
    cr.mark_rolled_back(redis, "v1")
    assert cr.read_status(redis, "v1")["status"] == "rolled_back"
