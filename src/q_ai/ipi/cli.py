"""IPI subcommands — indirect prompt injection via document ingestion.

This module is a thin composition layer. Each subcommand lives in its
own module under :mod:`q_ai.ipi.commands`; importing those modules
here runs the ``@app.command`` decorators against the shared Typer
``app`` instance (:mod:`q_ai.ipi.commands._shared`).

The public surface is ``app`` and a small set of symbols re-exported
for backwards compatibility with older test imports.
"""

from __future__ import annotations

from q_ai.ipi.commands._shared import (
    DOCX_TECHNIQUES,
    EML_TECHNIQUES,
    HTML_TECHNIQUES,
    ICS_TECHNIQUES,
    IMAGE_TECHNIQUES,
    IMPLEMENTED_FORMATS,
    MARKDOWN_TECHNIQUES,
    PHASE1_TECHNIQUES,
    PHASE2_TECHNIQUES,
    SUPPORTED_FORMATS,
    SUPPORTED_TUNNEL_PROVIDERS,
    app,
    console,
    validate_format,
)
from q_ai.ipi.commands.generate import parse_techniques


def _register_commands() -> None:
    """Import each subcommand module so its ``@app.command`` decorator fires.

    Iteration order here IS the Typer help-output order — matches the
    pre-refactor source order. ``importlib`` is used deliberately so
    ruff's static import sorter doesn't rearrange the sequence.
    """
    import importlib

    for name in (
        "generate",
        "probe",
        "sweep",
        "techniques",
        "formats",
        "listen",
        "status",
        "export",
        "reset",
    ):
        importlib.import_module(f"q_ai.ipi.commands.{name}")


_register_commands()


__all__ = [
    "DOCX_TECHNIQUES",
    "EML_TECHNIQUES",
    "HTML_TECHNIQUES",
    "ICS_TECHNIQUES",
    "IMAGE_TECHNIQUES",
    "IMPLEMENTED_FORMATS",
    "MARKDOWN_TECHNIQUES",
    "PHASE1_TECHNIQUES",
    "PHASE2_TECHNIQUES",
    "SUPPORTED_FORMATS",
    "SUPPORTED_TUNNEL_PROVIDERS",
    "app",
    "console",
    "parse_techniques",
    "validate_format",
]
