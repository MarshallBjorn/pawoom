"""Pamięciowa atrapa Redis: TTL na sztucznym zegarze i przełącznik awarii."""

from __future__ import annotations

from redis.exceptions import ConnectionError as RedisConnectionError


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.now = 0.0
        self.down = False
        self.closed = False

    # --- sterowanie testem ---
    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _check(self) -> None:
        if self.down:
            raise RedisConnectionError("Redis niedostępny (atrapa)")

    def _alive(self, key: str) -> bool:
        item = self.store.get(key)
        if item is None:
            return False
        if item[1] is not None and item[1] <= self.now:
            del self.store[key]
            return False
        return True

    # --- API używane przez aplikację ---
    def get(self, key: str) -> str | None:
        self._check()
        return self.store[key][0] if self._alive(key) else None

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._check()
        self.store[key] = (value, None if ex is None else self.now + ex)
        return True

    def delete(self, *keys: str) -> int:
        self._check()
        return sum(self.store.pop(k, None) is not None for k in keys)

    def ping(self) -> bool:
        self._check()
        return True

    def ttl(self, key: str) -> int:
        self._check()
        if not self._alive(key):
            return -2
        expires = self.store[key][1]
        return -1 if expires is None else int(expires - self.now)

    def close(self) -> None:
        self.closed = True
