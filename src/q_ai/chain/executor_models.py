"""Data models for the chain execution engine.

Contains StepOutput and TargetConfig — the data contract that the executor
and CLI build on. Kept separate from models.py to avoid touching existing code.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from q_ai.chain.models import StepStatus


@dataclass
class StepOutput:
    """Result of executing a single chain step.

    Attributes:
        step_id: Unique step identifier.
        module: Which qai module ran this step ('audit' or 'inject').
        technique: Specific technique or scanner used.
        success: Whether the step succeeded.
        status: Execution status enum.
        scan_result: ScanResult from audit module, if applicable.
        campaign: Campaign from inject module, if applicable.
        artifacts: Named artifacts for downstream consumption.
        started_at: When the step started.
        finished_at: When the step completed.
        error: Error message if the step failed.
    """

    step_id: str
    module: str
    technique: str
    success: bool
    status: StepStatus

    # Module-specific results (one will be populated)
    scan_result: Any | None = None
    campaign: Any | None = None
    generate_result: Any | None = None
    build_result: Any | None = None
    validation_result: Any | None = None

    # Named artifacts for downstream consumption
    artifacts: dict[str, str] = field(default_factory=dict)

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata fields to JSON-compatible dict.

        Excludes scan_result and campaign (large objects serialized
        separately by the executor when writing full reports).

        Returns:
            A dictionary with step metadata for JSON output.
        """
        return {
            "step_id": self.step_id,
            "module": self.module,
            "technique": self.technique,
            "success": self.success,
            "status": str(self.status),
            "artifacts": dict(self.artifacts),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


def _coerce_int(value: Any) -> int | None:
    """Coerce a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_yaml_sections(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse and validate YAML sections into typed dicts.

    Args:
        raw: Top-level parsed YAML mapping.

    Returns:
        Dict mapping section name to its validated sub-dict.

    Raises:
        ValueError: If any section is not a mapping.
    """
    sections = ("audit", "inject", "ipi", "cxp", "rxp")
    parsed: dict[str, dict[str, Any]] = {}
    for section in sections:
        val = raw.get(section, {}) or {}
        if not isinstance(val, dict):
            raise ValueError(  # noqa: TRY004
                f"'{section}' section must be a mapping, got {type(val).__name__}"
            )
        parsed[section] = val
    return parsed


@dataclass
class TargetConfig:
    """Target configuration for chain execution.

    Attributes:
        audit_transport: Transport type ('stdio', 'sse', 'streamable-http').
        audit_command: Command + args list for stdio transport.
        audit_url: URL for sse/streamable-http transport.
        inject_model: Model in provider/model format (e.g. anthropic/claude-sonnet-4-20250514).
        ipi_callback_url: Callback URL for IPI payload generation.
        ipi_output_dir: Output directory for IPI payloads.
        ipi_format: Document format for IPI payloads (pdf, md, html, etc.).
        cxp_format_id: CXP context file format ID (e.g. cursorrules, claude-md).
        cxp_output_dir: Output directory for CXP poisoned repos.
        cxp_rule_ids: List of CXP rule IDs to insert.
        rxp_model_id: Embedding model ID for RXP validation.
        rxp_profile_id: RXP validation profile ID.
        rxp_top_k: Top-k results for RXP retrieval validation.
    """

    audit_transport: str | None = None
    audit_command: list[str] | None = None
    audit_url: str | None = None
    inject_model: str | None = None
    ipi_callback_url: str | None = None
    ipi_output_dir: str | None = None
    ipi_format: str | None = None
    cxp_format_id: str | None = None
    cxp_output_dir: str | None = None
    cxp_rule_ids: list[str] | None = None
    rxp_model_id: str | None = None
    rxp_profile_id: str | None = None
    rxp_top_k: int | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> TargetConfig:
        """Load from a chain-targets.yaml file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            A TargetConfig populated from the file contents.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the YAML is malformed.
        """
        if not path.exists():
            raise FileNotFoundError(f"Target config not found: {path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Failed to parse {path}: {exc}") from exc

        if not isinstance(raw, dict):
            return cls()

        parsed = _parse_yaml_sections(raw)
        audit = parsed["audit"]
        inject = parsed["inject"]
        ipi = parsed["ipi"]
        cxp = parsed["cxp"]
        rxp = parsed["rxp"]

        raw_command = audit.get("command")
        if isinstance(raw_command, str):
            audit_command: list[str] | None = shlex.split(raw_command)
        elif isinstance(raw_command, list):
            audit_command = [str(c) for c in raw_command]
        else:
            audit_command = None

        raw_rule_ids = cxp.get("rule_ids")
        cxp_rule_ids: list[str] | None = None
        if isinstance(raw_rule_ids, list):
            cxp_rule_ids = [str(r) for r in raw_rule_ids]

        rxp_top_k = _coerce_int(rxp.get("top_k"))

        return cls(
            audit_transport=audit.get("transport"),
            audit_command=audit_command,
            audit_url=audit.get("url"),
            inject_model=inject.get("model") or os.environ.get("QAI_MODEL"),
            ipi_callback_url=ipi.get("callback_url"),
            ipi_output_dir=ipi.get("output_dir"),
            ipi_format=ipi.get("format"),
            cxp_format_id=cxp.get("format_id"),
            cxp_output_dir=cxp.get("output_dir"),
            cxp_rule_ids=cxp_rule_ids,
            rxp_model_id=rxp.get("model_id"),
            rxp_profile_id=rxp.get("profile_id"),
            rxp_top_k=rxp_top_k,
        )

    def with_overrides(self, **kwargs: Any) -> TargetConfig:
        """Return a new config with specified fields overridden.

        Non-None kwargs replace existing values. None kwargs are ignored.

        Args:
            **kwargs: Field names and their new values.

        Returns:
            A new TargetConfig with overrides applied.
        """
        import dataclasses

        current = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        for key, value in kwargs.items():
            if value is not None and key in current:
                current[key] = value
        return TargetConfig(**current)
