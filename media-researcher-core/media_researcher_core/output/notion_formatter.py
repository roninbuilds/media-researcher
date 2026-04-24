"""Notion output formatter — pushes targets as pages in a configured Notion database."""
from __future__ import annotations

import logging
from typing import Optional

from ..models import MediaTarget, ResearchReport

logger = logging.getLogger(__name__)

try:
    from notion_client import AsyncClient as NotionAsyncClient
    _NOTION_AVAILABLE = True
except ImportError:
    _NOTION_AVAILABLE = False


class NotionFormatter:
    """
    Writes a research report to a Notion database.

    Requires:
        NOTION_API_KEY       — Notion integration token
        NOTION_DATABASE_ID   — Target database ID (must already exist)

    Database must have these properties:
        Name (title), Type (select), Outlet (text), Score (number),
        Email (email), Twitter (url), LinkedIn (url), Website (url),
        Source (text), PitchAngle (text), Notes (text)
    """

    def __init__(self, api_key: Optional[str], database_id: Optional[str]) -> None:
        self.api_key = api_key
        self.database_id = database_id

    async def push(self, report: ResearchReport) -> list[str]:
        """Push all targets to Notion; return list of created page URLs."""
        if not _NOTION_AVAILABLE:
            raise RuntimeError(
                "notion-client package not installed. "
                "Run: pip install notion-client"
            )
        if not self.api_key or not self.database_id:
            raise RuntimeError(
                "NOTION_API_KEY and NOTION_DATABASE_ID must be set."
            )

        client = NotionAsyncClient(auth=self.api_key)
        page_urls: list[str] = []

        for target in report.targets:
            page = await self._create_page(client, target, report)
            url = page.get("url", "")
            if url:
                page_urls.append(url)
                logger.info("Notion page created: %s", url)

        return page_urls

    async def _create_page(self, client, target: MediaTarget, report: ResearchReport) -> dict:
        recent_titles = "\n".join(
            f"- {w.title} ({w.date.strftime('%Y-%m-%d') if w.date else '?'})"
            for w in target.recent_work[:5]
        )
        notes_text = " | ".join(target.enrichment_notes)

        properties = {
            "Name": {"title": [{"text": {"content": target.name}}]},
            "Type": {"select": {"name": target.target_type.value}},
            "Outlet": {"rich_text": [{"text": {"content": target.outlet or ""}}]},
            "Score": {"number": round(target.composite_score, 3)},
            "Source": {"rich_text": [{"text": {"content": target.source or ""}}]},
            "Notes": {"rich_text": [{"text": {"content": notes_text[:2000]}}]},
        }

        if target.contact.email:
            properties["Email"] = {"email": target.contact.email}
        if target.contact.twitter:
            properties["Twitter"] = {"url": target.contact.twitter}
        if target.contact.linkedin:
            properties["LinkedIn"] = {"url": target.contact.linkedin}
        if target.contact.website:
            properties["Website"] = {"url": target.contact.website}
        if target.pitch_angle:
            properties["PitchAngle"] = {
                "rich_text": [{"text": {"content": target.pitch_angle[:2000]}}]
            }

        return await client.pages.create(
            parent={"database_id": self.database_id},
            properties=properties,
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": f"Recent Work:\n{recent_titles}"}}
                        ]
                    },
                }
            ]
            if recent_titles
            else [],
        )

    def render(self, report: ResearchReport) -> str:
        """Synchronous render — returns a warning to use push() instead."""
        return (
            "Notion output requires async push(). "
            "Use `media-researcher run --format notion` via the CLI."
        )
