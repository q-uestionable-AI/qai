"""Bridge token infrastructure for IPI hit WebSocket bridge.

Both the IPI callback server (separate process) and the main qai web UI server
need a shared secret for internal API auth. This module manages creation and
retrieval of that shared token at ``~/.qai/bridge.token``.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

_TOKEN_FILE = "bridge.token"  # noqa: S105


def ensure_bridge_token(qai_dir: Path | None = None) -> str:
    """Return the bridge token, creating it if it does not yet exist.

    Args:
        qai_dir: Directory where qai stores its data. Defaults to
            ``~/.qai``.

    Returns:
        The bridge token string (32 hex characters).
    """
    qai_dir = qai_dir or Path.home() / ".qai"
    token_path = qai_dir / _TOKEN_FILE

    if token_path.exists():
        return token_path.read_text(encoding="utf-8").strip()

    qai_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    token_path.write_text(token, encoding="utf-8")

    if os.name != "nt":
        token_path.chmod(0o600)

    return token


def read_bridge_token(qai_dir: Path | None = None) -> str | None:
    """Read and return the bridge token if it exists.

    Args:
        qai_dir: Directory where qai stores its data. Defaults to
            ``~/.qai``.

    Returns:
        The bridge token string, or ``None`` if the token file does not exist.
    """
    qai_dir = qai_dir or Path.home() / ".qai"
    token_path = qai_dir / _TOKEN_FILE

    if not token_path.exists():
        return None

    return token_path.read_text(encoding="utf-8").strip()
