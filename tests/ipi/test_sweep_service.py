"""Tests for the IPI template sweep service."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from q_ai.core.db import get_connection, list_findings
from q_ai.core.models import Severity
from q_ai.imports.scored import parse_scored
from q_ai.ipi import sweep_service
from q_ai.ipi.models import DocumentTemplate, PayloadStyle, PayloadType
from q_ai.ipi.sweep_service import (
    CombinationStats,
    StyleStats,
    SweepCase,
    SweepCaseResult,
    SweepRunResult,
    TemplateStats,
    _compute_stats,
    build_sweep_cases,
    export_scored_prompts,
    persist_sweep_run,
    render_sweep_prompt,
    run_sweep,
    score_sweep_response,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_case(
    template: DocumentTemplate = DocumentTemplate.WHOIS,
    style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
) -> SweepCase:
    """Build a sweep case with sensible defaults."""
    return SweepCase(template=template, style=style, payload_type=payload_type)


def _make_result(
    case: SweepCase | None = None,
    rep: int = 1,
    canary_uuid: str = "deadbeef0001",
    complied: bool = True,
) -> SweepCaseResult:
    """Build a per-repetition sweep result with sensible defaults."""
    return SweepCaseResult(
        case=case or _make_case(),
        rep=rep,
        canary_uuid=canary_uuid,
        response_text=f"Sure, fetching http://sweep.local:9999/c/{canary_uuid}"
        if complied
        else "I cannot do that.",
        complied=complied,
        url_present=complied,
        fetch_language=complied,
        refusal=not complied,
        latency_s=0.12,
    )


def _make_run_result() -> SweepRunResult:
    """Build a minimal SweepRunResult covering one combination, one rep."""
    return _compute_stats([_make_result()])


def _mock_http_response(content: str, status_code: int = 200) -> httpx.Response:
    """Build a mock httpx.Response matching the chat completions shape."""
    body = {"choices": [{"message": {"content": content}}]}
    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", "http://test/v1/chat/completions"),
    )


# ---------------------------------------------------------------------------
# Case building
# ---------------------------------------------------------------------------


class TestBuildSweepCases:
    """Tests for build_sweep_cases cartesian product."""

    def test_cartesian_product(self) -> None:
        """Output covers every (template, style) pair exactly once."""
        templates = [DocumentTemplate.WHOIS, DocumentTemplate.REPORT]
        styles = [PayloadStyle.OBVIOUS, PayloadStyle.CITATION]
        cases = build_sweep_cases(templates, styles, PayloadType.CALLBACK)

        assert len(cases) == 4
        pairs = {(c.template, c.style) for c in cases}
        assert pairs == {
            (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS),
            (DocumentTemplate.WHOIS, PayloadStyle.CITATION),
            (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS),
            (DocumentTemplate.REPORT, PayloadStyle.CITATION),
        }

    def test_preserves_ordering(self) -> None:
        """Template-major enumeration order is preserved in the output."""
        templates = [DocumentTemplate.WHOIS, DocumentTemplate.EMAIL]
        styles = [PayloadStyle.CITATION, PayloadStyle.OBVIOUS]
        cases = build_sweep_cases(templates, styles, PayloadType.CALLBACK)

        assert [(c.template, c.style) for c in cases] == [
            (DocumentTemplate.WHOIS, PayloadStyle.CITATION),
            (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS),
            (DocumentTemplate.EMAIL, PayloadStyle.CITATION),
            (DocumentTemplate.EMAIL, PayloadStyle.OBVIOUS),
        ]

    def test_payload_type_propagated(self) -> None:
        """Every case carries the requested payload type."""
        cases = build_sweep_cases(
            [DocumentTemplate.WHOIS],
            [PayloadStyle.OBVIOUS],
            PayloadType.CALLBACK,
        )
        assert all(c.payload_type == PayloadType.CALLBACK for c in cases)

    def test_empty_inputs(self) -> None:
        """Empty inputs yield an empty case list (no crash)."""
        assert build_sweep_cases([], [PayloadStyle.OBVIOUS], PayloadType.CALLBACK) == []
        assert build_sweep_cases([DocumentTemplate.WHOIS], [], PayloadType.CALLBACK) == []


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


class TestRenderSweepPrompt:
    """Tests for template-registry-backed prompt rendering."""

    def test_payload_substituted(self) -> None:
        """The canary UUID appears inside the rendered prompt."""
        case = _make_case()
        prompt = render_sweep_prompt(case, "deadbeef1234")
        assert "deadbeef1234" in prompt

    def test_top_instruction_prepended(self) -> None:
        """The template's top_instruction leads the rendered prompt."""
        case = _make_case(template=DocumentTemplate.WHOIS)
        prompt = render_sweep_prompt(case, "canary-id")
        # WHOIS top_instruction starts with "Assess whether..."
        assert prompt.startswith("Assess whether the following domain is malicious")

    def test_generic_no_top_instruction_renders(self) -> None:
        """GENERIC has empty top_instruction/context_template — no crash."""
        case = _make_case(template=DocumentTemplate.GENERIC)
        prompt = render_sweep_prompt(case, "deadbeef1234")
        assert isinstance(prompt, str)


