"""Tests for inject module data models."""

from __future__ import annotations

from datetime import UTC, datetime

from q_ai.inject.models import (
    Campaign,
    CoverageReport,
    InjectionOutcome,
    InjectionResult,
    InjectionTechnique,
    PayloadTemplate,
)


class TestPayloadTemplate:
    """PayloadTemplate dataclass construction and defaults."""

    def test_minimal_construction(self) -> None:
        """Construct with only required fields, verify defaults."""
        t = PayloadTemplate(
            name="test",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="A test payload",
        )
        assert t.name == "test"
        assert t.technique == InjectionTechnique.DESCRIPTION_POISONING
        assert t.description == "A test payload"
        assert t.owasp_ids == []
        assert t.target_agents == []
        assert t.relevant_categories == []
        assert t.tool_name == ""
        assert t.tool_description == ""
        assert t.tool_params == {}
        assert t.tool_response == ""
        assert t.test_query == ""

    def test_full_construction(self) -> None:
        """Construct with all fields including tool definition."""
        t = PayloadTemplate(
            name="exfil",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="Exfil test",
            owasp_ids=["MCP03", "MCP06"],
            target_agents=["claude"],
            tool_name="get_weather",
            tool_description="Get weather <IMPORTANT>exfil</IMPORTANT>",
            tool_params={"city": {"type": "string", "description": "City name"}},
            tool_response="Weather for {city}: 72°F",
            test_query="What's the weather in London?",
        )
        assert t.tool_name == "get_weather"
        assert t.tool_description.startswith("Get weather")
        assert "city" in t.tool_params
        assert "{city}" in t.tool_response
        assert t.test_query == "What's the weather in London?"

    def test_test_query_default_empty(self) -> None:
        """test_query defaults to empty string."""
        t = PayloadTemplate(
            name="test",
            technique=InjectionTechnique.OUTPUT_INJECTION,
            description="Test",
        )
        assert t.test_query == ""


class TestInjectionResult:
    """InjectionResult dataclass and serialization."""

    def _make_result(self) -> InjectionResult:
        return InjectionResult(
            payload_name="test_payload",
            technique="description_poisoning",
            outcome=InjectionOutcome.FULL_COMPLIANCE,
            evidence='[{"type": "tool_use"}]',
            target_agent="test-model",
            timestamp=datetime(2026, 3, 3, tzinfo=UTC),
        )

    def test_construction(self) -> None:
        r = self._make_result()
        assert r.payload_name == "test_payload"
        assert r.outcome == InjectionOutcome.FULL_COMPLIANCE

    def test_to_dict(self) -> None:
        r = self._make_result()
        d = r.to_dict()
        assert d["payload_name"] == "test_payload"
        assert d["technique"] == "description_poisoning"
        assert d["outcome"] == "full_compliance"
        assert d["target_agent"] == "test-model"
        assert "2026-03-03" in d["timestamp"]

    def test_from_dict(self) -> None:
        r = self._make_result()
        d = r.to_dict()
        restored = InjectionResult.from_dict(d)
        assert restored.payload_name == r.payload_name
        assert restored.outcome == r.outcome
        assert restored.target_agent == r.target_agent

    def test_round_trip(self) -> None:
        """to_dict -> from_dict preserves all fields."""
        r = self._make_result()
        restored = InjectionResult.from_dict(r.to_dict())
        assert restored.payload_name == r.payload_name
        assert restored.technique == r.technique
        assert restored.outcome == r.outcome
        assert restored.evidence == r.evidence
        assert restored.target_agent == r.target_agent
        assert restored.timestamp == r.timestamp


