"""``qai ipi export`` — dump campaigns and hits to JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from q_ai.ipi import db
from q_ai.ipi.commands._shared import app, console
from q_ai.ipi.models import Campaign, Hit


def _build_ipi_interpret_prompt(campaigns: list[Campaign], hits: list[Hit]) -> str:
    """Assemble an AI-evaluation prompt from IPI export data.

    Args:
        campaigns: List of campaign objects with format and technique attributes.
        hits: List of hit objects.

    Returns:
        Prompt string ready for embedding in the export JSON.
    """
    n = len(campaigns)
    hit_count = len(hits)

    formats: list[str] = []
    techniques: list[str] = []
    for c in campaigns:
        f = getattr(c, "format", "")
        t = getattr(c, "technique", "")
        if f and f not in formats:
            formats.append(f)
        if t and t not in techniques:
            techniques.append(t)

    formats_str = ", ".join(formats) if formats else "multiple formats"
    techniques_str = ", ".join(techniques) if techniques else "multiple techniques"
    doc_str = f"{n} payload document{'s' if n != 1 else ''}"

    if n == 0:
        return "No payload documents generated."

    return (
        f"{doc_str} generated across {formats_str} "
        f"using {techniques_str}. "
        f"{hit_count} callback execution{'s' if hit_count != 1 else ''} recorded. "
        "Assess execution rates by technique and format, and evaluate "
        "detection risk for your target environment."
    )


@app.command()
def export(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output file (default: ~/.qai/exports/tracking.json)",
        ),
    ] = Path.home() / ".qai" / "exports" / "tracking.json",  # noqa: B008 — Typer default
) -> None:
    """Export campaigns and hits to JSON.

    Exports all campaign and hit data to a JSON file for external
    analysis, reporting, or backup purposes.
    """
    from q_ai.ipi.probe_service import get_unique_path, resolve_export_path

    output = resolve_export_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output = get_unique_path(output)
    campaigns = db.get_all_campaigns()
    all_hits = db.get_hits()

    data = {
        "prompt": _build_ipi_interpret_prompt(campaigns, all_hits),
        "campaigns": [
            {
                "uuid": c.uuid,
                "filename": c.filename,
                "format": c.format,
                "technique": c.technique,
                "payload_style": c.payload_style,
                "payload_type": c.payload_type,
                "callback_url": c.callback_url,
                "created_at": c.created_at.isoformat(),
            }
            for c in campaigns
        ],
        "hits": [
            {
                "uuid": h.uuid,
                "source_ip": h.source_ip,
                "user_agent": h.user_agent,
                "timestamp": h.timestamp.isoformat(),
            }
            for h in all_hits
        ],
    }

    output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    console.print(f"[green]OK Exported to {output}[/green]")
