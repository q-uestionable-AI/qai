"""Core data models for CXP context file poisoning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Objective:
    """What the payload tries to achieve.

    Args:
        id: Unique identifier (e.g., "backdoor", "exfil").
        name: Human-readable name.
        description: What success looks like.
        validators: Validator rule IDs that detect this objective.
    """

    id: str
    name: str
    description: str
    validators: list[str]


@dataclass
class AssistantFormat:
    """Instruction file format for a specific coding assistant.

    Args:
        id: Unique identifier (e.g., "claude-md", "cursorrules").
        filename: Filename the assistant reads (e.g., "CLAUDE.md").
        assistant: Assistant name (e.g., "Claude Code").
        syntax: File syntax type ("markdown", "json", "plaintext").
    """

    id: str
    filename: str
    assistant: str
    syntax: str


@dataclass
class Technique:
    """A specific payload: one objective in one format.

    Args:
        id: Unique identifier (e.g., "backdoor-claude-md").
        objective: The attack objective.
        format: The assistant instruction format.
        template: Jinja2 template for the poisoned file.
        trigger_prompt: Prompt the researcher uses to activate.
        project_type: Target project type ("python", "javascript", "generic").
    """

    id: str
    objective: Objective
    format: AssistantFormat
    template: str
    trigger_prompt: str
    project_type: str


@dataclass
class TestResult:
    """One test execution: one technique against one assistant.

    Args:
        id: UUID.
        campaign_id: Groups results from a testing session.
        technique_id: Which technique was tested.
        assistant: Which assistant was tested.
        model: Underlying model if known.
        timestamp: When the test was run.
        trigger_prompt: Actual prompt used.
        capture_mode: "file" or "output".
        captured_files: Paths to captured files (file mode).
        raw_output: Full text content.
        validation_result: "hit", "miss", "partial", or "error".
        validation_details: What the validator found.
        notes: Researcher observations.
        rules_inserted: Comma-separated rule IDs (v0.2+, empty for legacy results).
        format_id: Which format was used (v0.2+, empty for legacy results).
    """

    id: str
    campaign_id: str
    technique_id: str
    assistant: str
    model: str
    timestamp: datetime
    trigger_prompt: str
    capture_mode: str
    captured_files: list[str]
    raw_output: str
    validation_result: str
    validation_details: str
    notes: str
    rules_inserted: str = ""
    format_id: str = ""


@dataclass
class ValidatorRule:
    """A single detection pattern.

    Args:
        id: Rule identifier (e.g., "backdoor-hardcoded-cred").
        objective_id: Which objective this rule belongs to.
        name: Human-readable name.
        description: What this pattern detects.
        patterns: Regex patterns (any match = detection).
        severity: Rule severity ("high", "medium", "low").
    """

    id: str
    objective_id: str
    name: str
    description: str
    patterns: list[str]
    severity: str


@dataclass
class ValidationResult:
    """Result of validating one piece of captured output.

    Args:
        verdict: Overall result ("hit", "miss", "partial").
        matched_rules: Rule IDs that matched.
        details: Human-readable summary of findings.
    """

    verdict: str
    matched_rules: list[str]
    details: str


@dataclass
class Rule:
    """An insecure coding rule for context file poisoning.

    Attributes:
        id: Unique rule identifier (e.g., "weak-crypto-md5").
        name: Human-readable name.
        category: Rule category (e.g., "weak-crypto", "hardcoded-secrets").
        severity: Impact severity ("high", "medium", "low").
        description: What this rule produces when followed.
        content: Rule text keyed by syntax type ("markdown", "plaintext").
        section: Target section ID in the base template.
        trigger_prompts: Suggested prompts that exercise this rule.
        validators: Validator rule IDs that detect compliance.
    """

    id: str
    name: str
    category: str
    severity: str
    description: str
    content: dict[str, str]
    section: str
    trigger_prompts: list[str]
    validators: list[str]


@dataclass
class BuildResult:
    """Result of a CXP build operation.

    Attributes:
        repo_dir: Path to the generated repo directory.
        context_file: Path to the assembled context file.
        rules_inserted: List of rule IDs that were inserted.
        format_id: Format that was used.
        prompt_reference_path: Path to the prompt reference file.
        manifest_path: Path to the manifest file.
    """

    repo_dir: Path
    context_file: Path
    rules_inserted: list[str]
    format_id: str
    prompt_reference_path: Path
    manifest_path: Path


@dataclass
class Campaign:
    """A testing session grouping multiple test results.

    Args:
        id: UUID.
        name: Campaign name (e.g., "2026-03-01-cursor-backdoors").
        created: When the campaign was created.
        description: Campaign description.
    """

    id: str
    name: str
    created: datetime
    description: str
