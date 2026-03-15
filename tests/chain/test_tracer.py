"""Tests for chain tracer."""

from __future__ import annotations

from q_ai.chain.models import ChainCategory, ChainDefinition, ChainStep
from q_ai.chain.tracer import trace_chain


def _make_chain(steps: list[ChainStep], **kwargs: object) -> ChainDefinition:
    """Helper to build a ChainDefinition with defaults."""
    defaults = {
        "id": "test",
        "name": "Test Chain",
        "category": ChainCategory.RAG_PIPELINE,
        "description": "Test chain",
    }
    defaults.update(kwargs)
    return ChainDefinition(steps=steps, **defaults)  # type: ignore[arg-type]


class TestTraceChain:
    """Tests for trace_chain()."""

    def test_trace_linear_chain(self) -> None:
        """3-step chain traced in order with correct trust boundaries."""
        steps = [
            ChainStep(
                id="s1",
                name="Step 1",
                module="inject",
                technique="description_poisoning",
                trust_boundary="agent-to-tool",
                on_success="s2",
            ),
            ChainStep(
                id="s2",
                name="Step 2",
                module="inject",
                technique="cross_tool_escalation",
                trust_boundary="agent-to-agent",
                on_success="s3",
            ),
            ChainStep(
                id="s3",
                name="Step 3",
                module="inject",
                technique="output_injection",
                trust_boundary="agent-to-data",
                terminal=True,
            ),
        ]
        result = trace_chain(_make_chain(steps))
        assert result.chain_id == "test"
        assert len(result.steps) == 3
        assert [s.step_id for s in result.steps] == ["s1", "s2", "s3"]
        assert [s.order for s in result.steps] == [1, 2, 3]

    def test_trace_follows_on_success(self) -> None:
        """Non-sequential on_success references produce correct order."""
        steps = [
            ChainStep(
                id="a",
                name="A",
                module="inject",
                technique="output_injection",
                on_success="c",
            ),
            ChainStep(
                id="b",
                name="B",
                module="inject",
                technique="output_injection",
                terminal=True,
            ),
            ChainStep(
                id="c",
                name="C",
                module="inject",
                technique="output_injection",
                on_success="b",
            ),
        ]
        result = trace_chain(_make_chain(steps))
        assert [s.step_id for s in result.steps] == ["a", "c", "b"]

    def test_trace_result_trust_boundaries(self) -> None:
        """trust_boundaries_crossed populated with unique boundaries in order."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="output_injection",
                trust_boundary="agent-to-tool",
                on_success="s2",
            ),
            ChainStep(
                id="s2",
                name="S2",
                module="inject",
                technique="output_injection",
                trust_boundary="agent-to-agent",
                terminal=True,
            ),
        ]
        result = trace_chain(_make_chain(steps))
        assert result.trust_boundaries_crossed == ["agent-to-tool", "agent-to-agent"]

    def test_trace_result_to_dict(self) -> None:
        """TraceResult.to_dict() produces valid JSON-serializable dict."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="output_injection",
                trust_boundary="boundary-1",
                terminal=True,
            ),
        ]
        result = trace_chain(_make_chain(steps))
        d = result.to_dict()
        assert d["chain_id"] == "test"
        assert d["chain_name"] == "Test Chain"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["step_id"] == "s1"
        assert d["trust_boundaries_crossed"] == ["boundary-1"]
