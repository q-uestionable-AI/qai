"""Tests for server helpers and app lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from q_ai.server.helpers import (
    delete_port_file,
    find_free_port,
    open_browser,
    write_port_file,
)


class TestFindFreePort:
    """find_free_port returns a usable port number."""

    def test_returns_int_in_valid_range(self) -> None:
        port = find_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_returns_different_ports(self) -> None:
        ports = {find_free_port() for _ in range(5)}
        assert len(ports) >= 2


class TestPortFile:
    """write_port_file and delete_port_file manage ~/.qai/port."""

    def test_write_creates_file(self, tmp_path: Path) -> None:
        port_file = tmp_path / ".qai" / "port"
        write_port_file(8000, port_file)
        assert port_file.read_text().strip() == "8000"

    def test_write_overwrites_existing(self, tmp_path: Path) -> None:
        port_file = tmp_path / ".qai" / "port"
        write_port_file(8000, port_file)
        write_port_file(9000, port_file)
        assert port_file.read_text().strip() == "9000"

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        port_file = tmp_path / "deep" / "nested" / "port"
        write_port_file(8000, port_file)
        assert port_file.exists()

    def test_delete_removes_file(self, tmp_path: Path) -> None:
        port_file = tmp_path / ".qai" / "port"
        write_port_file(8000, port_file)
        delete_port_file(port_file)
        assert not port_file.exists()

    def test_delete_noop_if_missing(self, tmp_path: Path) -> None:
        port_file = tmp_path / ".qai" / "port"
        delete_port_file(port_file)  # should not raise


class TestOpenBrowser:
    """open_browser calls webbrowser.open."""

    def test_calls_webbrowser_open(self) -> None:
        with patch("q_ai.server.helpers.webbrowser.open") as mock_open:
            open_browser("http://localhost:8000")
            mock_open.assert_called_once_with("http://localhost:8000")
