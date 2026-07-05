"""A small thread-safe token-bucket rate limiter.

Copilot's consumer chat publishes no rate limit, so this is a *self-imposed*
ceiling: a safety valve that keeps automated callers from hammering your single
signed-in account. It is orthogonal to the concurrency lock in :mod:`server.api`
(which caps how many requests run *at once*); this caps how many run *per minute*.

Token bucket: the bucket holds at most ``burst`` tokens and refills at
``rpm / 60`` tokens per second. Each request spends one token. When the bucket is
empty the request is refused and told how long to wait — so short bursts are
absorbed up to ``burst`` while the long-run average is held at ``rpm``.
"""

import threading


class TokenBucket:
    """Classic token bucket. ``try_acquire`` is non-blocking and thread-safe."""

    def __init__(self, rpm: float, burst: int, *, monotonic=None):
        # rpm <= 0 disables limiting entirely (every acquire succeeds).
        self.rpm = float(rpm)
        self.rate = self.rpm / 60.0  # tokens per second
        self.capacity = max(1, int(burst))
        self._tokens = float(self.capacity)
        self._lock = threading.Lock()
        # Injectable clock keeps this unit-testable without real time passing.
        import time as _time
        self._now = monotonic or _time.monotonic
        self._updated = self._now()

    @property
    def enabled(self) -> bool:
        return self.rpm > 0

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._updated = now

    def try_acquire(self) -> tuple[bool, float]:
        """Spend one token if available.

        Returns ``(allowed, retry_after_seconds)``. When disabled, always
        ``(True, 0.0)``. When refused, ``retry_after`` is the time until one
        token has accrued (always > 0).
        """
        if not self.enabled:
            return True, 0.0
        with self._lock:
            now = self._now()
            self._refill(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True, 0.0
            # Time until the bucket reaches one whole token.
            deficit = 1.0 - self._tokens
            retry_after = deficit / self.rate if self.rate > 0 else 0.0
            return False, retry_after
