"""xAI (Grok) powered discovery — uses live web search to surface media targets.

Grok's real-time search access gives it a significant edge over static API sources:
it can find podcasts, journalists, and publications that have covered the exact topic
within the past few days, not just indexed results from weeks ago.

This discoverer runs alongside (not instead of) Listen Notes / Muck Rack / Apollo.
Deduplification happens in the runner via target IDs.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from ..brief import brief_hash
from ..config import Config
from ..models import ContactInfo, MediaTarget, RecentWork, ResearchBrief, TargetType
from .base import BaseDiscoverer

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a specialist media researcher with real-time web access. Your job is to find
the most relevant, high-quality outreach targets for a PR/media campaign.

Rules you must follow:
1. Only return targets that genuinely cover the stated topic — no tangential matches.
2. For each target, include the most recent relevant episode, article, or post you can
   find. If you cannot verify a URL, omit it rather than guessing.
3. For email addresses: ONLY include ones visibly listed on the target's own website
   or public bio. Never infer or guess an email pattern.
4. Return valid JSON only — no prose, no markdown fences.
5. If you cannot find enough real targets meeting the criteria, return fewer results
   rather than padding with poor matches.
"""

_DISCOVERY_PROMPT_TEMPLATE = """\
Research brief:
  Topic: {topic}
  Target types: {target_types}
  Recency: only include targets active in the last {recency_days} days
  Geographic filter: {geo_filter}
  Language: {language}
  Audience constraints: {audience_constraints}
  Extra notes: {extra_notes}

Search the web now and return the {num_results} best outreach targets matching this brief.

For each target return:
{{
  "id": "xai:<slugified-name>",
  "name": "...",
  "target_type": "podcasts" | "journalists" | "publications",
  "role": "Host" | "Reporter" | "Editor" | etc.,
  "outlet": "...",
  "audience_size": <integer or null>,
  "audience_unit": "monthly downloads" | "monthly readers" | etc.,
  "recent_work": [
    {{
      "title": "...",
      "url": "...",         // only if you can verify the URL exists
      "date": "YYYY-MM-DD", // only if you know the actual date
      "relevance_note": "one sentence on why this is relevant to the brief"
    }}
  ],
  "contact": {{
    "email": null,          // ONLY if publicly listed on the target's own site
    "twitter": "https://twitter.com/...",
    "linkedin": "https://linkedin.com/in/...",
    "website": "https://..."
  }},
  "source": "xai_web_search"
}}

Return a JSON array of target objects. No wrapping object, just the array.
"""


