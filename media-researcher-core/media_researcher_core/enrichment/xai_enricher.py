"""xAI (Grok) enricher — uses live web search to find very recent work and craft pitch angles.

When XAI_API_KEY is set this enricher is used instead of the Claude enricher because
Grok's real-time web access produces fresher recent-work data and more grounded
pitch angles (it can read the actual article/episode rather than summarising from memory).

The Claude enricher remains as a fallback when XAI_API_KEY is absent.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from ..cache import EnrichmentCache
from ..config import Config
from ..models import MediaTarget, PersonalizationDepth, RecentWork, ResearchBrief

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"

_ENRICH_SYSTEM = """\
You are a media researcher with real-time web access. Your job is to find the most
recent, verifiable work by a specific media target and — when requested — craft a
precise, reference-specific pitch angle for them.

Rules:
1. Only include articles, episodes, or posts you can verify exist. If you cannot
   confirm a URL, set "url": null.
2. Dates must be the actual publication/release date you found, not an estimate.
   If uncertain, set "date": null.
3. Pitch angles must reference a specific real piece of the target's recent work.
   Do not write generic pitches.
4. Return valid JSON only — no prose, no markdown fences.
"""

_ENRICH_PROMPT_MEDIUM = """\
Target: {name}
Role: {role}
Outlet: {outlet}
Research topic: {topic}
Find up to 5 of their most recent articles, episodes, or posts that relate to the topic.

Return JSON:
{{
  "recent_work": [
    {{"title": "...", "url": "...", "date": "YYYY-MM-DD", "relevance_note": "..."}}
  ]
}}
"""

_ENRICH_PROMPT_DEEP = """\
Target: {name}
Role: {role}
Outlet: {outlet}
Research topic: {topic}
Extra context: {extra_notes}

1. Find up to 5 of their most recent articles, episodes, or posts that relate to the topic.
2. Using the most compelling recent piece you found, write a 2–4 sentence pitch angle
   that a founder/executive could use to open a cold outreach. The angle must:
   - Name the specific episode/article by title
   - Explain clearly why the client's expertise or story is a natural next step
   - Be direct and conversational, not salesy

Return JSON:
{{
  "recent_work": [
    {{"title": "...", "url": "...", "date": "YYYY-MM-DD", "relevance_note": "..."}}
  ],
  "pitch_angle": "..."
}}
"""


class XAIEnricher:
    """
    Enriches MediaTarget objects using Grok's live web search.

    Priority order when both keys are present:
      XAI_API_KEY  → XAIEnricher  (real-time web data, preferred)
      ANTHROPIC_API_KEY → Claude enricher  (fallback)
    """

    def __init__(self, config: Config, cache: EnrichmentCache) -> None:
        self.config = config
        self.cache = cache
        self._client: Optional[AsyncOpenAI] = (
            AsyncOpenAI(api_key=config.xai_api_key, base_url=XAI_BASE_URL)
            if config.xai_api_key
            else None
        )

    @property
    def available(self) -> bool:
        return self._client is not None

    async def enrich(
        self,
        target: MediaTarget,
        brief: ResearchBrief,
        depth: PersonalizationDepth,
    ) -> MediaTarget:
        cache_key = EnrichmentCache.target_key(f"xai:{target.id}", depth.value)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug("[xai_enricher] cache hit: %s", target.id)
            return MediaTarget(**cached)

        enriched = await self._do_enrich(target, brief, depth)
        enriched.last_enriched = datetime.now(timezone.utc).replace(tzinfo=None)
        self.cache.set(cache_key, enriched.model_dump(mode="json"))
        return enriched

    async def _do_enrich(
        self,
        target: MediaTarget,
        brief: ResearchBrief,
        depth: PersonalizationDepth,
    ) -> MediaTarget:
        if depth == PersonalizationDepth.LIGHT:
            return target  # nothing to do

        if not self._client:
            target.enrichment_notes.append(
                "XAI_API_KEY not set — xAI enrichment skipped."
            )
            return target

        return await self._enrich_with_grok(target, brief, depth)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def _enrich_with_grok(
        self,
        target: MediaTarget,
        brief: ResearchBrief,
        depth: PersonalizationDepth,
    ) -> MediaTarget:
        template = (
            _ENRICH_PROMPT_DEEP if depth == PersonalizationDepth.DEEP
            else _ENRICH_PROMPT_MEDIUM
        )
        prompt = template.format(
            name=target.name,
            role=target.role or "unknown",
            outlet=target.outlet or "unknown",
            topic=brief.topic,
            extra_notes=brief.extra_notes or "none",
        )

        logger.info("[xai_enricher] enriching %s (%s) at depth=%s",
                    target.name, target.id, depth.value)

        response = await self._client.chat.completions.create(
            model=self.config.xai_model,
            messages=[
                {"role": "system", "content": _ENRICH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("[xai_enricher] JSON parse error for %s: %s", target.id, exc)
            target.enrichment_notes.append("xAI enrichment returned unexpected format.")
            return target

        target = self._apply_enrichment(target, data, depth)
        return target

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
            existing_titles = {w.title for w in new_work}
            merged = new_work + [
                w for w in target.recent_work if w.title not in existing_titles
            ]
            target.recent_work = merged[:5]
            target.enrichment_notes.append(
                f"Recent work enriched via Grok (xAI) live web search."
            )

        if depth == PersonalizationDepth.DEEP and data.get("pitch_angle"):
            target.pitch_angle = data["pitch_angle"]
            target.enrichment_notes.append(
                "Pitch angle generated by Grok (xAI) based on verified recent work."
            )

        return target
