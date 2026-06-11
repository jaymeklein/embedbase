"""Unit tests for the MCP token-bucket rate limiter.

Drives :class:`TokenBucketRateLimiter` with an injected fake clock so the
"60 requests per minute, 61st denied" behaviour is asserted deterministically
without sleeping.
"""

from __future__ import annotations

from api.services.mcp.rate_limit import TokenBucketRateLimiter


class _FakeClock:
    """A controllable monotonic clock for deterministic refill tests."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_allows_up_to_capacity_then_denies() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=60, now=clock)

    # All 60 tokens are available immediately (no time has elapsed).
    assert all(limiter.allow("key-a") for _ in range(60))
    # The 61st request in the same instant is throttled.
    assert limiter.allow("key-a") is False


def test_refills_one_token_per_second() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=60, now=clock)
    for _ in range(60):
        limiter.allow("key-a")
    assert limiter.allow("key-a") is False

    clock.advance(1.0)  # 60 rpm => 1 token/sec
    assert limiter.allow("key-a") is True
    assert limiter.allow("key-a") is False


def test_refill_is_capped_at_capacity() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=60, now=clock)
    limiter.allow("key-a")  # consume one

    clock.advance(3600.0)  # an hour of idle must not exceed capacity
    assert sum(limiter.allow("key-a") for _ in range(100)) == 60


def test_buckets_are_isolated_per_key() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=5, now=clock)
    assert all(limiter.allow("key-a") for _ in range(5))
    assert limiter.allow("key-a") is False
    # A different key has its own full bucket.
    assert all(limiter.allow("key-b") for _ in range(5))
    assert limiter.allow("key-b") is False


def test_rpm_is_configurable() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=2, now=clock)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False