class XAIDiscoverer(BaseDiscoverer):
    """
    Uses Grok (xAI) with live web search to discover media targets.

    Grok is queried once per brief (results cached 7 days) and returns a
    ranked list of targets with recent work already attached — skipping
    the need for a separate enrichment pass for basic depth levels.
    """

    SOURCE_NAME = "xai_web_search"

    def __init__(self, config: Config, cache) -> None:
        super().__init__(config, cache)
        self._client: Optional[AsyncOpenAI] = (
            AsyncOpenAI(
                api_key=config.xai_api_key,
                base_url=XAI_BASE_URL,
            )
            if config.xai_api_key
            else None
        )

    async def discover(self, brief: ResearchBrief) -> list[MediaTarget]:
        if not self._client:
            self._log_degraded("XAI_API_KEY not set — xAI discovery unavailable.")
            return []

        bh = brief_hash(brief)
        cache_key = self.cache.discovery_key(self.SOURCE_NAME, bh)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("[xai] returning %d cached results", len(cached))
            return [MediaTarget(**t) for t in cached]

        targets = await self._search_with_grok(brief)
        self.cache.set(cache_key, [t.model_dump(mode="json") for t in targets])
        return targets

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
    async def _search_with_grok(self, brief: ResearchBrief) -> list[MediaTarget]:
        target_types = _target_types_string(brief.target_type)
        audience_str = _audience_string(brief)

        prompt = _DISCOVERY_PROMPT_TEMPLATE.format(
            topic=brief.topic,
            target_types=target_types,
            recency_days=brief.recency_days,
            geo_filter=brief.geo_filter or "none (worldwide)",
            language=brief.language or "any",
            audience_constraints=audience_str,
            extra_notes=brief.extra_notes or "none",
            num_results=min(brief.num_results, 30),  # Grok works best with ≤30 per call
        )

        logger.info("[xai] querying Grok %s for %s targets on: %s",
                    self.config.xai_model, target_types, brief.topic)

        response = await self._client.chat.completions.create(
            model=self.config.xai_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # low temperature for factual research
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if Grok wraps in them
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            items: list[dict[str, Any]] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("[xai] JSON parse failed: %s\nRaw response:\n%s", exc, raw[:500])
            return []

        targets: list[MediaTarget] = []
        for item in items:
            t = self._parse_item(item)
            if t:
                targets.append(t)

        logger.info("[xai] parsed %d targets from Grok response", len(targets))
        return targets

    @staticmethod
    def _parse_item(item: dict[str, Any]) -> Optional[MediaTarget]:
        try:
            raw_type = item.get("target_type", "").lower()
            type_map = {
                "podcasts": TargetType.PODCASTS,
                "podcast": TargetType.PODCASTS,
                "journalists": TargetType.JOURNALISTS,
                "journalist": TargetType.JOURNALISTS,
                "publications": TargetType.PUBLICATIONS,
                "publication": TargetType.PUBLICATIONS,
            }
            target_type = type_map.get(raw_type, TargetType.PUBLICATIONS)

            # Parse recent work
            recent_work: list[RecentWork] = []
            for work in item.get("recent_work", [])[:5]:
                date = None
                if work.get("date"):
                    try:
                        from dateutil import parser as du_parser
                        date = du_parser.parse(work["date"])
                    except Exception:
                        pass
                recent_work.append(
                    RecentWork(
                        title=work.get("title", ""),
                        url=work.get("url"),
                        date=date,
                        relevance_note=work.get("relevance_note"),
                    )
                )

            # Parse contact — Grok respects the email policy in the system prompt
            contact_raw = item.get("contact") or {}
            contact = ContactInfo(
                email=contact_raw.get("email"),
                twitter=contact_raw.get("twitter"),
                linkedin=contact_raw.get("linkedin"),
                website=contact_raw.get("website"),
            )

            # Stable ID: xai:<slug>
            raw_id = item.get("id", "")
            if not raw_id.startswith("xai:"):
                slug = (item.get("name", "unknown") + "_" + (item.get("outlet") or ""))
                slug = slug.lower().replace(" ", "_")[:40]
                raw_id = f"xai:{slug}"

            return MediaTarget(
                id=raw_id,
                target_type=target_type,
                name=item.get("name", "Unknown"),
                role=item.get("role"),
                outlet=item.get("outlet"),
                audience_size=_safe_int(item.get("audience_size")),
                audience_unit=item.get("audience_unit"),
                recent_work=recent_work,
                contact=contact,
                source=item.get("source", "xai_web_search"),
                enrichment_notes=["Discovered via Grok (xAI) live web search."],
            )
        except Exception as exc:
            logger.debug("[xai] failed to parse item: %s — %s", item.get("name"), exc)
            return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _target_types_string(target_type: TargetType) -> str:
    if target_type == TargetType.MIXED:
        return "podcasts, journalists, and publications"
    return target_type.value


def _audience_string(brief: ResearchBrief) -> str:
    ac = brief.audience_constraints
    parts: list[str] = []
    if ac.min_downloads:
        parts.append(f"min {ac.min_downloads:,} monthly downloads")
    if ac.max_downloads:
        parts.append(f"max {ac.max_downloads:,} monthly downloads")
    if ac.min_readers:
        parts.append(f"min {ac.min_readers:,} monthly readers")
    if ac.max_readers:
        parts.append(f"max {ac.max_readers:,} monthly readers")
    return ", ".join(parts) if parts else "none"


def _safe_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
