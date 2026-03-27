"""Data models for the inject module.

Defines campaign, payload, and scoring structures for tool poisoning
and prompt injection testing against AI agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class InjectionTechnique(StrEnum):
    """Categories of tool-based injection techniques."""

    DESCRIPTION_POISONING = "description_poisoning"
    OUTPUT_INJECTION = "output_injection"
    CROSS_TOOL_ESCALATION = "cross_tool_escalation"


class InjectionOutcome(StrEnum):
    """Graded outcome for an injection attempt."""

    FULL_COMPLIANCE = "full_compliance"
    PARTIAL_COMPLIANCE = "partial_compliance"
    REFUSAL_WITH_LEAK = "refusal_with_leak"
    CLEAN_REFUSAL = "clean_refusal"
    ERROR = "error"


@dataclass
class PayloadTemplate:
    """A reusable injection payload definition.

    Attributes:
        name: Unique identifier for this payload.
        technique: The injection technique category.
        description: Human-readable description of what this payload tests.
        owasp_ids: Relevant OWASP MCP Top 10 / Agentic AI Top 10 IDs.
        target_agents: Agent types this payload is designed for (empty = universal).
        relevant_categories: Audit finding categories this payload tests (empty = universal).
        tool_name: MCP tool name to register.
        tool_description: The poisoned tool description text.
        tool_params: Tool input schema mapping param name to {type, description}.
        tool_response: Response template string with {param_name} substitution.
        test_query: User message sent alongside the poisoned tool during campaigns.
    """

    name: str
    technique: InjectionTechnique
    description: str
    owasp_ids: list[str] = field(default_factory=list)
    target_agents: list[str] = field(default_factory=list)
    relevant_categories: list[str] = field(default_factory=list)
    tool_name: str = ""
    tool_description: str = ""
    tool_params: dict[str, dict[str, str]] = field(default_factory=dict)
    tool_response: str = ""
    test_query: str = ""


@dataclass
class InjectionResult:
    """Result of a single injection attempt.

    Attributes:
        payload_name: Which payload was used.
        technique: Injection technique used.
        outcome: Graded effectiveness score.
        evidence: JSON-serialized API response content blocks.
        target_agent: Agent/model that was tested.
        timestamp: When the attempt occurred.
    """

    payload_name: str
    technique: str
    outcome: InjectionOutcome
    evidence: str
    target_agent: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "payload_name": self.payload_name,
            "technique": self.technique,
            "outcome": self.outcome.value,
            "evidence": self.evidence,
            "target_agent": self.target_agent,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InjectionResult:
        """Deserialize from dict."""
        return cls(
            payload_name=data["payload_name"],
            technique=data.get("technique", ""),
            outcome=InjectionOutcome(data["outcome"]),
            evidence=data["evidence"],
            target_agent=data["target_agent"],
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.now(UTC),
        )


@dataclass
class Campaign:
    """An injection testing campaign.

    Attributes:
        id: Unique campaign identifier.
        name: Human-readable campaign name.
        model: Model ID used for the campaign.
        results: All injection attempt results.
        started_at: Campaign start time.
        finished_at: Campaign end time.
    """

    id: str
    name: str
    model: str = ""
    results: list[InjectionResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def summary(self) -> dict[str, int]:
        """Count results by outcome category."""
        counts: dict[str, int] = {outcome.value: 0 for outcome in InjectionOutcome}
        for r in self.results:
            counts[r.outcome.value] += 1
        counts["total"] = len(self.results)
        return counts

    def _build_interpret_prompt(self) -> str:
        """Assemble an AI-evaluation prompt from inject campaign results.

        The prompt is findings-driven only: target model, techniques used,
        outcome distribution. Tool identity is excluded.

        Returns:
            Prompt string ready for embedding in the campaign JSON.
        """
        raw_techniques = (getattr(r, "technique", "") for r in self.results)
        techniques = list(
            dict.fromkeys(t for t in raw_techniques if isinstance(t, str) and t.strip())
        )
        techniques_str = ", ".join(techniques) if techniques else "injection techniques"

        counts = self.summary()
        total = counts.get("total", 0)
        target_str = (
            f"{total} injection payload{'s' if total != 1 else ''} "
            f"tested against {self.model or 'target model'}"
        )

        if total == 0:
            return f"{target_str} using {techniques_str}. No results recorded."

        outcome_parts = []
        full = counts.get("full_compliance", 0)
        partial = counts.get("partial_compliance", 0)
        refused = counts.get("clean_refusal", 0)
        if full:
            outcome_parts.append(f"{full} full compliance")
        if partial:
            outcome_parts.append(f"{partial} partial compliance")
        if refused:
            outcome_parts.append(f"{refused} clean refusal")
        outcome_str = ", ".join(outcome_parts) if outcome_parts else "mixed outcomes"

        return (
            f"{target_str} using {techniques_str}. "
            f"Outcomes: {outcome_str}. "
            "Analyze technique effectiveness by compliance rate and recommend "
            "follow-on testing priorities."
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "prompt": self._build_interpret_prompt(),
            "id": self.id,
            "name": self.name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "model": self.model,
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Campaign:
        """Deserialize from dict."""
        results = [InjectionResult.from_dict(r) for r in data.get("results", [])]
        started_at_raw = data.get("started_at")
        finished_at_raw = data.get("finished_at")
        return cls(
            id=data["id"],
            name=data["name"],
            model=data.get("model", ""),
            results=results,
            started_at=datetime.fromisoformat(started_at_raw)
            if isinstance(started_at_raw, str)
            else datetime.now(UTC),
            finished_at=datetime.fromisoformat(finished_at_raw)
            if isinstance(finished_at_raw, str)
            else None,
        )

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class CoverageReport:
    """Coverage analysis of audit findings exercised by inject templates.

    Attributes:
        audit_categories: All categories (native + imported) used for coverage.
        tested_categories: Categories exercised by inject templates that ran.
        untested_categories: Categories found but not exercised.
        coverage_ratio: Fraction of audit categories exercised (0.0 when none).
        template_matches: List of dicts with template name and matched categories.
        native_categories: Categories from native audit findings.
        imported_categories: Categories from imported external findings.
    """

    audit_categories: set[str]
    tested_categories: set[str]
    untested_categories: set[str]
    coverage_ratio: float
    template_matches: list[dict[str, Any]]
    native_categories: set[str] = field(default_factory=set)
    imported_categories: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "audit_categories": sorted(self.audit_categories),
            "tested_categories": sorted(self.tested_categories),
            "untested_categories": sorted(self.untested_categories),
            "coverage_ratio": self.coverage_ratio,
            "template_matches": self.template_matches,
            "native_categories": sorted(self.native_categories),
            "imported_categories": sorted(self.imported_categories),
        }
