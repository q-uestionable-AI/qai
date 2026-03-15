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
    """

    id: str
    name: str
    description: str
    modules: list[str] = field(default_factory=list)
    optional_modules: list[str] = field(default_factory=list)
    executor: Callable[..., Any] | None = None
    failure_mode: str = "best_effort"


WORKFLOWS: dict[str, WorkflowEntry] = {}


def register_workflow(entry: WorkflowEntry) -> None:
    """Register a workflow in the global registry.

    Args:
        entry: WorkflowEntry to register. Overwrites any existing entry with
            the same id.
    """
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
    ),
    WorkflowEntry(
        id="test_docs",
        name="Test Document Ingestion",
        description="Generate payloads for document pipelines and track execution callbacks.",
        modules=["ipi", "rxp"],
        optional_modules=["rxp"],
    ),
    WorkflowEntry(
        id="test_assistant",
        name="Test a Coding Assistant",
        description=(
            "Poison context files and validate whether AI assistants propagate tainted output."
        ),
        modules=["cxp"],
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
    ),
    WorkflowEntry(
        id="manage_research",
        name="Manage Research",
        description=(
            "Campaigns, evidence collection, reports, and CVE tracking across all modules."
        ),
        modules=["audit", "proxy", "inject", "ipi", "cxp", "rxp", "chain"],
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

    _assess = WORKFLOWS.get("assess")
    if _assess is not None:
        _assess.executor = assess_mcp_server


_register_executors()
