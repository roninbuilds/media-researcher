"""JSON output formatter."""
from __future__ import annotations

from ..models import ResearchReport


class JSONFormatter:
    def render(self, report: ResearchReport) -> str:
        return report.model_dump_json(indent=2)
