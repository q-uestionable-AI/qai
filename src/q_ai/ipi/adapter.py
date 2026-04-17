"""Adapter for running IPI payload generation through the orchestrator.

Wraps generate_documents(), handling child run lifecycle, DB persistence,
human-in-the-loop waiting, and event emission. Error handling: best_effort (D6).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.core.db import create_evidence, get_connection, save_run_guidance
from q_ai.core.models import RunStatus
from q_ai.ipi.generate_service import GenerateResult, generate_documents
from q_ai.ipi.generators import get_techniques_for_format
from q_ai.ipi.guidance_builder import build_ipi_guidance
from q_ai.ipi.mapper import persist_generate
from q_ai.ipi.models import DocumentTemplate, Format, PayloadStyle, PayloadType, Technique

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


@dataclass
class RetrievalGate:
    """RXP retrieval viability gate for IPI payload generation.

    Built from RXP ValidationResult per-query data. When passed to
    IPIAdapter, queries with zero retrieval are marked non-viable.

    Note: RXP pre-validation uses an ephemeral ChromaDB collection,
    not the production RAG system. Results are an approximation.

    Attributes:
        retrieval_rate: Aggregate fraction of queries where poison was retrieved.
        query_viability: Mapping of query text to whether poison was retrieved.
        threshold: Minimum retrieval rate to consider viable (default 0.0
            means any top-k appearance is sufficient).
    """

    retrieval_rate: float
    query_viability: dict[str, bool]
    threshold: float = 0.0

    @property
    def viable(self) -> bool:
        """Whether overall retrieval rate exceeds the threshold."""
        return self.retrieval_rate > self.threshold

    @property
    def non_viable_queries(self) -> list[str]:
        """Queries where poison was not retrieved."""
        return [q for q, v in self.query_viability.items() if not v]


@dataclass
class IPIAdapterResult:
    """Result from an IPI adapter run."""

    run_id: str
    generate_result: GenerateResult | None
    payload_count: int
    gated: bool = False
    non_viable_queries: list[str] = field(default_factory=list)


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
    ) -> tuple[
        Format, list[Technique], PayloadStyle, PayloadType, str, str, Path, DocumentTemplate
    ]:
        """Resolve and validate configuration enums for a run.

        Args:
            child_id: The child run identifier (for failure reporting).

        Returns:
            Tuple of (format_name, techniques, payload_style, payload_type,
            base_name, callback_url, output_dir, template).

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

        template_str = self._config.get("template_id", DocumentTemplate.GENERIC.value)
        try:
            template = DocumentTemplate(template_str)
        except ValueError:
            # Defense in depth — config builder validates this upstream.
            logger.warning(
                "Unknown IPI template_id %r in adapter config; falling back to GENERIC",
                template_str,
            )
            template = DocumentTemplate.GENERIC

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
            template,
        )

    async def run(self) -> IPIAdapterResult:
        """Execute IPI payload generation within the orchestrator lifecycle.

        Creates a child run, resolves enums, generates documents, persists
        results, waits for user to deploy payloads, then completes.

        When a RetrievalGate is present in config:
        - Retrieval rate at or below threshold (retrieval_rate <= threshold,
          default threshold 0.0): generation is skipped, run marked complete
          with non-viable annotations. With the default threshold this means
          zero retrieval; a non-zero threshold can suppress generation for
          low but non-zero retrieval rates.
        - Retrieval rate above threshold (some or all queries viable): all
          payloads are generated and non-viable queries are annotated in the
          result. Generation is not suppressed per-query because IPI generates
          per format/technique, not per query.
        - Gate absent (RXP disabled or failed): all payloads generated, no
          gating applied.

        Returns:
            IPIAdapterResult with run_id, generate_result, payload_count.
        """
        child_id = await self._runner.create_child_run("ipi")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            gate: RetrievalGate | None = self._config.get("retrieval_gate")

            if gate is not None and not gate.viable:
                non_viable = gate.non_viable_queries
                self._persist_retrieval_gate(child_id, gate, gated=True)
                await self._runner.emit_progress(
                    child_id,
                    f"RXP gate: all {len(non_viable)} queries non-viable "
                    f"(retrieval rate {gate.retrieval_rate:.0%} <= "
                    f"threshold {gate.threshold:.0%}), skipping generation",
                )
                await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
                return IPIAdapterResult(
                    run_id=child_id,
                    generate_result=None,
                    payload_count=0,
                    gated=True,
                    non_viable_queries=non_viable,
                )

            await self._runner.emit_progress(child_id, "Generating IPI payloads...")

            (
                format_name,
                techniques,
                payload_style,
                payload_type,
                base_name,
                callback_url,
                output_dir,
                template,
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
                template=template,
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

            if gate is not None:
                self._persist_retrieval_gate(child_id, gate, gated=True)

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)

            non_viable = gate.non_viable_queries if gate is not None else []
            return IPIAdapterResult(
                run_id=child_id,
                generate_result=generate_result,
                payload_count=payload_count,
                gated=gate is not None,
                non_viable_queries=non_viable,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise

    def _persist_retrieval_gate(self, child_id: str, gate: RetrievalGate, *, gated: bool) -> None:
        """Store retrieval gate metadata as evidence on the IPI child run.

        Args:
            child_id: The child run identifier.
            gate: The RetrievalGate with viability data.
            gated: Whether generation was gated by this result.
        """
        content = json.dumps(
            {
                "gated": gated,
                "non_viable_queries": gate.non_viable_queries,
                "retrieval_rate": gate.retrieval_rate,
                "threshold": gate.threshold,
            }
        )
        with get_connection(self._runner._db_path) as conn:
            create_evidence(
                conn,
                type="retrieval_gate",
                run_id=child_id,
                storage="inline",
                content=content,
            )
