"""Chain execution engine.

Walks the chain step graph, dispatches each step to the appropriate
module (audit or inject), collects results, and routes on success/failure.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from q_ai.chain.artifacts import extract_audit_artifacts, extract_inject_artifacts
from q_ai.chain.executor_models import StepOutput, TargetConfig
from q_ai.chain.models import ChainDefinition, ChainResult, ChainStep, StepStatus
from q_ai.chain.variables import resolve_variables

logger = logging.getLogger(__name__)


async def execute_chain(
    chain: ChainDefinition,
    target_config: TargetConfig,
) -> ChainResult:
    """Execute an attack chain against real targets.

    Walks the step graph, dispatching each step to the appropriate
    module executor. Collects StepOutputs and routes based on
    success/failure. Accumulates artifacts in a shared namespace
    for variable resolution.

    Args:
        chain: Validated chain definition.
        target_config: Target configuration for audit and inject steps.

    Returns:
        ChainResult with all step outputs and evidence.
    """
    result = ChainResult(
        chain_id=chain.id,
        chain_name=chain.name,
        target_config={
            "audit_transport": target_config.audit_transport,
            "audit_command": "<configured>" if target_config.audit_command else None,
            "audit_url": "<configured>" if target_config.audit_url else None,
            "inject_model": target_config.inject_model,
        },
        dry_run=False,
    )

    if not chain.steps:
        result.finished_at = datetime.now(UTC)
        return result

    artifact_namespace: dict[str, dict[str, str]] = {}
    step_outputs: list[StepOutput] = []
    step_map = {s.id: s for s in chain.steps}
    step_ids_in_order = [s.id for s in chain.steps]
    visited: set[str] = set()
    seen_boundaries: set[str] = set()

    current_id: str | None = step_ids_in_order[0]

    while current_id is not None:
        # Cycle protection
        if current_id in visited:
            logger.warning("Cycle detected at step '%s', stopping", current_id)
            step = step_map.get(current_id)
            step_outputs.append(
                StepOutput(
                    step_id=current_id,
                    module=step.module if step else "unknown",
                    technique=step.technique if step else "unknown",
                    success=False,
                    status=StepStatus.FAILED,
                    error=f"Cycle detected at step '{current_id}'",
                    finished_at=datetime.now(UTC),
                )
            )
            break

        step = step_map.get(current_id)
        if step is None:
            logger.warning("Step '%s' not found in chain, stopping", current_id)
            step_outputs.append(
                StepOutput(
                    step_id=current_id,
                    module="unknown",
                    technique="unknown",
                    success=False,
                    status=StepStatus.FAILED,
                    error=f"Step '{current_id}' not found in chain",
                    finished_at=datetime.now(UTC),
                )
            )
            break

        visited.add(current_id)

        # Track trust boundaries
        if step.trust_boundary and step.trust_boundary not in seen_boundaries:
            seen_boundaries.add(step.trust_boundary)
            result.trust_boundaries_crossed.append(step.trust_boundary)

        # Resolve input variables
        try:
            resolved_inputs = resolve_variables(step.inputs, artifact_namespace)
        except ValueError as exc:
            logger.warning("Variable resolution failed for step '%s': %s", step.id, exc)
            step_output = StepOutput(
                step_id=step.id,
                module=step.module,
                technique=step.technique,
                success=False,
                status=StepStatus.FAILED,
                error=str(exc),
                finished_at=datetime.now(UTC),
            )
            step_outputs.append(step_output)
            artifact_namespace[step.id] = step_output.artifacts
            current_id = _route_failure(step, step_ids_in_order)
            continue

        # Dispatch based on module
        try:
            if step.module == "audit":
                step_output = await execute_audit_step(step, target_config, resolved_inputs)
            elif step.module == "inject":
                step_output = await execute_inject_step(step, target_config, resolved_inputs)
            else:
                step_output = StepOutput(
                    step_id=step.id,
                    module=step.module,
                    technique=step.technique,
                    success=False,
                    status=StepStatus.FAILED,
                    error=f"Unknown module: {step.module}",
                    finished_at=datetime.now(UTC),
                )
        except Exception as exc:
            logger.exception("Unexpected error in step '%s'", step.id)
            step_output = StepOutput(
                step_id=step.id,
                module=step.module,
                technique=step.technique,
                success=False,
                status=StepStatus.FAILED,
                error=str(exc),
                finished_at=datetime.now(UTC),
            )

        # Accumulate artifacts and outputs
        artifact_namespace[step.id] = step_output.artifacts
        step_outputs.append(step_output)

        # Route based on result
        if step_output.success:
            current_id = _route_success(step, step_ids_in_order)
        else:
            current_id = _route_failure(step, step_ids_in_order)

    result.step_outputs = step_outputs
    result.finished_at = datetime.now(UTC)
    return result


def _route_success(step: ChainStep, step_ids_in_order: list[str]) -> str | None:
    """Determine next step on success.

    Args:
        step: Current chain step.
        step_ids_in_order: Ordered step IDs for implicit routing.

    Returns:
        Next step ID, or None to stop.
    """
    if step.terminal:
        return None
    if step.on_success == "abort":
        return None
    if step.on_success is not None:
        return step.on_success
    # Implicit next-in-order
    try:
        idx = step_ids_in_order.index(step.id)
        next_idx = idx + 1
        if next_idx < len(step_ids_in_order):
            return step_ids_in_order[next_idx]
    except ValueError:
        pass
    return None


def _route_failure(step: ChainStep, step_ids_in_order: list[str]) -> str | None:
    """Determine next step on failure.

    Args:
        step: Current chain step.
        step_ids_in_order: Ordered step IDs for implicit routing.

    Returns:
        Next step ID, or None to stop (abort).
    """
    if step.on_failure == "abort" or step.on_failure is None:
        return None
    # on_failure points to a specific step ID
    return step.on_failure


async def execute_audit_step(
    step: ChainStep,
    target_config: TargetConfig,
    resolved_inputs: dict[str, Any],
) -> StepOutput:
    """Execute an audit scan step.

    Connects to the target MCP server using the target config,
    runs the scanner specified by step.technique, and extracts
    standard artifacts from the results.

    Args:
        step: The chain step to execute.
        target_config: Audit target connection config.
        resolved_inputs: Resolved variable values from upstream steps.

    Returns:
        StepOutput with scan results and artifacts.
    """
    from q_ai.audit.orchestrator import run_scan

    started_at = datetime.now(UTC)

    if not target_config.audit_transport:
        return StepOutput(
            step_id=step.id,
            module="audit",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error="No audit transport configured in target config",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    # Build MCP connection from target config
    try:
        conn = _build_audit_connection(target_config)
    except ValueError as exc:
        return StepOutput(
            step_id=step.id,
            module="audit",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=str(exc),
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    try:
        async with conn:
            scan_result = await run_scan(conn, check_names=[step.technique])
    except Exception as exc:
        return StepOutput(
            step_id=step.id,
            module="audit",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=f"Audit scan failed: {exc}",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    artifacts = extract_audit_artifacts(scan_result)
    success = len(scan_result.findings) > 0

    return StepOutput(
        step_id=step.id,
        module="audit",
        technique=step.technique,
        success=success,
        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
        scan_result=scan_result,
        artifacts=artifacts,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def _build_audit_connection(target_config: TargetConfig) -> Any:
    """Build an MCPConnection from target config.

    Args:
        target_config: Target configuration with transport details.

    Returns:
        Configured MCPConnection (not yet connected).

    Raises:
        ValueError: If required config is missing for the transport.
    """
    from q_ai.mcp.connection import MCPConnection

    transport = target_config.audit_transport
    if transport == "stdio":
        if not target_config.audit_command:
            raise ValueError("audit_command is required for stdio transport")
        cmd = target_config.audit_command
        return MCPConnection.stdio(command=cmd[0], args=cmd[1:])
    elif transport == "sse":
        if not target_config.audit_url:
            raise ValueError("audit_url is required for SSE transport")
        return MCPConnection.sse(url=target_config.audit_url)
    elif transport == "streamable-http":
        if not target_config.audit_url:
            raise ValueError("audit_url is required for streamable-http transport")
        return MCPConnection.streamable_http(url=target_config.audit_url)
    else:
        raise ValueError(f"Unknown audit transport: {transport}")


async def execute_inject_step(
    step: ChainStep,
    target_config: TargetConfig,
    resolved_inputs: dict[str, Any],
) -> StepOutput:
    """Execute an inject campaign step.

    Loads the first payload template matching the step's technique,
    applies input overrides from upstream steps, runs a single-round
    campaign, and extracts standard artifacts.

    Args:
        step: The chain step to execute.
        target_config: Inject target model config.
        resolved_inputs: Resolved variable values from upstream steps.

    Returns:
        StepOutput with campaign results and artifacts.
    """
    from q_ai.inject.campaign import run_campaign
    from q_ai.inject.models import InjectionOutcome, InjectionTechnique
    from q_ai.inject.payloads.loader import filter_templates, load_all_templates

    started_at = datetime.now(UTC)

    model = target_config.inject_model
    if not model:
        return StepOutput(
            step_id=step.id,
            module="inject",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error="No inject_model configured in target config",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    # Convert technique string to enum
    try:
        technique_enum = InjectionTechnique(step.technique)
    except ValueError:
        return StepOutput(
            step_id=step.id,
            module="inject",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=f"Unknown injection technique: {step.technique}",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    # Load and filter templates
    templates = load_all_templates()
    matched = filter_templates(templates, technique=technique_enum)

    if not matched:
        return StepOutput(
            step_id=step.id,
            module="inject",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=f"No templates found for technique: {step.technique}",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    # Take first template only (deterministic), copy to avoid mutation
    template = deepcopy(matched[0])

    # Apply input overrides from resolved_inputs
    for key, value in resolved_inputs.items():
        if hasattr(template, key):
            setattr(template, key, value)

    # Run single-round campaign
    try:
        campaign = await run_campaign(
            templates=[template],
            model=model,
            rounds=1,
        )
    except Exception as exc:
        return StepOutput(
            step_id=step.id,
            module="inject",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=f"Inject campaign failed: {exc}",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    artifacts = extract_inject_artifacts(campaign)

    # Determine success: any result with FULL_COMPLIANCE or PARTIAL_COMPLIANCE
    success = any(
        r.outcome in (InjectionOutcome.FULL_COMPLIANCE, InjectionOutcome.PARTIAL_COMPLIANCE)
        for r in campaign.results
    )

    return StepOutput(
        step_id=step.id,
        module="inject",
        technique=step.technique,
        success=success,
        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
        campaign=campaign,
        artifacts=artifacts,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def write_chain_report(result: ChainResult, output_path: Path) -> Path:
    """Write a chain execution report to JSON.

    Args:
        result: The completed chain result.
        output_path: Path to write the JSON report.

    Returns:
        The path where the report was written.
    """
    report = result.to_dict()

    # Enrich step outputs with module-specific summaries
    enriched_outputs = []
    for step_output in result.step_outputs:
        step_dict = step_output.to_dict() if hasattr(step_output, "to_dict") else {}

        # Add audit-specific summary
        if step_output.module == "audit" and step_output.scan_result is not None:
            findings = getattr(step_output.scan_result, "findings", []) or []
            severity_counts: dict[str, int] = {}
            for f in findings:
                sev = str(getattr(f, "severity", "unknown")).lower()
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
            step_dict["total_findings"] = len(findings)
            step_dict["severity_summary"] = severity_counts

        # Add inject-specific summary
        if step_output.module == "inject" and step_output.campaign is not None:
            campaign = step_output.campaign
            if hasattr(campaign, "results") and campaign.results:
                first_result = campaign.results[0]
                step_dict["outcome"] = str(getattr(first_result, "outcome", ""))
                step_dict["payload_name"] = str(getattr(first_result, "payload_name", ""))

        enriched_outputs.append(step_dict)

    report["step_outputs"] = enriched_outputs

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path
