"""Shared data models for media-researcher-core."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


# ── Enums ────────────────────────────────────────────────────────────────────

class TargetType(str, Enum):
    PODCASTS = "podcasts"
    JOURNALISTS = "journalists"
    PUBLICATIONS = "publications"
    MIXED = "mixed"


class PersonalizationDepth(str, Enum):
    LIGHT = "light"    # contact info only
    MEDIUM = "medium"  # recent work summary
    DEEP = "deep"      # tailored pitch angle


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"
    NOTION = "notion"


# ── Brief ────────────────────────────────────────────────────────────────────

class AudienceConstraints(BaseModel):
    min_downloads: Optional[int] = None
    max_downloads: Optional[int] = None
    min_readers: Optional[int] = None
    max_readers: Optional[int] = None


class ResearchBrief(BaseModel):
    target_type: TargetType
    topic: str = Field(..., description="Free-text topic/beat, e.g. 'AI infrastructure, developer tools'")
    audience_constraints: AudienceConstraints = Field(default_factory=AudienceConstraints)
    recency_days: int = Field(default=90, description="Only include targets active in last N days")
    geo_filter: Optional[str] = Field(default=None, description="e.g. 'US', 'English-language'")
    language: Optional[str] = Field(default=None, description="e.g. 'en'")
    num_results: int = Field(default=20, ge=1, le=100)
    depth: PersonalizationDepth = PersonalizationDepth.MEDIUM
    extra_notes: Optional[str] = None


# ── Target ───────────────────────────────────────────────────────────────────

class RecentWork(BaseModel):
    title: str
    url: Optional[str] = None
    date: Optional[datetime] = None
    summary: Optional[str] = None
    relevance_note: Optional[str] = None  # why this piece matters to the brief


class ContactInfo(BaseModel):
    email: Optional[str] = Field(
        default=None,
        description="Only publicly listed email from the target's own site or public bio.",
    )
    twitter: Optional[str] = None
    linkedin: Optional[str] = None
    website: Optional[str] = None


class MediaTarget(BaseModel):
    # Identity
    id: str = Field(..., description="Stable identifier, e.g. listennotes:{podcast_id}")
    target_type: TargetType
    name: str
    role: Optional[str] = None        # e.g. "Host", "Senior Reporter"
    outlet: Optional[str] = None      # publication or podcast network

    # Metrics
    audience_size: Optional[int] = None
    audience_unit: Optional[str] = None  # "monthly downloads", "monthly readers", etc.

    # Enrichment
    recent_work: list[RecentWork] = Field(default_factory=list)
    contact: ContactInfo = Field(default_factory=ContactInfo)

    # Deep personalization
    pitch_angle: Optional[str] = Field(
        default=None,
        description="[Deep only] Proposed angle connecting recent work to the brief topic.",
    )

    # Scoring
    topical_fit_score: float = 0.0    # 0–1
    audience_score: float = 0.0       # 0–1
    recency_score: float = 0.0        # 0–1
    response_likelihood_score: float = 0.0  # 0–1
    composite_score: float = 0.0      # weighted final score

    # Metadata
    source: Optional[str] = None      # which API/method discovered this target
    last_enriched: Optional[datetime] = None
    enrichment_notes: list[str] = Field(default_factory=list)  # warnings, limitations


# ── Report ───────────────────────────────────────────────────────────────────

class ResearchReport(BaseModel):
    brief: ResearchBrief
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    targets: list[MediaTarget] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=list,
        description="Notes on missing API keys, degraded sources, skipped data, etc.",
    )
