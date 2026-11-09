"""In-memory token bucket per key (spec "Request pipeline" 3). Cloudflare's rate rules are the first
layer; this one protects the services behind api-service when something gets through."""

from __future__ import annotations

import time
from typing import Callable


class RateLimiter:
    def __init__(self, burst: int, per_minute: float, *, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000):
        self.burst = burst
        self.rate = per_minute / 60.0
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, last update)

    def allow(self, key: str) -> bool:
        now = self._clock()
        tokens, last = self._buckets.get(key, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last) * self.rate)
        allowed = tokens >= 1
        self._buckets[key] = (tokens - 1 if allowed else tokens, now)
        if len(self._buckets) > self._max_keys:   # forget the fullest (idle) buckets first
            for k, _ in sorted(self._buckets.items(), key=lambda kv: -kv[1][0])[: self._max_keys // 10]:
                del self._buckets[k]
        return allowed

    def retry_after(self, key: str) -> int:
        tokens, _ = self._buckets.get(key, (float(self.burst), 0.0))
        return max(1, int((1 - tokens) / self.rate) + 1) if self.rate else 60
