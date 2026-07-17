"""Token-bucket rate limiting for the MCP server.

Each API key gets an independent bucket holding up to ``rpm`` tokens that refill
at ``rpm / 60`` tokens per second. A request consumes one token; when the bucket
is empty the request is denied (the transport maps this to HTTP 429). The clock
is injectable so the refill behaviour can be tested without sleeping.

``rpm`` is supplied as a *callable* and resolved on every :meth:`allow` call rather
than captured at construction, so a config change takes effect on the next request
instead of at the next restart: the middleware is built once when the MCP app is
mounted at startup and is never rebuilt (``apply_config`` swaps the live config
singleton, not the mount). A raised limit widens the bucket immediately; a lowered
one is clamped down by the existing refill cap.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucketRateLimiter:
    """A per-key token-bucket limiter sized for the *current* "N requests per minute"."""

    def __init__(self, rpm: Callable[[], int], *, now: Callable[[], float] = time.monotonic) -> None:
        """Initialise the limiter.

        Args:
            rpm: Returns the maximum requests per minute per key (also the burst
                capacity). Called on every :meth:`allow`, so it can read live config
                and a change applies without rebuilding the limiter.
            now: Monotonic clock returning seconds; injectable for tests.
        """
        self._rpm = rpm
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
            rpm = self._rpm()
            capacity = float(rpm)
            now = self._now()
            tokens, last = self._buckets.get(key, (capacity, now))
            # min() re-clamps to the *current* capacity, so a lowered rpm shrinks an
            # already-full bucket on the next call instead of letting it drain at the old size.
            tokens = min(capacity, tokens + (now - last) * (rpm / 60.0))
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            return allowed
