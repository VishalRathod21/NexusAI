"""Tool-result cache.

Web searches and page fetches are slow, rate-limited and often repeated (three
agents research the same topic), so their results are cached by
(server, tool, arguments).

Redis is used when `REDIS_URL` is set, which lets separate agent processes
share one cache. Without it, each process keeps a small in-memory cache. If
Redis goes down mid-run the cache degrades to in-memory instead of failing the
tool call: a cache is an optimisation, never a dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def make_key(server: str, tool: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"nexusai:tool:{server}:{tool}:{digest}"


class ToolCache:
    def __init__(self, redis_url: str = "", ttl_seconds: int = 3600, max_items: int = 500):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._memory: dict[str, tuple[float, str]] = {}
        self._redis: Any = None
        self._redis_failed = False
        if redis_url:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(
                    redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2
                )
            except Exception as exc:
                logger.warning("Redis unavailable, using in-memory cache: %s", exc)

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None and not self._redis_failed else "memory"

    def _redis_down(self, exc: Exception) -> None:
        if not self._redis_failed:
            logger.warning("Redis error, falling back to in-memory cache: %s", exc)
        self._redis_failed = True

    async def get(self, key: str) -> Any | None:
        if self._redis is not None and not self._redis_failed:
            try:
                raw = await self._redis.get(key)
                return json.loads(raw) if raw else None
            except Exception as exc:
                self._redis_down(exc)
        entry = self._memory.get(key)
        if not entry:
            return None
        expires, raw = entry
        if expires < time.time():
            self._memory.pop(key, None)
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any) -> None:
        raw = json.dumps(value, default=str)
        if self._redis is not None and not self._redis_failed:
            try:
                await self._redis.set(key, raw, ex=self.ttl)
                return
            except Exception as exc:
                self._redis_down(exc)
        if len(self._memory) >= self.max_items:
            self._memory.pop(next(iter(self._memory)))
        self._memory[key] = (time.time() + self.ttl, raw)
