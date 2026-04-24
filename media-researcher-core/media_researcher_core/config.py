"""Configuration — all secrets from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScoringWeights:
    """Configurable weights for the composite scoring formula.

    All weights must sum to 1.0.  Adjust via env vars:
        SCORER_WEIGHT_TOPICAL_FIT        (default 0.35)
        SCORER_WEIGHT_AUDIENCE           (default 0.25)
        SCORER_WEIGHT_RECENCY            (default 0.20)
        SCORER_WEIGHT_RESPONSE_LIKELIHOOD(default 0.20)
    """
    topical_fit: float = field(
        default_factory=lambda: float(os.environ.get("SCORER_WEIGHT_TOPICAL_FIT", "0.35"))
    )
    audience: float = field(
        default_factory=lambda: float(os.environ.get("SCORER_WEIGHT_AUDIENCE", "0.25"))
    )
    recency: float = field(
        default_factory=lambda: float(os.environ.get("SCORER_WEIGHT_RECENCY", "0.20"))
    )
    response_likelihood: float = field(
        default_factory=lambda: float(os.environ.get("SCORER_WEIGHT_RESPONSE_LIKELIHOOD", "0.20"))
    )

    def validate(self) -> None:
        total = self.topical_fit + self.audience + self.recency + self.response_likelihood
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"Scoring weights must sum to 1.0, got {total:.3f}. "
                "Check SCORER_WEIGHT_* environment variables."
            )


@dataclass
class Config:
    # ── API keys (all optional — skill degrades gracefully) ───────────────
    listen_notes_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("LISTEN_NOTES_API_KEY")
    )
    muck_rack_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("MUCK_RACK_API_KEY")
    )
    apollo_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("APOLLO_API_KEY")
    )
    anthropic_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY")
    )
    xai_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("XAI_API_KEY")
    )
    xai_model: str = field(
        default_factory=lambda: os.environ.get("XAI_MODEL", "grok-3-latest")
    )
    tinyfish_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("TINYFISH_API_KEY")
    )
    notion_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("NOTION_API_KEY")
    )
    notion_database_id: Optional[str] = field(
        default_factory=lambda: os.environ.get("NOTION_DATABASE_ID")
    )

    # ── Cache ─────────────────────────────────────────────────────────────
    cache_dir: str = field(
        default_factory=lambda: os.environ.get(
            "MEDIA_RESEARCHER_CACHE_DIR",
            os.path.expanduser("~/.cache/media-researcher"),
        )
    )
    cache_ttl_seconds: int = field(
        default_factory=lambda: int(
            os.environ.get("MEDIA_RESEARCHER_CACHE_TTL", str(7 * 24 * 3600))
        )
    )

    # ── Output ────────────────────────────────────────────────────────────
    output_dir: str = field(
        default_factory=lambda: os.environ.get(
            "MEDIA_RESEARCHER_OUTPUT_DIR", "/mnt/user-data/outputs"
        )
    )

    # ── Claude model ──────────────────────────────────────────────────────
    claude_model: str = field(
        default_factory=lambda: os.environ.get(
            "MEDIA_RESEARCHER_CLAUDE_MODEL", "claude-sonnet-4-6"
        )
    )

    # ── Scoring weights ───────────────────────────────────────────────────
    scoring_weights: ScoringWeights = field(default_factory=ScoringWeights)

    def available_sources(self) -> dict[str, bool]:
        return {
            "listen_notes": bool(self.listen_notes_api_key),
            "muck_rack": bool(self.muck_rack_api_key),
            "apollo": bool(self.apollo_api_key),
            "xai": bool(self.xai_api_key),
            "claude_enrichment": bool(self.anthropic_api_key),
            "tinyfish_outreach": bool(self.tinyfish_api_key),
            "notion_output": bool(self.notion_api_key and self.notion_database_id),
        }
