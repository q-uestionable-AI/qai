"""Tests for q-ai core data models."""
from __future__ import annotations

import datetime

from q_ai.core.models import (
    Evidence,
    Finding,
    Run,
    RunStatus,
    Severity,
    Target,
)


class TestEnums:
    def test_severity_values(self) -> None:
        assert Severity.INFO == 0
        assert Severity.LOW == 1
        assert Severity.MEDIUM == 2
        assert Severity.HIGH == 3
        assert Severity.CRITICAL == 4

    def test_run_status_values(self) -> None:
        assert RunStatus.PENDING == 0
        assert RunStatus.RUNNING == 1
        assert RunStatus.COMPLETED == 2
        assert RunStatus.FAILED == 3
        assert RunStatus.CANCELLED == 4


class TestRun:
    def test_construction(self) -> None:
        run = Run(id="abc123", module="audit", status=RunStatus.PENDING)
        assert run.id == "abc123"
        assert run.module == "audit"
        assert run.status == RunStatus.PENDING
        assert run.parent_run_id is None

    def test_to_dict(self) -> None:
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        run = Run(
            id="abc",
            module="audit",
            status=RunStatus.RUNNING,
            started_at=now,
        )
        d = run.to_dict()
        assert d["id"] == "abc"
        assert d["status"] == 1
        assert d["started_at"] == now.isoformat()

    def test_from_row(self) -> None:
        row = {
            "id": "abc",
            "module": "audit",
            "status": 2,
            "parent_run_id": None,
            "name": "scan1",
            "target_id": None,
            "config": '{"key": "val"}',
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
        }
        run = Run.from_row(row)
        assert run.status == RunStatus.COMPLETED
        assert run.config == {"key": "val"}
        assert run.started_at == datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


class TestTarget:
    def test_construction(self) -> None:
        t = Target(id="t1", type="server", name="test-mcp")
        assert t.type == "server"
        assert t.uri is None

    def test_to_dict(self) -> None:
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        t = Target(id="t1", type="server", name="test", created_at=now)
        d = t.to_dict()
        assert d["type"] == "server"
        assert d["created_at"] == now.isoformat()

    def test_from_row(self) -> None:
        row = {
            "id": "t1",
            "type": "server",
            "name": "test",
            "uri": "http://localhost",
            "metadata": '{"k": "v"}',
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        t = Target.from_row(row)
        assert t.metadata == {"k": "v"}


class TestFinding:
    def test_construction(self) -> None:
        f = Finding(
            id="f1",
            run_id="r1",
            module="audit",
            category="command_injection",
            severity=Severity.HIGH,
            title="Test finding",
        )
        assert f.severity == Severity.HIGH
        assert f.framework_ids is None

    def test_to_dict_and_from_row_roundtrip(self) -> None:
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        f = Finding(
            id="f1",
            run_id="r1",
            module="audit",
            category="command_injection",
            severity=Severity.CRITICAL,
            title="Injection",
            framework_ids={"owasp_mcp_top10": "MCP05"},
            created_at=now,
        )
        d = f.to_dict()
        assert d["severity"] == 4
        assert d["framework_ids"] == '{"owasp_mcp_top10": "MCP05"}'
        f2 = Finding.from_row(d)
        assert f2.severity == Severity.CRITICAL
        assert f2.framework_ids == {"owasp_mcp_top10": "MCP05"}


class TestEvidence:
    def test_construction_inline(self) -> None:
        e = Evidence(id="e1", type="request", content='{"tool": "ls"}')
        assert e.storage == "inline"

    def test_to_dict(self) -> None:
        e = Evidence(id="e1", type="file", storage="file", path="/tmp/test.log")
        d = e.to_dict()
        assert d["storage"] == "file"
        assert d["path"] == "/tmp/test.log"

    def test_from_row(self) -> None:
        row = {
            "id": "e1",
            "finding_id": "f1",
            "run_id": "r1",
            "type": "request",
            "mime_type": "application/json",
            "hash": None,
            "storage": "inline",
            "content": "test content",
            "path": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        e = Evidence.from_row(row)
        assert e.finding_id == "f1"
        assert e.content == "test content"
