"""Podcast discovery via Listen Notes API (primary) with web-search fallback."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..brief import brief_hash
from ..cache import EnrichmentCache
from ..config import Config
from ..models import AudienceConstraints, MediaTarget, ResearchBrief, TargetType
from .base import BaseDiscoverer

logger = logging.getLogger(__name__)

LISTEN_NOTES_BASE = "https://listen-api.listennotes.com/api/v2"


class PodcastDiscoverer(BaseDiscoverer):
    """Discovers podcasts using Listen Notes API; falls back to web search hint."""

    SOURCE_NAME = "listen_notes"

    async def discover(self, brief: ResearchBrief) -> list[MediaTarget]:
        bh = brief_hash(brief)
        cache_key = self.cache.discovery_key(self.SOURCE_NAME, bh)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("[podcasts] returning %d cached results", len(cached))
            return [MediaTarget(**t) for t in cached]

        if not self.config.listen_notes_api_key:
            self._log_degraded("LISTEN_NOTES_API_KEY not set — podcast discovery unavailable.")
            return []

        targets = await self._search_listen_notes(brief)

        self.cache.set(cache_key, [t.model_dump(mode="json") for t in targets])
        return targets

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_listen_notes(self, brief: ResearchBrief) -> list[MediaTarget]:
        headers = {"X-ListenAPI-Key": self.config.listen_notes_api_key}

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=brief.recency_days)
        params: dict[str, Any] = {
            "q": brief.topic,
            "type": "podcast",
            "page_size": min(brief.num_results * 2, 40),  # over-fetch then filter
            "language": brief.language or "English",
            "published_after": int(cutoff.timestamp()),
            "safe_mode": 1,
        }
        if brief.geo_filter:
            params["region"] = brief.geo_filter

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{LISTEN_NOTES_BASE}/search", headers=headers, params=params
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[MediaTarget] = []
        for item in data.get("results", []):
            target = self._parse_podcast(item)
            if target and self._passes_audience_filter(
                target, brief.audience_constraints
            ):
                results.append(target)

        logger.info("[podcasts] listen_notes returned %d candidates", len(results))
        return results[: brief.num_results]

    def _parse_podcast(self, item: dict[str, Any]) -> Optional[MediaTarget]:
        try:
            podcast_id = item.get("id", "")
            return MediaTarget(
                id=f"listennotes:{podcast_id}",
                target_type=TargetType.PODCASTS,
                name=item.get("title_original", "Unknown"),
                role="Host",
                outlet=item.get("publisher_original"),
                audience_size=item.get("total_episodes"),  # proxy until episode-level data
                audience_unit="total episodes",
                source=self.SOURCE_NAME,
                contact={
                    "website": item.get("website") or item.get("listennotes_url"),
                    "twitter": _extract_twitter(item.get("extra", {}).get("twitter_handle", "")),
                },
                enrichment_notes=[
                    f"Listen Notes ID: {podcast_id}",
                    "Audience download figures require Listen Notes Pro plan or manual lookup.",
                ],
            )
        except Exception as exc:
            logger.debug("Failed to parse podcast item: %s — %s", item.get("id"), exc)
            return None

    @staticmethod
    def _passes_audience_filter(target: MediaTarget, constraints: AudienceConstraints) -> bool:
        # If the audience_size is None we can't filter — include optimistically
        if target.audience_size is None:
            return True
        size = target.audience_size
        if constraints.min_downloads and size < constraints.min_downloads:
            return False
        if constraints.max_downloads and size > constraints.max_downloads:
            return False
        return True


def _extract_twitter(handle: str) -> Optional[str]:
    if not handle:
        return None
    handle = handle.strip().lstrip("@")
    return f"https://twitter.com/{handle}" if handle else None
