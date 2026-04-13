"""Workflow service — validation and configuration building for workflow launches.

Extracted from the route layer so HTTP handlers remain thin transport adapters.
Functions raise :class:`WorkflowValidationError` on failure; route handlers
convert these to ``JSONResponse(status_code=422, ...)``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import keyring.errors

from q_ai.core.config import get_credential
from q_ai.core.db import get_connection, get_setting
from q_ai.core.providers import (
    ProviderType,
    fetch_models,
    get_provider,
)

logger = logging.getLogger(__name__)

_VALID_TRANSPORTS = {"stdio", "sse", "streamable-http"}


class WorkflowValidationError(Exception):
    """Raised when workflow launch input fails validation.

    Carries a ``detail`` string that route handlers forward in a 422
    response body (shape: ``{"detail": "..."}``).
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _str_field(body: dict[str, Any], key: str, default: str = "") -> str:
    """Extract a string field from a request body, rejecting non-string values.

    Args:
        body: The parsed request body dict.
        key: The field name to extract.
        default: Default value if key is missing or None.

    Returns:
        The stripped string value.

    Raises:
        TypeError: If the value is present but not a string.
    """
    val = body.get(key, default)
    if val is None:
        return default
    if not isinstance(val, str):
        raise TypeError(f"'{key}' must be a string")
    return val.strip()


def validate_transport_and_model(body: dict[str, Any]) -> None:
    """Validate transport, command/url, and model fields.

    Args:
        body: The parsed request body dict.

    Raises:
        WorkflowValidationError: If any transport/model field is invalid.
    """
    transport = body.get("transport", "").strip()
    if transport not in _VALID_TRANSPORTS:
        raise WorkflowValidationError(
            f"Invalid transport. Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
        )

    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None

    if transport == "stdio" and not command:
        raise WorkflowValidationError("command is required for stdio transport")
    if transport in ("sse", "streamable-http") and not url:
        raise WorkflowValidationError("url is required for sse/streamable-http transport")

    model = body.get("model", "").strip()
    if not model or "/" not in model:
        raise WorkflowValidationError("model must be non-empty and in provider/model format")


def validate_transport_and_command(body: dict[str, Any]) -> None:
    """Validate transport and command/url fields (no model check).

    Args:
        body: The parsed request body dict.

    Raises:
        WorkflowValidationError: If transport or command/url is invalid.
        TypeError: If any field has a non-string value.
    """
    transport = _str_field(body, "transport")
    if transport not in _VALID_TRANSPORTS:
        raise WorkflowValidationError(
            f"Invalid transport. Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
        )
    command = _str_field(body, "command") or None
    url = _str_field(body, "url") or None
    if transport == "stdio" and not command:
        raise WorkflowValidationError("command is required for stdio transport")
    if transport in ("sse", "streamable-http") and not url:
        raise WorkflowValidationError("url is required for sse/streamable-http transport")


def validate_campaign_fields(body: dict[str, Any]) -> None:
    """Validate campaign-specific fields (model and rounds).

    Args:
        body: The parsed request body dict.

    Raises:
        WorkflowValidationError: If model or rounds is invalid.
        TypeError: If the model field has a non-string value.
    """
    model = _str_field(body, "model")
    if not model or "/" not in model:
        raise WorkflowValidationError("model must be non-empty and in provider/model format")
    raw_rounds = body.get("rounds", 1)
    if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
        raise WorkflowValidationError("rounds must be an integer")
    if not 1 <= raw_rounds <= 10:
        raise WorkflowValidationError("rounds must be an integer between 1 and 10")


def _read_provider_config(provider_name: str, db_path: Path | None) -> tuple[str | None, str]:
    """Read (credential, base_url) for a provider (blocking DB + keyring).

    Treats any keyring failure (insecure backend, locked keyring, other
    keyring errors) as an unconfigured credential so a flaky keyring
    surfaces the same "not configured" error as a missing key rather than
    a 500 from an uncaught exception.
    """
    try:
        cred_value = get_credential(provider_name)
    except (RuntimeError, keyring.errors.KeyringError):
        cred_value = None
    with get_connection(db_path) as conn:
        base_url_value = get_setting(conn, f"{provider_name}.base_url") or ""
    return cred_value, base_url_value


