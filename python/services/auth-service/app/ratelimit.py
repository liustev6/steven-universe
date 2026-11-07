"""In-memory sliding-window rate limit for /oauth2/token (per client and IP)."""

from __future__ import annotations

import time
from collections import deque
from typing import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ):
        self.limit = limit
        self.window = window
        self.clock = clock
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._evict(now)
            hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str) -> int:
        hits = self._hits.get(key)
        if not hits:
            return 0
        return max(1, int(self.window - (self.clock() - hits[0])) + 1)

    def _evict(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
            del self._hits[key]
