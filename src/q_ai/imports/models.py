"""Data models for external tool import."""

from __future__ import annotations

from dataclasses import dataclass, field

from q_ai.core.models import Severity


@dataclass
class TaxonomyBridge:
    """Mapping between an external taxonomy ID and a qai category.

    Attributes:
        external_framework: Framework name, e.g. ``"owasp_llm_top10"``.
        external_id: Identifier within the framework, e.g. ``"LLM01"``.
        qai_category: Mapped qai category, or ``None`` if no equivalent.
        confidence: ``"direct"``, ``"adjacent"``, or ``"none"``.
    """

    external_framework: str
    external_id: str
    qai_category: str | None
    confidence: str  # "direct" | "adjacent" | "none"


@dataclass
class ImportedFinding:
    """Normalized finding ready for DB insertion.

    Attributes:
        category: qai category (from taxonomy bridge) or original external category.
        severity: Mapped severity level.
        title: Human-readable finding title.
        description: Optional extended description.
        source_tool: Origin tool name (``"garak"``, ``"pyrit"``, or ``"sarif"``).
        source_tool_version: Detected version of the external tool.
        original_id: Original finding/probe ID from the external tool.
        original_taxonomy: Original taxonomy mappings preserved intact.
        raw_evidence: JSON-serialized raw data for evidence storage.
    """

    category: str
    severity: Severity
    title: str
    description: str | None
    source_tool: str
    source_tool_version: str | None
    original_id: str | None
    original_taxonomy: dict[str, str] = field(default_factory=dict)
    raw_evidence: str = ""


@dataclass
class ImportResult:
    """Return type from each parser.

    Attributes:
        findings: Parsed findings ready for persistence.
        tool_name: Name of the external tool.
        tool_version: Detected version of the external tool.
        parser_version: Version of qai's parser.
        source_file: Original filename for provenance.
        errors: Non-fatal parse warnings.
    """

    findings: list[ImportedFinding]
    tool_name: str
    tool_version: str | None
    parser_version: str
    source_file: str
    errors: list[str] = field(default_factory=list)