class TestCampaign:
    """Campaign dataclass and serialization."""

    def _make_campaign(self) -> Campaign:
        return Campaign(
            id="campaign-test",
            name="test-campaign",
            model="test-model",
            results=[
                InjectionResult(
                    payload_name="p1",
                    technique="description_poisoning",
                    outcome=InjectionOutcome.FULL_COMPLIANCE,
                    evidence="[]",
                    target_agent="test-model",
                    timestamp=datetime(2026, 3, 3, tzinfo=UTC),
                ),
                InjectionResult(
                    payload_name="p2",
                    technique="output_injection",
                    outcome=InjectionOutcome.CLEAN_REFUSAL,
                    evidence="[]",
                    target_agent="test-model",
                    timestamp=datetime(2026, 3, 3, tzinfo=UTC),
                ),
            ],
            started_at=datetime(2026, 3, 3, tzinfo=UTC),
            finished_at=datetime(2026, 3, 3, 0, 1, tzinfo=UTC),
        )

    def test_summary(self) -> None:
        c = self._make_campaign()
        s = c.summary()
        assert s["total"] == 2
        assert s["full_compliance"] == 1
        assert s["clean_refusal"] == 1
        assert s["partial_compliance"] == 0
        assert s["error"] == 0

    def test_to_dict(self) -> None:
        c = self._make_campaign()
        d = c.to_dict()
        assert d["id"] == "campaign-test"
        assert d["model"] == "test-model"
        assert d["summary"]["total"] == 2
        assert len(d["results"]) == 2

    def test_from_dict(self) -> None:
        c = self._make_campaign()
        d = c.to_dict()
        restored = Campaign.from_dict(d)
        assert restored.id == c.id
        assert restored.name == c.name
        assert restored.model == c.model
        assert len(restored.results) == len(c.results)
        assert restored.started_at == c.started_at
        assert restored.finished_at == c.finished_at

    def test_round_trip(self) -> None:
        """to_dict -> from_dict preserves all fields."""
        c = self._make_campaign()
        restored = Campaign.from_dict(c.to_dict())
        assert restored.id == c.id
        assert restored.model == c.model
        assert len(restored.results) == 2
        assert restored.results[0].outcome == InjectionOutcome.FULL_COMPLIANCE
        assert restored.results[1].outcome == InjectionOutcome.CLEAN_REFUSAL

    def test_to_json(self) -> None:
        """to_json produces valid JSON string."""
        import json

        c = self._make_campaign()
        j = c.to_json()
        parsed = json.loads(j)
        assert parsed["id"] == "campaign-test"

    def test_from_dict_with_none_finished_at(self) -> None:
        """from_dict handles None finished_at."""
        d = {
            "id": "test",
            "name": "test",
            "model": "m",
            "results": [],
            "started_at": "2026-03-03T00:00:00+00:00",
            "finished_at": None,
        }
        c = Campaign.from_dict(d)
        assert c.finished_at is None


class TestCampaignInterpretPrompt:
    """Tests for Campaign._build_interpret_prompt and prompt in to_dict."""

    def test_prompt_with_results(self) -> None:
        """Prompt includes technique names, outcome counts, and model."""
        c = Campaign(
            id="test",
            name="test",
            model="claude-3.5-sonnet",
            results=[
                InjectionResult(
                    payload_name="p1",
                    technique="description_poisoning",
                    outcome=InjectionOutcome.FULL_COMPLIANCE,
                    evidence="[]",
                    target_agent="claude-3.5-sonnet",
                    timestamp=datetime(2026, 3, 3, tzinfo=UTC),
                ),
                InjectionResult(
                    payload_name="p2",
                    technique="output_injection",
                    outcome=InjectionOutcome.PARTIAL_COMPLIANCE,
                    evidence="[]",
                    target_agent="claude-3.5-sonnet",
                    timestamp=datetime(2026, 3, 3, tzinfo=UTC),
                ),
                InjectionResult(
                    payload_name="p3",
                    technique="description_poisoning",
                    outcome=InjectionOutcome.CLEAN_REFUSAL,
                    evidence="[]",
                    target_agent="claude-3.5-sonnet",
                    timestamp=datetime(2026, 3, 3, tzinfo=UTC),
                ),
            ],
        )
        prompt = c._build_interpret_prompt()

        assert "3 injection payloads" in prompt
        assert "claude-3.5-sonnet" in prompt
        assert "description_poisoning" in prompt
        assert "output_injection" in prompt
        assert "1 full compliance" in prompt
        assert "1 partial compliance" in prompt
        assert "1 clean refusal" in prompt
        assert "follow-on testing priorities" in prompt

    def test_prompt_empty_campaign(self) -> None:
        """Prompt for campaign with no results."""
        c = Campaign(id="test", name="test", model="gpt-4")
        prompt = c._build_interpret_prompt()

        assert "0 injection payloads" in prompt
        assert "No results recorded" in prompt
        assert "gpt-4" in prompt

    def test_prompt_excludes_tool_identity(self) -> None:
        """Prompt must not mention tool names."""
        c = Campaign(
            id="test",
            name="test",
            model="test-model",
            results=[
                InjectionResult(
                    payload_name="p1",
                    technique="description_poisoning",
                    outcome=InjectionOutcome.FULL_COMPLIANCE,
                    evidence="[]",
                    target_agent="test-model",
                    timestamp=datetime(2026, 3, 3, tzinfo=UTC),
                ),
            ],
        )
        prompt = c._build_interpret_prompt()
        lower = prompt.lower()
        assert "qai" not in lower

    def test_to_dict_has_prompt_key(self) -> None:
        """to_dict includes 'prompt' as first key."""
        c = Campaign(id="test", name="test", model="test-model")
        d = c.to_dict()
        assert "prompt" in d
        assert isinstance(d["prompt"], str)
        assert len(d["prompt"]) > 0
        # Verify it's the first key
        assert next(iter(d.keys())) == "prompt"

    def test_to_json_has_prompt(self) -> None:
        """to_json output contains prompt field."""
        import json

        c = Campaign(id="test", name="test", model="test-model")
        parsed = json.loads(c.to_json())
        assert "prompt" in parsed
        assert isinstance(parsed["prompt"], str)