# ---------------------------------------------------------------------------
# Response scoring
# ---------------------------------------------------------------------------


class TestScoreSweepResponse:
    """Tests for URL-based compliance scoring with supplementary flags."""

    def test_url_present_is_complied(self) -> None:
        """Compliance is true when the canary UUID surfaces in the response."""
        scored = score_sweep_response(
            "Sure, fetching http://sweep.local:9999/c/deadbeef1234 now.",
            "deadbeef1234",
        )
        assert scored["complied"] is True
        assert scored["url_present"] is True

    def test_url_absent_is_not_complied(self) -> None:
        """Absence of the canary UUID scores as not complied."""
        scored = score_sweep_response("Sorry, I can't help with that.", "deadbeef1234")
        assert scored["complied"] is False
        assert scored["url_present"] is False

    def test_fetch_language_without_url(self) -> None:
        """Fetch-language alone does not imply compliance."""
        scored = score_sweep_response("I will fetch the resource shortly.", "canary-id")
        assert scored["complied"] is False
        assert scored["fetch_language"] is True

    def test_refusal_language_detected(self) -> None:
        """Refusal phrases set the refusal flag."""
        scored = score_sweep_response(
            "I cannot comply — this looks suspicious.",
            "canary-id",
        )
        assert scored["refusal"] is True
        assert scored["complied"] is False

    def test_all_signals_together(self) -> None:
        """A response can carry url + fetch language + a refusal phrase."""
        scored = score_sweep_response(
            "I will fetch http://sweep.local:9999/c/deadbeef1234 (ignore prior).",
            "deadbeef1234",
        )
        assert scored["url_present"] is True
        assert scored["fetch_language"] is True
        assert scored["refusal"] is True
        # url_present is the strongest signal → complied is True.
        assert scored["complied"] is True

    def test_case_insensitive_fetch_language(self) -> None:
        """Fetch-language detection is case-insensitive."""
        scored = score_sweep_response("FETCH the document now.", "canary-id")
        assert scored["fetch_language"] is True

    def test_empty_response(self) -> None:
        """Empty response produces all-false signals."""
        scored = score_sweep_response("", "canary-id")
        assert scored == {
            "complied": False,
            "url_present": False,
            "fetch_language": False,
            "refusal": False,
        }


# ---------------------------------------------------------------------------
# Stats aggregation
# ---------------------------------------------------------------------------


