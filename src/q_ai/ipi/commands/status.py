"""``qai ipi status`` — check campaigns and hits."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from q_ai.ipi import db
from q_ai.ipi.commands._shared import app, console, validate_format
from q_ai.ipi.models import Campaign, Hit, HitConfidence, PayloadType, Technique


def _display_single_campaign(campaign: Campaign, hits: list[Hit]) -> None:
    """Render detailed status output for a single campaign.

    Args:
        campaign: The campaign to display.
        hits: List of hits associated with this campaign.
    """
    console.print(f"\n[bold]Campaign:[/bold] {escape(campaign.uuid)}")
    console.print(f"  File: {escape(campaign.filename)}")
    console.print(f"  Format: {campaign.format}")
    console.print(f"  Technique: {campaign.technique}")
    console.print(f"  Payload Style: {campaign.payload_style}")
    console.print(f"  Payload Type: {campaign.payload_type}")
    console.print(f"  Created: {campaign.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

    if not hits:
        console.print("\n[dim]No hits recorded[/dim]")
        return

    console.print(f"\n[bold green]Hit {len(hits)} hit(s):[/bold green]")
    for hit in hits:
        ts = hit.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        token_icon = "+" if hit.token_valid else "x"
        conf = hit.confidence.value
        console.print(
            f"  * {ts} from {escape(hit.source_ip)}  [token:{token_icon}] [confidence:{conf}]"
        )


def _filter_campaigns(
    campaigns: list[Campaign],
    format_name: str | None,
    technique: str | None,
    payload_type: str | None,
) -> list[Campaign]:
    """Apply optional filters to a list of campaigns.

    Args:
        campaigns: Full list of campaigns to filter.
        format_name: If provided, filter by this format name.
        technique: If provided, filter by this technique name.
        payload_type: If provided, filter by this payload type name.

    Returns:
        Filtered list of campaigns.

    Raises:
        typer.Exit: If a filter value is invalid.
    """
    if format_name:
        validated_format = validate_format(format_name)
        campaigns = [c for c in campaigns if c.format == validated_format]

    if technique:
        try:
            technique_enum = Technique(technique)
        except ValueError:
            console.print(f"[red]X Invalid technique: {technique}[/red]")
            console.print(f"  Valid techniques: {', '.join(t.value for t in Technique)}")
            raise typer.Exit(1) from None
        campaigns = [c for c in campaigns if c.technique == technique_enum]

    if payload_type:
        try:
            payload_type_enum = PayloadType(payload_type)
        except ValueError:
            console.print(f"[red]X Invalid payload type: {payload_type}[/red]")
            console.print(f"  Valid options: {', '.join(p.value for p in PayloadType)}")
            raise typer.Exit(1) from None
        campaigns = [c for c in campaigns if c.payload_type == payload_type_enum]

    return campaigns


def _confidence_summary(campaign_hits: list[Hit]) -> str:
    """Build a Rich-formatted confidence breakdown string (H/M/L counts).

    Args:
        campaign_hits: List of hits for a single campaign.

    Returns:
        Rich markup string showing high/medium/low confidence counts,
        or a dimmed dash if no hits.
    """
    if not campaign_hits:
        return "[dim]-[/dim]"
    high = sum(1 for h in campaign_hits if h.confidence == HitConfidence.HIGH)
    med = sum(1 for h in campaign_hits if h.confidence == HitConfidence.MEDIUM)
    low = sum(1 for h in campaign_hits if h.confidence == HitConfidence.LOW)
    return f"[green]{high}H[/green]/[yellow]{med}M[/yellow]/[red]{low}L[/red]"


def _display_campaigns_table(
    campaigns: list[Campaign],
    hits_by_uuid: dict[str, list[Hit]],
) -> None:
    """Render a Rich table of all campaigns with hit summaries.

    Args:
        campaigns: List of campaigns to display.
        hits_by_uuid: Mapping from campaign UUID to its list of hits.
    """
    table = Table(title="IPI Campaigns")
    table.add_column("UUID", style="cyan", no_wrap=True)
    table.add_column("File")
    table.add_column("Format")
    table.add_column("Technique")
    table.add_column("Payload Style")
    table.add_column("Payload Type")
    table.add_column("Hits", justify="center")
    table.add_column("Confidence", justify="center")
    table.add_column("Created")

    for c in campaigns:
        campaign_hits = hits_by_uuid.get(c.uuid, [])
        hit_count = len(campaign_hits)
        hit_style = "bold green" if hit_count > 0 else "dim"

        table.add_row(
            escape(c.uuid[:8] + "..."),
            escape(c.filename),
            escape(c.format),
            escape(c.technique),
            escape(c.payload_style),
            escape(c.payload_type),
            f"[{hit_style}]{hit_count}[/{hit_style}]",
            _confidence_summary(campaign_hits),
            c.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)
    console.print("\n[dim]Use 'qai ipi status <uuid>' for details[/dim]")


@app.command()
def status(
    uuid: Annotated[str | None, typer.Argument(help="Campaign UUID (optional)")] = None,
    format_name: Annotated[str | None, typer.Option("--format", help="Filter by format")] = None,
    technique: Annotated[
        str | None, typer.Option("--technique", help="Filter by technique")
    ] = None,
    payload_type: Annotated[
        str | None, typer.Option("--payload-type", help="Filter by payload type")
    ] = None,
) -> None:
    """Check status of campaigns and hits.

    Without arguments, displays a table of all campaigns with hit counts.
    With a UUID argument, shows detailed information for that campaign
    including all recorded hits.

    Supports filtering by format, technique, and payload type.
    """
    if uuid:
        campaign = db.get_campaign(uuid)
        if not campaign:
            console.print(f"[red]X Campaign not found: {uuid}[/red]")
            raise typer.Exit(1)
        _display_single_campaign(campaign, db.get_hits(uuid))
        return

    campaigns = _filter_campaigns(db.get_all_campaigns(), format_name, technique, payload_type)

    if not campaigns:
        if format_name or technique or payload_type:
            console.print("[dim]No campaigns match the provided filters.[/dim]")
        else:
            console.print("[dim]No campaigns found. Run 'qai ipi generate' first.[/dim]")
        return

    all_hits = db.get_hits()
    hits_by_uuid: dict[str, list[Hit]] = {}
    for hit in all_hits:
        hits_by_uuid.setdefault(hit.uuid, []).append(hit)

    _display_campaigns_table(campaigns, hits_by_uuid)
