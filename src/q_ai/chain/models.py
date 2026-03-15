"""Data models for the chain module.

Defines attack chain structure, execution results, and blast radius
analysis for multi-agent exploitation scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ChainCategory(StrEnum):
    """Categories of attack chain architectures."""

    RAG_PIPELINE = "rag_pipeline"
    AGENT_DELEGATION = "agent_delegation"
    MCP_ECOSYSTEM = "mcp_ecosystem"
    HYBRID = "hybrid"


class StepStatus(StrEnum):
    """Execution status of a chain step."""

    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ChainStep:
    """A single step in an attack chain.

    Attributes:
        id: Unique step identifier within the chain.
        name: Human-readable step description.
        module: Which qai module provides this step ('audit', 'inject').
        technique: Specific technique or scanner to use.
        depends_on: Step IDs that must succeed before this step.
        status: Current execution status.
        trust_boundary: Trust boundary crossed by this step.
        on_success: Step ID to execute on success, or "abort".
        on_failure: Step ID to execute on failure, or "abort".
        terminal: Whether this is the final step in a path.
        inputs: Input parameters for live execution (ignored in dry-run).
    """

    id: str
    name: str
    module: str
    technique: str
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    trust_boundary: str | None = None
    on_success: str | None = None
    on_failure: str = "abort"
    terminal: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChainDefinition:
    """A declarative attack chain definition.

    Attributes:
        id: Unique chain identifier.
        name: Human-readable chain name.
        category: Architecture category this chain targets.
        description: What this chain demonstrates.
        steps: Ordered sequence of attack steps.
        entry_cves: Optional CVE IDs used as entry points.
    """

    id: str
    name: str
    category: ChainCategory
    description: str
    steps: list[ChainStep] = field(default_factory=list)
    entry_cves: list[str] = field(default_factory=list)


@dataclass
class ChainResult:
    """Results from executing an attack chain.

    Supports both dry-run tracing (populates ``steps``) and live execution
    (populates ``step_outputs``). Both fields can coexist.

    Attributes:
        chain_id: Which chain was executed.
        chain_name: Human-readable chain name.
        target_config: Target configuration used for execution.
        step_outputs: Execution results (list of StepOutput from executor_models).
        trust_boundaries_crossed: Trust boundaries crossed during execution.
        started_at: Execution start time.
        finished_at: Execution end time.
        dry_run: Whether this was a simulation.
        steps: Steps with updated status (backward compat for tracer).
    """

    chain_id: str
    chain_name: str = ""
    target_config: dict[str, Any] = field(default_factory=dict)
    step_outputs: list[Any] = field(default_factory=list)
    trust_boundaries_crossed: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    dry_run: bool = True

    # Backward compat: tracer still populates these
    steps: list[ChainStep] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Did the chain reach a terminal step successfully?"""
        # Prefer live execution outputs when available.
        if self.step_outputs:
            last = self.step_outputs[-1]
            return bool(getattr(last, "success", False))

        # Fall back to dry-run tracing via recorded steps.
        if self.steps:
            terminal_steps = [s for s in self.steps if s.terminal]
            last_step = terminal_steps[-1] if terminal_steps else self.steps[-1]
            return last_step.status == StepStatus.SUCCESS

        # No execution evidence available.
        return False

    def _build_interpret_prompt(self) -> str:
        """Assemble an AI-evaluation prompt from chain execution results.

        The prompt is findings-driven only: step count, modules used,
        trust boundaries crossed, outcome summary. Tool identity excluded.

        Returns:
            Prompt string ready for embedding in chain reports.
        """
        total = len(self.step_outputs)
        if total == 0 and self.steps:
            # Dry-run path: summarize from tracer steps
            step_count = len(self.steps)
            mods: dict[str, int] = {}
            techs: list[str] = []
            for s in self.steps:
                mods[s.module] = mods.get(s.module, 0) + 1
                if s.technique and s.technique not in techs:
                    techs.append(s.technique)
            mod_str = ", ".join(f"{c} {m}" for m, c in mods.items())
            tech_str = ", ".join(techs) if techs else "standard techniques"
            chain_label = self.chain_name or self.chain_id
            return (
                f"{step_count}-step attack chain '{chain_label}' traced in dry-run mode "
                f"({mod_str}) using {tech_str}. "
                "No live execution performed. Review the attack path for feasibility "
                "and recommend which steps to prioritize for live testing."
            )
        if total == 0:
            return (
                f"Chain '{self.chain_name or self.chain_id}' produced no step outputs. "
                "Verify chain definition and target configuration."
            )

        succeeded = sum(1 for s in self.step_outputs if getattr(s, "success", False))
        failed = total - succeeded

        # Collect modules and techniques used
        modules: dict[str, int] = {}
        techniques: list[str] = []
        for s in self.step_outputs:
            mod = getattr(s, "module", "unknown")
            modules[mod] = modules.get(mod, 0) + 1
            tech = getattr(s, "technique", "")
            if tech and tech not in techniques:
                techniques.append(tech)

        module_parts = [f"{count} {mod}" for mod, count in modules.items()]
        module_str = ", ".join(module_parts)
        technique_str = ", ".join(techniques) if techniques else "standard techniques"

        boundary_str = ""
        if self.trust_boundaries_crossed:
            boundary_str = (
                f" Trust boundaries crossed: {' → '.join(self.trust_boundaries_crossed)}."
            )

        outcome_str = f"{succeeded}/{total} steps succeeded"
        if failed:
            outcome_str += f", {failed} failed"

        chain_label = self.chain_name or self.chain_id

        return (
            f"{total}-step attack chain '{chain_label}' executed "
            f"({module_str}) using {technique_str}. "
            f"{outcome_str}.{boundary_str} "
            "Analyze attack path effectiveness, identify which trust boundary "
            "transitions were most exploitable, and recommend defensive priorities."
        )

    def to_dict(self) -> dict[str, Any]:
        """Full JSON serialization including step evidence.

        Includes both step_outputs (live execution) and steps (dry-run
        tracing) when populated, for backward compatibility.

        Returns:
            A dictionary representation suitable for JSON output.
        """
        step_dicts = []
        for s in self.step_outputs:
            if hasattr(s, "to_dict"):
                step_dicts.append(s.to_dict())
            else:
                step_dicts.append(str(s))

        # Backward-compatible serialization of tracer steps.
        tracer_steps = []
        for step in self.steps:
            tracer_steps.append(
                {
                    "id": step.id,
                    "name": step.name,
                    "module": step.module,
                    "technique": step.technique,
                    "status": str(step.status),
                    "trust_boundary": step.trust_boundary,
                    "terminal": step.terminal,
                }
            )

        result: dict[str, Any] = {
            "prompt": self._build_interpret_prompt(),
            "chain_id": self.chain_id,
            "chain_name": self.chain_name,
            "target_config": self.target_config,
            "step_outputs": step_dicts,
            "trust_boundaries_crossed": self.trust_boundaries_crossed,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "dry_run": self.dry_run,
            "success": self.success,
        }

        if tracer_steps:
            result["steps"] = tracer_steps

        return result
