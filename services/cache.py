import json
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MemoryCache:
    """Minimal thread-safe in-memory cache with per-key TTL."""

    def __init__(self, max_items: int = 1000):
        self._data = {}
        self._lock = threading.Lock()
        self._max_items = max_items

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at < time.monotonic():
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        with self._lock:
            if key not in self._data and len(self._data) >= self._max_items:
                now = time.monotonic()
                for stale in [k for k, v in self._data.items() if v[0] < now]:
                    del self._data[stale]
                if key not in self._data and len(self._data) >= self._max_items:
                    self._data.pop(next(iter(self._data)))
            self._data[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class AppCache:
    """JSON cache backed by Redis when reachable, otherwise by memory."""

    def __init__(self, redis_url: Optional[str] = None):
        self._memory = MemoryCache()
        self._redis = None
        if redis_url:
            self._redis = self._connect(redis_url)

    @staticmethod
    def _connect(redis_url: str):
        try:
            import redis

            client = redis.Redis.from_url(
                redis_url, socket_connect_timeout=2, socket_timeout=2
            )
            client.ping()
            logger.info("Redis cache enabled")
            return client
        except Exception as exc:
            logger.warning("Redis unavailable (%s); using in-memory cache", exc)
            return None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def get(self, key: str) -> Optional[Any]:
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
                return json.loads(raw) if raw is not None else None
            except Exception as exc:
                logger.warning("Redis get failed (%s); using in-memory cache", exc)
                self._redis = None
        raw = self._memory.get(key)
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        raw = json.dumps(value, default=str)
        if self._redis is not None:
            try:
                self._redis.set(key, raw, ex=ttl)
                return
            except Exception as exc:
                logger.warning("Redis set failed (%s); using in-memory cache", exc)
                self._redis = None
        self._memory.set(key, raw, ttl)
