"""Brief loading, validation, and interactive elicitation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Prompt, IntPrompt, Confirm

from .models import (
    AudienceConstraints,
    PersonalizationDepth,
    ResearchBrief,
    TargetType,
)

console = Console()


# ── File-based loading ────────────────────────────────────────────────────────

def load_brief_from_file(path: str | Path) -> ResearchBrief:
    """Load a brief from a YAML or JSON file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Brief file not found: {p}")

    raw: dict[str, Any]
    if p.suffix in (".yaml", ".yml"):
        raw = yaml.safe_load(p.read_text())
    elif p.suffix == ".json":
        raw = json.loads(p.read_text())
    else:
        raise ValueError(f"Unsupported brief format: {p.suffix}. Use .yaml or .json")

    try:
        return ResearchBrief(**raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid brief in {p}:\n{exc}") from exc


# ── Interactive elicitation ───────────────────────────────────────────────────

def interactive_brief() -> ResearchBrief:
    """Prompt the user interactively for all brief fields."""
    console.rule("[bold cyan]Media Researcher — Interactive Brief")

    # Target type
    type_choices = [t.value for t in TargetType]
    raw_type = Prompt.ask(
        "Target type",
        choices=type_choices,
        default="mixed",
    )
    target_type = TargetType(raw_type)

    # Topic
    topic = Prompt.ask("Topic / beat (free text)")
    if not topic.strip():
        raise ValueError("Topic cannot be empty.")

    # Audience constraints
    min_dl = _optional_int("Minimum audience size (downloads/readers per month) [leave blank to skip]")
    max_dl = _optional_int("Maximum audience size [leave blank to skip]")
    audience_constraints = AudienceConstraints(
        min_downloads=min_dl if target_type == TargetType.PODCASTS else None,
        max_downloads=max_dl if target_type == TargetType.PODCASTS else None,
        min_readers=min_dl if target_type != TargetType.PODCASTS else None,
        max_readers=max_dl if target_type != TargetType.PODCASTS else None,
    )

    # Recency
    recency_days = IntPrompt.ask("Only include targets active in the last N days", default=90)

    # Geo / language
    geo = Prompt.ask("Geographic / language filter [leave blank for any]", default="")
    language = Prompt.ask("Language code [e.g. en, leave blank for any]", default="")

    # Results
    num_results = IntPrompt.ask("Number of results desired", default=20)

    # Depth
    depth_choices = [d.value for d in PersonalizationDepth]
    raw_depth = Prompt.ask(
        "Personalization depth",
        choices=depth_choices,
        default="medium",
    )

    # Extra notes
    extra = Prompt.ask("Any extra notes for the researcher? [leave blank to skip]", default="")

    return ResearchBrief(
        target_type=target_type,
        topic=topic,
        audience_constraints=audience_constraints,
        recency_days=recency_days,
        geo_filter=geo or None,
        language=language or None,
        num_results=num_results,
        depth=PersonalizationDepth(raw_depth),
        extra_notes=extra or None,
    )


# ── Utilities ─────────────────────────────────────────────────────────────────

def brief_hash(brief: ResearchBrief) -> str:
    """Stable hash of the brief for cache keying (excludes depth and num_results)."""
    key = json.dumps(
        {
            "target_type": brief.target_type.value,
            "topic": brief.topic,
            "recency_days": brief.recency_days,
            "geo_filter": brief.geo_filter,
            "language": brief.language,
        },
        sort_keys=True,
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _optional_int(prompt: str) -> Optional[int]:
    raw = Prompt.ask(prompt, default="")
    if raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            console.print("[yellow]Invalid number, skipping.[/yellow]")
    return None
