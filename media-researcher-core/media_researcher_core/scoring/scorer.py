"""Composite scoring and ranking of media targets.

Scoring Formula (weights configurable via env vars — see config.py):

    composite = (
        w_topical_fit       * topical_fit_score         +
        w_audience          * audience_score             +
        w_recency           * recency_score              +
        w_response_likelihood * response_likelihood_score
    )

Sub-score details:
  topical_fit_score (0–1):
      Keyword overlap between the brief topic and the target's name, role, outlet,
      and recent work titles. Simple TF-IDF-style overlap (no external API needed).

  audience_score (0–1):
      Log-normalized audience size within the observed range.
      Missing audience_size defaults to 0.3 (unknown, neither rewarded nor penalised).

  recency_score (0–1):
      Fraction of recent_work items within the brief's recency window.
      Full score if most recent item is < 14 days old.

  response_likelihood_score (0–1):
      Heuristic:
        - Podcasts with < 50k audience size: +0.2 bonus (smaller = more approachable)
        - Journalists with a recent article on the EXACT topic: +0.3 bonus
        - Publications: baseline 0.5 (always harder to get a response directly)
        - Decays linearly with audience size above 1M.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone, timedelta

from ..config import ScoringWeights
from ..models import MediaTarget, ResearchBrief, TargetType


class Scorer:
    """Scores and sorts MediaTarget objects in-place."""

    def __init__(self, weights: ScoringWeights) -> None:
        weights.validate()
        self.weights = weights

    def score_and_rank(
        self, targets: list[MediaTarget], brief: ResearchBrief
    ) -> list[MediaTarget]:
        topic_keywords = _tokenize(brief.topic)
        audience_sizes = [t.audience_size for t in targets if t.audience_size is not None]
        max_audience = max(audience_sizes, default=1)
        min_audience = min(audience_sizes, default=1)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=brief.recency_days)

        for target in targets:
            target.topical_fit_score = self._topical_fit(target, topic_keywords)
            target.audience_score = self._audience(target, min_audience, max_audience)
            target.recency_score = self._recency(target, cutoff)
            target.response_likelihood_score = self._response_likelihood(target, topic_keywords)
            target.composite_score = (
                self.weights.topical_fit * target.topical_fit_score
                + self.weights.audience * target.audience_score
                + self.weights.recency * target.recency_score
                + self.weights.response_likelihood * target.response_likelihood_score
            )

        targets.sort(key=lambda t: t.composite_score, reverse=True)
        return targets

    # ── Sub-scorers ───────────────────────────────────────────────────────

    @staticmethod
    def _topical_fit(target: MediaTarget, keywords: set[str]) -> float:
        if not keywords:
            return 0.5
        candidate_text = " ".join(
            filter(
                None,
                [
                    target.name,
                    target.role,
                    target.outlet,
                    *(w.title for w in target.recent_work),
                    *(w.relevance_note or "" for w in target.recent_work),
                ],
            )
        )
        candidate_tokens = _tokenize(candidate_text)
        if not candidate_tokens:
            return 0.0
        overlap = len(keywords & candidate_tokens)
        return min(overlap / len(keywords), 1.0)

    @staticmethod
    def _audience(target: MediaTarget, min_size: int, max_size: int) -> float:
        if target.audience_size is None:
            return 0.3  # unknown audience — neutral
        if max_size == min_size:
            return 0.5
        # Log-normalise to 0–1
        log_val = math.log1p(target.audience_size)
        log_min = math.log1p(min_size)
        log_max = math.log1p(max_size)
        if log_max == log_min:
            return 0.5
        return (log_val - log_min) / (log_max - log_min)

    @staticmethod
    def _recency(target: MediaTarget, cutoff: datetime) -> float:
        if not target.recent_work:
            return 0.0
        dated = [w for w in target.recent_work if w.date is not None]
        if not dated:
            return 0.2  # unknown dates — low but not zero
        most_recent = max(dated, key=lambda w: w.date)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Ensure both datetimes are naive for comparison
        most_recent_date = most_recent.date
        if most_recent_date.tzinfo is not None:
            most_recent_date = most_recent_date.replace(tzinfo=None)
        age_days = (now - most_recent_date).days
        if age_days <= 14:
            return 1.0
        if age_days <= 30:
            return 0.8
        if age_days <= 60:
            return 0.6
        if age_days <= 90:
            return 0.4
        return 0.1

    @staticmethod
    def _response_likelihood(target: MediaTarget, keywords: set[str]) -> float:
        base = 0.5
        audience = target.audience_size or 0

        if target.target_type == TargetType.PODCASTS:
            if audience < 10_000:
                base = 0.85
            elif audience < 50_000:
                base = 0.75
            elif audience < 200_000:
                base = 0.6
            else:
                base = 0.4

        elif target.target_type == TargetType.JOURNALISTS:
            # Bonus for recent article on the exact topic
            recent_titles = " ".join(w.title for w in target.recent_work)
            topic_hit = len(keywords & _tokenize(recent_titles)) / max(len(keywords), 1)
            base = min(0.5 + topic_hit * 0.4, 0.9)

        elif target.target_type == TargetType.PUBLICATIONS:
            base = 0.3  # Publications are harder; editorial pitches are slower

        return base


# ── Helpers ───────────────────────────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "it", "its", "this", "that", "as", "i", "we",
}


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 2}
