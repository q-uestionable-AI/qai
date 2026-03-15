"""Detection rule generation from chain execution results.

Reads a completed ChainResult and generates Sigma or Wazuh detection
rules that would identify the observed attack patterns in a monitored
environment.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape  # nosec B406


def _sanitize_yaml_value(val: str) -> str:
    """Quote a YAML scalar if it contains special characters."""
    if any(c in val for c in (":", "\n", "#", '"', "'", "[", "]", "{", "}")):
        return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return val


# ---------------------------------------------------------------------------
# MITRE ATT&CK mapping (intentionally simple)
# ---------------------------------------------------------------------------

_MITRE_MAP: dict[tuple[str, str], dict[str, Any]] = {
    ("audit", "injection"): {
        "tactic": "execution",
        "technique_id": "T1059",
        "technique_name": "Command Execution",
        "tags": ["attack.execution", "attack.t1059"],
    },
    ("audit", "auth"): {
        "tactic": "persistence",
        "technique_id": "T1078",
        "technique_name": "Valid Accounts",
        "tags": ["attack.persistence", "attack.t1078"],
    },
    ("audit", "tool_poisoning"): {
        "tactic": "defense_evasion",
        "technique_id": "T1036",
        "technique_name": "Masquerading",
        "tags": ["attack.defense_evasion", "attack.t1036"],
    },
    ("inject", "description_poisoning"): {
        "tactic": "defense_evasion",
        "technique_id": "T1036",
        "technique_name": "Masquerading",
        "tags": ["attack.defense_evasion", "attack.t1036"],
    },
    ("inject", "output_injection"): {
        "tactic": "execution",
        "technique_id": "T1059.006",
        "technique_name": "Python",
        "tags": ["attack.execution", "attack.t1059.006"],
    },
    ("inject", "cross_tool_escalation"): {
        "tactic": "lateral_movement",
        "technique_id": "T1570",
        "technique_name": "Lateral Tool Transfer",
        "tags": ["attack.lateral_movement", "attack.t1570"],
    },
}

_DEFAULT_MITRE: dict[str, Any] = {
    "tactic": "execution",
    "technique_id": "T1059",
    "technique_name": "Command Execution",
    "tags": ["attack.execution", "attack.t1059"],
}


def _get_mitre(module: str, technique: str) -> dict[str, Any]:
    """Look up MITRE ATT&CK mapping for a module/technique pair."""
    return _MITRE_MAP.get((module, technique), _DEFAULT_MITRE)


# ---------------------------------------------------------------------------
# Sigma rule generation
# ---------------------------------------------------------------------------


def _sigma_rule_for_audit_step(
    step: dict[str, Any],
    chain_id: str,
    chain_name: str,
) -> str:
    """Generate a Sigma rule for a successful audit step."""
    step_id = step.get("step_id") or step.get("id", "unknown")
    technique = step.get("technique", "unknown")
    artifacts = step.get("artifacts", {})
    tool_name = artifacts.get("vulnerable_tool", "unknown")
    vuln_type = artifacts.get("vulnerability_type", "")

    mitre = _get_mitre("audit", technique)
    title_label = mitre.get("technique_name") or vuln_type or technique

    # Build OWASP reference from vulnerability_type if available
    owasp_ref = f" ({_sanitize_yaml_value(vuln_type)})" if vuln_type else ""

    s_tool = _sanitize_yaml_value(tool_name)
    s_technique = _sanitize_yaml_value(technique)
    s_chain_id = _sanitize_yaml_value(chain_id)
    s_step_id = _sanitize_yaml_value(step_id)

    lines = [
        f"title: MCP {title_label} Detected - {s_tool}",
        f"id: {uuid.uuid4()}",
        "status: experimental",
        "level: high",
        "description: |",
        f"  qai chain execution detected {s_technique} vulnerability",
        f"  in MCP tool '{s_tool}'{owasp_ref}.",
        f"  Generated from chain '{s_chain_id}', step '{s_step_id}'.",
        "logsource:",
        "  category: application",
        "  product: mcp-server",
        "detection:",
        "  selection:",
        f"    tool_name: {s_tool}",
        f"    technique: {s_technique}",
        "  condition: selection",
        "tags:",
    ]
    for tag in mitre["tags"]:
        lines.append(f"  - {tag}")
    lines.extend(
        [
            "fields:",
            "  - tool_name",
            "  - technique",
            "  - trust_boundary",
            "falsepositives:",
            "  - Legitimate tool usage with similar patterns",
            "references:",
            "  - https://owasp.org/www-project-mcp-top-10/",
        ]
    )
    return "\n".join(lines)


def _sigma_rule_for_inject_step(
    step: dict[str, Any],
    chain_id: str,
    chain_name: str,
) -> str:
    """Generate a Sigma rule for a successful inject step."""
    step_id = step.get("step_id") or step.get("id", "unknown")
    technique = step.get("technique", "unknown")
    artifacts = step.get("artifacts", {})
    outcome = artifacts.get("best_outcome", "unknown")
    mitre = _get_mitre("inject", technique)

    # Determine level based on outcome
    level = "critical" if "compliance" in outcome else "high"

    s_technique = _sanitize_yaml_value(technique)
    s_outcome = _sanitize_yaml_value(outcome)
    s_chain_id = _sanitize_yaml_value(chain_id)
    s_step_id = _sanitize_yaml_value(step_id)

    outcome_match = outcome.split("_")[-1] if "_" in outcome else outcome
    s_outcome_match = _sanitize_yaml_value(outcome_match)

    lines = [
        f"title: MCP Tool Poisoning Detected - {s_technique}",
        f"id: {uuid.uuid4()}",
        "status: experimental",
        f"level: {level}",
        "description: |",
        f"  qai chain execution achieved tool poisoning via {s_technique}",
        f"  technique with {s_outcome} outcome.",
        f"  Generated from chain '{s_chain_id}', step '{s_step_id}'.",
        "logsource:",
        "  category: application",
        "  product: mcp-agent",
        "detection:",
        "  selection:",
        f"    technique: {s_technique}",
        f"    outcome|contains: {s_outcome_match}",
        "  condition: selection",
        "tags:",
    ]
    for tag in mitre["tags"]:
        lines.append(f"  - {tag}")
    lines.extend(
        [
            "fields:",
            "  - technique",
            "  - payload_name",
            "  - compliance_rate",
            "falsepositives:",
            "  - Benign tool description updates",
            "references:",
            "  - https://owasp.org/www-project-mcp-top-10/",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wazuh rule generation
# ---------------------------------------------------------------------------

_WAZUH_RULE_ID_BASE = 100_000


def _wazuh_rule_for_audit_step(
    step: dict[str, Any],
    chain_id: str,
    chain_name: str,
    rule_id: int,
) -> str:
    """Generate a Wazuh XML rule for a successful audit step."""
    step_id = step.get("step_id") or step.get("id", "unknown")
    technique = step.get("technique", "unknown")
    artifacts = step.get("artifacts", {})
    tool_name = artifacts.get("vulnerable_tool", "unknown")

    mitre = _get_mitre("audit", technique)

    e_tool = _xml_escape(str(tool_name))
    e_technique = _xml_escape(str(technique))
    e_chain_id = _xml_escape(str(chain_id))
    e_step_id = _xml_escape(str(step_id))

    return (
        f'<rule id="{rule_id}" level="10">\n'
        f"  <description>MCP {e_technique} detected in tool '{e_tool}' "
        f"(chain '{e_chain_id}', step '{e_step_id}')</description>\n"
        f'  <field name="tool_name">{e_tool}</field>\n'
        f'  <field name="technique">{e_technique}</field>\n'
        f"  <mitre>\n"
        f"    <id>{_xml_escape(str(mitre['technique_id']))}</id>\n"
        f"  </mitre>\n"
        f"  <group>mcp,qai,{e_technique}</group>\n"
        f"</rule>"
    )


def _wazuh_rule_for_inject_step(
    step: dict[str, Any],
    chain_id: str,
    chain_name: str,
    rule_id: int,
) -> str:
    """Generate a Wazuh XML rule for a successful inject step."""
    step_id = step.get("step_id") or step.get("id", "unknown")
    technique = step.get("technique", "unknown")
    artifacts = step.get("artifacts", {})
    outcome = artifacts.get("best_outcome", "unknown")

    mitre = _get_mitre("inject", technique)

    e_technique = _xml_escape(str(technique))
    e_outcome = _xml_escape(str(outcome))
    e_chain_id = _xml_escape(str(chain_id))
    e_step_id = _xml_escape(str(step_id))

    return (
        f'<rule id="{rule_id}" level="12">\n'
        f"  <description>MCP tool poisoning via {e_technique} "
        f"with {e_outcome} outcome "
        f"(chain '{e_chain_id}', step '{e_step_id}')</description>\n"
        f'  <field name="technique">{e_technique}</field>\n'
        f'  <field name="outcome">{e_outcome}</field>\n'
        f"  <mitre>\n"
        f"    <id>{_xml_escape(str(mitre['technique_id']))}</id>\n"
        f"  </mitre>\n"
        f"  <group>mcp,qai,{e_technique}</group>\n"
        f"</rule>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_detection_rules(
    result: dict[str, Any],
    format: str = "sigma",  # noqa: A002
) -> list[str]:
    """Generate detection rules from chain execution results.

    Produces one rule per successful step. Failed steps are skipped
    (nothing to detect if the attack didn't work).

    Args:
        result: A chain result dict (as produced by ChainResult.to_dict()
            or loaded from a chain run JSON report).
        format: 'sigma' or 'wazuh'.

    Returns:
        List of rule strings (YAML for Sigma, XML for Wazuh).
    """
    if format not in ("sigma", "wazuh"):
        msg = f"Unsupported format '{format}'. Expected 'sigma' or 'wazuh'."
        raise ValueError(msg)

    chain_id = result.get("chain_id", "unknown")
    chain_name = result.get("chain_name", "")

    step_outputs = result.get("step_outputs", [])
    steps_from_tracer = result.get("steps", [])

    # Use step_outputs for live runs, fall back to tracer steps for dry-runs
    steps_to_analyze = step_outputs if step_outputs else steps_from_tracer

    rules: list[str] = []
    wazuh_rule_id = _WAZUH_RULE_ID_BASE

    for step in steps_to_analyze:
        # Determine success — default True for tracer steps (follow success path)
        success = step.get("success", True)
        if "status" in step:
            success = step.get("status") == "success"

        if not success:
            continue

        module = step.get("module", "unknown")

        if format == "sigma":
            if module == "audit":
                rules.append(_sigma_rule_for_audit_step(step, chain_id, chain_name))
            elif module == "inject":
                rules.append(_sigma_rule_for_inject_step(step, chain_id, chain_name))
        elif format == "wazuh":
            if module == "audit":
                wazuh_rule_id += 1
                rules.append(_wazuh_rule_for_audit_step(step, chain_id, chain_name, wazuh_rule_id))
            elif module == "inject":
                wazuh_rule_id += 1
                rules.append(_wazuh_rule_for_inject_step(step, chain_id, chain_name, wazuh_rule_id))

    return rules


def write_detection_rules(
    rules: list[str],
    output_path: Path,
    format: str = "sigma",  # noqa: A002
) -> Path:
    """Write detection rules to output file or directory.

    When output_path is a directory, writes one file per rule with naming
    ``{index}_{format}.{ext}``. When output_path is a file, concatenates
    all rules (separated by ``---`` for Sigma, wrapped in ``<group>`` for
    Wazuh).

    Args:
        rules: List of rule strings from generate_detection_rules.
        output_path: File or directory path.
        format: 'sigma' or 'wazuh'.

    Returns:
        The path where rules were written.
    """
    output_path = Path(output_path)
    if format == "sigma":
        ext = "yml"
    elif format == "wazuh":
        ext = "xml"
    else:
        msg = f"Unsupported format '{format}'. Expected 'sigma' or 'wazuh'."
        raise ValueError(msg)

    if output_path.suffix:
        # Treat as file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if format == "wazuh":
            combined = '<group name="qai">\n' + "\n\n".join(rules) + "\n</group>"
        else:
            combined = "\n---\n".join(rules)
        output_path.write_text(combined, encoding="utf-8")
    else:
        # Treat as directory
        output_path.mkdir(parents=True, exist_ok=True)
        for i, rule in enumerate(rules, 1):
            filename = f"rule_{i:03d}.{ext}"
            (output_path / filename).write_text(rule, encoding="utf-8")

    return output_path
