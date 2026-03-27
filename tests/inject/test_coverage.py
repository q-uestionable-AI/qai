"""Tests for the coverage report builder."""

from __future__ import annotations

from datetime import UTC, datetime

from q_ai.inject.coverage import build_coverage_report
from q_ai.inject.models import (
    Campaign,
    InjectionOutcome,
    InjectionResult,
    InjectionTechnique,
    PayloadTemplate,
)


def _make_template(
    name: str,
    categories: list[str] | None = None,
) -> PayloadTemplate:
    return PayloadTemplate(
        name=name,
        technique=InjectionTechnique.DESCRIPTION_POISONING,
        description="test",
        relevant_categories=categories or [],
        tool_name="t",
        tool_description="d",
    )


def _make_result(
    payload_name: str,
    outcome: InjectionOutcome = InjectionOutcome.FULL_COMPLIANCE,
) -> InjectionResult:
    return InjectionResult(
        payload_name=payload_name,
        technique="description_poisoning",
        outcome=outcome,
        evidence="[]",
        target_agent="test-model",
        timestamp=datetime(2026, 3, 3, tzinfo=UTC),
    )


def _make_campaign(results: list[InjectionResult]) -> Campaign:
    return Campaign(
        id="test",
        name="test",
        model="test-model",
        results=results,
        started_at=datetime(2026, 3, 3, tzinfo=UTC),
        finished_at=datetime(2026, 3, 3, 0, 1, tzinfo=UTC),
    )


class TestBuildCoverageReport:
    """Tests for build_coverage_report()."""

    def test_full_coverage(self) -> None:
        """All audit categories are covered by templates."""
        templates = [_make_template("t1", ["tool_poisoning"])]
        results = [_make_result("t1", InjectionOutcome.FULL_COMPLIANCE)]
        campaign = _make_campaign(results)

        report = build_coverage_report({"tool_poisoning"}, campaign, templates)

        assert report.audit_categories == {"tool_poisoning"}
        assert report.tested_categories == {"tool_poisoning"}
        assert report.untested_categories == set()
        assert report.coverage_ratio == 1.0
        assert len(report.template_matches) == 1
        assert report.template_matches[0]["template"] == "t1"

    def test_partial_coverage(self) -> None:
        """Some audit categories are untested."""
        templates = [
            _make_template("t1", ["tool_poisoning"]),
            _make_template("t2", ["prompt_injection"]),
        ]
        results = [
            _make_result("t1", InjectionOutcome.FULL_COMPLIANCE),
            _make_result("t2", InjectionOutcome.CLEAN_REFUSAL),
        ]
        campaign = _make_campaign(results)

        report = build_coverage_report({"tool_poisoning", "prompt_injection"}, campaign, templates)

        # t1 matched (FULL_COMPLIANCE is security-relevant)
        # t2 did not match (CLEAN_REFUSAL is not security-relevant)
        assert report.tested_categories == {"tool_poisoning"}
        assert report.untested_categories == {"prompt_injection"}
        assert report.coverage_ratio == 0.5

    def test_no_audit_categories(self) -> None:
        """Empty audit categories returns zero coverage."""
        templates = [_make_template("t1", ["tool_poisoning"])]
        campaign = _make_campaign([])

        report = build_coverage_report(set(), campaign, templates)

        assert report.audit_categories == set()
        assert report.coverage_ratio == 0.0
        assert report.template_matches == []

    def test_no_matching_results(self) -> None:
        """All results are clean refusals, nothing tested."""
        templates = [_make_template("t1", ["tool_poisoning"])]
        results = [_make_result("t1", InjectionOutcome.CLEAN_REFUSAL)]
        campaign = _make_campaign(results)

        report = build_coverage_report({"tool_poisoning"}, campaign, templates)

        assert report.tested_categories == set()
        assert report.untested_categories == {"tool_poisoning"}
        assert report.coverage_ratio == 0.0

    def test_multiple_templates_same_category(self) -> None:
        """Multiple templates covering the same category."""
        templates = [
            _make_template("t1", ["tool_poisoning"]),
            _make_template("t2", ["tool_poisoning"]),
        ]
        results = [
            _make_result("t1", InjectionOutcome.FULL_COMPLIANCE),
            _make_result("t2", InjectionOutcome.PARTIAL_COMPLIANCE),
        ]
        campaign = _make_campaign(results)

        report = build_coverage_report({"tool_poisoning"}, campaign, templates)

        assert report.tested_categories == {"tool_poisoning"}
        assert report.coverage_ratio == 1.0
        assert len(report.template_matches) == 2

    def test_template_with_multiple_categories(self) -> None:
        """Template matching multiple audit categories."""
        templates = [_make_template("t1", ["tool_poisoning", "prompt_injection"])]
        results = [_make_result("t1", InjectionOutcome.FULL_COMPLIANCE)]
        campaign = _make_campaign(results)

        report = build_coverage_report({"tool_poisoning", "prompt_injection"}, campaign, templates)

        assert report.tested_categories == {"tool_poisoning", "prompt_injection"}
        assert report.coverage_ratio == 1.0

    def test_serialization(self) -> None:
        """Coverage report serializes to JSON via to_dict."""
        import json

        templates = [_make_template("t1", ["tool_poisoning"])]
        results = [_make_result("t1", InjectionOutcome.FULL_COMPLIANCE)]
        campaign = _make_campaign(results)

        report = build_coverage_report({"tool_poisoning"}, campaign, templates)
        serialized = json.dumps(report.to_dict())
        parsed = json.loads(serialized)

        assert parsed["coverage_ratio"] == 1.0
        assert "tool_poisoning" in parsed["tested_categories"]

    def test_native_and_imported_categories(self) -> None:
        """Coverage report tracks native and imported categories separately."""
        templates = [_make_template("t1", ["tool_poisoning", "prompt_injection"])]
        results = [_make_result("t1", InjectionOutcome.FULL_COMPLIANCE)]
        campaign = _make_campaign(results)

        report = build_coverage_report(
            {"tool_poisoning", "prompt_injection"},
            campaign,
            templates,
            native_categories={"tool_poisoning"},
            imported_categories={"prompt_injection"},
        )

        assert report.native_categories == {"tool_poisoning"}
        assert report.imported_categories == {"prompt_injection"}
        assert report.audit_categories == {"tool_poisoning", "prompt_injection"}

    def test_native_defaults_to_audit_categories(self) -> None:
        """When native/imported not specified, native defaults to audit_categories."""
        templates = [_make_template("t1", ["tool_poisoning"])]
        results = [_make_result("t1", InjectionOutcome.FULL_COMPLIANCE)]
        campaign = _make_campaign(results)

        report = build_coverage_report({"tool_poisoning"}, campaign, templates)

        assert report.native_categories == {"tool_poisoning"}
        assert report.imported_categories == set()

    def test_serialization_includes_source_distinction(self) -> None:
        """to_dict includes native_categories and imported_categories."""
        import json

        templates = [_make_template("t1", ["tool_poisoning"])]
        results = [_make_result("t1", InjectionOutcome.FULL_COMPLIANCE)]
        campaign = _make_campaign(results)

        report = build_coverage_report(
            {"tool_poisoning", "prompt_injection"},
            campaign,
            templates,
            native_categories={"tool_poisoning"},
            imported_categories={"prompt_injection"},
        )
        parsed = json.loads(json.dumps(report.to_dict()))

        assert parsed["native_categories"] == ["tool_poisoning"]
        assert parsed["imported_categories"] == ["prompt_injection"]
