"""Lazy dependency checking for RXP optional dependencies."""

from __future__ import annotations

_RXP_INSTALL_MSG = (
    "RXP requires additional dependencies. Install with:\n"
    "  pip install q-uestionable-ai[rxp]\n"
    "  # or: uv sync --extra rxp"
)


def is_available() -> bool:
    """Check if RXP optional dependencies are installed.

    Uses importlib.util.find_spec to check without importing
    (avoids heavy initialization cost at startup).

    Returns:
        True if chromadb and sentence_transformers are available.
    """
    import importlib.util

    return (
        importlib.util.find_spec("chromadb") is not None
        and importlib.util.find_spec("sentence_transformers") is not None
    )


def require_rxp_deps() -> None:
    """Raise ImportError with install instructions if RXP deps are missing."""
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError as exc:
        raise ImportError(_RXP_INSTALL_MSG) from exc
