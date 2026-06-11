"""Token-bucket rate limiting for the MCP server.

Each API key gets an independent bucket holding up to ``rpm`` tokens that refill
at ``rpm / 60`` tokens per second. A request consumes one token; when the bucket
is empty the request is denied (the transport maps this to HTTP 429). The clock
is injectable so the refill behaviour can be tested without sleeping.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucketRateLimiter:
    """A per-key token-bucket limiter sized for "N requests per minute".

    Attributes:
        rpm: Bucket capacity and the steady-state requests-per-minute ceiling.
    """

    def __init__(self, rpm: int, *, now: Callable[[], float] = time.monotonic) -> None:
        """Initialise the limiter.

        Args:
            rpm: Maximum requests per minute per key (also the burst capacity).
            now: Monotonic clock returning seconds; injectable for tests.
        """
        self._capacity = float(rpm)
        self._refill_per_sec = rpm / 60.0
        self._now = now
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Consume one token for ``key``; return whether the request is allowed.

        Args:
            key: The API key (or any caller identity) to throttle independently.

        Returns:
            ``True`` if a token was available and consumed, ``False`` otherwise.
        """
        with self._lock:
            now = self._now()
            tokens, last = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._refill_per_sec)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            return allowed
