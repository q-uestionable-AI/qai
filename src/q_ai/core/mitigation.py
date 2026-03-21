"""Mitigation guidance resolver for security findings.

Provides structured, deterministic, tiered guidance for every finding:
- Tier 1: Taxonomy-level actions from mitigations.yaml (per category)
- Tier 2: Rule-based actions from metadata predicates
- Tier 3: Contextual risk factors from mitigations.yaml (per category)

The MitigationResolver is a pure function: ScanFinding in, MitigationGuidance out.
No DB, template, or I/O access beyond the initial YAML load.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
import yaml.constructor

from q_ai.mcp.models import ScanFinding

logger = logging.getLogger("q_ai.core.mitigation")


class _DuplicateKeyLoader(yaml.SafeLoader):
    """YAML loader that raises on duplicate keys within a mapping."""


def _check_duplicates(loader: yaml.Loader, node: yaml.Node) -> dict:
    """Construct a mapping while detecting duplicate keys.

    Args:
        loader: The active YAML loader.
        node: The mapping node to construct.

    Returns:
        The constructed dict.

    Raises:
        ValueError: If any key appears more than once in the mapping.
    """
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in mapping:
            msg = f"Duplicate YAML key '{key}' at line {key_node.start_mark.line + 1}"
            raise ValueError(msg)
        mapping[key] = loader.construct_object(value_node)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _check_duplicates,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset(
    {
        "command_injection",
        "auth",
        "token_exposure",
        "permissions",
        "tool_poisoning",
        "supply_chain",
        "prompt_injection",
        "audit_telemetry",
        "shadow_servers",
        "context_sharing",
    }
)

DEFAULT_DISCLAIMER = (
    "This guidance is not exhaustive. Combine with your organization's "
    "security policies and risk tolerance."
)

# ---------------------------------------------------------------------------
# StrEnum types
# ---------------------------------------------------------------------------


class SectionKind(StrEnum):
    """Kind of guidance section."""

    ACTIONS = "actions"
    FACTORS = "factors"


class SourceType(StrEnum):
    """Provenance of a guidance section."""

    TAXONOMY = "taxonomy"
    RULE = "rule"
    CONTEXT = "context"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GuidanceSection:
    """A single section of mitigation guidance.

    Attributes:
        kind: Whether this section contains actions or factors.
        source_type: Provenance — taxonomy, rule, or context.
        source_ids: Identifiers for the source (framework names, predicates, categories).
        items: The guidance strings in this section.
    """

    kind: SectionKind
    source_type: SourceType
    source_ids: list[str]
    items: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict.

        Returns:
            Dict with string enum values and list copies.
        """
        return {
            "kind": self.kind.value,
            "source_type": self.source_type.value,
            "source_ids": list(self.source_ids),
            "items": list(self.items),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuidanceSection:
        """Deserialize from a plain dict.

        Args:
            data: Dict with kind, source_type, source_ids, items.

        Returns:
            A GuidanceSection instance.

        Raises:
            ValueError: If required keys are missing or enum values are invalid.
        """
        missing = [k for k in ("kind", "source_type") if k not in data]
        if missing:
            msg = f"GuidanceSection missing required keys: {missing}"
            raise ValueError(msg)
        try:
            kind = SectionKind(data["kind"])
        except ValueError:
            msg = f"Invalid SectionKind value: {data['kind']!r}"
            raise ValueError(msg) from None
        try:
            source_type = SourceType(data["source_type"])
        except ValueError:
            msg = f"Invalid SourceType value: {data['source_type']!r}"
            raise ValueError(msg) from None
        return cls(
            kind=kind,
            source_type=source_type,
            source_ids=list(data.get("source_ids", [])),
            items=list(data.get("items", [])),
        )


@dataclass
class MitigationGuidance:
    """Complete mitigation guidance for a finding.

    Attributes:
        sections: Ordered list of guidance sections.
        caveats: Top-level caveats (from general_note, etc.).
        schema_version: Serialization format version.
        disclaimer: Standard non-exhaustiveness disclaimer.
    """

    sections: list[GuidanceSection] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    schema_version: int = 1
    disclaimer: str = DEFAULT_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict.

        Returns:
            Dict suitable for JSON serialization.
        """
        return {
            "sections": [s.to_dict() for s in self.sections],
            "caveats": list(self.caveats),
            "schema_version": self.schema_version,
            "disclaimer": self.disclaimer,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MitigationGuidance:
        """Deserialize from a plain dict.

        Fail-soft: unknown schema_version returns a fallback guidance with
        a single caveat rather than raising an exception.

        Args:
            data: Dict with sections, caveats, schema_version, disclaimer.

        Returns:
            A MitigationGuidance instance.
        """
        version = data.get("schema_version", 1)
        if version != 1:
            return cls(
                caveats=["Mitigation guidance format not supported by this version"],
            )
        return cls(
            sections=[GuidanceSection.from_dict(s) for s in data.get("sections", [])],
            caveats=list(data.get("caveats", [])),
            schema_version=version,
            disclaimer=data.get("disclaimer", DEFAULT_DISCLAIMER),
        )


# ---------------------------------------------------------------------------
# Normalization layer — metadata keys → canonical predicates
# ---------------------------------------------------------------------------

# Static one-to-one mapping: (metadata_key, value) → predicate string.
# Each entry maps a specific metadata key+value pair to a canonical predicate.
PREDICATE_MAP: dict[tuple[str, str], str] = {
    # injection scanner
    ("detection_mode", "canary"): "detection_mode:canary",
    ("detection_mode", "pattern"): "detection_mode:pattern",
    ("detection_mode", "error_based"): "detection_mode:error_based",
    ("cwe", "CWE78"): "cwe:command_injection",
    ("cwe", "CWE88"): "cwe:argument_injection",
    ("cwe", "CWE22"): "cwe:path_traversal",
    ("technique", "shell_command"): "technique:shell_command",
    ("technique", "path_traversal"): "technique:path_traversal",
    ("technique", "argument_injection"): "technique:argument_injection",
    # auth scanner
    ("transport", "stdio"): "transport:stdio",
    ("transport", "sse"): "transport:sse",
    ("transport", "streamable-http"): "transport:http",
    # permissions scanner — only when max_length is absent
    ("has_max_length", "False"): "param:unbounded_input",
}


def _extract_predicates(metadata: dict[str, Any]) -> set[str]:
    """Extract compound predicates from metadata combinations.

    Handles predicates that require logic beyond simple key/value mapping.

    Args:
        metadata: Finding metadata dict from a scanner.

    Returns:
        Set of canonical predicate strings.
    """
    predicates: set[str] = set()

    # High write-tool percentage indicates broad write access
    write_pct = metadata.get("write_tools_pct")
    if isinstance(write_pct, (int, float)) and write_pct > 0.5:
        predicates.add("permissions:high_write_ratio")

    # Large tool count suggests over-permissioned server
    tool_count = metadata.get("tool_count")
    if isinstance(tool_count, int) and tool_count > 20:
        predicates.add("permissions:many_tools")

    # Secret findings indicate exposed credentials
    if metadata.get("secret_findings"):
        predicates.add("token:secrets_in_response")

    # Environment variable leakage
    if metadata.get("env_findings"):
        predicates.add("token:env_var_leak")

    # Hidden characters in tool descriptions
    if metadata.get("hidden_chars"):
        predicates.add("poisoning:hidden_chars")

    # Homoglyph attacks
    if metadata.get("homoglyphs"):
        predicates.add("poisoning:homoglyphs")

    # CVE presence
    if metadata.get("cve_id"):
        predicates.add("supply_chain:known_cve")

    # Debug tools detected
    if metadata.get("debug_tools"):
        predicates.add("shadow:debug_tools")

    # Cross-tool references in prompts
    if metadata.get("referenced_tools"):
        predicates.add("prompt_injection:cross_tool_ref")

    return predicates


def normalize_metadata(metadata: dict[str, Any]) -> set[str]:
    """Convert scanner metadata into canonical predicates.

    Uses both the static PREDICATE_MAP for simple key/value lookups and
    extraction functions for compound predicates.

    Unknown metadata keys are silently ignored.

    Args:
        metadata: Finding metadata dict from a scanner.

    Returns:
        Set of canonical predicate strings.
    """
    predicates: set[str] = set()

    # Static mapping
    for key, value in metadata.items():
        lookup = (key, str(value))
        if lookup in PREDICATE_MAP:
            predicates.add(PREDICATE_MAP[lookup])

    # Compound extraction
    predicates.update(_extract_predicates(metadata))

    return predicates


# ---------------------------------------------------------------------------
# YAML loader with validation
# ---------------------------------------------------------------------------


def _load_mitigations_yaml(yaml_path: Path | None = None) -> dict[str, Any]:
    """Load and validate mitigations.yaml.

    Args:
        yaml_path: Path to YAML file. Defaults to bundled data file.

    Returns:
        Validated dict with 'categories' and 'rules' keys.

    Raises:
        ValueError: On validation failure (unknown categories, empty lists,
            invalid values, duplicate predicates).
    """
    if yaml_path is None:
        from importlib import resources

        data_files = resources.files("q_ai.core.data")
        resource = data_files.joinpath("mitigations.yaml")
        with resource.open() as f:
            data: dict = yaml.load(f, Loader=_DuplicateKeyLoader)  # noqa: S506
    else:
        with yaml_path.open() as f:
            data = yaml.load(f, Loader=_DuplicateKeyLoader)  # noqa: S506

    categories = data.get("categories", {})
    rules = data.get("rules", {})

    # Validate categories
    unknown = set(categories.keys()) - VALID_CATEGORIES
    if unknown:
        msg = f"Unknown categories in mitigations.yaml: {sorted(unknown)}"
        raise ValueError(msg)

    missing = VALID_CATEGORIES - set(categories.keys())
    if missing:
        msg = f"Missing categories in mitigations.yaml: {sorted(missing)}"
        raise ValueError(msg)

    for cat_name, cat_data in categories.items():
        actions = cat_data.get("tier1_actions", [])
        if not actions:
            msg = f"Empty tier1_actions for category '{cat_name}'"
            raise ValueError(msg)
        factors = cat_data.get("tier3_factors", [])
        if not factors:
            msg = f"Empty tier3_factors for category '{cat_name}'"
            raise ValueError(msg)

    # Validate rules — duplicate keys already caught by _DuplicateKeyLoader
    for predicate_name, rule_data in rules.items():
        actions = rule_data.get("actions", [])
        if not actions:
            msg = f"Empty actions for rule predicate '{predicate_name}'"
            raise ValueError(msg)

    return data


# ---------------------------------------------------------------------------
# MitigationResolver
# ---------------------------------------------------------------------------


class MitigationResolver:
    """Generates structured mitigation guidance for findings.

    Pure function resolver: loads YAML once at construction, then
    resolve() is data-in/data-out with no DB, template, or I/O access.

    Follows the same pattern as FrameworkResolver:
    - Constructor loads YAML via importlib.resources
    - Optional yaml_path for testing
    - Single resolve() method
    """

    def __init__(self, yaml_path: Path | None = None) -> None:
        """Load and validate mitigations data.

        Args:
            yaml_path: Path to YAML file. Defaults to bundled data file.

        Raises:
            ValueError: If YAML validation fails.
        """
        data = _load_mitigations_yaml(yaml_path)
        self._categories: dict[str, Any] = data.get("categories", {})
        self._rules: dict[str, Any] = data.get("rules", {})

    def resolve(self, finding: ScanFinding) -> MitigationGuidance:
        """Generate mitigation guidance for a finding.

        Args:
            finding: A ScanFinding with framework_ids already resolved.

        Returns:
            MitigationGuidance with deterministic section ordering:
            taxonomy actions first, rule actions second, factors third.
        """
        sections: list[GuidanceSection] = []
        caveats: list[str] = []

        cat_data = self._categories.get(finding.category, {})

        # Tier 1: Taxonomy actions
        tier1_actions = cat_data.get("tier1_actions", [])
        if tier1_actions:
            source_ids = sorted(finding.framework_ids.keys()) if finding.framework_ids else []
            sections.append(
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.TAXONOMY,
                    source_ids=source_ids,
                    items=list(tier1_actions),
                )
            )

        # Tier 2: Rule-based actions from metadata predicates
        predicates = normalize_metadata(finding.metadata)
        if predicates:
            matched_actions: list[str] = []
            matched_predicates: list[str] = []
            for predicate in sorted(predicates):
                rule = self._rules.get(predicate)
                if rule:
                    matched_predicates.append(predicate)
                    for action in rule.get("actions", []):
                        if action not in matched_actions:
                            matched_actions.append(action)

            if matched_actions:
                sections.append(
                    GuidanceSection(
                        kind=SectionKind.ACTIONS,
                        source_type=SourceType.RULE,
                        source_ids=matched_predicates,
                        items=matched_actions,
                    )
                )

        # Tier 3: Contextual factors
        tier3_factors = cat_data.get("tier3_factors", [])
        if tier3_factors:
            sections.append(
                GuidanceSection(
                    kind=SectionKind.FACTORS,
                    source_type=SourceType.CONTEXT,
                    source_ids=[finding.category],
                    items=list(tier3_factors),
                )
            )

        # Caveats from general_note
        general_note = cat_data.get("general_note")
        if general_note:
            caveats.append(general_note)

        return MitigationGuidance(
            sections=sections,
            caveats=caveats,
        )
