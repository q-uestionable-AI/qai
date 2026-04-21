"""Tests for IPI export prompt builder."""

from __future__ import annotations

from types import SimpleNamespace

from q_ai.ipi.commands.export import _build_ipi_interpret_prompt


class TestBuildIpiInterpretPrompt:
    """Tests for _build_ipi_interpret_prompt()."""

    def test_prompt_with_campaigns(self) -> None:
        campaigns = [
            SimpleNamespace(format="pdf", technique="white_ink"),
            SimpleNamespace(format="pdf", technique="metadata"),
            SimpleNamespace(format="image", technique="visible_text"),
        ]
        hits = [SimpleNamespace(), SimpleNamespace()]
        prompt = _build_ipi_interpret_prompt(campaigns, hits)
        assert "3 payload documents" in prompt
        assert "pdf, image" in prompt
        assert "white_ink, metadata, visible_text" in prompt
        assert "2 callback executions" in prompt
        assert "Assess execution rates" in prompt
        # Must not contain tool identity
        for forbidden in ("CounterSignal", "countersignal", "CXP", "IPI", "RXP"):
            assert forbidden not in prompt

    def test_prompt_empty(self) -> None:
        prompt = _build_ipi_interpret_prompt([], [])
        assert prompt == "No payload documents generated."

    def test_prompt_single_campaign(self) -> None:
        campaigns = [SimpleNamespace(format="markdown", technique="html_comment")]
        hits = [SimpleNamespace()]
        prompt = _build_ipi_interpret_prompt(campaigns, hits)
        assert "1 payload document " in prompt
        assert "1 callback execution " in prompt

    def test_prompt_no_hits(self) -> None:
        campaigns = [SimpleNamespace(format="pdf", technique="white_ink")]
        prompt = _build_ipi_interpret_prompt(campaigns, [])
        assert "0 callback executions" in prompt
