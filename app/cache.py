"""Cache-aside w Redis: JSON, TTL, krótkie timeouty i degradacja przy awarii.

Każda operacja łapie błędy Redis — cache jest opcjonalną, odtwarzalną kopią,
więc jego awaria nie może zablokować ani cofnąć operacji na bazie.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("app.cache")

HIT, MISS, BYPASS = "HIT", "MISS", "BYPASS"

def make_redis(url: str, timeout: float = 0.25) -> Redis:
    """Klient bez ponowień i z krótkimi timeoutami; nie łączy się przy tworzeniu."""
    return Redis.from_url(
        url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        retry_on_timeout=False,
        decode_responses=True,
    )

class JsonCache:
    def __init__(self, client: Redis, prefix: str, ttl: int, resource_name: str) -> None:
        if ttl <= 0:
            raise ValueError("TTL cache musi być dodatni.")
        if not prefix:
            raise ValueError("Prefiks cache nie może być pusty.")
        self._client = client
        self._prefix = prefix
        self._ttl = ttl
        self._resource = resource_name

    def key(self, resource_id: str) -> str:
        return f"{self._prefix}:{self._resource}:{resource_id}"

    def get(self, resource_id: str) -> tuple[dict[str, Any] | None, str]:
        """Zwraca (wartość, HIT|MISS|BYPASS)."""
        key = self.key(resource_id)
        try:
            raw = self._client.get(key)
        except RedisError as exc:
            logger.warning("cache BYPASS (GET %s): %s", key, exc)
            return None, BYPASS
        if raw is None:
            logger.info("cache MISS %s", key)
            return None, MISS
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("cache: uszkodzony wpis %s — traktuję jak MISS", key)
            self.delete(resource_id)
            return None, MISS
        logger.info("cache HIT %s", key)
        return value, HIT

    def put(self, resource_id: str, payload: dict[str, Any]) -> bool:
        key = self.key(resource_id)
        try:
            self._client.set(key, json.dumps(payload), ex=self._ttl)
        except RedisError as exc:
            logger.warning("cache: SET %s nieudany: %s", key, exc)
            return False
        return True

    def delete(self, resource_id: str) -> bool:
        """False = unieważnienie nieudane; stary wpis może żyć do końca TTL."""
        key = self.key(resource_id)
        try:
            self._client.delete(key)
        except RedisError as exc:
            logger.warning("cache: DEL %s nieudany (stary wpis do końca TTL): %s", key, exc)
            return False
        return True