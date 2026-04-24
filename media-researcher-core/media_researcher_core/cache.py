"""7-day disk cache for enrichment results."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import diskcache

from .config import Config

logger = logging.getLogger(__name__)


class EnrichmentCache:
    """Thin wrapper around diskcache.Cache with a 7-day TTL."""

    def __init__(self, config: Config) -> None:
        self._cache = diskcache.Cache(config.cache_dir)
        self._ttl = config.cache_ttl_seconds

    # ── Public API ────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        value = self._cache.get(key)
        if value is not None:
            logger.debug("cache hit: %s", key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._cache.set(key, value, expire=self._ttl)
        logger.debug("cache set: %s (ttl=%ds)", key, self._ttl)

    def delete(self, key: str) -> None:
        self._cache.delete(key)

    def clear(self) -> None:
        self._cache.clear()

    def close(self) -> None:
        self._cache.close()

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def target_key(target_id: str, depth: str) -> str:
        """Stable cache key for a given target + depth combination."""
        return f"enrichment:{target_id}:{depth}"

    @staticmethod
    def discovery_key(source: str, brief_hash: str) -> str:
        """Cache key for discovery results (shorter TTL might be desirable for discovery)."""
        return f"discovery:{source}:{brief_hash}"
