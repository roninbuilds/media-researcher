"""Target enrichment: recent work, contact info, and deep pitch-angle generation via Claude."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import anthropic
import httpx

from ..cache import EnrichmentCache
from ..config import Config
from ..models import ContactInfo, MediaTarget, PersonalizationDepth, RecentWork, ResearchBrief

logger = logging.getLogger(__name__)

# System prompt for Claude enrichment
_ENRICHMENT_SYSTEM = """\
You are a research assistant for a media outreach team. Given information about a media target
(podcast, journalist, or publication), find and summarize their most recent relevant work and
suggest a targeted pitch angle. Be accurate — do not fabricate URLs, dates, or articles.
If you are unsure, say so. Return valid JSON only, no prose.
"""

_LIGHT_NOTE = (
    "Light depth requested — contact info only, recent work not fetched to conserve API calls."
)


class Enricher:
    """
    Enriches MediaTarget objects with recent work summaries and (for deep depth)
    Claude-generated pitch angles.

    Email policy:
        Only emails that are publicly listed on the target's OWN website or public bio
        are included. Never guessed or pattern-derived emails.
    """

    def __init__(self, config: Config, cache: EnrichmentCache) -> None:
        self.config = config
        self.cache = cache
        self._claude: Optional[anthropic.AsyncAnthropic] = (
            anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
            if config.anthropic_api_key
            else None
        )

    async def enrich(
        self,
        target: MediaTarget,
        brief: ResearchBrief,
        depth: PersonalizationDepth,
    ) -> MediaTarget:
        """Return the enriched version of target (may return cached result)."""
        cache_key = EnrichmentCache.target_key(target.id, depth.value)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug("enrichment cache hit: %s", target.id)
            return MediaTarget(**cached)

        enriched = await self._do_enrich(target, brief, depth)
        enriched.last_enriched = datetime.now(timezone.utc).replace(tzinfo=None)
        self.cache.set(cache_key, enriched.model_dump(mode="json"))
        return enriched

    # ── Internal ──────────────────────────────────────────────────────────

    async def _do_enrich(
        self,
        target: MediaTarget,
        brief: ResearchBrief,
        depth: PersonalizationDepth,
    ) -> MediaTarget:
        if depth == PersonalizationDepth.LIGHT:
            target.enrichment_notes.append(_LIGHT_NOTE)
            return target

        if not self._claude:
            target.enrichment_notes.append(
                "ANTHROPIC_API_KEY not set — medium/deep enrichment unavailable. "
                "Set the key for recent work summaries and pitch angles."
            )
            return target

        if depth in (PersonalizationDepth.MEDIUM, PersonalizationDepth.DEEP):
            target = await self._enrich_with_claude(target, brief, depth)

        return target

    async def _enrich_with_claude(
        self,
        target: MediaTarget,
        brief: ResearchBrief,
        depth: PersonalizationDepth,
    ) -> MediaTarget:
        prompt = self._build_prompt(target, brief, depth)
        try:
            response = await self._claude.messages.create(
                model=self.config.claude_model,
                max_tokens=1500,
                system=_ENRICHMENT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            target = self._apply_enrichment(target, data, depth)
        except json.JSONDecodeError as exc:
            logger.warning("Claude returned non-JSON for %s: %s", target.id, exc)
            target.enrichment_notes.append(
                "Claude enrichment returned unexpected format; manual review needed."
            )
        except anthropic.APIError as exc:
            logger.error("Anthropic API error enriching %s: %s", target.id, exc)
            target.enrichment_notes.append(f"Claude enrichment failed: {exc}")
        return target

    def _build_prompt(
        self, target: MediaTarget, brief: ResearchBrief, depth: PersonalizationDepth
    ) -> str:
        sections = [
            f"Target name: {target.name}",
            f"Role: {target.role or 'unknown'}",
            f"Outlet: {target.outlet or 'unknown'}",
            f"Target type: {target.target_type.value}",
            f"Research topic: {brief.topic}",
            f"Recency window: last {brief.recency_days} days",
        ]
        if target.contact.website:
            sections.append(f"Website: {target.contact.website}")
        if target.recent_work:
            existing = "\n".join(
                f"  - {w.title} ({w.date.date() if w.date else 'date unknown'})"
                for w in target.recent_work[:3]
            )
            sections.append(f"Known recent work:\n{existing}")

        task_parts = [
            "1. Find up to 5 recent articles/episodes/posts by this target that relate to the research topic.",
            "   For each: provide title, URL (if findable), date, and a one-sentence relevance note.",
            "   If you cannot verify a URL, omit it rather than guessing.",
        ]

        if depth == PersonalizationDepth.DEEP:
            task_parts.append(
                "2. Based on the target's recent work and the research topic, write a concise "
                "pitch angle (2-4 sentences) that a client could use to open an outreach message. "
                "The angle should reference a specific recent piece of the target's work and explain "
                "how the client's expertise connects to it."
            )

        if depth == PersonalizationDepth.MEDIUM:
            json_schema = """{
  "recent_work": [
    {"title": "...", "url": "...", "date": "YYYY-MM-DD", "relevance_note": "..."}
  ]
}"""
        else:
            json_schema = """{
  "recent_work": [
    {"title": "...", "url": "...", "date": "YYYY-MM-DD", "relevance_note": "..."}
  ],
  "pitch_angle": "..."
}"""

        return (
            "\n".join(sections)
            + "\n\nTask:\n"
            + "\n".join(task_parts)
            + f"\n\nReturn JSON matching this schema:\n{json_schema}"
        )

    @staticmethod
    def _apply_enrichment(
        target: MediaTarget, data: dict, depth: PersonalizationDepth
    ) -> MediaTarget:
        new_work: list[RecentWork] = []
        for item in data.get("recent_work", []):
            try:
                date = None
                if item.get("date"):
                    from dateutil import parser as du_parser
                    date = du_parser.parse(item["date"])
                new_work.append(
                    RecentWork(
                        title=item.get("title", ""),
                        url=item.get("url"),
                        date=date,
                        relevance_note=item.get("relevance_note"),
                    )
                )
            except Exception:
                pass

        if new_work:
            # Merge: keep any original work not superseded
            existing_titles = {w.title for w in new_work}
            merged = new_work + [
                w for w in target.recent_work if w.title not in existing_titles
            ]
            target.recent_work = merged[:5]

        if depth == PersonalizationDepth.DEEP and data.get("pitch_angle"):
            target.pitch_angle = data["pitch_angle"]

        return target
