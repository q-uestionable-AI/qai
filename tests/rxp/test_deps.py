"""Tests for RXP dependency guard."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from q_ai.rxp._deps import _RXP_INSTALL_MSG, require_rxp_deps


class TestDepsGuard:
    """Tests for the RXP dependency guard."""

    def test_require_rxp_deps_missing(self) -> None:
        """Simulated missing import raises ImportError with install message."""
        with (
            patch.dict("sys.modules", {"chromadb": None}),
            pytest.raises(ImportError, match="pip install q-uestionable-ai"),
        ):
            require_rxp_deps()

    def test_require_rxp_deps_installed(self) -> None:
        """Passes when deps are installed (no exception raised)."""
        try:
            import chromadb  # noqa: F401
            import sentence_transformers  # noqa: F401
        except ImportError:
            pytest.skip("RXP deps not installed")
        require_rxp_deps()  # Should not raise

    def test_install_message_content(self) -> None:
        assert "pip install q-uestionable-ai[rxp]" in _RXP_INSTALL_MSG
        assert "uv sync --extra rxp" in _RXP_INSTALL_MSG
