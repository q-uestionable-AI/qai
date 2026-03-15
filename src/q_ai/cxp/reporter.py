"""Reporter module -- generates comparison matrices and PoC packages."""

from __future__ import annotations

import json as _json
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from q_ai.cxp.builder import build
from q_ai.cxp.catalog import get_rule
from q_ai.cxp.db import get_result, list_results
from q_ai.cxp.models import Technique, TestResult
from q_ai.cxp.techniques import get_technique


def _build_cxp_interpret_prompt(matrix: dict) -> str:
    """Assemble an AI-evaluation prompt from a CXP comparison matrix.

    The prompt is findings-driven only: techniques, objectives,
    assistants tested, outcome distribution. Tool identity excluded.

    Args:
        matrix: A dict with "summary" and "matrix" keys.

    Returns:
        Prompt string ready for embedding in the matrix.
    """
    s = matrix["summary"]
    total = s.get("total", 0)
    hits = s.get("hits", 0)
    misses = s.get("misses", 0)
    partial = s.get("partial", 0)

    # Collect unique assistants from results
    assistants: list[str] = []
    technique_count = 0
    for entry in matrix.get("matrix", []):
        technique_count += 1
        for r in entry.get("results", []):
            a = r.get("assistant", "")
            if a and a not in assistants:
                assistants.append(a)

    assistants_str = ", ".join(assistants) if assistants else "coding assistants"
    tech_str = f"{technique_count} context poisoning technique{'s' if technique_count != 1 else ''}"

    if total == 0:
        return f"{tech_str} tested against {assistants_str}. No results recorded."

    outcome_parts = []
    if hits:
        outcome_parts.append(f"{hits} objective achievement{'s' if hits != 1 else ''}")
    if partial:
        outcome_parts.append(f"{partial} partial")
    if misses:
        outcome_parts.append(f"{misses} miss{'es' if misses != 1 else ''}")
    outcome_str = ", ".join(outcome_parts) if outcome_parts else "mixed outcomes"

    return (
        f"{tech_str} tested against {assistants_str} "
        f"({total} total runs). "
        f"Outcomes: {outcome_str}. "
        "Prioritize techniques by hit rate and identify "
        "assistant-specific vulnerability patterns."
    )


