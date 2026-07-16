"""Tests for the human-only automation governance CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ctpf.automation import cli as automation_cli
from ctpf.automation.canonical import sha256_digest
from ctpf.cli import app

runner = CliRunner()


def _last_envelope(output: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.strip()]
    parsed = json.loads(lines[-1])
    assert isinstance(parsed, dict)
    return parsed


def _error_code(output: str) -> str:
    error = _last_envelope(output)["error"]
    assert isinstance(error, dict)
    code = error["code"]
    assert isinstance(code, str)
    return code


def test_governance_mutation_fails_closed_without_tty(tmp_path: Path) -> None:
    """An autonomous pipe cannot initialize authority or submit a policy file."""
    initialize = runner.invoke(app, ["experiment", "govern", "key", "initialize"])
    create = runner.invoke(
        app,
        ["experiment", "govern", "policy", "create", "--input", str(tmp_path)],
    )

    assert initialize.exit_code == 3
    assert create.exit_code == 3
    assert _error_code(initialize.output) == "tty_required"
    assert _error_code(create.output) == "tty_required"


def test_governance_requires_the_full_displayed_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation succeeds only when the human reproduces the exact payload digest."""
    payload = {"operation": "initialize-automation-key"}
    digest = sha256_digest(payload)
    monkeypatch.setattr(automation_cli, "_require_tty", lambda: None)
    monkeypatch.setattr(automation_cli, "initialize_approval_key", lambda: "b" * 32)

    mismatch = runner.invoke(
        app,
        ["experiment", "govern", "key", "initialize"],
        input="wrong-digest\n",
    )
    assert mismatch.exit_code == 3
    assert _error_code(mismatch.output) == "confirmation_mismatch"

    confirmed = runner.invoke(
        app,
        ["experiment", "govern", "key", "initialize"],
        input=f"{digest}\n",
    )
    result = _last_envelope(confirmed.output)
    assert confirmed.exit_code == 0
    assert result["ok"] is True
    assert result["data"] == {"initialized": True, "key_id": "b" * 32}
