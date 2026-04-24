"""media-researcher-core: discover, enrich, score, and report on media targets."""
from .models import (
    ResearchBrief,
    ResearchReport,
    MediaTarget,
    TargetType,
    PersonalizationDepth,
    OutputFormat,
)
from .runner import run_research

__all__ = [
    "ResearchBrief",
    "ResearchReport",
    "MediaTarget",
    "TargetType",
    "PersonalizationDepth",
    "OutputFormat",
    "run_research",
]
