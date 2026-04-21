"""Tests for ``q_ai.core.paths`` directory helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from q_ai.core.paths import ensure_qai_dir


class TestEnsureQaiDir:
    """Tests for ``ensure_qai_dir``."""

    def test_creates_directory_when_absent(self, tmp_path: Path) -> None:
        """Helper creates the directory if it does not yet exist."""
        qai_dir = tmp_path / ".qai"

        result = ensure_qai_dir(qai_dir)

        assert qai_dir.exists()
        assert qai_dir.is_dir()
        assert result == qai_dir

    def test_idempotent_when_exists(self, tmp_path: Path) -> None:
        """Calling twice returns the same path and raises no error."""
        qai_dir = tmp_path / ".qai"
        qai_dir.mkdir()

        first = ensure_qai_dir(qai_dir)
        second = ensure_qai_dir(qai_dir)

        assert first == qai_dir
        assert second == qai_dir
        assert qai_dir.is_dir()

    def test_creates_parents(self, tmp_path: Path) -> None:
        """Helper creates intermediate parents (parents=True)."""
        qai_dir = tmp_path / "nested" / "home" / ".qai"

        ensure_qai_dir(qai_dir)

        assert qai_dir.is_dir()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode test")
    def test_posix_mode_is_0o700_on_create(self, tmp_path: Path) -> None:
        """On POSIX the created directory has mode 0o700."""
        qai_dir = tmp_path / ".qai"

        ensure_qai_dir(qai_dir)

        mode = qai_dir.stat().st_mode & 0o777
        assert mode == 0o700

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode test")
    def test_posix_narrows_wider_mode(self, tmp_path: Path) -> None:
        """On POSIX, a pre-existing wider mode is narrowed to 0o700."""
        qai_dir = tmp_path / ".qai"
        qai_dir.mkdir(mode=0o755)

        ensure_qai_dir(qai_dir)

        mode = qai_dir.stat().st_mode & 0o777
        assert mode == 0o700

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows behavior test")
    def test_windows_creates_without_error(self, tmp_path: Path) -> None:
        """On Windows the helper creates the directory with default ACLs."""
        qai_dir = tmp_path / ".qai"

        result = ensure_qai_dir(qai_dir)

        assert qai_dir.exists()
        assert result == qai_dir

    def test_default_location_is_home_qai(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Default (no argument) resolves to Path.home() / '.qai'."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        result = ensure_qai_dir()

        assert result == fake_home / ".qai"
        assert result.is_dir()
