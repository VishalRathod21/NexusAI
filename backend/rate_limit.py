"""Sliding-window rate limiter.

Used for two things:
* login attempts  - slows down password guessing
* submissions     - stops one account from flooding the agents

State is kept in this process's memory. That is correct for one backend
instance. With several instances behind a load balancer each would count
separately, so the counters would move to Redis (same interface).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, window_seconds: float, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        return hits

    def check(self, key: str, limit: int, window_seconds: float) -> float:
        """Seconds until the next attempt is allowed. 0 means allowed now."""
        now = time.monotonic()
        hits = self._prune(key, window_seconds, now)
        if len(hits) < limit:
            return 0.0
        return max(0.0, hits[0] + window_seconds - now)

    def hit(self, key: str) -> None:
        self._hits[key].append(time.monotonic())

    def allow(self, key: str, limit: int, window_seconds: float) -> float:
        """Check and record in one step. Returns the wait time (0 = allowed)."""
        wait = self.check(key, limit, window_seconds)
        if wait == 0.0:
            self.hit(key)
        return wait

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
