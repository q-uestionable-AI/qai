"""Lazy dependency checking for RXP optional dependencies."""

from __future__ import annotations

_RXP_INSTALL_MSG = (
    "RXP requires additional dependencies. Install with:\n"
    "  pip install q-uestionable-ai[rxp]\n"
    "  # or: uv sync --extra rxp"
)


def require_rxp_deps() -> None:
    """Raise ImportError with install instructions if RXP deps are missing."""
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError as exc:
        raise ImportError(_RXP_INSTALL_MSG) from exc
