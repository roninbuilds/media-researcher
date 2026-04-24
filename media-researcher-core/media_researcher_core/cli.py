"""CLI entry point: `media-researcher run`."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .brief import interactive_brief, load_brief_from_file
from .config import Config
from .models import OutputFormat, PersonalizationDepth, ResearchReport
from .output import CSVFormatter, JSONFormatter, MarkdownFormatter, NotionFormatter
from .outreach import OutreachConfig, OutreachStatus, TinyFishSender
from .runner import run_research

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """media-researcher — produce prioritised media target lists from a research brief."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


@main.command()
@click.option("--brief", "-b", "brief_path", type=click.Path(exists=True), default=None,
              help="Path to YAML/JSON brief file.")
@click.option("--interactive", "-i", "interactive", is_flag=True, default=False,
              help="Prompt for brief fields interactively.")
@click.option("--out", "-o", "out_path", default=None,
              help="Output file path. Defaults to /mnt/user-data/outputs/<timestamp>.<ext>")
@click.option("--format", "-f", "fmt",
              type=click.Choice(["markdown", "json", "csv", "notion"], case_sensitive=False),
              default="markdown", show_default=True,
              help="Output format.")
@click.option("--depth", "-d",
              type=click.Choice(["light", "medium", "deep"], case_sensitive=False),
              default=None,
              help="Personalization depth (overrides brief if provided).")
@click.pass_context
def run(
    ctx: click.Context,
    brief_path: str | None,
    interactive: bool,
    out_path: str | None,
    fmt: str,
    depth: str | None,
) -> None:
    """Discover, enrich, score, and report on media targets from a research brief."""
    if not brief_path and not interactive:
        console.print(
            "[bold red]Error:[/bold red] Provide --brief <file> or use --interactive."
        )
        sys.exit(1)

    # Load brief
    try:
        if interactive:
            brief = interactive_brief()
        else:
            brief = load_brief_from_file(brief_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Brief error:[/bold red] {exc}")
        sys.exit(1)

    # Override depth from CLI if provided
    if depth:
        brief.depth = PersonalizationDepth(depth)

    config = Config()
    console.print(f"\n[bold cyan]Running research…[/bold cyan]")
    _show_source_status(config)

    # Run async pipeline
    report: ResearchReport = asyncio.run(run_research(brief, config))

    console.print(
        f"\n[green]Research complete:[/green] {len(report.targets)} targets found."
    )

    if report.limitations:
        console.print("\n[yellow]Limitations:[/yellow]")
        for lim in report.limitations:
            console.print(f"  • {lim}")

    # Format output
    output_format = OutputFormat(fmt.lower())
    rendered = _render(report, output_format, config)

    # Write output
    dest = _resolve_output_path(out_path, output_format, report)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)

    if output_format == OutputFormat.NOTION:
        console.print(f"[cyan]Pushing to Notion…[/cyan]")
        urls = asyncio.run(
            NotionFormatter(config.notion_api_key, config.notion_database_id).push(report)
        )
        console.print(f"[green]Created {len(urls)} Notion pages.[/green]")
        for url in urls:
            console.print(f"  {url}")
        return

    Path(dest).write_text(rendered, encoding="utf-8")
    console.print(f"\n[bold green]Report written to:[/bold green] {dest}")

    # Show preview of top 5
    _show_top5_preview(report)


@main.command()
@click.option("--report", "-r", "report_path", required=True,
              type=click.Path(exists=True),
              help="Path to a JSON research report (produced by `run --format json`).")
@click.option("--sender-name", envvar="OUTREACH_SENDER_NAME", default=None,
              help="Your full name (or set OUTREACH_SENDER_NAME).")
@click.option("--sender-email", envvar="OUTREACH_SENDER_EMAIL", default=None,
              help="Your reply-to email (or set OUTREACH_SENDER_EMAIL).")
@click.option("--sender-company", envvar="OUTREACH_SENDER_COMPANY", default="",
              help="Your company / show name (optional).")
