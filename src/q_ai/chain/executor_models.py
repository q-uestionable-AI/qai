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


@dataclass
class TargetConfig:
    """Target configuration for chain execution.

    Attributes:
        audit_transport: Transport type ('stdio', 'sse', 'streamable-http').
        audit_command: Command + args list for stdio transport.
        audit_url: URL for sse/streamable-http transport.
        inject_model: Model ID for injection campaigns.
    """

    audit_transport: str | None = None
    audit_command: list[str] | None = None
    audit_url: str | None = None
    inject_model: str | None = None

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

        audit = raw.get("audit", {}) or {}
        inject = raw.get("inject", {}) or {}

        if not isinstance(audit, dict):
            raise ValueError(f"'audit' section must be a mapping, got {type(audit).__name__}")
        if not isinstance(inject, dict):
            raise ValueError(f"'inject' section must be a mapping, got {type(inject).__name__}")

        raw_command = audit.get("command")
        if isinstance(raw_command, str):
            audit_command: list[str] | None = shlex.split(raw_command)
        elif isinstance(raw_command, list):
            audit_command = [str(c) for c in raw_command]
        else:
            audit_command = None

        return cls(
            audit_transport=audit.get("transport"),
            audit_command=audit_command,
            audit_url=audit.get("url"),
            inject_model=inject.get("model") or os.environ.get("QAI_MODEL"),
        )

    def with_overrides(self, **kwargs: Any) -> TargetConfig:
        """Return a new config with specified fields overridden.

        Non-None kwargs replace existing values. None kwargs are ignored.

        Args:
            **kwargs: Field names and their new values.

        Returns:
            A new TargetConfig with overrides applied.
        """
        audit_transport = self.audit_transport
        audit_command = self.audit_command
        audit_url = self.audit_url
        inject_model = self.inject_model

        if kwargs.get("audit_transport") is not None:
            audit_transport = kwargs["audit_transport"]
        if kwargs.get("audit_command") is not None:
            audit_command = kwargs["audit_command"]
        if kwargs.get("audit_url") is not None:
            audit_url = kwargs["audit_url"]
        if kwargs.get("inject_model") is not None:
            inject_model = kwargs["inject_model"]

        return TargetConfig(
            audit_transport=audit_transport,
            audit_command=audit_command,
            audit_url=audit_url,
            inject_model=inject_model,
        )