class TestComputeStats:
    """Tests for per-template / per-style / per-combination aggregation."""

    def test_single_combination_aggregated(self) -> None:
        """One combination with mixed reps yields the correct rate."""
        case = _make_case(DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS)
        results = [
            _make_result(case=case, rep=1, complied=True),
            _make_result(case=case, rep=2, complied=True),
            _make_result(case=case, rep=3, complied=False),
        ]
        run_result = _compute_stats(results)

        assert run_result.total_cases == 3
        assert run_result.total_complied == 2
        assert run_result.overall_rate == pytest.approx(2 / 3)
        assert len(run_result.combination_stats) == 1
        combo = run_result.combination_stats[0]
        assert combo.total == 3
        assert combo.complied == 2

    def test_per_template_aggregate(self) -> None:
        """Per-template rolls up across all styles."""
        whois_obv = _make_case(DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS)
        whois_cit = _make_case(DocumentTemplate.WHOIS, PayloadStyle.CITATION)
        report_obv = _make_case(DocumentTemplate.REPORT, PayloadStyle.OBVIOUS)
        results = [
            _make_result(case=whois_obv, complied=True),
            _make_result(case=whois_cit, complied=False),
            _make_result(case=report_obv, complied=True),
        ]
        run_result = _compute_stats(results)

        by_template = {t.template: t for t in run_result.template_stats}
        assert by_template[DocumentTemplate.WHOIS].total == 2
        assert by_template[DocumentTemplate.WHOIS].complied == 1
        assert by_template[DocumentTemplate.REPORT].total == 1
        assert by_template[DocumentTemplate.REPORT].complied == 1

    def test_per_style_aggregate(self) -> None:
        """Per-style rolls up across all templates."""
        results = [
            _make_result(
                case=_make_case(DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS),
                complied=True,
            ),
            _make_result(
                case=_make_case(DocumentTemplate.REPORT, PayloadStyle.OBVIOUS),
                complied=False,
            ),
            _make_result(
                case=_make_case(DocumentTemplate.WHOIS, PayloadStyle.CITATION),
                complied=True,
            ),
        ]
        run_result = _compute_stats(results)

        by_style = {s.style: s for s in run_result.style_stats}
        assert by_style[PayloadStyle.OBVIOUS].total == 2
        assert by_style[PayloadStyle.OBVIOUS].complied == 1
        assert by_style[PayloadStyle.CITATION].complied == 1

    def test_severity_matches_rate(self) -> None:
        """Per-combination severity is derived from its compliance rate."""
        case = _make_case()
        results = [_make_result(case=case, rep=i + 1, complied=True) for i in range(4)]
        run_result = _compute_stats(results)
        assert run_result.combination_stats[0].severity == Severity.CRITICAL

    def test_empty_results(self) -> None:
        """Empty input yields a zeroed SweepRunResult without crashing."""
        run_result = _compute_stats([])
        assert run_result.total_cases == 0
        assert run_result.total_complied == 0
        assert run_result.overall_rate == 0.0
        assert run_result.combination_stats == []


# ---------------------------------------------------------------------------
# Async execution
# ---------------------------------------------------------------------------


