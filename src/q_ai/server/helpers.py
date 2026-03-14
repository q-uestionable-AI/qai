"""Helper utilities for the q-ai web server."""

from __future__ import annotations

import socket
import webbrowser
from pathlib import Path

_DEFAULT_PORT_FILE = Path.home() / ".qai" / "port"


def find_free_port() -> int:
    """Find an available TCP port by binding to port 0.

    Returns:
        An available port number.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def write_port_file(port: int, path: Path | None = None) -> None:
    """Write the active server port to a file.

    Creates parent directories if they don't exist.

    Args:
        port: The port number to write.
        path: File path. Defaults to ~/.qai/port.
    """
    target = path or _DEFAULT_PORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(port))


def delete_port_file(path: Path | None = None) -> None:
    """Remove the port file if it exists.

    Args:
        path: File path. Defaults to ~/.qai/port.
    """
    target = path or _DEFAULT_PORT_FILE
    target.unlink(missing_ok=True)


def open_browser(url: str) -> None:
    """Open a URL in the default browser.

    Args:
        url: The URL to open.
    """
    webbrowser.open(url)
