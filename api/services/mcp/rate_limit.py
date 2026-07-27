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

A key's ``rpm`` is the global default unless a **per-key override** is set via
:meth:`set_key_limit` — the MCP middleware pushes the authenticated user's configured
limit there just after auth, so a per-user cap set in the console governs that key (a
``0``/``None`` clears it back to the global). Overrides resolve the same per-call way,
so a changed limit also applies on the next request.
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
        self._overrides: dict[str, int] = {}
        self._lock = threading.Lock()

    def set_key_limit(self, key: str, rpm: int | None) -> None:
        """Set (or clear) ``key``'s per-key rpm override.

        A positive ``rpm`` caps this key instead of the global default from the next
        :meth:`allow`; ``None`` or a non-positive value clears the override so the key
        falls back to the live global rate. The MCP middleware calls this right after it
        authenticates a request, so a per-user limit configured in the console governs
        that key without rebuilding the limiter.

        Args:
            key: The API key (or caller identity) whose ceiling to override.
            rpm: The per-key requests-per-minute cap, or ``None``/``0`` to inherit the
                global default.
        """
        with self._lock:
            if rpm and rpm > 0:
                self._overrides[key] = rpm
            else:
                self._overrides.pop(key, None)

    def _effective_rpm(self, key: str) -> int:
        """``key``'s per-key override if one is set, else the live global default."""
        return self._overrides.get(key) or self._rpm()

    def allow(self, key: str) -> bool:
        """Consume one token for ``key``; return whether the request is allowed.

        Args:
            key: The API key (or any caller identity) to throttle independently.

        Returns:
            ``True`` if a token was available and consumed, ``False`` otherwise.
        """
        with self._lock:
            rpm = self._effective_rpm(key)
            capacity = float(rpm)
            now = self._now()
            tokens, last = self._buckets.get(key, (capacity, now))
            # min() re-clamps to the *current* capacity, so a lowered rpm shrinks an
            # already-full bucket on the next call instead of letting it drain at the old size.
            tokens = min(capacity, tokens + (now - last) * (rpm / 60.0))
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            return allowed

    def snapshot(self, key: str) -> dict[str, int | float]:
        """A **non-consuming** view of ``key``'s current budget (for the ``get_rate_limit`` tool).

        Refills ``key``'s bucket to *now* the same way :meth:`allow` does, but consumes no token and
        does not mutate stored state — a pure read. Returns the configured ``limit_rpm``, the whole
        tokens ``remaining`` (calls available right now), and ``reset_seconds`` (seconds until at
        least one more token refills; ``0`` when a call is already available).

        Args:
            key: The API key (or caller identity) whose budget to inspect.

        Returns:
            ``{"limit_rpm": int, "remaining": int, "reset_seconds": float}``.
        """
        with self._lock:
            rpm = self._effective_rpm(key)
            capacity = float(rpm)
            now = self._now()
            tokens, last = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * (rpm / 60.0))
            reset = 0.0 if tokens >= 1.0 or rpm <= 0 else (1.0 - tokens) * 60.0 / rpm
            return {"limit_rpm": rpm, "remaining": int(tokens), "reset_seconds": round(reset, 3)}
