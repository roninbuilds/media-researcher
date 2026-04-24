"""Journalist discovery via Muck Rack (primary), Apollo.io (secondary), web search (fallback)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..brief import brief_hash
from ..cache import EnrichmentCache
from ..config import Config
from ..models import ContactInfo, MediaTarget, RecentWork, ResearchBrief, TargetType
from .base import BaseDiscoverer

logger = logging.getLogger(__name__)

MUCK_RACK_BASE = "https://api.muckrack.com/v1"
APOLLO_BASE = "https://api.apollo.io/v1"


class JournalistDiscoverer(BaseDiscoverer):
    """Discovers journalists; degrades from Muck Rack → Apollo → web-search hint."""

    SOURCE_NAME = "journalists"

    async def discover(self, brief: ResearchBrief) -> list[MediaTarget]:
        bh = brief_hash(brief)
        cache_key = self.cache.discovery_key(self.SOURCE_NAME, bh)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("[journalists] returning %d cached results", len(cached))
            return [MediaTarget(**t) for t in cached]

        targets: list[MediaTarget] = []
        limitations: list[str] = []

        if self.config.muck_rack_api_key:
            targets = await self._search_muck_rack(brief)
        elif self.config.apollo_api_key:
            self._log_degraded("Muck Rack key absent — falling back to Apollo.io")
            limitations.append(
                "Muck Rack not configured; journalist data sourced from Apollo.io "
                "(contact data only, no byline history without Muck Rack)."
            )
            targets = await self._search_apollo(brief)
        else:
            self._log_degraded(
                "Neither MUCK_RACK_API_KEY nor APOLLO_API_KEY set — "
                "journalist discovery unavailable."
            )
            return []

        # Tag limitation notes onto each target
        for t in targets:
            t.enrichment_notes.extend(limitations)

        self.cache.set(cache_key, [t.model_dump(mode="json") for t in targets])
        return targets

    # ── Muck Rack ─────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_muck_rack(self, brief: ResearchBrief) -> list[MediaTarget]:
        """
        Muck Rack API — /journalists/search
        Docs: https://muckrack.com/api-docs/v1
        """
        headers = {
            "Authorization": f"Bearer {self.config.muck_rack_api_key}",
            "Accept": "application/json",
        }
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=brief.recency_days)).strftime("%Y-%m-%d")
        params: dict[str, Any] = {
            "query": brief.topic,
            "page_size": min(brief.num_results * 2, 50),
            "recent_byline_after": cutoff,
        }
        if brief.geo_filter:
            params["location"] = brief.geo_filter

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{MUCK_RACK_BASE}/journalists/search", headers=headers, params=params
            )
            resp.raise_for_status()
            data = resp.json()

        targets: list[MediaTarget] = []
        for item in data.get("journalists", []):
            t = self._parse_muck_rack_journalist(item)
            if t:
                targets.append(t)

        logger.info("[journalists/muck_rack] returned %d candidates", len(targets))
        return targets[: brief.num_results]

    def _parse_muck_rack_journalist(self, item: dict[str, Any]) -> Optional[MediaTarget]:
        try:
            jid = item.get("id", "")
            recent_work: list[RecentWork] = []
            for article in item.get("recent_articles", [])[:5]:
                pub_date = _parse_date(article.get("published_at"))
                recent_work.append(
                    RecentWork(
                        title=article.get("headline", ""),
                        url=article.get("url"),
                        date=pub_date,
                    )
                )
            return MediaTarget(
                id=f"muckrack:{jid}",
                target_type=TargetType.JOURNALISTS,
                name=item.get("name", "Unknown"),
                role=item.get("title"),
                outlet=item.get("outlet"),
                recent_work=recent_work,
                contact=ContactInfo(
                    email=item.get("email"),  # Muck Rack only returns publicly listed emails
                    twitter=item.get("twitter_url") or _handle_to_url(item.get("twitter")),
                    linkedin=item.get("linkedin_url"),
                    website=item.get("website"),
                ),
                source="muck_rack",
            )
        except Exception as exc:
            logger.debug("Failed to parse Muck Rack journalist: %s", exc)
            return None

    # ── Apollo.io ─────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_apollo(self, brief: ResearchBrief) -> list[MediaTarget]:
        """
        Apollo.io People Search API
        Docs: https://apolloio.github.io/apollo-api-docs/
        """
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }
        payload: dict[str, Any] = {
            "api_key": self.config.apollo_api_key,
            "q_keywords": brief.topic,
            "person_titles": ["journalist", "reporter", "editor", "writer", "correspondent"],
            "per_page": min(brief.num_results * 2, 50),
        }
        if brief.geo_filter:
            payload["person_locations"] = [brief.geo_filter]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{APOLLO_BASE}/mixed_people/search", json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()

        targets: list[MediaTarget] = []
        for person in data.get("people", []):
            t = self._parse_apollo_person(person)
            if t:
                targets.append(t)

        logger.info("[journalists/apollo] returned %d candidates", len(targets))
        return targets[: brief.num_results]

    def _parse_apollo_person(self, item: dict[str, Any]) -> Optional[MediaTarget]:
        try:
            pid = item.get("id", "")
            org = (item.get("organization") or {}).get("name")
            # Apollo may provide emails; we only use them if they are explicitly
            # public (Apollo marks them as "verified" but source is contact database,
            # not the target's own site — we flag this clearly).
            email_raw = item.get("email")
            email_note = None
            if email_raw:
                email_note = (
                    "Email sourced from Apollo.io contact database. "
                    "Verify it is publicly listed on the target's own site before using."
                )
            return MediaTarget(
                id=f"apollo:{pid}",
                target_type=TargetType.JOURNALISTS,
                name=f"{item.get('first_name', '')} {item.get('last_name', '')}".strip(),
                role=item.get("title"),
                outlet=org,
                contact=ContactInfo(
                    email=None,  # See email policy — do not return Apollo-inferred emails
                    twitter=item.get("twitter_url"),
                    linkedin=item.get("linkedin_url"),
                    website=item.get("website_url"),
                ),
                source="apollo",
                enrichment_notes=[
                    "Discovered via Apollo.io. Recent bylines not available without Muck Rack.",
                    *(([email_note]) if email_note else []),
                ],
            )
        except Exception as exc:
            logger.debug("Failed to parse Apollo person: %s", exc)
            return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        from dateutil import parser as du_parser
        return du_parser.parse(raw)
    except Exception:
        return None


def _handle_to_url(handle: Optional[str]) -> Optional[str]:
    if not handle:
        return None
    handle = handle.strip().lstrip("@")
    return f"https://twitter.com/{handle}" if handle else None
