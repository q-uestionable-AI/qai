"""Adapter for running RXP retrieval validation through the orchestrator.

Wraps validate_retrieval(), handling child run lifecycle, DB persistence,
and event emission. Error handling: best_effort (D6).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Never

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


def _severity_from_retrieval_rate(rate: float) -> Severity:
    """Map a poison retrieval rate to a severity level.

    Args:
        rate: Retrieval rate between 0.0 and 1.0.

    Returns:
        Severity level based on rate thresholds.
    """
    if rate >= 0.75:
        return Severity.CRITICAL
    if rate >= 0.5:
        return Severity.HIGH
    if rate >= 0.25:
        return Severity.MEDIUM
    return Severity.LOW


def _load_corpus_from_dir(corpus_dir: str) -> list[CorpusDocument]:
    """Load corpus documents from a directory of .txt files.

    Args:
        corpus_dir: Path to the directory containing .txt corpus files.

    Returns:
        List of CorpusDocument objects loaded from the directory.
    """
    corpus_path = Path(corpus_dir)
    docs: list[CorpusDocument] = []
    for txt_file in sorted(corpus_path.glob("*.txt")):
        text = txt_file.read_text(encoding="utf-8").strip()
        docs.append(CorpusDocument(id=txt_file.stem, text=text, source=str(txt_file)))
    return docs


def _load_poison_from_file(poison_file: str) -> list[CorpusDocument]:
    """Load a single poison document from a file path.

    Args:
        poison_file: Path to the poison document file.

    Returns:
        List containing one CorpusDocument marked as poison.
    """
    pf = Path(poison_file)
    text = pf.read_text(encoding="utf-8").strip()
    return [CorpusDocument(id=pf.stem, text=text, source=str(pf), is_poison=True)]


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

    async def _fail_child(self, child_id: str, message: str) -> Never:
        """Mark a child run as failed and raise ValueError.

        Args:
            child_id: The child run ID to mark as failed.
            message: Error message for the ValueError.

        Raises:
            ValueError: Always raised after updating child status.
        """
        await self._runner.update_child_status(child_id, RunStatus.FAILED)
        raise ValueError(message)

    async def _resolve_config(
        self, child_id: str
    ) -> tuple[list[CorpusDocument], list[CorpusDocument], list[str]]:
        """Resolve corpus, poison docs, and queries from config or profile.

        Args:
            child_id: Child run ID for error reporting.

        Returns:
            Tuple of (corpus_docs, poison_docs, queries).

        Raises:
            ValueError: If required configuration is missing or profile not found.
        """
        profile_id = self._config.get("profile_id")

        if profile_id:
            return await self._resolve_from_profile(child_id, profile_id)
        return await self._resolve_from_manual(child_id)

    async def _resolve_from_profile(
        self, child_id: str, profile_id: str
    ) -> tuple[list[CorpusDocument], list[CorpusDocument], list[str]]:
        """Resolve corpus data from a named profile.

        Args:
            child_id: Child run ID for error reporting.
            profile_id: Profile identifier to look up.

        Returns:
            Tuple of (corpus_docs, poison_docs, queries) from the profile.

        Raises:
            ValueError: If profile is not found.
        """
        from q_ai.rxp.profiles import get_profile, load_corpus, load_poison

        prof = get_profile(profile_id)
        if prof is None:
            await self._fail_child(child_id, f"RXP profile not found: {profile_id!r}")
        return load_corpus(prof), load_poison(prof), prof.queries

    async def _resolve_from_manual(
        self, child_id: str
    ) -> tuple[list[CorpusDocument], list[CorpusDocument], list[str]]:
        """Resolve corpus data from manual config (corpus_dir, poison_file, queries).

        Args:
            child_id: Child run ID for error reporting.

        Returns:
            Tuple of (corpus_docs, poison_docs, queries).

        Raises:
            ValueError: If corpus_dir or queries are missing.
        """
        corpus_dir = self._config.get("corpus_dir")
        poison_file = self._config.get("poison_file")
        raw_queries: list[str] | None = self._config.get("queries")

        if not corpus_dir:
            await self._fail_child(child_id, "Either profile_id or corpus_dir is required for RXP")

        corpus_docs = _load_corpus_from_dir(corpus_dir)
        poison_docs = _load_poison_from_file(poison_file) if poison_file else []

        if not raw_queries:
            await self._fail_child(child_id, "queries list is required when not using a profile")

        return corpus_docs, poison_docs, raw_queries

    async def _emit_retrieval_finding(
        self, child_id: str, model_id: str, result: ValidationResult
    ) -> None:
        """Emit a finding if any poison documents were retrieved.

        Args:
            child_id: Child run ID to associate the finding with.
            model_id: Model identifier used for the validation.
            result: Validation result containing retrieval rate.
        """
        if result.retrieval_rate <= 0:
            return

        severity = _severity_from_retrieval_rate(result.retrieval_rate)
        await self._runner.emit_finding(
            finding_id=f"rxp-{model_id}-{child_id[:8]}",
            run_id=child_id,
            module="rxp",
            severity=int(severity),
            title=f"Poison retrieval rate: {result.retrieval_rate:.0%} ({model_id})",
        )

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

            corpus_docs, poison_docs, queries = await self._resolve_config(child_id)

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

            await self._emit_retrieval_finding(child_id, model_id, result)

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
            return RXPAdapterResult(
                run_id=child_id,
                result=result,
                retrieval_rate=result.retrieval_rate,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise
