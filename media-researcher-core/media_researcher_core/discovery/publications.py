"""Publication discovery via curated lists + Claude-assisted web research."""
from __future__ import annotations

import logging
from typing import Any

from ..brief import brief_hash
from ..config import Config
from ..models import ContactInfo, MediaTarget, ResearchBrief, TargetType
from .base import BaseDiscoverer

logger = logging.getLogger(__name__)

# ── Curated seed lists by broad topic area ───────────────────────────────────
# These are well-known tech/business publications; extend as needed.
# Each entry: (name, website, audience_unit, approx_monthly_readers)

TECH_PUBLICATIONS: list[tuple[str, str, str, int]] = [
    ("TechCrunch", "https://techcrunch.com", "monthly visitors", 10_000_000),
    ("The Verge", "https://theverge.com", "monthly visitors", 25_000_000),
    ("Wired", "https://wired.com", "monthly visitors", 35_000_000),
    ("Ars Technica", "https://arstechnica.com", "monthly visitors", 15_000_000),
    ("VentureBeat", "https://venturebeat.com", "monthly visitors", 5_000_000),
    ("ZDNet", "https://zdnet.com", "monthly visitors", 12_000_000),
    ("InfoQ", "https://infoq.com", "monthly visitors", 1_500_000),
    ("Hacker News", "https://news.ycombinator.com", "daily active users", 500_000),
    ("MIT Technology Review", "https://technologyreview.com", "monthly visitors", 3_000_000),
    ("Protocol (archived)", "https://protocol.com", "monthly visitors", 1_000_000),
    ("The Register", "https://theregister.com", "monthly visitors", 10_000_000),
    ("IEEE Spectrum", "https://spectrum.ieee.org", "monthly visitors", 2_000_000),
    ("SD Times", "https://sdtimes.com", "monthly visitors", 300_000),
    ("Computerworld", "https://computerworld.com", "monthly visitors", 5_000_000),
    ("Slashdot", "https://slashdot.org", "monthly visitors", 3_000_000),
]

DEVELOPER_PUBLICATIONS: list[tuple[str, str, str, int]] = [
    ("Dev.to", "https://dev.to", "monthly visitors", 5_000_000),
    ("DZone", "https://dzone.com", "monthly visitors", 2_000_000),
    ("Smashing Magazine", "https://smashingmagazine.com", "monthly visitors", 2_500_000),
    ("CSS-Tricks", "https://css-tricks.com", "monthly visitors", 1_000_000),
    ("Towards Data Science", "https://towardsdatascience.com", "monthly visitors", 3_000_000),
    ("The New Stack", "https://thenewstack.io", "monthly visitors", 1_000_000),
    ("Opensource.com", "https://opensource.com", "monthly visitors", 800_000),
]

BUSINESS_PUBLICATIONS: list[tuple[str, str, str, int]] = [
    ("Forbes", "https://forbes.com", "monthly visitors", 70_000_000),
    ("Fast Company", "https://fastcompany.com", "monthly visitors", 20_000_000),
    ("Inc.", "https://inc.com", "monthly visitors", 25_000_000),
    ("Harvard Business Review", "https://hbr.org", "monthly visitors", 10_000_000),
    ("Business Insider", "https://businessinsider.com", "monthly visitors", 100_000_000),
]

AI_ML_PUBLICATIONS: list[tuple[str, str, str, int]] = [
    ("The Batch (deeplearning.ai)", "https://deeplearning.ai/the-batch", "subscribers", 500_000),
    ("Import AI (Jack Clark)", "https://importai.substack.com", "subscribers", 100_000),
    ("AI Alignment Forum", "https://alignmentforum.org", "monthly visitors", 100_000),
    ("Papers With Code Blog", "https://paperswithcode.com", "monthly visitors", 2_000_000),
    ("TLDR AI", "https://tldr.tech/ai", "subscribers", 500_000),
    ("The Rundown AI", "https://therundown.ai", "subscribers", 600_000),
]

