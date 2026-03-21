"""Workflow registry for q-ai orchestration.

Maps workflow IDs to metadata and executor functions. The launcher UI reads
metadata (name, description, modules) and the orchestrator looks up the
executor function.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowEntry:
    """A registered workflow with metadata and optional executor.

    Attributes:
        id: Unique workflow identifier (e.g. "assess").
        name: Human-readable workflow name.
        description: Short description for the launcher UI.
        modules: List of module names used by this workflow.
        optional_modules: Modules that can be skipped if deps are unavailable.
        executor: Async executor function, or None if not yet implemented.
        failure_mode: Error handling policy — "best_effort" or "fail_fast".
        requires_provider: Whether the workflow requires a configured LLM provider.
            When False, the launcher shows the form without a provider check and
            the launch API skips credential validation. Defaults to True.
        is_hero: Whether this workflow should render as the hero card in the
            launcher grid. Only one workflow should have this set to True.
        visible_in_launcher: Whether to show this workflow in the launcher grid.
            Defaults to True. Set False to hide from the launcher while keeping
            the workflow available via CLI and run history.
    """

    id: str
    name: str
    description: str
    modules: list[str] = field(default_factory=list)
    optional_modules: list[str] = field(default_factory=list)
    executor: Callable[..., Any] | None = None
    failure_mode: str = "best_effort"
    requires_provider: bool = True
    is_hero: bool = False
    visible_in_launcher: bool = True


WORKFLOWS: dict[str, WorkflowEntry] = {}


def register_workflow(entry: WorkflowEntry) -> None:
    """Register a workflow in the global registry.

    Args:
        entry: WorkflowEntry to register. Overwrites any existing entry with
            the same id.

    Raises:
        ValueError: If entry has is_hero=True and another workflow (with a
            different id) is already registered as the hero.
    """
    if entry.is_hero:
        for existing in WORKFLOWS.values():
            if existing.is_hero and existing.id != entry.id:
                raise ValueError(
                    f"Cannot register '{entry.id}' as hero: "
                    f"'{existing.id}' is already the hero workflow"
                )
    WORKFLOWS[entry.id] = entry


def get_workflow(workflow_id: str) -> WorkflowEntry | None:
    """Look up a workflow by ID.

    Args:
        workflow_id: The workflow ID to look up.

    Returns:
        The WorkflowEntry if found, None otherwise.
    """
    return WORKFLOWS.get(workflow_id)


def list_workflows() -> list[WorkflowEntry]:
    """Return all registered workflows.

    Returns:
        List of all WorkflowEntry objects in registration order.
    """
    return list(WORKFLOWS.values())


# ---------------------------------------------------------------------------
# Register the 6 workflows at module load time (all with executor=None)
# ---------------------------------------------------------------------------

_DEFAULT_WORKFLOWS = [
    WorkflowEntry(
        id="assess",
        name="Assess an MCP Server",
        description=(
            "Scan, intercept, and test tool trust boundaries in Model Context Protocol servers."
        ),
        modules=["audit", "proxy", "inject"],
        is_hero=True,
    ),
    WorkflowEntry(
        id="test_docs",
        name="Test Document Ingestion",
        description="Generate payloads for document pipelines and validate ingestion behavior.",
        modules=["ipi", "rxp"],
        optional_modules=["rxp"],
        requires_provider=False,
    ),
    WorkflowEntry(
        id="test_assistant",
        name="Test Context Poisoning",
        description=(
            "Poison context files and validate whether AI assistants propagate tainted output."
        ),
        modules=["cxp"],
        requires_provider=False,
    ),
    WorkflowEntry(
        id="trace_path",
        name="Trace an Attack Path",
        description="Compose individual vulnerabilities into multi-step exploitation chains.",
        modules=["chain"],
        failure_mode="fail_fast",
    ),
    WorkflowEntry(
        id="blast_radius",
        name="Measure Blast Radius",
        description="Analyze reach from a compromise point and generate detection rules.",
        modules=["chain"],
        requires_provider=False,
    ),
    WorkflowEntry(
        id="generate_report",
        name="Generate Report",
        description=(
            "Generate a cross-module findings report and optional evidence pack for a target."
        ),
        modules=[],
        requires_provider=False,
        visible_in_launcher=False,
    ),
]

for _wf in _DEFAULT_WORKFLOWS:
    register_workflow(_wf)

# ---------------------------------------------------------------------------
# Wire executor functions into registered workflows
# ---------------------------------------------------------------------------


def _register_executors() -> None:
    """Attach executor functions to registered workflows.

    Called at import time to wire up implemented workflow executors.
    Wrapped in a function to keep the import of workflow modules
    isolated from the top-level namespace.
    """
    from q_ai.orchestrator.workflows.assess import assess_mcp_server
    from q_ai.orchestrator.workflows.blast_radius import measure_blast_radius
    from q_ai.orchestrator.workflows.generate_report import generate_report
    from q_ai.orchestrator.workflows.test_assistant import test_coding_assistant
    from q_ai.orchestrator.workflows.test_docs import test_document_ingestion
    from q_ai.orchestrator.workflows.trace_path import trace_attack_path

    _assess = WORKFLOWS.get("assess")
    if _assess is not None:
        _assess.executor = assess_mcp_server

    for wf_id, executor in [
        ("test_docs", test_document_ingestion),
        ("test_assistant", test_coding_assistant),
        ("trace_path", trace_attack_path),
        ("blast_radius", measure_blast_radius),
        ("generate_report", generate_report),
    ]:
        entry = WORKFLOWS.get(wf_id)
        if entry is not None:
            entry.executor = executor


_register_executors()
