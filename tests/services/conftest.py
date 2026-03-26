"""Shared fixtures for service layer tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from q_ai.core.db import (
    create_evidence,
    create_finding,
    create_run,
    update_run_status,
)
from q_ai.core.models import RunStatus, Severity
from q_ai.core.schema import migrate


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Create an in-memory-like temp DB with schema applied, returning an open connection."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def sample_run(db: sqlite3.Connection) -> str:
    """Create a sample workflow run and return its ID."""
    run_id = create_run(db, module="workflow", name="assess", source="web")
    update_run_status(db, run_id, RunStatus.COMPLETED)
    return run_id


@pytest.fixture
def sample_child_runs(db: sqlite3.Connection, sample_run: str) -> list[str]:
    """Create child runs under the sample workflow run."""
    audit_id = create_run(db, module="audit", parent_run_id=sample_run, source="web")
    update_run_status(db, audit_id, RunStatus.COMPLETED)
    inject_id = create_run(db, module="inject", parent_run_id=sample_run, source="web")
    update_run_status(db, inject_id, RunStatus.COMPLETED)
    return [audit_id, inject_id]


@pytest.fixture
def sample_findings(db: sqlite3.Connection, sample_child_runs: list[str]) -> list[str]:
    """Create sample findings across child runs."""
    audit_id, inject_id = sample_child_runs
    f1 = create_finding(
        db,
        run_id=audit_id,
        module="audit",
        category="command_injection",
        severity=Severity.HIGH,
        title="Command injection via tool",
    )
    f2 = create_finding(
        db,
        run_id=audit_id,
        module="audit",
        category="permission_bypass",
        severity=Severity.MEDIUM,
        title="Permission bypass in resource",
    )
    f3 = create_finding(
        db,
        run_id=inject_id,
        module="inject",
        category="tool_poisoning",
        severity=Severity.CRITICAL,
        title="Successful tool poisoning",
    )
    return [f1, f2, f3]


@pytest.fixture
def sample_evidence(
    db: sqlite3.Connection, sample_findings: list[str], sample_child_runs: list[str]
) -> list[str]:
    """Create sample evidence attached to findings and runs."""
    f1, f2, _f3 = sample_findings
    audit_id = sample_child_runs[0]
    e1 = create_evidence(db, type="request", finding_id=f1, content='{"tool": "exec"}')
    e2 = create_evidence(db, type="response", finding_id=f1, content="injected output")
    e3 = create_evidence(db, type="log", run_id=audit_id, content="scan log data")
    e4 = create_evidence(db, type="response", finding_id=f2, content="bypass result")
    return [e1, e2, e3, e4]
