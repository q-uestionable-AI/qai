"""Dry-run trace engine for attack chain definitions.

Walks the chain graph along the success path and produces a step-by-step
execution report without making any real connections or sending any payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from q_ai.chain.models import ChainDefinition


@dataclass
class TraceStep:
    """A single step in a chain trace result.

    Attributes:
        step_id: Identifier of the corresponding ChainStep.
        name: Human-readable step name.
        module: qai module that provides this step.
        technique: Specific technique used in this step.
        trust_boundary: Trust boundary crossed by this step, if any.
        order: 1-based execution order in the trace.
    """

    step_id: str
    name: str
    module: str
    technique: str
    trust_boundary: str | None
    order: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns:
            A dictionary representation of this trace step.
        """
        return {
            "step_id": self.step_id,
            "name": self.name,
            "module": self.module,
            "technique": self.technique,
            "trust_boundary": self.trust_boundary,
            "order": self.order,
        }


@dataclass
class TraceResult:
    """The result of tracing an attack chain's success path.

    Attributes:
        chain_id: Identifier of the traced chain.
        chain_name: Human-readable name of the traced chain.
        steps: Ordered list of trace steps along the success path.
        trust_boundaries_crossed: Unique trust boundaries encountered, in traversal order.
    """

    chain_id: str
    chain_name: str
    steps: list[TraceStep] = field(default_factory=list)
    trust_boundaries_crossed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns:
            A dictionary representation of this trace result.
        """
        return {
            "chain_id": self.chain_id,
            "chain_name": self.chain_name,
            "steps": [s.to_dict() for s in self.steps],
            "trust_boundaries_crossed": self.trust_boundaries_crossed,
        }


def trace_chain(chain: ChainDefinition) -> TraceResult:
    """Trace the execution path assuming all steps succeed.

    Walks from the first step, following on_success references.
    If on_success is None on a non-terminal step, follows implicit
    next-in-order. Stops at terminal steps or when no successor exists.
    If a cycle is detected during tracing, stop (but validator should
    have caught this already).

    Args:
        chain: The chain definition to trace.

    Returns:
        A TraceResult containing the ordered steps and trust boundaries
        encountered along the success path.
    """
    result = TraceResult(chain_id=chain.id, chain_name=chain.name)

    if not chain.steps:
        return result

    # Build a lookup map from step id to ChainStep for O(1) access.
    step_map = {s.id: s for s in chain.steps}

    # Track visited IDs to detect cycles.
    visited: set[str] = set()

    # Build an ordered list of step IDs as defined for implicit next-in-order.
    step_ids_in_order = [s.id for s in chain.steps]

    current_id: str | None = step_ids_in_order[0]
    order = 1

    # Track unique trust boundaries in traversal order.
    seen_boundaries: set[str] = set()

    while current_id is not None:
        if current_id in visited:
            # Cycle detected — stop tracing.
            break

        step = step_map.get(current_id)
        if step is None:
            # Referenced step does not exist — stop tracing.
            break

        visited.add(current_id)

        trace_step = TraceStep(
            step_id=step.id,
            name=step.name,
            module=step.module,
            technique=step.technique,
            trust_boundary=step.trust_boundary,
            order=order,
        )
        result.steps.append(trace_step)

        if step.trust_boundary is not None and step.trust_boundary not in seen_boundaries:
            seen_boundaries.add(step.trust_boundary)
            result.trust_boundaries_crossed.append(step.trust_boundary)

        order += 1

        if step.terminal:
            # Terminal step — stop here.
            break

        if step.on_success is not None:
            # Explicit successor.
            current_id = step.on_success
        else:
            # Implicit next-in-order successor.
            try:
                idx = step_ids_in_order.index(current_id)
                next_idx = idx + 1
                if next_idx < len(step_ids_in_order):
                    current_id = step_ids_in_order[next_idx]
                else:
                    current_id = None
            except ValueError:
                current_id = None

    return result