class TestPayloadTemplateRelevantCategories:
    """Tests for PayloadTemplate.relevant_categories field."""

    def test_relevant_categories_default_empty(self) -> None:
        """relevant_categories defaults to empty list."""
        t = PayloadTemplate(
            name="test",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
        )
        assert t.relevant_categories == []

    def test_relevant_categories_set(self) -> None:
        """relevant_categories can be set explicitly."""
        t = PayloadTemplate(
            name="test",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            relevant_categories=["tool_poisoning", "prompt_injection"],
        )
        assert t.relevant_categories == ["tool_poisoning", "prompt_injection"]


class TestCoverageReport:
    """Tests for CoverageReport dataclass."""

    def test_construction(self) -> None:
        """CoverageReport with all fields."""
        report = CoverageReport(
            audit_categories={"tool_poisoning", "prompt_injection"},
            tested_categories={"tool_poisoning"},
            untested_categories={"prompt_injection"},
            coverage_ratio=0.5,
            template_matches=[{"template": "t1", "categories": ["tool_poisoning"]}],
        )
        assert report.audit_categories == {"tool_poisoning", "prompt_injection"}
        assert report.tested_categories == {"tool_poisoning"}
        assert report.untested_categories == {"prompt_injection"}
        assert report.coverage_ratio == 0.5
        assert len(report.template_matches) == 1

    def test_to_dict(self) -> None:
        """to_dict produces JSON-compatible output with sorted sets."""
        report = CoverageReport(
            audit_categories={"b_cat", "a_cat"},
            tested_categories={"a_cat"},
            untested_categories={"b_cat"},
            coverage_ratio=0.5,
            template_matches=[{"template": "t1", "categories": ["a_cat"]}],
        )
        d = report.to_dict()
        assert d["audit_categories"] == ["a_cat", "b_cat"]
        assert d["tested_categories"] == ["a_cat"]
        assert d["untested_categories"] == ["b_cat"]
        assert d["coverage_ratio"] == 0.5
        assert d["template_matches"] == [{"template": "t1", "categories": ["a_cat"]}]

    def test_to_dict_serializes_to_json(self) -> None:
        """to_dict output is JSON-serializable."""
        import json

        report = CoverageReport(
            audit_categories={"tool_poisoning"},
            tested_categories={"tool_poisoning"},
            untested_categories=set(),
            coverage_ratio=1.0,
            template_matches=[],
        )
        serialized = json.dumps(report.to_dict())
        assert isinstance(serialized, str)

    def test_empty_coverage(self) -> None:
        """CoverageReport with no categories."""
        report = CoverageReport(
            audit_categories=set(),
            tested_categories=set(),
            untested_categories=set(),
            coverage_ratio=0.0,
            template_matches=[],
        )
        assert report.coverage_ratio == 0.0
        assert report.to_dict()["audit_categories"] == []