@click.option("--targets", "-t", default=None,
              help="Comma-separated 1-based target numbers to contact, e.g. '1,3,5'. "
                   "Omit to contact all targets that have a contact URL.")
@click.option("--delay", default=10, show_default=True,
              help="Seconds to wait between submissions.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Preview what would be sent without actually submitting anything.")
@click.option("--log", "log_path", default=None,
              help="Path to write a JSON outreach log. Defaults to outputs/outreach_<ts>.json.")
@click.pass_context
def outreach(
    ctx: click.Context,
    report_path: str,
    sender_name: str | None,
    sender_email: str | None,
    sender_company: str,
    targets: str | None,
    delay: int,
    dry_run: bool,
    log_path: str | None,
) -> None:
    """Send contact requests to researched targets via TinyFish web agent.

    \b
    Requires:
      TINYFISH_API_KEY    — your TinyFish API key
      OUTREACH_SENDER_NAME / --sender-name
      OUTREACH_SENDER_EMAIL / --sender-email

    \b
    Always previews targets before sending. Add --dry-run to skip confirmation.
    """
    config = Config()

    if not config.tinyfish_api_key:
        console.print(
            "[bold red]Error:[/bold red] TINYFISH_API_KEY is not set.\n"
            "Run: export TINYFISH_API_KEY=<your-key>"
        )
        sys.exit(1)

    # Load report
    try:
        import json as _json
        report = ResearchReport.model_validate_json(Path(report_path).read_text())
    except Exception as exc:
        console.print(f"[bold red]Failed to load report:[/bold red] {exc}")
        console.print("Tip: run `media-researcher run --format json` to generate a JSON report.")
        sys.exit(1)

    # Build outreach config (CLI flags override env vars which override defaults)
    ocfg = OutreachConfig(delay_seconds=delay)
    if sender_name:
        ocfg.sender_name = sender_name
    if sender_email:
        ocfg.sender_email = sender_email
    if sender_company:
        ocfg.sender_company = sender_company

    try:
        ocfg.validate()
    except ValueError as exc:
        console.print(f"[bold red]Outreach config error:[/bold red] {exc}")
        sys.exit(1)

    # Parse target selection
    target_indices: list[int] | None = None
    if targets:
        try:
            target_indices = [int(x.strip()) for x in targets.split(",")]
        except ValueError:
            console.print("[bold red]--targets must be comma-separated integers, e.g. '1,3,5'[/bold red]")
            sys.exit(1)

    selected = (
        [report.targets[i - 1] for i in target_indices if 1 <= i <= len(report.targets)]
        if target_indices else report.targets
    )

    # Preview table
    console.rule("[bold cyan]Outreach Preview[/bold cyan]")
    console.print(
        f"Sender: [bold]{ocfg.sender_name}[/bold] <{ocfg.sender_email}>"
        + (f" / {ocfg.sender_company}" if ocfg.sender_company else "")
    )
    console.print(f"Report: {report_path}  ({len(report.targets)} total targets)\n")

    preview_table = Table(show_header=True, header_style="bold")
    preview_table.add_column("#", width=4)
    preview_table.add_column("Name")
    preview_table.add_column("Outlet")
    preview_table.add_column("Contact URL")
    preview_table.add_column("Has Pitch Angle")

    from .outreach.tinyfish_sender import _best_contact_url
    contactable = 0
    for i, t in enumerate(selected, 1):
        url = _best_contact_url(t)
        has_pitch = "[green]yes[/green]" if t.pitch_angle else "[dim]no[/dim]"
        url_display = url[:55] + "…" if url and len(url) > 55 else (url or "[red]none[/red]")
        if url:
            contactable += 1
        preview_table.add_row(str(i), t.name, t.outlet or "—", url_display, has_pitch)

    console.print(preview_table)
    console.print(
        f"\n[bold]{contactable}[/bold] of {len(selected)} targets have a contact URL."
    )

    if dry_run:
        console.print("\n[yellow]Dry run — nothing will be sent.[/yellow]")
        return

    if contactable == 0:
        console.print("[yellow]No contactable targets found. Nothing to send.[/yellow]")
        return

    # ── Explicit confirmation gate ────────────────────────────────────────
    console.print(
        "\n[bold yellow]⚠  This will submit contact forms on behalf of "
        f"{ocfg.sender_name} <{ocfg.sender_email}>.[/bold yellow]"
    )
    confirm_input = console.input(
        f"Type [bold]SEND[/bold] to confirm sending to {contactable} target(s), "
        "or anything else to cancel: "
    )
    if confirm_input.strip() != "SEND":
        console.print("[yellow]Cancelled. Nothing was sent.[/yellow]")
        return

    # ── Send ──────────────────────────────────────────────────────────────
    sender = TinyFishSender(config.tinyfish_api_key, ocfg)

    console.print(f"\n[cyan]Sending… ({delay}s delay between submissions)[/cyan]\n")
    results = asyncio.run(
        sender.send_all(
            report=report,
            target_indices=target_indices,
            confirm=True,
            log_path=log_path or _outreach_log_path(config),
        )
    )

    # Summary
    sent = sum(1 for r in results if r.status == OutreachStatus.SENT)
    no_form = sum(1 for r in results if r.status == OutreachStatus.NO_FORM)
    skipped = sum(1 for r in results if r.status == OutreachStatus.SKIPPED)
    failed = sum(1 for r in results if r.status == OutreachStatus.FAILED)

    console.rule("[bold green]Outreach Complete[/bold green]")
    console.print(f"  [green]Sent:[/green]       {sent}")
    console.print(f"  [yellow]No form found:[/yellow] {no_form}")
    console.print(f"  [dim]Skipped:[/dim]      {skipped}")
    if failed:
        console.print(f"  [red]Failed:[/red]       {failed}")

    log_dest = log_path or _outreach_log_path(config)
    console.print(f"\nLog: {log_dest}")


