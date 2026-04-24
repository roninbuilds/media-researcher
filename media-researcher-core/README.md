# media-researcher-core

Core Python package powering the `media-researcher` skill.

## Architecture

```
media_researcher_core/
├── __init__.py         — public API: run_research(), models
├── models.py           — Pydantic data models (ResearchBrief, MediaTarget, etc.)
├── config.py           — Config dataclass, all env var reads, scoring weights
├── cache.py            — 7-day diskcache wrapper
├── brief.py            — Brief loading (YAML/JSON) and interactive elicitation
├── runner.py           — Main orchestration pipeline
├── cli.py              — Click CLI entry point
├── discovery/
│   ├── base.py         — BaseDiscoverer ABC
│   ├── podcasts.py     — Listen Notes API
│   ├── journalists.py  — Muck Rack → Apollo.io fallback
│   └── publications.py — Curated topic-based lists
├── enrichment/
│   └── enricher.py     — Claude-powered recent work + pitch angle generation
├── scoring/
│   └── scorer.py       — Composite scoring and ranking
└── output/
    ├── markdown_formatter.py
    ├── json_formatter.py
    ├── csv_formatter.py
    └── notion_formatter.py
```

## Programmatic Usage

```python
import asyncio
from media_researcher_core import run_research
from media_researcher_core.models import ResearchBrief, TargetType, PersonalizationDepth

brief = ResearchBrief(
    target_type=TargetType.PODCASTS,
    topic="AI infrastructure, developer tools",
    num_results=10,
    depth=PersonalizationDepth.DEEP,
)

report = asyncio.run(run_research(brief))

for i, target in enumerate(report.targets, 1):
    print(f"{i}. {target.name} ({target.composite_score:.2f})")
    if target.pitch_angle:
        print(f"   Pitch: {target.pitch_angle}")
```
