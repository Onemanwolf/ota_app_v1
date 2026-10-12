from __future__ import annotations

from collections.abc import Callable


class TokenBucketLimiter:
    """Per-channel token bucket: `per_minute` tokens, refilled continuously."""

    def __init__(self, per_minute: int, monotonic: Callable[[], float]) -> None:
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0
        self._now = monotonic
        self._buckets: dict[str, tuple[float, float]] = {}

    def try_acquire(self, channel: str) -> bool:
        now = self._now()
        tokens, last = self._buckets.get(channel, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._rate)
        if tokens < 1.0:
            self._buckets[channel] = (tokens, now)
            return False
        self._buckets[channel] = (tokens - 1.0, now)
        return True
