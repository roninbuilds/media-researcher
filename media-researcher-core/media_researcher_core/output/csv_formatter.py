"""CSV output formatter (flat, one row per target)."""
from __future__ import annotations

import csv
import io

from ..models import ResearchReport


_FIELDS = [
    "rank",
    "name",
    "target_type",
    "role",
    "outlet",
    "composite_score",
    "topical_fit_score",
    "audience_score",
    "recency_score",
    "response_likelihood_score",
    "audience_size",
    "audience_unit",
    "email",
    "twitter",
    "linkedin",
    "website",
    "recent_work_titles",
    "pitch_angle",
    "source",
    "enrichment_notes",
]


class CSVFormatter:
    def render(self, report: ResearchReport) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_FIELDS)
        writer.writeheader()
        for i, t in enumerate(report.targets, 1):
            writer.writerow(
                {
                    "rank": i,
                    "name": t.name,
                    "target_type": t.target_type.value,
                    "role": t.role or "",
                    "outlet": t.outlet or "",
                    "composite_score": round(t.composite_score, 4),
                    "topical_fit_score": round(t.topical_fit_score, 4),
                    "audience_score": round(t.audience_score, 4),
                    "recency_score": round(t.recency_score, 4),
                    "response_likelihood_score": round(t.response_likelihood_score, 4),
                    "audience_size": t.audience_size or "",
                    "audience_unit": t.audience_unit or "",
                    "email": t.contact.email or "",
                    "twitter": t.contact.twitter or "",
                    "linkedin": t.contact.linkedin or "",
                    "website": t.contact.website or "",
                    "recent_work_titles": "; ".join(w.title for w in t.recent_work),
                    "pitch_angle": t.pitch_angle or "",
                    "source": t.source or "",
                    "enrichment_notes": " | ".join(t.enrichment_notes),
                }
            )
        return buf.getvalue()
