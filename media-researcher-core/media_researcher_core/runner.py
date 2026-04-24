"""Main orchestration: discovery → enrichment → scoring → report.

Enricher priority:
  1. XAIEnricher  — when XAI_API_KEY is set (real-time Grok web search; preferred)
  2. Enricher     — when ANTHROPIC_API_KEY is set and XAI_API_KEY is absent
  3. No enrichment — light depth only if neither key is set
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from .cache import EnrichmentCache
from .config import Config, ScoringWeights
from .discovery import JournalistDiscoverer, PodcastDiscoverer, PublicationDiscoverer, XAIDiscoverer
from .enrichment import Enricher, XAIEnricher
from .models import (
    MediaTarget,
    PersonalizationDepth,
    ResearchBrief,
    ResearchReport,
    TargetType,
)
from .scoring import Scorer

logger = logging.getLogger(__name__)


async def run_research(
    brief: ResearchBrief,
    config: Optional[Config] = None,
) -> ResearchReport:
    """
    Full research pipeline:
      1. Discover candidates from all relevant sources (xAI + specialist APIs in parallel).
      2. Deduplicate by target ID.
      3. Enrich each target to the requested depth (xAI preferred; Claude fallback).
      4. Score and rank.
      5. Return a ResearchReport.
    """
    if config is None:
        config = Config()

    cache = EnrichmentCache(config)
    available = config.available_sources()
    limitations: list[str] = []

    # ── 1. Discovery ──────────────────────────────────────────────────────
    discovery_tasks = []

    # xAI (Grok) — runs for ALL target types when key is set; real-time web search
    if available["xai"]:
        discovery_tasks.append(XAIDiscoverer(config, cache).discover(brief))
        logger.info("xAI (Grok) discovery enabled — %s", config.xai_model)
    else:
        limitations.append(
            "XAI_API_KEY not set — Grok live-search discovery disabled. "
            "Set the key for real-time web-powered target discovery."
        )

    # Specialist APIs — supplement xAI with structured database sources
    if brief.target_type in (TargetType.PODCASTS, TargetType.MIXED):
        discovery_tasks.append(PodcastDiscoverer(config, cache).discover(brief))
        if not available["listen_notes"]:
            limitations.append(
                "LISTEN_NOTES_API_KEY not set — Listen Notes podcast discovery disabled."
            )

    if brief.target_type in (TargetType.JOURNALISTS, TargetType.MIXED):
        discovery_tasks.append(JournalistDiscoverer(config, cache).discover(brief))
        if not available["muck_rack"] and not available["apollo"]:
            limitations.append(
                "Neither MUCK_RACK_API_KEY nor APOLLO_API_KEY set — "
                "journalist database discovery disabled."
            )
        elif not available["muck_rack"]:
            limitations.append(
                "MUCK_RACK_API_KEY not set — journalist discovery uses Apollo.io "
                "(no byline history)."
            )

    if brief.target_type in (TargetType.PUBLICATIONS, TargetType.MIXED):
        discovery_tasks.append(PublicationDiscoverer(config, cache).discover(brief))

    if not discovery_tasks:
        limitations.append("No discovery sources available. Set at least XAI_API_KEY.")

    # Run all discoverers in parallel
    raw_targets: list[MediaTarget] = []
    results = await asyncio.gather(*discovery_tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.error("Discovery task failed: %s", result)
            limitations.append(f"A discovery source failed: {result}")
        else:
            raw_targets.extend(result)

    # ── 2. Deduplicate by ID ──────────────────────────────────────────────
    # xAI may surface targets that also appear in specialist APIs.
    # Keep the richer version (most recent_work wins).
    seen: dict[str, MediaTarget] = {}
    for t in raw_targets:
        if t.id not in seen or len(t.recent_work) > len(seen[t.id].recent_work):
            seen[t.id] = t
    targets = list(seen.values())
    logger.info("Candidates after dedup: %d (from %d raw)", len(targets), len(raw_targets))

    # ── 3. Enrichment ─────────────────────────────────────────────────────
    xai_enricher = XAIEnricher(config, cache)
    claude_enricher = Enricher(config, cache)

    if brief.depth != PersonalizationDepth.LIGHT:
        if not xai_enricher.available and not available["claude_enrichment"]:
            limitations.append(
                f"Neither XAI_API_KEY nor ANTHROPIC_API_KEY set — "
                f"{brief.depth.value} enrichment unavailable. "
                "Results contain discovery data only."
            )
        elif xai_enricher.available:
            logger.info("Using xAI (Grok) for enrichment at depth=%s", brief.depth.value)
        else:
            logger.info("Using Claude for enrichment at depth=%s", brief.depth.value)

    enriched: list[MediaTarget] = []
    for target in targets:
        try:
            if brief.depth == PersonalizationDepth.LIGHT:
                # No enrichment needed; xAI discovery already included recent_work
                enriched.append(target)
            elif xai_enricher.available:
                e = await xai_enricher.enrich(target, brief, brief.depth)
                enriched.append(e)
            else:
                e = await claude_enricher.enrich(target, brief, brief.depth)
                enriched.append(e)
        except Exception as exc:
            logger.error("Enrichment failed for %s: %s", target.id, exc)
            target.enrichment_notes.append(f"Enrichment error: {exc}")
            enriched.append(target)

    # ── 4. Scoring ────────────────────────────────────────────────────────
    scorer = Scorer(config.scoring_weights)
    ranked = scorer.score_and_rank(enriched, brief)
    final = ranked[: brief.num_results]

    return ResearchReport(
        brief=brief,
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        targets=final,
        limitations=limitations,
    )
