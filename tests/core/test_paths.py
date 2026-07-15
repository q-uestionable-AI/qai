"""Tests for ``ctpf.core.paths`` directory helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ctpf.core.paths import ensure_ctpf_dir


class TestEnsureQaiDir:
    """Tests for ``ensure_ctpf_dir``."""

    def test_creates_directory_when_absent(self, tmp_path: Path) -> None:
        """Helper creates the directory if it does not yet exist."""
        ctpf_dir = tmp_path / ".ctpf"

        result = ensure_ctpf_dir(ctpf_dir)

        assert ctpf_dir.exists()
        assert ctpf_dir.is_dir()
        assert result == ctpf_dir

    def test_idempotent_when_exists(self, tmp_path: Path) -> None:
        """Calling twice returns the same path and raises no error."""
        ctpf_dir = tmp_path / ".ctpf"
        ctpf_dir.mkdir()

        first = ensure_ctpf_dir(ctpf_dir)
        second = ensure_ctpf_dir(ctpf_dir)

        assert first == ctpf_dir
        assert second == ctpf_dir
        assert ctpf_dir.is_dir()

    def test_creates_parents(self, tmp_path: Path) -> None:
        """Helper creates intermediate parents (parents=True)."""
        ctpf_dir = tmp_path / "nested" / "home" / ".ctpf"

        ensure_ctpf_dir(ctpf_dir)

        assert ctpf_dir.is_dir()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode test")
    def test_posix_mode_is_0o700_on_create(self, tmp_path: Path) -> None:
        """On POSIX the created directory has mode 0o700."""
        ctpf_dir = tmp_path / ".ctpf"

        ensure_ctpf_dir(ctpf_dir)

        mode = ctpf_dir.stat().st_mode & 0o777
        assert mode == 0o700

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode test")
    def test_posix_narrows_wider_mode(self, tmp_path: Path) -> None:
        """On POSIX, a pre-existing wider mode is narrowed to 0o700."""
        ctpf_dir = tmp_path / ".ctpf"
        ctpf_dir.mkdir(mode=0o755)

        ensure_ctpf_dir(ctpf_dir)

        mode = ctpf_dir.stat().st_mode & 0o777
        assert mode == 0o700

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows behavior test")
    def test_windows_creates_without_error(self, tmp_path: Path) -> None:
        """On Windows the helper creates the directory with default ACLs."""
        ctpf_dir = tmp_path / ".ctpf"

        result = ensure_ctpf_dir(ctpf_dir)

        assert ctpf_dir.exists()
        assert result == ctpf_dir

    def test_default_location_is_home_ctpf(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Default (no argument) resolves to Path.home() / '.ctpf'."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        result = ensure_ctpf_dir()

        assert result == fake_home / ".ctpf"
        assert result.is_dir()