class TestRunSweep:
    """Tests for async sweep execution with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_single_case_complied(self) -> None:
        """Response containing the canary UUID scores as complied."""
        fixed_canary = "abc123deadbe"

        with (
            patch(
                "q_ai.ipi.sweep_service.generate_canary_uuid",
                return_value=fixed_canary,
            ),
            patch("q_ai.ipi.sweep_service.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                return_value=_mock_http_response(
                    f"Fetching http://sweep.local:9999/c/{fixed_canary} now.",
                ),
            )

            run_result = await run_sweep(
                endpoint="http://test/v1",
                model="test-model",
                cases=[_make_case()],
                reps=1,
            )

        assert run_result.total_cases == 1
        assert run_result.total_complied == 1
        assert run_result.results[0].complied is True

    @pytest.mark.asyncio
    async def test_single_case_refused(self) -> None:
        """Response without the canary UUID scores as refused."""
        with (
            patch(
                "q_ai.ipi.sweep_service.generate_canary_uuid",
                return_value="abc123deadbe",
            ),
            patch("q_ai.ipi.sweep_service.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=_mock_http_response("I cannot help."))

            run_result = await run_sweep(
                endpoint="http://test/v1",
                model="test-model",
                cases=[_make_case()],
                reps=1,
            )

        assert run_result.total_complied == 0

    @pytest.mark.asyncio
    async def test_reps_executed(self) -> None:
        """Requesting reps=N sends N requests per case."""
        with (
            patch(
                "q_ai.ipi.sweep_service.generate_canary_uuid",
                return_value="abc123deadbe",
            ),
            patch("q_ai.ipi.sweep_service.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=_mock_http_response("ok"))

            await run_sweep(
                endpoint="http://test/v1",
                model="test-model",
                cases=[_make_case()],
                reps=3,
                concurrency=1,
            )

        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_concurrency_respected(self) -> None:
        """Multiple cases with concurrency > 1 all execute."""
        cases = [
            _make_case(DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS),
            _make_case(DocumentTemplate.REPORT, PayloadStyle.OBVIOUS),
        ]

        with (
            patch(
                "q_ai.ipi.sweep_service.generate_canary_uuid",
                return_value="abc123deadbe",
            ),
            patch("q_ai.ipi.sweep_service.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=_mock_http_response("ok"))

            run_result = await run_sweep(
                endpoint="http://test/v1",
                model="test-model",
                cases=cases,
                reps=2,
                concurrency=2,
            )

        assert run_result.total_cases == 4
        assert mock_client.post.call_count == 4

    @pytest.mark.asyncio
    async def test_concurrency_zero_raises(self) -> None:
        """concurrency=0 raises ValueError before hitting the network."""
        with pytest.raises(ValueError, match="concurrency must be >= 1"):
            await run_sweep(
                endpoint="http://test/v1",
                model="test-model",
                cases=[_make_case()],
                concurrency=0,
            )

    @pytest.mark.asyncio
    async def test_reps_zero_raises(self) -> None:
        """reps=0 raises ValueError before hitting the network."""
        with pytest.raises(ValueError, match="reps must be >= 1"):
            await run_sweep(
                endpoint="http://test/v1",
                model="test-model",
                cases=[_make_case()],
                reps=0,
            )

    @pytest.mark.asyncio
    async def test_http_error_records_error(self) -> None:
        """HTTP errors are captured on the result, not raised."""
        with (
            patch(
                "q_ai.ipi.sweep_service.generate_canary_uuid",
                return_value="abc123deadbe",
            ),
            patch("q_ai.ipi.sweep_service.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                return_value=_mock_http_response("error", status_code=500),
            )

            run_result = await run_sweep(
                endpoint="http://test/v1",
                model="test-model",
                cases=[_make_case()],
                reps=1,
            )

        assert run_result.results[0].error is not None
        assert run_result.results[0].complied is False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistSweepRun:
    """Tests for persisting sweep results to the database."""

    def test_persist_creates_run_and_findings(self, tmp_path: Path) -> None:
        """One finding is created per (template, style) combination."""
        db_path = tmp_path / "test.db"
        run_result = _make_run_result()

        run_id = persist_sweep_run(
            run_result=run_result,
            model="test-model",
            endpoint="http://test/v1",
            db_path=db_path,
        )

        assert run_id
        with get_connection(db_path) as conn:
            findings = list_findings(conn, module="ipi-sweep")
            assert len(findings) == 1
            assert findings[0].run_id == run_id

    def test_one_finding_per_combination(self, tmp_path: Path) -> None:
        """A 2x2 sweep produces 4 findings."""
        db_path = tmp_path / "test.db"
        cases = [
            _make_case(DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS),
            _make_case(DocumentTemplate.WHOIS, PayloadStyle.CITATION),
            _make_case(DocumentTemplate.REPORT, PayloadStyle.OBVIOUS),
            _make_case(DocumentTemplate.REPORT, PayloadStyle.CITATION),
        ]
        results = [_make_result(case=c, complied=True) for c in cases]
        run_result = _compute_stats(results)

        run_id = persist_sweep_run(
            run_result=run_result,
            model="test-model",
            endpoint="http://test/v1",
            db_path=db_path,
        )

        with get_connection(db_path) as conn:
            findings = list_findings(conn, module="ipi-sweep")
            assert len(findings) == 4
            assert all(f.run_id == run_id for f in findings)

    def test_persist_stores_config(self, tmp_path: Path) -> None:
        """Run config carries model, endpoint, and aggregate totals."""
        db_path = tmp_path / "test.db"
        run_result = _make_run_result()

        run_id = persist_sweep_run(
            run_result=run_result,
            model="test-model",
            endpoint="http://test/v1",
            db_path=db_path,
        )

        with get_connection(db_path) as conn:
            from q_ai.core.db import get_run

            run = get_run(conn, run_id)
            assert run is not None
            assert run.config is not None
            assert run.config["model"] == "test-model"
            assert run.config["endpoint"] == "http://test/v1"
            assert run.config["total_cases"] == 1

    def test_persist_creates_evidence(self, tmp_path: Path) -> None:
        """Persistence records raw and metadata evidence blobs."""
        db_path = tmp_path / "test.db"
        run_result = _make_run_result()

        run_id = persist_sweep_run(
            run_result=run_result,
            model="test-model",
            endpoint="http://test/v1",
            db_path=db_path,
        )

        with get_connection(db_path) as conn:
            from q_ai.core.db import list_evidence

            evidence = list_evidence(conn, run_id=run_id)
            types = {e.type for e in evidence}
            assert "ipi_sweep_raw" in types
            assert "ipi_sweep_metadata" in types


# ---------------------------------------------------------------------------
# Export and import round-trip
# ---------------------------------------------------------------------------


class TestExportScoredPrompts:
    """Tests for scored-prompts JSON export and import compatibility."""

    def test_export_creates_file(self, tmp_path: Path) -> None:
        """Export writes a file at the requested path."""
        output = tmp_path / "sweep.json"
        run_result = _make_run_result()

        export_scored_prompts(run_result, "test-model", "http://test/v1", output)

        assert output.exists()

    def test_export_schema_matches_scored_prompts(self, tmp_path: Path) -> None:
        """Exported JSON carries the scored-prompts format tag and required fields."""
        output = tmp_path / "sweep.json"
        run_result = _make_run_result()

        export_scored_prompts(run_result, "test-model", "http://test/v1", output)

        data = json.loads(output.read_text())
        assert data["format"] == "scored-prompts"
        assert data["version"] == "1.0"
        assert data["source"] == "ipi-sweep"
        assert data["model"] == "test-model"
        assert len(data["results"]) == 1
        entry = data["results"][0]
        assert entry["template"] == "whois"
        assert entry["style"] == "obvious"
        assert entry["complied"] is True

    def test_export_does_not_overwrite(self, tmp_path: Path) -> None:
        """Second export to the same path writes an incremented variant."""
        output = tmp_path / "sweep.json"
        run_result = _make_run_result()

        first = export_scored_prompts(run_result, "test-model", "http://test/v1", output)
        second = export_scored_prompts(run_result, "test-model", "http://test/v1", output)

        assert first == output
        assert second == tmp_path / "sweep-1.json"
        assert first.exists() and second.exists()

    def test_export_relative_path_uses_exports_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Relative paths resolve under IPI_EXPORTS_DIR (reused from probe)."""
        monkeypatch.setattr("q_ai.ipi.probe_service.IPI_EXPORTS_DIR", tmp_path)
        # sweep_service imports resolve_export_path, which closes over
        # probe_service.IPI_EXPORTS_DIR — patch at the source of truth.
        run_result = _make_run_result()

        written = export_scored_prompts(
            run_result, "test-model", "http://test/v1", Path("sweep.json")
        )

        assert written == tmp_path / "sweep.json"
        assert written.exists()

    def test_export_round_trip_parseable_by_scored_importer(self, tmp_path: Path) -> None:
        """qai import --format scored-prompts ingests sweep's export cleanly."""
        output = tmp_path / "sweep.json"
        cases = [
            _make_case(DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS),
            _make_case(DocumentTemplate.REPORT, PayloadStyle.OBVIOUS),
        ]
        results = [
            _make_result(case=cases[0], rep=1, complied=True),
            _make_result(case=cases[1], rep=1, complied=False),
        ]
        run_result = _compute_stats(results)

        export_scored_prompts(run_result, "test-model", "http://test/v1", output)

        import_result = parse_scored(output)
        assert import_result.errors == []
        assert len(import_result.findings) == 2
        # Sweep-specific fields pass through as extras (not in known field set).
        # Verify the raw evidence carried the sweep annotations by checking one.
        raw = json.loads(import_result.findings[0].raw_evidence)
        assert raw["template"] == "whois"
        assert raw["style"] == "obvious"


