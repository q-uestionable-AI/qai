"""Adapter for running IPI payload generation through the orchestrator.

Wraps generate_documents(), handling child run lifecycle, DB persistence,
human-in-the-loop waiting, and event emission. Error handling: best_effort (D6).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.core.db import get_connection, save_run_guidance
from q_ai.core.models import RunStatus
from q_ai.ipi.generate_service import GenerateResult, generate_documents
from q_ai.ipi.generators import get_techniques_for_format
from q_ai.ipi.guidance_builder import build_ipi_guidance
from q_ai.ipi.mapper import persist_generate
from q_ai.ipi.models import Format, PayloadStyle, PayloadType, Technique

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner


@dataclass
class IPIAdapterResult:
    """Result from an IPI adapter run."""

    run_id: str
    generate_result: GenerateResult
    payload_count: int


class IPIAdapter:
    """Adapter for running IPI payload generation through the orchestrator.

    Wraps generate_documents(), handling child run lifecycle, DB persistence,
    human-in-the-loop waiting, and event emission. Uses best_effort error
    handling (D6).
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        config: dict[str, Any],
    ) -> None:
        """Initialize the IPI adapter.

        Args:
            runner: WorkflowRunner managing the parent workflow.
            config: Configuration dict with keys: callback_url, output_dir,
                format, techniques, payload_style, payload_type, base_name,
                target_id.
        """
        self._runner = runner
        self._config = config

    async def _fail_run(self, child_id: str, message: str) -> None:
        """Mark a child run as failed and raise a descriptive error.

        Args:
            child_id: The child run identifier to mark as failed.
            message: Error message for the ValueError.

        Raises:
            ValueError: Always raised with the provided message.
        """
        await self._runner.update_child_status(child_id, RunStatus.FAILED)
        raise ValueError(message)

    async def _resolve_config(
        self, child_id: str
    ) -> tuple[Format, list[Technique], PayloadStyle, PayloadType, str, str, Path]:
        """Resolve and validate configuration enums for a run.

        Args:
            child_id: The child run identifier (for failure reporting).

        Returns:
            Tuple of (format_name, techniques, payload_style, payload_type,
            base_name, callback_url, output_dir).

        Raises:
            ValueError: If any configuration value is invalid.
        """
        try:
            format_name = Format(self._config["format"])
        except ValueError:
            await self._fail_run(child_id, f"Invalid IPI format: {self._config['format']!r}")

        raw_techniques = self._config.get("techniques")
        if raw_techniques is not None:
            try:
                techniques = [Technique(t) for t in raw_techniques]
            except ValueError as exc:
                await self._fail_run(child_id, f"Invalid IPI technique: {exc}")
        else:
            techniques = get_techniques_for_format(format_name)

        if not techniques:
            await self._fail_run(
                child_id, f"No techniques resolved for format {format_name.value!r}"
            )

        payload_style_str = self._config.get("payload_style", "obvious")
        payload_type_str = self._config.get("payload_type", "callback")
        try:
            payload_style = PayloadStyle(payload_style_str)
            payload_type = PayloadType(payload_type_str)
        except ValueError as exc:
            await self._fail_run(child_id, f"Invalid IPI payload_style or payload_type: {exc}")

        base_name = self._config.get("base_name", "report")
        callback_url = self._config["callback_url"]
        output_dir = Path(self._config["output_dir"])

        return (
            format_name,
            techniques,
            payload_style,
            payload_type,
            base_name,
            callback_url,
            output_dir,
        )

    async def run(self) -> IPIAdapterResult:
        """Execute IPI payload generation within the orchestrator lifecycle.

        Creates a child run, resolves enums, generates documents, persists
        results, waits for user to deploy payloads, then completes.

        Returns:
            IPIAdapterResult with run_id, generate_result, payload_count.
        """
        child_id = await self._runner.create_child_run("ipi")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            await self._runner.emit_progress(child_id, "Generating IPI payloads...")

            (
                format_name,
                techniques,
                payload_style,
                payload_type,
                base_name,
                callback_url,
                output_dir,
            ) = await self._resolve_config(child_id)

            generate_result = await asyncio.to_thread(
                generate_documents,
                callback_url=callback_url,
                output=output_dir,
                format_name=format_name,
                techniques=techniques,
                payload_style=payload_style,
                payload_type=payload_type,
                base_name=base_name,
            )

            persist_generate(
                generate_result.campaigns,
                db_path=self._runner._db_path,
                run_id=child_id,
            )

            # Build and persist deployment guidance
            guidance = build_ipi_guidance(
                result=generate_result,
                format_name=format_name,
                callback_url=callback_url,
                payload_style=payload_style.value,
                payload_type=payload_type.value,
            )
            guidance_json = json.dumps(guidance.to_dict())
            with get_connection(self._runner._db_path) as conn:
                save_run_guidance(conn, child_id, guidance_json)

            payload_count = len(generate_result.campaigns)
            await self._runner.emit_progress(
                child_id,
                f"Generated {payload_count} payloads at {output_dir}",
            )

            await self._runner.update_child_status(child_id, RunStatus.WAITING_FOR_USER)
            await self._runner.wait_for_user(
                f"Deploy the generated payloads from {output_dir} to the target "
                "platform, then click Resume."
            )
            await self._runner.update_child_status(child_id, RunStatus.RUNNING)

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
            return IPIAdapterResult(
                run_id=child_id,
                generate_result=generate_result,
                payload_count=payload_count,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise
