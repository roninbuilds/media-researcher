"""TinyFish-powered outreach: fills contact forms on target websites.

IMPORTANT — Human confirmation is REQUIRED before any message is sent.
This module never sends autonomously. The CLI gate (or explicit API call to
`confirm=True`) is the only path to actual submission.

TinyFish API docs: https://docs.tinyfish.ai
Auth header: X-API-Key
Endpoint: POST https://agent.tinyfish.ai/v1/automation/run
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import MediaTarget, ResearchReport

logger = logging.getLogger(__name__)

TINYFISH_RUN_URL = "https://agent.tinyfish.ai/v1/automation/run"
TINYFISH_RUN_ASYNC_URL = "https://agent.tinyfish.ai/v1/automation/run-async"

# Default delay between submissions — be respectful of target sites
DEFAULT_DELAY_SECONDS = 10

# ── Data models ───────────────────────────────────────────────────────────────

class OutreachStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    SKIPPED = "skipped"      # no contact URL available
    FAILED = "failed"
    NO_FORM = "no_form"      # TinyFish navigated but found no contact form


@dataclass
class OutreachConfig:
    """Sender identity and message template.

    All fields can be set via constructor or env vars:
        OUTREACH_SENDER_NAME
        OUTREACH_SENDER_EMAIL
        OUTREACH_SENDER_COMPANY
        OUTREACH_SUBJECT_TEMPLATE  (may contain {target_name}, {outlet})
        OUTREACH_BODY_TEMPLATE     (may contain {target_name}, {outlet}, {pitch_angle})
        OUTREACH_DELAY_SECONDS     (default: 10)
    """
    sender_name: str = field(
        default_factory=lambda: os.environ.get("OUTREACH_SENDER_NAME", "")
    )
    sender_email: str = field(
        default_factory=lambda: os.environ.get("OUTREACH_SENDER_EMAIL", "")
    )
    sender_company: str = field(
        default_factory=lambda: os.environ.get("OUTREACH_SENDER_COMPANY", "")
    )
    subject_template: str = field(
        default_factory=lambda: os.environ.get(
            "OUTREACH_SUBJECT_TEMPLATE",
            "Podcast/media pitch from {sender_name} ({sender_company})",
        )
    )
    body_template: str = field(
        default_factory=lambda: os.environ.get(
            "OUTREACH_BODY_TEMPLATE",
            _DEFAULT_BODY_TEMPLATE,
        )
    )
    delay_seconds: int = field(
        default_factory=lambda: int(
            os.environ.get("OUTREACH_DELAY_SECONDS", str(DEFAULT_DELAY_SECONDS))
        )
    )

    def validate(self) -> None:
        if not self.sender_name:
            raise ValueError("OUTREACH_SENDER_NAME must be set.")
        if not self.sender_email:
            raise ValueError("OUTREACH_SENDER_EMAIL must be set.")

    def render_subject(self, target: MediaTarget) -> str:
        return self.subject_template.format(
            target_name=target.name,
            outlet=target.outlet or target.name,
            sender_name=self.sender_name,
            sender_company=self.sender_company,
        )

    def render_body(self, target: MediaTarget) -> str:
        pitch = target.pitch_angle or (
            f"I'd love to discuss {target.outlet or 'your work'} and how I might "
            "be able to contribute value for your audience."
        )
        return self.body_template.format(
            target_name=target.name,
            outlet=target.outlet or "your outlet",
            pitch_angle=pitch,
            sender_name=self.sender_name,
            sender_email=self.sender_email,
            sender_company=self.sender_company,
        )


_DEFAULT_BODY_TEMPLATE = """\
Hi {target_name},

{pitch_angle}

I'd love to connect. Happy to provide more context, a one-pager, or a call whenever works for you.