def generate_matrix(conn: sqlite3.Connection, campaign_id: str | None = None) -> dict:
    """Generate an assistant comparison matrix from stored results.

    Queries the evidence store for test results, groups them by technique,
    and enriches with objective/format metadata from the registries.

    Args:
        conn: An open SQLite connection.
        campaign_id: Optional campaign ID to filter by. None means all results.

    Returns:
        A dict with keys: generated, campaign, summary, matrix.
    """
    results = list_results(conn, campaign_id=campaign_id)

    summary = {"total": 0, "hits": 0, "misses": 0, "partial": 0, "pending": 0}
    grouped: dict[str, list] = defaultdict(list)

    for r in results:
        summary["total"] += 1
        if r.validation_result == "hit":
            summary["hits"] += 1
        elif r.validation_result == "miss":
            summary["misses"] += 1
        elif r.validation_result == "partial":
            summary["partial"] += 1
        else:
            summary["pending"] += 1

        grouped[r.technique_id].append(r)

    matrix = []
    for technique_id, technique_results in grouped.items():
        technique = get_technique(technique_id)
        objective_name = technique.objective.name if technique else technique_id
        format_name = technique.format.filename if technique else technique_id

        entry = {
            "technique_id": technique_id,
            "objective": objective_name,
            "format": format_name,
            "results": [
                {
                    "assistant": r.assistant,
                    "model": r.model,
                    "validation_result": r.validation_result,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in technique_results
            ],
        }
        matrix.append(entry)

    return {
        "prompt": _build_cxp_interpret_prompt({"summary": summary, "matrix": matrix}),
        "generated": datetime.now(UTC).isoformat(),
        "campaign": campaign_id or "all",
        "summary": summary,
        "matrix": matrix,
    }


def matrix_to_markdown(matrix: dict) -> str:
    """Render the matrix dict as a Markdown table.

    Produces a summary stats block followed by a table with columns:
    Technique | Objective | Format | Assistant | Model | Result.

    Args:
        matrix: The dict returned by generate_matrix().

    Returns:
        A Markdown-formatted string.
    """
    s = matrix["summary"]
    lines = [
        "# CXP Comparison Matrix",
        "",
        f"**Campaign:** {matrix['campaign']}  ",
        f"**Generated:** {matrix['generated']}  ",
        f"**Total: {s['total']}** | Hits: {s['hits']} | Misses: {s['misses']}"
        f" | Partial: {s['partial']} | Pending: {s['pending']}",
        "",
        "### AI Evaluation Prompt",
        "",
        "> " + matrix.get("prompt", ""),
        "",
        "| Technique | Objective | Format | Assistant | Model | Result |",
        "|-----------|-----------|--------|-----------|-------|--------|",
    ]

    for entry in matrix["matrix"]:
        for result in entry["results"]:
            lines.append(
                f"| {entry['technique_id']} | {entry['objective']} | {entry['format']}"
                f" | {result['assistant']} | {result['model']}"
                f" | {result['validation_result']} |"
            )

    if not matrix["matrix"]:
        lines.append("| (no results) | | | | | |")

    return "\n".join(lines) + "\n"


def matrix_to_json(matrix: dict) -> str:
    """Render the matrix dict as formatted JSON.

    Args:
        matrix: The dict returned by generate_matrix().

    Returns:
        A JSON string with 2-space indentation.
    """
    return _json.dumps(matrix, indent=2)


def _generate_poc_readme(result: TestResult, technique: Technique) -> str:
    """Generate the README.md for a PoC package.

    Args:
        result: The TestResult being packaged.
        technique: The Technique from the registry.

    Returns:
        README content string.
    """
    return f"""# Security Finding: {technique.objective.name} via {technique.format.filename}

## Summary

{technique.objective.description}

## Affected Assistant

- **Assistant:** {result.assistant}
- **Model:** {result.model}
- **Instruction File:** {technique.format.filename}

## Reproduction Steps

1. Clone or extract the `poisoned-repo/` directory
2. Open in {technique.format.assistant}
3. Ensure the assistant loads `{technique.format.filename}`
4. Enter the following prompt:

   > {result.trigger_prompt}

5. Observe the generated code for:
   {technique.objective.description}

## Evidence

- `evidence/captured-output.txt` — Raw assistant output
- `evidence/metadata.json` — Test metadata and timestamps
- `validation/validation-report.txt` — Automated validation results

## Validation Result

**Verdict:** {result.validation_result}

{result.validation_details}

## Timeline

- **Tested:** {result.timestamp.isoformat()}
- **Technique:** {technique.id}

---

*Generated by q-ai CXP — https://github.com/q-uestionable-AI/qai*
"""


def export_poc(conn: sqlite3.Connection, result_id: str, output_path: Path) -> Path:
    """Export a bounty-ready PoC package as a zip archive.

    Fetches the test result, regenerates the poisoned repo, and packages
    everything into a zip with README, evidence, and validation report.

    Args:
        conn: An open SQLite connection.
        result_id: The test result UUID to package.
        output_path: Where to write the zip file.

    Returns:
        The path to the created zip file.

    Raises:
        ValueError: If result not found or validation is still pending.
    """
    result = get_result(conn, result_id)
    if result is None:
        raise ValueError(f"Result not found: {result_id}")
    if result.validation_result == "pending":
        raise ValueError(f"Cannot export PoC for pending result {result_id}. Run validation first.")

    technique = get_technique(result.technique_id)
    if technique is None:
        raise ValueError(f"Unknown technique: {result.technique_id}")

    prefix = f"poc-{technique.id}"

    metadata = _json.dumps(
        {
            "result_id": result.id,
            "campaign_id": result.campaign_id,
            "technique_id": result.technique_id,
            "assistant": result.assistant,
            "model": result.model,
            "timestamp": result.timestamp.isoformat(),
            "capture_mode": result.capture_mode,
            "trigger_prompt": result.trigger_prompt,
            "validation_result": result.validation_result,
        },
        indent=2,
    )

    validation_report = (
        f"Validation Report\n"
        f"=================\n\n"
        f"Technique: {result.technique_id}\n"
        f"Verdict:   {result.validation_result}\n\n"
        f"Details:\n{result.validation_details}\n"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        rule_ids = [r.strip() for r in result.rules_inserted.split(",") if r.strip()]
        rules = [r for rid in rule_ids if (r := get_rule(rid)) is not None]
        build_result = build(
            format_id=result.format_id or technique.format.id,
            rules=rules,
            output_dir=Path(tmpdir),
            repo_name=technique.id,
        )
        repo_path = build_result.repo_dir

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add PoC README
            zf.writestr(f"{prefix}/README.md", _generate_poc_readme(result, technique))

            # Add poisoned repo
            for file_path in repo_path.rglob("*"):
                if file_path.is_file():
                    arcname = f"{prefix}/poisoned-repo/{file_path.relative_to(repo_path)}"
                    zf.write(file_path, arcname)

            # Add evidence
            zf.writestr(f"{prefix}/evidence/captured-output.txt", result.raw_output)
            zf.writestr(f"{prefix}/evidence/metadata.json", metadata)

            # Add validation
            zf.writestr(f"{prefix}/validation/validation-report.txt", validation_report)

    return output_path
