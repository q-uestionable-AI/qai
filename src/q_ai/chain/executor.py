"""Chain execution engine.

Walks the chain step graph, dispatches each step to the appropriate
module (audit or inject), collects results, and routes on success/failure.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from q_ai.chain.artifacts import (
    extract_audit_artifacts,
    extract_cxp_artifacts,
    extract_inject_artifacts,
    extract_ipi_artifacts,
    extract_rxp_artifacts,
)
from q_ai.chain.executor_models import StepOutput, TargetConfig
from q_ai.chain.models import ChainDefinition, ChainResult, ChainStep, StepStatus
from q_ai.chain.variables import resolve_variables

logger = logging.getLogger(__name__)


def _build_initial_result(
    chain: ChainDefinition,
    target_config: TargetConfig,
) -> ChainResult:
    """Build the initial ChainResult with target config metadata.

    Args:
        chain: The chain definition being executed.
        target_config: Target configuration for audit and inject steps.

    Returns:
        A ChainResult pre-populated with chain metadata and target config.
    """
    return ChainResult(
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


async def _dispatch_step(
    step: ChainStep,
    target_config: TargetConfig,
    resolved_inputs: dict[str, Any],
) -> StepOutput:
    """Dispatch a single chain step to the appropriate module executor.

    Routes to audit, inject, ipi, cxp, or rxp executors based on
    step.module, or returns a failed StepOutput for unknown modules.

    Args:
        step: The chain step to execute.
        target_config: Target configuration for the step.
        resolved_inputs: Resolved variable values from upstream steps.

    Returns:
        StepOutput from the module executor.
    """
    if step.module == "audit":
        return await execute_audit_step(step, target_config, resolved_inputs)
    if step.module == "inject":
        return await execute_inject_step(step, target_config, resolved_inputs)
    if step.module == "ipi":
        return await execute_ipi_step(step, target_config, resolved_inputs)
    if step.module == "cxp":
        return await execute_cxp_step(step, target_config, resolved_inputs)
    if step.module == "rxp":
        return await execute_rxp_step(step, target_config, resolved_inputs)
    return StepOutput(
        step_id=step.id,
        module=step.module,
        technique=step.technique,
        success=False,
        status=StepStatus.FAILED,
        error=f"Unknown module: {step.module}",
        finished_at=datetime.now(UTC),
    )


async def _execute_step_loop(  # noqa: PLR0913
    current_id: str,
    step_map: dict[str, ChainStep],
    visited: set[str],
    seen_boundaries: set[str],
    result: ChainResult,
    artifact_namespace: dict[str, dict[str, str]],
    step_outputs: list[StepOutput],
    step_ids_in_order: list[str],
    target_config: TargetConfig,
    gate_callback: Callable[[str, str], Any] | None,
) -> str | None:
    """Execute one step of the chain loop and return the next step ID.

    Returns None to signal the loop should stop.
    """
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
        return None

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
        return None

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
        return _route_failure(step, step_ids_in_order)

    # Dispatch based on module
    try:
        step_output = await _dispatch_step(step, target_config, resolved_inputs)
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

    # Handle manual gate for IPI/CXP steps
    if step_output.success and _step_has_manual_gate(step, resolved_inputs):
        step_output = await _handle_manual_gate(step, step_output, gate_callback)

    # Accumulate artifacts and outputs
    artifact_namespace[step.id] = step_output.artifacts
    step_outputs.append(step_output)

    # Route based on result
    if step_output.success:
        return _route_success(step, step_ids_in_order)
    return _route_failure(step, step_ids_in_order)


_GATE_MESSAGES: dict[str, str] = {
    "ipi": "Deploy IPI payloads and click Resume",
    "cxp": "Open the repo in your coding assistant and run the trigger prompt, then click Resume",
}

_NO_GATE_ERROR = (
    "This chain requires a manual gate but no gate callback is configured. "
    "Use the web UI for chains with manual gates."
)


async def execute_chain(
    chain: ChainDefinition,
    target_config: TargetConfig,
    gate_callback: Callable[[str, str], Any] | None = None,
) -> ChainResult:
    """Execute an attack chain against real targets.

    Walks the step graph, dispatching each step to the appropriate
    module executor. Collects StepOutputs and routes based on
    success/failure. Accumulates artifacts in a shared namespace
    for variable resolution.

    Args:
        chain: Validated chain definition.
        target_config: Target configuration for all module steps.
        gate_callback: Optional async callable(step_id, message) that blocks
            until the operator resumes. Required for chains with manual gates.
            When None, manual gate steps fail with a clear message.

    Returns:
        ChainResult with all step outputs and evidence.
    """
    result = _build_initial_result(chain, target_config)

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
        current_id = await _execute_step_loop(
            current_id,
            step_map,
            visited,
            seen_boundaries,
            result,
            artifact_namespace,
            step_outputs,
            step_ids_in_order,
            target_config,
            gate_callback,
        )

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
    if transport == "sse":
        if not target_config.audit_url:
            raise ValueError("audit_url is required for SSE transport")
        return MCPConnection.sse(url=target_config.audit_url)
    if transport == "streamable-http":
        if not target_config.audit_url:
            raise ValueError("audit_url is required for streamable-http transport")
        return MCPConnection.streamable_http(url=target_config.audit_url)
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


async def _handle_manual_gate(
    step: ChainStep,
    step_output: StepOutput,
    gate_callback: Callable[[str, str], Any] | None,
) -> StepOutput:
    """Handle manual gate pause after a successful step.

    If gate_callback is provided, sets status to WAITING and awaits the
    callback. If gate_callback is None, returns a failed StepOutput.

    Args:
        step: The chain step requiring a gate.
        step_output: The successful step output to gate.
        gate_callback: Async callable or None.

    Returns:
        Original step_output (resumed) or a failed replacement.
    """
    gate_msg = _GATE_MESSAGES.get(step.module, "Manual gate: resume to continue")
    if gate_callback is None:
        return StepOutput(
            step_id=step.id,
            module=step.module,
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=_NO_GATE_ERROR,
            started_at=step_output.started_at,
            finished_at=datetime.now(UTC),
        )
    step_output.status = StepStatus.WAITING
    await gate_callback(step.id, gate_msg)
    step_output.status = StepStatus.SUCCESS
    return step_output


def _step_has_manual_gate(step: ChainStep, resolved_inputs: dict[str, Any]) -> bool:
    """Check if a step requires a manual gate pause.

    A step has a manual gate if ``manual_gate`` is truthy in either
    the step's inputs or the resolved inputs.

    Args:
        step: The chain step to check.
        resolved_inputs: Resolved variable values from upstream steps.

    Returns:
        True if the step should pause for manual gate.
    """
    if step.inputs.get("manual_gate"):
        return True
    return bool(resolved_inputs.get("manual_gate"))


async def execute_ipi_step(
    step: ChainStep,
    target_config: TargetConfig,
    resolved_inputs: dict[str, Any],
) -> StepOutput:
    """Execute an IPI payload generation step.

    Generates IPI payloads using generate_documents(). Runs in a
    thread pool since the generator is synchronous.

    Args:
        step: The chain step to execute.
        target_config: Target configuration with IPI settings.
        resolved_inputs: Resolved variable values from upstream steps.

    Returns:
        StepOutput with generate_result and artifacts.
    """
    import asyncio

    started_at = datetime.now(UTC)

    callback_url = (
        resolved_inputs.get("callback_url")
        or target_config.ipi_callback_url
        or "http://localhost:8080"
    )
    output_dir = resolved_inputs.get("output_dir") or target_config.ipi_output_dir
    if not output_dir:
        return StepOutput(
            step_id=step.id,
            module="ipi",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error="No ipi_output_dir configured in target config or step inputs",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    # step.technique is the IPI Format (pdf, html, etc.), not an IPI Technique
    format_name = resolved_inputs.get("format") or target_config.ipi_format or step.technique
    techniques_raw = resolved_inputs.get("techniques")

    try:
        from q_ai.ipi.generate_service import generate_documents
        from q_ai.ipi.generators import get_techniques_for_format
        from q_ai.ipi.models import Format, Technique

        fmt = Format(format_name)
        if techniques_raw:
            if isinstance(techniques_raw, str):
                technique_list = [Technique(t.strip()) for t in techniques_raw.split(",")]
            else:
                technique_list = [Technique(t) for t in techniques_raw]
        else:
            # Use all available techniques for this format
            technique_list = get_techniques_for_format(fmt)

        generate_result = await asyncio.to_thread(
            generate_documents,
            callback_url=callback_url,
            output=Path(output_dir),
            format_name=fmt,
            techniques=technique_list,
        )
    except Exception as exc:
        return StepOutput(
            step_id=step.id,
            module="ipi",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=f"IPI generation failed: {exc}",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    artifacts = extract_ipi_artifacts(generate_result)
    campaigns = getattr(generate_result, "campaigns", []) or []
    success = len(campaigns) > 0

    if success:
        try:
            from q_ai.ipi.guidance_builder import build_ipi_guidance

            payload_style = resolved_inputs.get("payload_style", "obvious")
            payload_type = resolved_inputs.get("payload_type", "callback")
            guidance = build_ipi_guidance(
                result=generate_result,
                format_name=fmt,
                callback_url=callback_url,
                payload_style=payload_style,
                payload_type=payload_type,
            )
            artifacts["guidance"] = json.dumps(guidance.to_dict())
        except Exception:
            logger.debug("Failed to build IPI guidance for chain step", exc_info=True)

    return StepOutput(
        step_id=step.id,
        module="ipi",
        technique=step.technique,
        success=success,
        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
        generate_result=generate_result,
        artifacts=artifacts,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


async def execute_cxp_step(
    step: ChainStep,
    target_config: TargetConfig,
    resolved_inputs: dict[str, Any],
) -> StepOutput:
    """Execute a CXP poisoned repo build step.

    Builds a poisoned context repo using the CXP builder. Runs in a
    thread pool since the builder is synchronous.

    Args:
        step: The chain step to execute.
        target_config: Target configuration with CXP settings.
        resolved_inputs: Resolved variable values from upstream steps.

    Returns:
        StepOutput with build_result and artifacts.
    """
    import asyncio

    started_at = datetime.now(UTC)

    format_id = resolved_inputs.get("format_id") or target_config.cxp_format_id
    if not format_id:
        return StepOutput(
            step_id=step.id,
            module="cxp",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error="No cxp_format_id configured in target config or step inputs",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    output_dir = resolved_inputs.get("output_dir") or target_config.cxp_output_dir
    if not output_dir:
        return StepOutput(
            step_id=step.id,
            module="cxp",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error="No cxp_output_dir configured in target config or step inputs",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    rule_ids_raw = resolved_inputs.get("rule_ids") or target_config.cxp_rule_ids or []
    if isinstance(rule_ids_raw, str):
        rule_ids_raw = [r.strip() for r in rule_ids_raw.split(",") if r.strip()]

    try:
        from q_ai.cxp.builder import build
        from q_ai.cxp.catalog import get_rule

        rules = []
        for rid in rule_ids_raw:
            rule = get_rule(rid)
            if rule is not None:
                rules.append(rule)
            else:
                logger.warning("CXP rule ID '%s' not found in catalog, skipping", rid)

        build_result = await asyncio.to_thread(
            build,
            format_id=format_id,
            rules=rules,
            output_dir=Path(output_dir),
            repo_name=f"chain-cxp-{step.id}",
        )
    except Exception as exc:
        return StepOutput(
            step_id=step.id,
            module="cxp",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=f"CXP build failed: {exc}",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    artifacts = extract_cxp_artifacts(build_result)
    success = bool(getattr(build_result, "repo_dir", None))

    if success:
        try:
            from q_ai.cxp.guidance_builder import build_cxp_guidance

            guidance = build_cxp_guidance(
                result=build_result,
                rules=rules,
                format_id=format_id,
            )
            artifacts["guidance"] = json.dumps(guidance.to_dict())
        except Exception:
            logger.debug("Failed to build CXP guidance for chain step", exc_info=True)

    return StepOutput(
        step_id=step.id,
        module="cxp",
        technique=step.technique,
        success=success,
        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
        build_result=build_result,
        artifacts=artifacts,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


async def execute_rxp_step(
    step: ChainStep,
    target_config: TargetConfig,
    resolved_inputs: dict[str, Any],
) -> StepOutput:
    """Execute an RXP retrieval validation step.

    Runs retrieval validation with lazy dependency import. Handles
    missing optional deps gracefully.

    Args:
        step: The chain step to execute.
        target_config: Target configuration with RXP settings.
        resolved_inputs: Resolved variable values from upstream steps.

    Returns:
        StepOutput with validation_result and artifacts.
    """
    import asyncio

    started_at = datetime.now(UTC)

    model_id = resolved_inputs.get("model_id") or target_config.rxp_model_id
    if not model_id:
        return StepOutput(
            step_id=step.id,
            module="rxp",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error="No rxp_model_id configured in target config or step inputs",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    profile_id = resolved_inputs.get("profile_id") or target_config.rxp_profile_id
    top_k = resolved_inputs.get("top_k") or target_config.rxp_top_k or 5

    if not profile_id:
        return StepOutput(
            step_id=step.id,
            module="rxp",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error="RXP step requires a profile_id or rxp_profile_id in target config",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    try:
        from q_ai.rxp._deps import require_rxp_deps

        require_rxp_deps()
    except ImportError as exc:
        return StepOutput(
            step_id=step.id,
            module="rxp",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=str(exc),
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    try:
        from q_ai.rxp.profiles import get_profile, load_corpus, load_poison
        from q_ai.rxp.validator import validate_retrieval

        prof = get_profile(profile_id)
        if prof is None:
            return StepOutput(
                step_id=step.id,
                module="rxp",
                technique=step.technique,
                success=False,
                status=StepStatus.FAILED,
                error=f"RXP profile not found: {profile_id!r}",
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        corpus_docs = load_corpus(prof)
        poison_docs = load_poison(prof)
        queries = prof.queries

        validation_result = await asyncio.to_thread(
            validate_retrieval, corpus_docs, poison_docs, queries, model_id, int(top_k)
        )
    except Exception as exc:
        return StepOutput(
            step_id=step.id,
            module="rxp",
            technique=step.technique,
            success=False,
            status=StepStatus.FAILED,
            error=f"RXP validation failed: {exc}",
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    artifacts = extract_rxp_artifacts(validation_result)
    success = validation_result.retrieval_rate > 0

    return StepOutput(
        step_id=step.id,
        module="rxp",
        technique=step.technique,
        success=success,
        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
        validation_result=validation_result,
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

        # Add IPI-specific summary
        if step_output.module == "ipi" and step_output.generate_result is not None:
            campaigns = getattr(step_output.generate_result, "campaigns", []) or []
            step_dict["payload_count"] = len(campaigns)

        # Add CXP-specific summary
        if step_output.module == "cxp" and step_output.build_result is not None:
            step_dict["repo_dir"] = str(getattr(step_output.build_result, "repo_dir", ""))

        # Add RXP-specific summary
        if step_output.module == "rxp" and step_output.validation_result is not None:
            step_dict["retrieval_rate"] = getattr(
                step_output.validation_result, "retrieval_rate", 0.0
            )

        enriched_outputs.append(step_dict)

    report["step_outputs"] = enriched_outputs

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path