TOPIC_MAP: dict[str, list[tuple[str, str, str, int]]] = {
    "ai": AI_ML_PUBLICATIONS + TECH_PUBLICATIONS,
    "artificial intelligence": AI_ML_PUBLICATIONS + TECH_PUBLICATIONS,
    "machine learning": AI_ML_PUBLICATIONS + TECH_PUBLICATIONS,
    "developer": DEVELOPER_PUBLICATIONS + TECH_PUBLICATIONS,
    "engineering": DEVELOPER_PUBLICATIONS + TECH_PUBLICATIONS,
    "startup": BUSINESS_PUBLICATIONS + TECH_PUBLICATIONS,
    "fintech": BUSINESS_PUBLICATIONS + TECH_PUBLICATIONS,
    "saas": BUSINESS_PUBLICATIONS + TECH_PUBLICATIONS,
    "cloud": TECH_PUBLICATIONS + DEVELOPER_PUBLICATIONS,
    "data": DEVELOPER_PUBLICATIONS + AI_ML_PUBLICATIONS,
    "security": TECH_PUBLICATIONS,
    "infra": TECH_PUBLICATIONS + DEVELOPER_PUBLICATIONS,
}


class PublicationDiscoverer(BaseDiscoverer):
    """
    Discovers publications from curated lists filtered to the brief topic.
    Claude-assisted enrichment will fill in editors/beats later in the pipeline.
    """

    SOURCE_NAME = "publications"

    async def discover(self, brief: ResearchBrief) -> list[MediaTarget]:
        bh = brief_hash(brief)
        cache_key = self.cache.discovery_key(self.SOURCE_NAME, bh)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("[publications] returning %d cached results", len(cached))
            return [MediaTarget(**t) for t in cached]

        candidates = self._select_publications(brief)
        targets = [self._to_target(pub) for pub in candidates]
        targets = self._apply_audience_filter(targets, brief)

        self.cache.set(cache_key, [t.model_dump(mode="json") for t in targets])
        logger.info("[publications] found %d candidates from curated lists", len(targets))
        return targets[: brief.num_results]

    # ── Internal ──────────────────────────────────────────────────────────

    def _select_publications(
        self, brief: ResearchBrief
    ) -> list[tuple[str, str, str, int]]:
        topic_lower = brief.topic.lower()
        seen: set[str] = set()
        result: list[tuple[str, str, str, int]] = []

        for keyword, pubs in TOPIC_MAP.items():
            if keyword in topic_lower:
                for pub in pubs:
                    if pub[0] not in seen:
                        seen.add(pub[0])
                        result.append(pub)

        # Fallback: if no keyword matched, return all tech publications
        if not result:
            result = TECH_PUBLICATIONS

        return result

    @staticmethod
    def _to_target(pub: tuple[str, str, str, int]) -> MediaTarget:
        name, website, audience_unit, audience_size = pub
        pub_id = name.lower().replace(" ", "_").replace("/", "_")
        return MediaTarget(
            id=f"pub:{pub_id}",
            target_type=TargetType.PUBLICATIONS,
            name=name,
            outlet=name,
            audience_size=audience_size,
            audience_unit=audience_unit,
            contact=ContactInfo(website=website),
            source="curated_list",
            enrichment_notes=[
                "Publication discovered from curated list. "
                "Use enrichment step to identify specific editors/beat writers.",
                "Audience figures are approximate and may be outdated.",
            ],
        )

    @staticmethod
    def _apply_audience_filter(
        targets: list[MediaTarget], brief: ResearchBrief
    ) -> list[MediaTarget]:
        ac = brief.audience_constraints
        if not (ac.min_readers or ac.max_readers):
            return targets
        filtered = []
        for t in targets:
            if t.audience_size is None:
                filtered.append(t)
                continue
            if ac.min_readers and t.audience_size < ac.min_readers:
                continue
            if ac.max_readers and t.audience_size > ac.max_readers:
                continue
            filtered.append(t)
        return filtered
