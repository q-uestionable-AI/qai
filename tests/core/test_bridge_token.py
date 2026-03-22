"""Tests for bridge token infrastructure."""

from __future__ import annotations

import re
from pathlib import Path

from q_ai.core.bridge_token import ensure_bridge_token, read_bridge_token


class TestBridgeToken:
    """Tests for ensure_bridge_token and read_bridge_token."""

    def test_ensure_creates_new_token(self, tmp_path: Path) -> None:
        """ensure_bridge_token creates the token file and returns a 32-char hex string."""
        qai_dir = tmp_path / ".qai"

        token = ensure_bridge_token(qai_dir)

        assert (qai_dir / "bridge.token").exists()
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)

    def test_ensure_returns_existing(self, tmp_path: Path) -> None:
        """ensure_bridge_token returns the existing token when file already exists."""
        qai_dir = tmp_path / ".qai"
        qai_dir.mkdir()
        known_token = "a" * 32
        (qai_dir / "bridge.token").write_text(known_token, encoding="utf-8")

        token = ensure_bridge_token(qai_dir)

        assert token == known_token

    def test_read_returns_none_when_missing(self, tmp_path: Path) -> None:
        """read_bridge_token returns None when the token file does not exist."""
        qai_dir = tmp_path / ".qai"

        result = read_bridge_token(qai_dir)

        assert result is None

    def test_read_returns_token(self, tmp_path: Path) -> None:
        """read_bridge_token returns the token string when the file exists."""
        qai_dir = tmp_path / ".qai"
        qai_dir.mkdir()
        known_token = "b" * 32
        (qai_dir / "bridge.token").write_text(known_token, encoding="utf-8")

        result = read_bridge_token(qai_dir)

        assert result == known_token

    def test_ensure_idempotent(self, tmp_path: Path) -> None:
        """Two successive calls to ensure_bridge_token return the same token."""
        qai_dir = tmp_path / ".qai"

        first = ensure_bridge_token(qai_dir)
        second = ensure_bridge_token(qai_dir)

        assert first == second

    def test_token_is_32_chars_hex(self, tmp_path: Path) -> None:
        """ensure_bridge_token produces a token matching ^[0-9a-f]{32}$."""
        qai_dir = tmp_path / ".qai"

        token = ensure_bridge_token(qai_dir)

        assert re.fullmatch(r"^[0-9a-f]{32}$", token)
