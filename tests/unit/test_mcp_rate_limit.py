"""Unit tests for the MCP token-bucket rate limiter.

Drives :class:`TokenBucketRateLimiter` with an injected fake clock so the
"60 requests per minute, 61st denied" behaviour is asserted deterministically
without sleeping. The rpm provider is likewise injected, which also covers the
limit changing live (a config save) without the limiter being rebuilt.
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
    limiter = TokenBucketRateLimiter(rpm=lambda: 60, now=clock)

    # All 60 tokens are available immediately (no time has elapsed).
    assert all(limiter.allow("key-a") for _ in range(60))
    # The 61st request in the same instant is throttled.
    assert limiter.allow("key-a") is False


def test_refills_one_token_per_second() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 60, now=clock)
    for _ in range(60):
        limiter.allow("key-a")
    assert limiter.allow("key-a") is False

    clock.advance(1.0)  # 60 rpm => 1 token/sec
    assert limiter.allow("key-a") is True
    assert limiter.allow("key-a") is False


def test_refill_is_capped_at_capacity() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 60, now=clock)
    limiter.allow("key-a")  # consume one

    clock.advance(3600.0)  # an hour of idle must not exceed capacity
    assert sum(limiter.allow("key-a") for _ in range(100)) == 60


def test_buckets_are_isolated_per_key() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 5, now=clock)
    assert all(limiter.allow("key-a") for _ in range(5))
    assert limiter.allow("key-a") is False
    # A different key has its own full bucket.
    assert all(limiter.allow("key-b") for _ in range(5))
    assert limiter.allow("key-b") is False


def test_rpm_is_configurable() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 2, now=clock)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False


def test_raised_rpm_applies_without_rebuilding_the_limiter() -> None:
    """A config save must take effect on the next request, not the next restart.

    Regression guard: rpm used to be captured in ``__init__``, so the limiter built when
    the MCP app was mounted at startup kept the boot-time limit forever — ``apply_config``
    swaps the live config singleton but never rebuilds the mount.
    """
    clock = _FakeClock()
    rpm = 2
    limiter = TokenBucketRateLimiter(rpm=lambda: rpm, now=clock)
    assert all(limiter.allow("k") for _ in range(2))
    assert limiter.allow("k") is False  # bucket empty at the old limit

    rpm = 60  # the config PUT lands — same limiter instance, no restart
    clock.advance(1.0)  # 60 rpm => 1 token/sec, the NEW refill rate
    assert limiter.allow("k") is True


def test_lowered_rpm_shrinks_an_already_full_bucket() -> None:
    """Dropping the limit must bite immediately — a bucket filled at the old, higher
    capacity is re-clamped on the next call instead of serving one last oversized burst."""
    clock = _FakeClock()
    rpm = 60
    limiter = TokenBucketRateLimiter(rpm=lambda: rpm, now=clock)
    limiter.allow("k")  # bucket sitting at 59 tokens under the old capacity

    rpm = 5
    assert sum(limiter.allow("k") for _ in range(100)) == 5  # clamped to the new capacity


def test_snapshot_is_non_consuming() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 5, now=clock)
    # A fresh key is at full capacity; snapshotting must not spend a token.
    snap = limiter.snapshot("k")
    assert snap == {"limit_rpm": 5, "remaining": 5, "reset_seconds": 0.0}
    assert limiter.snapshot("k")["remaining"] == 5  # still full — read-only
    assert all(limiter.allow("k") for _ in range(5))  # all 5 tokens really available


def test_snapshot_reports_remaining_and_reset_after_exhaustion() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 60, now=clock)  # 1 token/sec
    for _ in range(60):
        limiter.allow("k")
    snap = limiter.snapshot("k")
    assert snap["remaining"] == 0
    # Empty bucket at 1 token/sec → ~1s until the next call is allowed.
    assert snap["reset_seconds"] == 1.0
    clock.advance(0.5)  # halfway to the next token
    assert limiter.snapshot("k")["reset_seconds"] == 0.5


# ── per-key overrides (the console's per-user MCP rate limit) ──────────────────


def test_set_key_limit_caps_a_key_below_the_global() -> None:
    """A per-key override throttles that key at its own rate, not the global default."""
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 60, now=clock)
    limiter.set_key_limit("k", 3)
    assert sum(limiter.allow("k") for _ in range(100)) == 3  # capped at 3, not 60
    # A key with no override still gets the full global default — the override is per key.
    assert sum(limiter.allow("other") for _ in range(100)) == 60


def test_set_key_limit_can_raise_a_key_above_the_global() -> None:
    """An override can also lift a single key above the global default."""
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 5, now=clock)
    limiter.set_key_limit("k", 20)
    assert sum(limiter.allow("k") for _ in range(100)) == 20


def test_clearing_key_limit_reverts_to_the_global() -> None:
    """0/None clears the override so the key falls back to the live global rate."""
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 10, now=clock)
    limiter.set_key_limit("k", 2)
    assert sum(limiter.allow("k") for _ in range(100)) == 2

    clock.advance(3600.0)  # let the bucket refill fully under whichever cap applies
    limiter.set_key_limit("k", None)  # clear → back to the global 10
    assert sum(limiter.allow("k") for _ in range(100)) == 10

    clock.advance(3600.0)
    limiter.set_key_limit("k", 0)  # 0 means "inherit" too
    assert sum(limiter.allow("k") for _ in range(100)) == 10


def test_snapshot_reports_the_key_override() -> None:
    """get_rate_limit reads snapshot(), which must report the per-key ceiling, not the global."""
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(rpm=lambda: 60, now=clock)
    limiter.set_key_limit("k", 4)
    assert limiter.snapshot("k") == {"limit_rpm": 4, "remaining": 4, "reset_seconds": 0.0}
