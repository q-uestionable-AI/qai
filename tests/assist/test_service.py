"""Tests for the assist service module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from q_ai.assist.service import (
    AssistantNotConfiguredError,
    _resolve_embedding_model,
    _resolve_knowledge_dir,
    _resolve_model_string,
)


class TestResolveModelString:
    """Model string resolution from config."""

    @patch("q_ai.assist.service.resolve")
    def test_builds_provider_model_string(self, mock_resolve: object) -> None:
        # resolve returns (value, source) tuples
        def side_effect(key: str, **kwargs: object) -> tuple[str | None, str]:
            if key == "assist.provider":
                return "ollama", "db"
            if key == "assist.model":
                return "llama3.1", "db"
            return None, "default"

        mock_resolve.side_effect = side_effect  # type: ignore[attr-defined]
        result = _resolve_model_string()
        assert result == "ollama/llama3.1"

    @patch("q_ai.assist.service.resolve")
    def test_strips_duplicate_provider_prefix(self, mock_resolve: object) -> None:
        """Model set via UI already includes provider prefix — no double-prefix."""

        def side_effect(key: str, **kwargs: object) -> tuple[str | None, str]:
            if key == "assist.provider":
                return "anthropic", "db"
            if key == "assist.model":
                return "anthropic/claude-opus-4-6", "db"
            return None, "default"

        mock_resolve.side_effect = side_effect  # type: ignore[attr-defined]
        result = _resolve_model_string()
        assert result == "anthropic/claude-opus-4-6"

    @patch("q_ai.assist.service.resolve", return_value=(None, "default"))
    def test_raises_when_not_configured(self, _mock: object) -> None:
        with pytest.raises(AssistantNotConfiguredError, match="not configured"):
            _resolve_model_string()

    @patch("q_ai.assist.service.resolve")
    def test_raises_when_only_provider_set(self, mock_resolve: object) -> None:
        def side_effect(key: str, **kwargs: object) -> tuple[str | None, str]:
            if key == "assist.provider":
                return "ollama", "db"
            return None, "default"

        mock_resolve.side_effect = side_effect  # type: ignore[attr-defined]
        with pytest.raises(AssistantNotConfiguredError):
            _resolve_model_string()


class TestResolveEmbeddingModel:
    """Embedding model resolution."""

    @patch("q_ai.assist.service.resolve", return_value=(None, "default"))
    def test_default_model(self, _mock: object) -> None:
        result = _resolve_embedding_model()
        assert result == "all-MiniLM-L6-v2"

    @patch("q_ai.assist.service.resolve", return_value=("custom-model", "db"))
    def test_custom_model(self, _mock: object) -> None:
        result = _resolve_embedding_model()
        assert result == "custom-model"


class TestResolveKnowledgeDir:
    """Knowledge directory resolution."""

    @patch("q_ai.assist.service.resolve", return_value=(None, "default"))
    def test_default_dir(self, _mock: object) -> None:
        result = _resolve_knowledge_dir()
        assert result.name == "knowledge"
        assert ".qai" in str(result)

    @patch("q_ai.assist.service.resolve", return_value=("/custom/path", "db"))
    def test_custom_dir(self, _mock: object) -> None:
        from pathlib import PurePosixPath

        result = _resolve_knowledge_dir()
        # Compare as PurePosixPath to handle Windows path separator differences
        assert PurePosixPath(str(result).replace("\\", "/")) == PurePosixPath("/custom/path")