Best,
{sender_name}
{sender_company}
{sender_email}
"""


@dataclass
class OutreachResult:
    target_id: str
    target_name: str
    outlet: Optional[str]
    contact_url: Optional[str]
    status: OutreachStatus
    tinyfish_response: Optional[dict] = None
    error: Optional[str] = None
    sent_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "target_name": self.target_name,
            "outlet": self.outlet,
            "contact_url": self.contact_url,
            "status": self.status.value,
            "error": self.error,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }


# ── Sender ────────────────────────────────────────────────────────────────────

class TinyFishSender:
    """
    Sends contact requests to media targets via TinyFish web agent.

    Workflow:
      1. For each target, resolve the best contact URL (website > twitter > linkedin)
      2. Build a goal string describing what to fill in the contact form
      3. Call TinyFish /run endpoint (synchronous; waits for completion)
      4. Log result

    Requires explicit `confirm=True` to actually send — passing False is a dry-run.
    """

    def __init__(self, api_key: str, outreach_config: OutreachConfig) -> None:
        self.api_key = api_key
        self.cfg = outreach_config

    async def send_all(
        self,
        report: ResearchReport,
        target_indices: Optional[list[int]] = None,
        confirm: bool = False,
        log_path: Optional[str] = None,
    ) -> list[OutreachResult]:
        """
        Send contact requests to targets in the report.

        Args:
            report:          Research report from run_research().
            target_indices:  1-based list of targets to contact. None = all.
            confirm:         Must be True to actually send. False = dry-run preview only.
            log_path:        Optional path to write a JSON outreach log.

        Returns:
            List of OutreachResult objects (one per target attempted).
        """
        self.cfg.validate()

        targets = report.targets
        if target_indices:
            targets = [
                report.targets[i - 1]
                for i in target_indices
                if 1 <= i <= len(report.targets)
            ]

        results: list[OutreachResult] = []

        for i, target in enumerate(targets):
            contact_url = _best_contact_url(target)

            if not contact_url:
                results.append(
                    OutreachResult(
                        target_id=target.id,
                        target_name=target.name,
                        outlet=target.outlet,
                        contact_url=None,
                        status=OutreachStatus.SKIPPED,
                        error="No contact URL available (no website, Twitter, or LinkedIn).",
                    )
                )
                logger.info("[outreach] SKIP %s — no contact URL", target.name)
                continue

            subject = self.cfg.render_subject(target)
            body = self.cfg.render_body(target)

            if not confirm:
                # Dry run — log but don't send
                results.append(
                    OutreachResult(
                        target_id=target.id,
                        target_name=target.name,
                        outlet=target.outlet,
                        contact_url=contact_url,
                        status=OutreachStatus.PENDING,
                    )
                )
                logger.info("[outreach] DRY RUN %s → %s", target.name, contact_url)
                continue

            # Live send
            result = await self._send_one(target, contact_url, subject, body)
            results.append(result)

            # Respect rate limits — delay between submissions
            if i < len(targets) - 1:
                logger.info(
                    "[outreach] waiting %ds before next submission…",
                    self.cfg.delay_seconds,
                )
                await asyncio.sleep(self.cfg.delay_seconds)

        if log_path:
            self._write_log(results, log_path)

        return results

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=5, max=30))
    async def _send_one(
        self,
        target: MediaTarget,
        contact_url: str,
        subject: str,
        body: str,
    ) -> OutreachResult:
        goal = _build_goal(
            contact_url=contact_url,
            sender_name=self.cfg.sender_name,
            sender_email=self.cfg.sender_email,
            subject=subject,
            body=body,
        )

        payload = {
            "url": contact_url,
            "goal": goal,
            "browser_profile": "stealth",
            "agent_config": {"mode": "default", "max_steps": 60},
        }

        logger.info("[outreach] SEND %s → %s", target.name, contact_url)

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    TINYFISH_RUN_URL,
                    json=payload,
                    headers={
                        "X-API-Key": self.api_key,
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            # Detect "no form found" signal from TinyFish
            output_text = str(data.get("output") or data.get("result") or "").lower()
            if "no_form_found" in output_text or "no contact form" in output_text:
                status = OutreachStatus.NO_FORM
            else:
                status = OutreachStatus.SENT

            return OutreachResult(
                target_id=target.id,
                target_name=target.name,
                outlet=target.outlet,
                contact_url=contact_url,
                status=status,
                tinyfish_response=data,
                sent_at=datetime.now(timezone.utc),
            )

        except httpx.HTTPStatusError as exc:
            logger.error("[outreach] HTTP error for %s: %s", target.name, exc)
            return OutreachResult(
                target_id=target.id,
                target_name=target.name,
                outlet=target.outlet,
                contact_url=contact_url,
                status=OutreachStatus.FAILED,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except Exception as exc:
            logger.error("[outreach] Unexpected error for %s: %s", target.name, exc)
            return OutreachResult(
                target_id=target.id,
                target_name=target.name,
                outlet=target.outlet,
                contact_url=contact_url,
                status=OutreachStatus.FAILED,
                error=str(exc),
            )

    @staticmethod
    def _write_log(results: list[OutreachResult], path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        records = [r.to_dict() for r in results]
        Path(path).write_text(json.dumps(records, indent=2), encoding="utf-8")
        logger.info("[outreach] log written to %s", path)


# ── Goal builder ──────────────────────────────────────────────────────────────

def _build_goal(
    contact_url: str,
    sender_name: str,
    sender_email: str,
    subject: str,
    body: str,
) -> str:
    """
    Build a natural-language goal for the TinyFish agent.
    The agent will navigate to the URL and fill whatever contact/pitch form it finds.
    """
    # Truncate body to avoid goal becoming too long for the agent
    body_truncated = body[:1200] if len(body) > 1200 else body

    return (
        f"Go to {contact_url} and find the contact page, pitch submission form, "
        f"or inquiry form. "
        f"Fill it with the following information:\n"
        f"  Name: {sender_name}\n"
        f"  Email: {sender_email}\n"
        f"  Subject (if the form has a subject field): {subject}\n"
        f"  Message / Body:\n{body_truncated}\n\n"
        f"Submit the form once all fields are filled. "
        f"If you cannot find any contact form on the site, respond with exactly "
        f"'no_form_found' and do nothing else. "
        f"Do not navigate away from the domain. "
        f"Do not subscribe to newsletters or create accounts."
    )


def _best_contact_url(target: MediaTarget) -> Optional[str]:
    """Return the most appropriate URL to send TinyFish to for this target."""
    # Website contact form is ideal — TinyFish can find the /contact page
    if target.contact.website:
        return target.contact.website
    # Twitter DM profile (TinyFish can navigate there; user may send a DM manually)
    if target.contact.twitter:
        return target.contact.twitter
    # LinkedIn as last resort
    if target.contact.linkedin:
        return target.contact.linkedin
    return None
