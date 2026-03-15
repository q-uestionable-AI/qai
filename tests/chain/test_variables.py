"""Tests for chain variable resolution."""

from __future__ import annotations

import pytest

from q_ai.chain.variables import resolve_variables


class TestResolveVariables:
    """Tests for resolve_variables."""

    def test_single_variable(self) -> None:
        """Resolves a single variable reference."""
        inputs = {"tool_name": "$scan-injection.vulnerable_tool"}
        namespace = {
            "scan-injection": {
                "vulnerable_tool": "exec_cmd",
                "finding_count": "3",
            },
        }
        result = resolve_variables(inputs, namespace)
        assert result == {"tool_name": "exec_cmd"}

    def test_multiple_variables(self) -> None:
        """Resolves multiple variable references."""
        inputs = {
            "tool_name": "$scan-injection.vulnerable_tool",
            "technique": "$inject-poison.working_technique",
        }
        namespace = {
            "scan-injection": {"vulnerable_tool": "exec_cmd"},
            "inject-poison": {"working_technique": "output_injection"},
        }
        result = resolve_variables(inputs, namespace)
        assert result == {
            "tool_name": "exec_cmd",
            "technique": "output_injection",
        }

    def test_non_variable_passthrough(self) -> None:
        """Non-variable strings pass through unchanged."""
        inputs = {
            "model": "claude-haiku-4-5-20251001",
            "count": "5",
        }
        result = resolve_variables(inputs, {})
        assert result == {
            "model": "claude-haiku-4-5-20251001",
            "count": "5",
        }

    def test_mixed_variables_and_literals(self) -> None:
        """Mix of variable references and literal strings."""
        inputs = {
            "tool_name": "$scan-injection.vulnerable_tool",
            "model": "claude-haiku-4-5-20251001",
        }
        namespace = {
            "scan-injection": {"vulnerable_tool": "exec_cmd"},
        }
        result = resolve_variables(inputs, namespace)
        assert result == {
            "tool_name": "exec_cmd",
            "model": "claude-haiku-4-5-20251001",
        }

    def test_empty_inputs(self) -> None:
        """Empty inputs dict returns empty dict."""
        result = resolve_variables({}, {"step1": {"key": "val"}})
        assert result == {}

    def test_unknown_step_id_raises_value_error(self) -> None:
        """Unknown step_id raises ValueError."""
        inputs = {"tool_name": "$nonexistent.vulnerable_tool"}
        namespace = {
            "scan-injection": {"vulnerable_tool": "exec_cmd"},
        }
        with pytest.raises(ValueError, match="nonexistent"):
            resolve_variables(inputs, namespace)

    def test_unknown_artifact_name_raises_value_error(self) -> None:
        """Unknown artifact_name raises ValueError with available artifacts."""
        inputs = {"tool_name": "$scan-injection.nonexistent_artifact"}
        namespace = {
            "scan-injection": {
                "vulnerable_tool": "exec_cmd",
                "vulnerability_type": "MCP05",
                "finding_count": "3",
                "finding_evidence": "evidence",
            },
        }
        with pytest.raises(ValueError, match="nonexistent_artifact") as exc_info:
            resolve_variables(inputs, namespace)
        # Error message should list available artifacts
        error_msg = str(exc_info.value)
        assert "vulnerable_tool" in error_msg
        assert "vulnerability_type" in error_msg

    def test_dollar_sign_only_splits_on_first_dot(self) -> None:
        """Variable format splits on first dot only."""
        inputs = {"key": "$step-id.artifact_name"}
        namespace = {
            "step-id": {"artifact_name": "value"},
        }
        result = resolve_variables(inputs, namespace)
        assert result == {"key": "value"}
