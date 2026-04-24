"""Default Markdown report formatter."""
from __future__ import annotations

from datetime import datetime

from ..models import MediaTarget, PersonalizationDepth, ResearchReport, TargetType

_TYPE_EMOJI = {
    TargetType.PODCASTS: "🎙️",
    TargetType.JOURNALISTS: "✍️",
    TargetType.PUBLICATIONS: "📰",
    TargetType.MIXED: "🔀",
}


class MarkdownFormatter:
    def render(self, report: ResearchReport) -> str:
        brief = report.brief
        sections: list[str] = []

        # Header
        sections.append(
            f"# Media Research Report\n"
            f"**Topic:** {brief.topic}  \n"
            f"**Target types:** {brief.target_type.value}  \n"
            f"**Depth:** {brief.depth.value}  \n"
            f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  \n"
        )

        # Limitations banner
        if report.limitations:
            lim_lines = "\n".join(f"- {l}" for l in report.limitations)
            sections.append(
                f"> **Research Limitations**\n>\n"
                + "\n".join(f"> {l}" for l in lim_lines.split("\n"))
            )

        # ⚠️ Critical notice
        sections.append(
            "> **Next step:** Run `media-researcher outreach --report <this-file.json>` "
            "to send contact requests via TinyFish. You will see a preview and must "
            "type `SEND` to confirm before anything is submitted."
        )

        # Summary table (top 10)
        top = report.targets[:10]
        if top:
            headers = "| # | Name | Outlet | Type | Score | Audience |"
            divider = "|---|------|--------|------|-------|----------|"
            rows = []
            for i, t in enumerate(top, 1):
                audience = (
                    f"{t.audience_size:,} {t.audience_unit}"
                    if t.audience_size
                    else "unknown"
                )
                rows.append(
                    f"| {i} | {t.name} | {t.outlet or '—'} "
                    f"| {_TYPE_EMOJI.get(t.target_type, '')} {t.target_type.value} "
                    f"| {t.composite_score:.2f} | {audience} |"
                )
            sections.append(
                "## Top Results\n\n" + headers + "\n" + divider + "\n" + "\n".join(rows)
            )

        # Per-target sections
        sections.append("---\n\n## Target Profiles")
        for i, target in enumerate(report.targets, 1):
            sections.append(self._render_target(i, target, brief.depth))

        return "\n\n".join(sections)

    @staticmethod
    def _render_target(idx: int, t: MediaTarget, depth: PersonalizationDepth) -> str:
        lines: list[str] = [
            f"### {idx}. {t.name}",
            f"**Type:** {t.target_type.value} | **Outlet:** {t.outlet or '—'} | "
            f"**Role:** {t.role or '—'}  ",
            f"**Composite Score:** {t.composite_score:.2f}  ",
        ]

        # Scores breakdown
        lines.append(
            f"_Topical fit: {t.topical_fit_score:.2f} | "
            f"Audience: {t.audience_score:.2f} | "
            f"Recency: {t.recency_score:.2f} | "
            f"Response likelihood: {t.response_likelihood_score:.2f}_"
        )

        # Audience
        if t.audience_size:
            lines.append(f"**Audience:** {t.audience_size:,} {t.audience_unit or ''}")

        # Contact
        contact_parts: list[str] = []
        if t.contact.email:
            contact_parts.append(f"Email: {t.contact.email}")
        if t.contact.twitter:
            contact_parts.append(f"Twitter: [{t.contact.twitter}]({t.contact.twitter})")
        if t.contact.linkedin:
            contact_parts.append(f"LinkedIn: [{t.contact.linkedin}]({t.contact.linkedin})")
        if t.contact.website:
            contact_parts.append(f"Web: [{t.contact.website}]({t.contact.website})")
        if contact_parts:
            lines.append("**Contact:** " + " | ".join(contact_parts))

        # Recent work (medium + deep)
        if depth in (PersonalizationDepth.MEDIUM, PersonalizationDepth.DEEP) and t.recent_work:
            lines.append("\n**Recent Relevant Work:**")
            for w in t.recent_work[:5]:
                date_str = w.date.strftime("%Y-%m-%d") if w.date else "date unknown"
                url_part = f" — [{w.url}]({w.url})" if w.url else ""
                note = f" _{w.relevance_note}_" if w.relevance_note else ""
                lines.append(f"- {w.title} ({date_str}){url_part}{note}")

        # Pitch angle (deep only)
        if depth == PersonalizationDepth.DEEP and t.pitch_angle:
            lines.append(f"\n**Suggested Pitch Angle:**\n> {t.pitch_angle}")

        # Notes / limitations
        if t.enrichment_notes:
            notes = "\n".join(f"  - {n}" for n in t.enrichment_notes)
            lines.append(f"\n<details><summary>Research notes</summary>\n\n{notes}\n\n</details>")

        return "\n".join(lines)