def _outreach_log_path(config: Config) -> str:
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(config.output_dir, f"outreach_{ts}.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _render(report: ResearchReport, fmt: OutputFormat, config: Config) -> str:
    if fmt == OutputFormat.MARKDOWN:
        return MarkdownFormatter().render(report)
    if fmt == OutputFormat.JSON:
        return JSONFormatter().render(report)
    if fmt == OutputFormat.CSV:
        return CSVFormatter().render(report)
    # Notion is handled separately (async push)
    return MarkdownFormatter().render(report)


def _resolve_output_path(
    out_path: str | None, fmt: OutputFormat, report: ResearchReport
) -> str:
    if out_path:
        return out_path
    ext_map = {
        OutputFormat.MARKDOWN: "md",
        OutputFormat.JSON: "json",
        OutputFormat.CSV: "csv",
        OutputFormat.NOTION: "md",
    }
    ts = report.generated_at.strftime("%Y%m%d_%H%M%S")
    filename = f"media-research_{ts}.{ext_map[fmt]}"
    output_dir = os.environ.get("MEDIA_RESEARCHER_OUTPUT_DIR", "/mnt/user-data/outputs")
    return os.path.join(output_dir, filename)


def _show_source_status(config: Config) -> None:
    sources = config.available_sources()
    table = Table(title="API Source Status", show_header=True)
    table.add_column("Source")
    table.add_column("Status")
    for source, available in sources.items():
        status = "[green]✓ configured[/green]" if available else "[yellow]✗ not set[/yellow]"
        table.add_row(source, status)
    console.print(table)


def _show_top5_preview(report: ResearchReport) -> None:
    if not report.targets:
        return
    console.print("\n[bold]Top 5 Targets:[/bold]")
    table = Table(show_header=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Name")
    table.add_column("Outlet")
    table.add_column("Type")
    table.add_column("Score", justify="right")
    for i, t in enumerate(report.targets[:5], 1):
        table.add_row(
            str(i),
            t.name,
            t.outlet or "—",
            t.target_type.value,
            f"{t.composite_score:.2f}",
        )
    console.print(table)
    remaining = len(report.targets) - 5
    if remaining > 0:
        console.print(
            f"\n[dim]…and {remaining} more targets in the full report.[/dim]"
        )