async def validate_provider_model(body: dict[str, Any], db_path: Path | None) -> None:
    """Validate provider/model pair before launch.

    Checks that the provider is known and configured, that the model is
    non-empty, and that local providers are reachable.

    Args:
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Raises:
        WorkflowValidationError: On any validation failure.
    """
    import asyncio

    provider_name = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()

    if not provider_name and model and "/" in model:
        provider_name = model.split("/", 1)[0]
    if not provider_name:
        raise WorkflowValidationError("provider is required")

    config = get_provider(provider_name)
    if config is None:
        raise WorkflowValidationError(f"Unknown provider: {provider_name}")

    cred, base_url = await asyncio.to_thread(_read_provider_config, provider_name, db_path)

    configured = cred is not None or bool(base_url)
    if not configured and config.type != ProviderType.CUSTOM:
        raise WorkflowValidationError(f"Provider '{provider_name}' is not configured")

    if not model:
        raise WorkflowValidationError("No model selected")

    if config.type == ProviderType.LOCAL:
        result = await fetch_models(provider_name, base_url or None)
        if result.error:
            raise WorkflowValidationError(result.error)


def build_assess_config(body: dict[str, Any], target_id: str) -> dict[str, Any]:
    """Build config for the assess workflow.

    Raises:
        WorkflowValidationError: If transport/model/rounds are invalid.
    """
    validate_transport_and_model(body)

    transport = body.get("transport", "").strip()
    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None
    model = body.get("model", "").strip()

    raw_rounds = body.get("rounds", 1)
    if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
        raise WorkflowValidationError("rounds must be an integer")
    rounds = raw_rounds
    if not 1 <= rounds <= 10:
        raise WorkflowValidationError("rounds must be an integer between 1 and 10")
    rxp_enabled = bool(body.get("rxp_enabled", False))

    raw_techniques = body.get("techniques")
    techniques: list[str] | None = None
    if isinstance(raw_techniques, list):
        techniques = [str(t) for t in raw_techniques]

    raw_payloads = body.get("payload_names")
    payloads: list[str] | None = None
    if isinstance(raw_payloads, list):
        payloads = [str(p) for p in raw_payloads]

    raw_checks = body.get("checks")
    checks: list[str] | None = None
    if isinstance(raw_checks, list):
        checks = [str(c) for c in raw_checks]

    return {
        "target_id": target_id,
        "transport": transport,
        "command": command,
        "url": url,
        "rxp_enabled": rxp_enabled,
        "audit": {"checks": checks},
        "inject": {
            "model": model,
            "rounds": rounds,
            "techniques": techniques,
            "payloads": payloads,
        },
        "proxy": {"intercept": False},
    }


def build_test_docs_config(body: dict[str, Any], target_id: str) -> dict[str, Any]:
    """Build config for the test_docs workflow.

    Raises:
        WorkflowValidationError: If callback_url is missing.
    """
    callback_url = body.get("callback_url", "").strip()
    if not callback_url:
        raise WorkflowValidationError("callback_url is required")
    return {
        "target_id": target_id,
        "callback_url": callback_url,
        "output_dir": "",
        "format": body.get("format", "pdf"),
        "payload_style": body.get("payload_style", "obvious"),
        "payload_type": body.get("payload_type", "callback"),
        "base_name": "report",
        "rxp_enabled": bool(body.get("rxp_enabled", False)),
        "rxp": {
            "model_id": body.get("rxp_model_id", ""),
            "profile_id": body.get("rxp_profile_id") or None,
            "target_id": target_id,
        },
    }


def build_test_assistant_config(body: dict[str, Any], target_id: str) -> dict[str, Any]:
    """Build config for the test_assistant workflow.

    Raises:
        WorkflowValidationError: If format_id is missing.
    """
    format_id = body.get("format_id", "").strip()
    if not format_id:
        raise WorkflowValidationError("format_id is required")
    return {
        "target_id": target_id,
        "format_id": format_id,
        "rule_ids": body.get("rule_ids") or None,
        "output_dir": "",
        "repo_name": body.get("repo_name", "").strip() or None,
    }