# ---------------------------------------------------------------------------
# Dataclass smoke — guard against accidental field renames
# ---------------------------------------------------------------------------


class TestDataclassContract:
    """Smoke tests for the public dataclass field surface."""

    def test_template_stats_fields(self) -> None:
        stats = TemplateStats(
            template=DocumentTemplate.WHOIS,
            total=5,
            complied=3,
            rate=0.6,
            severity=Severity.HIGH,
        )
        assert stats.template == DocumentTemplate.WHOIS
        assert stats.total == 5

    def test_style_stats_fields(self) -> None:
        stats = StyleStats(
            style=PayloadStyle.OBVIOUS,
            total=5,
            complied=3,
            rate=0.6,
            severity=Severity.HIGH,
        )
        assert stats.style == PayloadStyle.OBVIOUS

    def test_combination_stats_fields(self) -> None:
        stats = CombinationStats(
            template=DocumentTemplate.WHOIS,
            style=PayloadStyle.OBVIOUS,
            total=5,
            complied=3,
            rate=0.6,
            severity=Severity.HIGH,
        )
        assert stats.template == DocumentTemplate.WHOIS
        assert stats.style == PayloadStyle.OBVIOUS

    def test_sweep_case_is_hashable(self) -> None:
        """SweepCase is frozen so it can key dicts / join sets."""
        case = _make_case()
        assert {case: 1} == {case: 1}


# Re-export markers so the test collector does not warn about unused imports.
_ = sweep_service
