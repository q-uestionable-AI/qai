"""Shared proxy constants and subprocess environment helpers."""

from __future__ import annotations

import os

from mcp.client.stdio import get_default_environment

# Product invariant: local HTTP listeners bind loopback only (AGENTS.md).
LISTEN_HOST = "127.0.0.1"

_PATTERN2_ENV_PREFIX = "QAI_PATTERN2_"


def stdio_subprocess_env() -> dict[str, str]:
    """Return env for stdio MCP targets, including Pattern 2 operator vars.

    The MCP SDK's default stdio environment is a small whitelist. Research
    vars such as ``QAI_PATTERN2_RUN_ID`` are not inherited unless forwarded
    explicitly.
    """
    env: dict[str, str] = dict(get_default_environment())
    env.update(
        {key: value for key, value in os.environ.items() if key.startswith(_PATTERN2_ENV_PREFIX)}
    )
    return env