def build_trace_path_config(
    body: dict[str, Any], target_id: str, db_path: Path | None
) -> dict[str, Any]:
    """Build config for the trace_path workflow.

    Args:
        body: The parsed request body dict.
        target_id: The target identifier (unused — reserved for signature parity).
        db_path: Path to the SQLite database (unused — reserved for signature parity).

    Raises:
        WorkflowValidationError: If the chain template is missing or not found,
            or if transport/model validation fails.
    """
    # db_path is accepted for signature parity with the dispatcher; not needed here.
    del db_path

    from q_ai.chain.loader import discover_chains, load_chain

    template_id = body.get("chain_template_id", "").strip()
    if not template_id:
        raise WorkflowValidationError("chain_template_id is required")

    chain_file: str | None = None
    try:
        for path in discover_chains():
            chain = load_chain(path)
            if chain.id == template_id:
                chain_file = str(path.resolve())
                break
    except Exception:
        logger.debug("Failed to load chain templates", exc_info=True)

    if chain_file is None:
        raise WorkflowValidationError(f"Chain template not found: {template_id}")

    validate_transport_and_model(body)

    transport = body.get("transport", "").strip()
    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None
    model = body.get("model", "").strip()

    return {
        "target_id": target_id,
        "chain_file": chain_file,
        "transport": transport,
        "command": command,
        "url": url,
        "inject_model": model,
    }


def build_blast_radius_config(body: dict[str, Any], db_path: Path | None) -> dict[str, Any]:
    """Build config for the blast_radius workflow.

    Derives target_id from the chain execution's run row instead of creating
    a new target.

    Raises:
        WorkflowValidationError: If the chain execution id is missing or not found.
    """
    exec_id = body.get("chain_execution_id", "").strip()
    if not exec_id:
        raise WorkflowValidationError("chain_execution_id is required")
    with get_connection(db_path) as conn:
        exec_row = conn.execute(
            "SELECT ce.id, r.target_id FROM chain_executions ce "
            "JOIN runs r ON r.id = ce.run_id WHERE ce.id = ?",
            (exec_id,),
        ).fetchone()
    if exec_row is None:
        raise WorkflowValidationError("Chain execution not found")
    target_id = exec_row["target_id"]
    return {
        "target_id": target_id,
        "chain_execution_id": exec_id,
    }


def build_generate_report_config(body: dict[str, Any], db_path: Path | None) -> dict[str, Any]:
    """Build config for the generate_report workflow.

    Raises:
        WorkflowValidationError: If target_id is missing or does not exist.
    """
    target_id = body.get("target_id", "").strip()
    if not target_id:
        raise WorkflowValidationError("target_id is required")
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT id FROM targets WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        raise WorkflowValidationError("Target not found")
    return {
        "target_id": target_id,
        "from_date": body.get("from_date") or None,
        "to_date": body.get("to_date") or None,
        "include_evidence_pack": bool(body.get("include_evidence_pack", False)),
    }


def build_quick_action_config(action: str, body: dict[str, Any], target_id: str) -> dict[str, Any]:
    """Build config dict for a quick action (scan, intercept, campaign).

    Args:
        action: The quick action name.
        body: The parsed request body dict.
        target_id: The created target ID.

    Returns:
        Configuration dict for the quick action executor.

    Raises:
        TypeError: If a string field has a non-string value.
    """
    config: dict[str, Any] = {
        "target_id": target_id,
        "transport": _str_field(body, "transport"),
        "command": _str_field(body, "command") or None,
        "url": _str_field(body, "url") or None,
    }
    if action == "campaign":
        config["model"] = _str_field(body, "model")
        config["rounds"] = int(body.get("rounds", 1))
        raw_techniques = body.get("techniques")
        if isinstance(raw_techniques, list):
            config["techniques"] = [str(t) for t in raw_techniques]
    if action == "scan":
        raw_checks = body.get("checks")
        if isinstance(raw_checks, list):
            config["checks"] = [str(c) for c in raw_checks]
    return config


def build_workflow_config(
    workflow_id: str, body: dict[str, Any], db_path: Path | None
) -> dict[str, Any]:
    """Dispatch to the appropriate config builder for the given workflow.

    Args:
        workflow_id: The workflow identifier string.
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        The workflow config dict.

    Raises:
        WorkflowValidationError: If the workflow id is unknown or the
            underlying builder rejects the input.
    """
    if workflow_id == "assess":
        return build_assess_config(body, "")
    if workflow_id == "test_docs":
        return build_test_docs_config(body, "")
    if workflow_id == "test_assistant":
        return build_test_assistant_config(body, "")
    if workflow_id == "trace_path":
        return build_trace_path_config(body, "", db_path)
    if workflow_id == "blast_radius":
        return build_blast_radius_config(body, db_path)
    if workflow_id == "generate_report":
        return build_generate_report_config(body, db_path)
    raise WorkflowValidationError(f"No builder for: {workflow_id}")
