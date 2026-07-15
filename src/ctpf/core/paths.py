"""Path helpers for the CTPF data directory.

The ``~/.ctpf/`` directory stores the SQLite database, framework cache,
backups, and historical local evidence. Because these can contain captured
request bodies, headers, and findings that may include sensitive values, the
helper centralizes creation of that directory so it is born with permissions
``0o700`` on POSIX.

Sites that only *compute* a ``Path.home() / ".ctpf" / ...`` sub-path —
without materializing a directory — do not need this helper. Only
sites that actually call ``mkdir`` on ``~/.ctpf/`` or a child directory
import from here.

Windows ACL management at the Python level is fragile, so on Windows
the helper creates the directory and returns; default NTFS ACLs apply.
See ``SECURITY.md`` (``## Evidence at Rest``) for the access boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_ctpf_dir(ctpf_dir: Path | None = None) -> Path:
    """Ensure the ctpf data directory exists, hardened to ``0o700`` on POSIX.

    Idempotent and safe to call repeatedly. On POSIX the mode is enforced
    on every call so a directory previously created with wider
    permissions (older installs, manual ``mkdir``) is narrowed.

    On Windows the directory is created with default NTFS ACLs; no
    Python-level ACL management is attempted.

    Args:
        ctpf_dir: Override for the ctpf data directory location. When
            ``None``, resolves to ``Path.home() / ".ctpf"``.

    Returns:
        The resolved directory path.
    """
    resolved = ctpf_dir if ctpf_dir is not None else Path.home() / ".ctpf"
    resolved.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        resolved.chmod(0o700)
    return resolved
