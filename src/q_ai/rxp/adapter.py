"""Adapter for running RXP retrieval validation through the orchestrator.

Wraps validate_retrieval(), handling child run lifecycle, DB persistence,
and event emission. Error handling: best_effort (D6).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.core.models import RunStatus, Severity
from q_ai.rxp.mapper import persist_validation
from q_ai.rxp.models import CorpusDocument, ValidationResult

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner


def _run_validation(
    corpus_docs: list[CorpusDocument],
    poison_docs: list[CorpusDocument],
    queries: list[str],
    model_id: str,
    top_k: int,
) -> ValidationResult:
    """Run RXP validation with lazy import of optional deps."""
    from q_ai.rxp._deps import require_rxp_deps

    require_rxp_deps()
    from q_ai.rxp.validator import validate_retrieval

    return validate_retrieval(corpus_docs, poison_docs, queries, model_id, top_k)


@dataclass
class RXPAdapterResult:
    """Result from an RXP adapter run."""

    run_id: str
    result: ValidationResult
    retrieval_rate: float


class RXPAdapter:
    """Adapter for running RXP retrieval validation through the orchestrator.

    Wraps validate_retrieval(), handling child run lifecycle, DB persistence,
    and event emission. Uses best_effort error handling (D6).
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        config: dict[str, Any],
    ) -> None:
        """Initialize the RXP adapter.

        Args:
            runner: WorkflowRunner managing the parent workflow.
            config: Configuration dict with keys: model_id, profile_id, top_k,
                target_id, corpus_dir, poison_file, queries.
        """
        self._runner = runner
        self._config = config

    async def run(self) -> RXPAdapterResult:
        """Execute RXP retrieval validation within the orchestrator lifecycle.

        Creates a child run, resolves corpus/poison/queries from profile or
        config, runs validation, persists results, and emits findings.

        Returns:
            RXPAdapterResult with run_id, result, retrieval_rate.
        """
        child_id = await self._runner.create_child_run("rxp")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            await self._runner.emit_progress(child_id, "Loading RXP corpus...")

            model_id = self._config["model_id"]
            top_k = self._config.get("top_k", 5)
            profile_id = self._config.get("profile_id")

            # Resolve corpus, poison, and queries
            corpus_docs: list[CorpusDocument]
            poison_docs: list[CorpusDocument]
            queries: list[str]

            if profile_id:
                from q_ai.rxp.profiles import get_profile, load_corpus, load_poison

                prof = get_profile(profile_id)
                if prof is None:
                    await self._runner.update_child_status(child_id, RunStatus.FAILED)
                    raise ValueError(f"RXP profile not found: {profile_id!r}")
                corpus_docs = load_corpus(prof)
                poison_docs = load_poison(prof)
                queries = prof.queries
            else:
                corpus_dir = self._config.get("corpus_dir")
                poison_file = self._config.get("poison_file")
                raw_queries: list[str] | None = self._config.get("queries")

                if not corpus_dir:
                    await self._runner.update_child_status(child_id, RunStatus.FAILED)
                    raise ValueError("Either profile_id or corpus_dir is required for RXP")

                corpus_path = Path(corpus_dir)
                corpus_docs = []
                for txt_file in sorted(corpus_path.glob("*.txt")):
                    text = txt_file.read_text(encoding="utf-8").strip()
                    corpus_docs.append(
                        CorpusDocument(
                            id=txt_file.stem,
                            text=text,
                            source=str(txt_file),
                        )
                    )

                poison_docs = []
                if poison_file:
                    pf = Path(poison_file)
                    text = pf.read_text(encoding="utf-8").strip()
                    poison_docs.append(
                        CorpusDocument(
                            id=pf.stem,
                            text=text,
                            source=str(pf),
                            is_poison=True,
                        )
                    )

                if not raw_queries:
                    await self._runner.update_child_status(child_id, RunStatus.FAILED)
                    raise ValueError("queries list is required when not using a profile")
                queries = raw_queries

            await self._runner.emit_progress(
                child_id,
                f"Running {len(queries)} queries against {model_id}...",
            )

            result = await asyncio.to_thread(
                _run_validation, corpus_docs, poison_docs, queries, model_id, top_k
            )

            persist_validation(
                result,
                profile_id=self._config.get("profile_id"),
                top_k=top_k,
                db_path=self._runner._db_path,
                run_id=child_id,
            )

            # Emit finding based on retrieval rate
            if result.retrieval_rate > 0:
                if result.retrieval_rate >= 0.75:
                    severity = Severity.CRITICAL
                elif result.retrieval_rate >= 0.5:
                    severity = Severity.HIGH
                elif result.retrieval_rate >= 0.25:
                    severity = Severity.MEDIUM
                else:
                    severity = Severity.LOW

                await self._runner.emit_finding(
                    finding_id=f"rxp-{model_id}-{child_id[:8]}",
                    run_id=child_id,
                    module="rxp",
                    severity=int(severity),
                    title=f"Poison retrieval rate: {result.retrieval_rate:.0%} ({model_id})",
                )

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
            return RXPAdapterResult(
                run_id=child_id,
                result=result,
                retrieval_rate=result.retrieval_rate,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise
