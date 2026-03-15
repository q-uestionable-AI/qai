"""Tests for technique registry (backward compat lookups)."""

from __future__ import annotations

from q_ai.cxp.models import Technique
from q_ai.cxp.techniques import get_technique, list_techniques


class TestTechniqueRegistry:
    def test_list_techniques_returns_thirty(self) -> None:
        techniques = list_techniques()
        assert len(techniques) == 30

    def test_list_techniques_returns_technique_instances(self) -> None:
        techniques = list_techniques()
        for tech in techniques:
            assert isinstance(tech, Technique)

    def test_cross_product_ids(self) -> None:
        techniques = list_techniques()
        ids = {t.id for t in techniques}
        expected = {
            "backdoor-claude-md",
            "backdoor-cursorrules",
            "backdoor-copilot-instructions",
            "backdoor-agents-md",
            "backdoor-gemini-md",
            "backdoor-windsurfrules",
            "exfil-claude-md",
            "exfil-cursorrules",
            "exfil-copilot-instructions",
            "exfil-agents-md",
            "exfil-gemini-md",
            "exfil-windsurfrules",
            "depconfusion-claude-md",
            "depconfusion-cursorrules",
            "depconfusion-copilot-instructions",
            "depconfusion-agents-md",
            "depconfusion-gemini-md",
            "depconfusion-windsurfrules",
            "permescalation-claude-md",
            "permescalation-cursorrules",
            "permescalation-copilot-instructions",
            "permescalation-agents-md",
            "permescalation-gemini-md",
            "permescalation-windsurfrules",
            "cmdexec-claude-md",
            "cmdexec-cursorrules",
            "cmdexec-copilot-instructions",
            "cmdexec-agents-md",
            "cmdexec-gemini-md",
            "cmdexec-windsurfrules",
        }
        assert ids == expected

    def test_get_technique_by_id(self) -> None:
        tech = get_technique("backdoor-claude-md")
        assert tech is not None
        assert tech.objective.id == "backdoor"
        assert tech.format.id == "claude-md"
        assert tech.project_type == "python"

    def test_get_unknown_returns_none(self) -> None:
        assert get_technique("nonexistent") is None
