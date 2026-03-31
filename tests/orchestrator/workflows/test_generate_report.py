"""Tests for the generate_report workflow."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.core.models import RunStatus, Severity

if TYPE_CHECKING:
    import pytest
from q_ai.core.schema import migrate
from q_ai.orchestrator.workflows.generate_report import (
    _EMPTY_MSG,
    _config_to_cli,
    _parse_framework_ids,
    _render_audit_section,
    _render_inject_section,
    _render_negative_results_section,
    _render_proxy_section,
    generate_report,
)

_GET_CONN_PATCH = "q_ai.orchestrator.workflows.generate_report.get_connection"
_GET_TARGET_PATCH = "q_ai.orchestrator.workflows.generate_report.get_target"


def _make_runner(run_id: str = "run-export-1", db_path: Path | None = None) -> MagicMock:
    """Create a mock WorkflowRunner."""
    runner = MagicMock()
    runner.run_id = run_id
    runner._db_path = db_path
    runner.emit_progress = AsyncMock()
    runner.emit_finding = AsyncMock()
    runner.complete = AsyncMock()
    runner.fail = AsyncMock()
    return runner


def _make_target(
    target_id: str = "target-1",
    name: str = "test-target",
    target_type: str = "server",
    uri: str | None = "http://example.com",
) -> MagicMock:
    """Create a mock Target."""
    target = MagicMock()
    target.id = target_id
    target.name = name
    target.type = target_type
    target.uri = uri
    return target


def _base_config(tmp_path: Path) -> dict:
    """Create a minimal valid config with output_dir."""
    output_dir = tmp_path / "exports" / "generate_report" / "run-export-1"
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "target_id": "target-1",
        "from_date": None,
        "to_date": None,
        "include_evidence_pack": False,
        "output_dir": str(output_dir),
    }


def _setup_db(db_path: Path) -> None:
    """Create and migrate a test database."""
    conn = sqlite3.connect(str(db_path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_target(db_path: Path, target_id: str = "target-1") -> None:
    """Insert a target row."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO targets (id, type, name, uri, created_at) VALUES (?, ?, ?, ?, ?)",
            (target_id, "server", "test-target", "http://example.com", "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_workflow_run(
    db_path: Path,
    run_id: str = "parent-run-1",
    target_id: str = "target-1",
    started_at: str = "2026-03-10T10:00:00.123456+00:00",
) -> str:
    """Insert a workflow run whose config contains target_id."""
    config = json.dumps({"target_id": target_id})
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO runs (id, module, name, config, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, "workflow", "assess", config, int(RunStatus.COMPLETED), started_at),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _seed_child_run(
    db_path: Path,
    child_id: str = "child-run-1",
    parent_id: str = "parent-run-1",
    module: str = "audit",
    config: dict | None = None,
) -> str:
    """Insert a child run."""
    config_json = json.dumps(config) if config else None
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO runs (id, module, parent_run_id, config, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                child_id,
                module,
                parent_id,
                config_json,
                int(RunStatus.COMPLETED),
                "2026-03-10T10:01:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return child_id


def _seed_finding(
    db_path: Path,
    run_id: str = "child-run-1",
    severity: int = int(Severity.HIGH),
    title: str = "Test finding",
    finding_id: str | None = None,
    module: str = "audit",
    description: str | None = None,
    framework_ids: str | None = None,
    source_ref: str | None = None,
) -> str:
    """Insert a finding."""
    fid = finding_id or f"finding-{run_id}"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO findings "
            "(id, run_id, module, category, severity, title, description, "
            "framework_ids, source_ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fid,
                run_id,
                module,
                "test_cat",
                severity,
                title,
                description,
                framework_ids,
                source_ref,
                "2026-03-10",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return fid


def _seed_evidence_for_finding(
    db_path: Path,
    finding_id: str,
    ev_id: str = "ev-f-1",
    ev_type: str = "file",
    mime_type: str = "text/plain",
) -> str:
    """Insert an evidence record linked to a finding."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO evidence "
            "(id, finding_id, run_id, type, mime_type, storage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ev_id, finding_id, "child-run-1", ev_type, mime_type, "file", "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()
    return ev_id


def _seed_ipi_payload(db_path: Path, run_id: str = "child-run-1") -> None:
    """Insert an IPI payload."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO ipi_payloads "
            "(id, run_id, uuid, token, format, technique, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ipi-1", run_id, "uuid-1", "tok-1", "pdf", "injection", "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_ipi_hit(db_path: Path, uuid: str = "uuid-1", confidence: str = "high") -> None:
    """Insert an IPI hit."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO ipi_hits (id, uuid, confidence, timestamp) VALUES (?, ?, ?, ?)",
            ("hit-1", uuid, confidence, "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_cxp_result(db_path: Path, run_id: str = "child-run-1") -> None:
    """Insert a CXP test result."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO cxp_test_results "
            "(id, run_id, campaign_id, technique_id, assistant, trigger_prompt, "
            "capture_mode, raw_output, validation_result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cxp-1",
                run_id,
                "camp-1",
                "technique-a",
                "cursor",
                "prompt",
                "auto",
                "output",
                "hit",
                "2026-03-10",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_rxp_validation(db_path: Path, run_id: str = "child-run-1") -> None:
    """Insert an RXP validation."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO rxp_validations "
            "(id, run_id, model_id, total_queries, poison_retrievals, "
            "retrieval_rate, mean_poison_rank, top_k, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("rxp-1", run_id, "all-MiniLM-L6-v2", 10, 8, 0.8, 2.5, 5, "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_chain_execution(db_path: Path, run_id: str = "child-run-1") -> str:
    """Insert a chain execution."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO chain_executions "
            "(id, run_id, chain_id, chain_name, dry_run, success, trust_boundaries, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ce-1", run_id, "chain-1", "Test Chain", 0, 1, '["net-to-internal"]', "2026-03-10"),
        )
        conn.execute(
            "INSERT INTO chain_step_outputs "
            "(id, execution_id, step_id, module, technique, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("step-1", "ce-1", "s1", "inject", "rce", 1, "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()
    return "ce-1"


def _seed_evidence(
    db_path: Path,
    run_id: str = "child-run-1",
    ev_id: str = "ev-1",
    path: str | None = None,
) -> str:
    """Insert an evidence record."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO evidence "
            "(id, run_id, type, storage, path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ev_id, run_id, "file", "file", path, "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()
    return ev_id


def _seed_audit_scan(
    db_path: Path,
    run_id: str = "child-run-1",
    transport: str = "stdio",
    scanners_run: str | None = None,
    scan_duration: float = 5.0,
) -> None:
    """Insert an audit scan record."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO audit_scans "
            "(run_id, transport, scanners_run, scan_duration_seconds) "
            "VALUES (?, ?, ?, ?)",
            (run_id, transport, scanners_run, scan_duration),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_inject_result(
    db_path: Path,
    run_id: str = "child-run-1",
    result_id: str = "inj-1",
    technique: str = "tool_poisoning",
    outcome: str = "success",
    target_agent: str = "test-agent",
) -> None:
    """Insert an inject result record."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO inject_results "
            "(id, run_id, payload_name, technique, outcome, target_agent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (result_id, run_id, "payload-1", technique, outcome, target_agent, "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_proxy_session(
    db_path: Path,
    run_id: str = "child-run-1",
    transport: str = "stdio",
    message_count: int = 10,
    duration_seconds: float = 3.5,
) -> None:
    """Insert a proxy session record."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO proxy_sessions "
            "(run_id, transport, message_count, duration_seconds) "
            "VALUES (?, ?, ?, ?)",
            (run_id, transport, message_count, duration_seconds),
        )
        conn.commit()
    finally:
        conn.close()


# =====================================================================
# Tests
# =====================================================================


class TestTargetNotFound:
    """Target not found at executor time -> FAILED."""

    async def test_target_not_found_fails(self, tmp_path: Path) -> None:
        """Missing target -> FAILED status."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.FAILED)
        runner.emit_progress.assert_awaited()


class TestNoRuns:
    """No runs for target -> all sections render with empty message, COMPLETED."""

    async def test_empty_report_completed(self, tmp_path: Path) -> None:
        """No matching runs -> COMPLETED, report has all empty sections."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        report_path = Path(config["output_dir"]) / "report.md"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")

        # All sections present
        assert "## Runs Overview" in content
        assert "## Findings" in content
        assert "## IPI Campaign Summary" in content
        assert "## CXP Test Summary" in content
        assert "## RXP Validation Summary" in content
        assert "## Chain Execution Summary" in content
        assert "## Analyst Notes" in content
        # New sections
        assert "## Audit Summary" in content
        assert "## Inject Summary" in content
        assert "## Proxy Summary" in content
        assert "## What Was Tested" in content
        assert "## Reproduction" in content

        # Empty sections show the empty message
        assert "No data in scope." in content

        # Header info
        assert "test-target" in content
        assert "Export run ID" in content


class TestRunsAndFindings:
    """Runs and findings present -> all sections populated."""

    async def test_full_report(self, tmp_path: Path) -> None:
        """Full data -> all sections populated correctly."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path)
        _seed_finding(db_path)
        _seed_ipi_payload(db_path)
        _seed_ipi_hit(db_path)
        _seed_cxp_result(db_path)
        _seed_rxp_validation(db_path)
        _seed_chain_execution(db_path)

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        report_path = Path(config["output_dir"]) / "report.md"
        content = report_path.read_text(encoding="utf-8")

        # All sections present
        assert "## Runs Overview" in content
        assert "## Findings" in content
        assert "## IPI Campaign Summary" in content
        assert "## CXP Test Summary" in content
        assert "## RXP Validation Summary" in content
        assert "## Chain Execution Summary" in content
        assert "## Analyst Notes" in content

        # Data populated
        assert "Test finding" in content
        assert "HIGH" in content
        assert "Payloads generated:" in content
        assert "Hits recorded:" in content
        assert "technique-a" in content
        assert "all-MiniLM-L6-v2" in content
        assert "Trust boundaries crossed:" in content


class TestDateFilter:
    """Date filter -> out-of-range runs excluded."""

    async def test_date_filter_excludes_old_runs(self, tmp_path: Path) -> None:
        """Runs outside the date window are excluded."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)

        # In-range run
        _seed_workflow_run(
            db_path,
            run_id="in-range",
            started_at="2026-03-10T10:00:00.500000+00:00",
        )
        _seed_child_run(db_path, child_id="child-in", parent_id="in-range")
        _seed_finding(db_path, run_id="child-in", title="In-range finding")

        # Out-of-range run (before window)
        _seed_workflow_run(
            db_path,
            run_id="out-range",
            started_at="2026-01-01T10:00:00.500000+00:00",
        )
        _seed_child_run(db_path, child_id="child-out", parent_id="out-range")
        _seed_finding(db_path, run_id="child-out", title="Out-of-range finding")

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        config["from_date"] = "2026-03-01"
        config["to_date"] = "2026-03-31"

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        report_path = Path(config["output_dir"]) / "report.md"
        content = report_path.read_text(encoding="utf-8")

        assert "In-range finding" in content
        assert "Out-of-range finding" not in content


class TestEvidencePack:
    """Evidence pack -> ZIP produced with correct structure."""

    async def test_evidence_zip_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Evidence pack produces ZIP with fixed layout."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path)

        # Create evidence file inside the monkeypatched ~/.qai/
        qai_dir = tmp_path / ".qai"
        qai_dir.mkdir(parents=True, exist_ok=True)
        evidence_file = qai_dir / "test_evidence_file.txt"
        evidence_file.write_text("evidence content", encoding="utf-8")

        _seed_evidence(db_path, path=str(evidence_file))

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        zip_path = Path(config["output_dir"]) / "report.zip"
        assert zip_path.exists()

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "report.md" in names
            assert "manifest.md" in names
            # Evidence file should be in evidence/<run_id>/<ev_id>-<filename>
            ev_entries = [n for n in names if n.startswith("evidence/")]
            assert len(ev_entries) == 1
            assert "ev-1-" in ev_entries[0]


class TestMissingEvidenceFiles:
    """Missing evidence files -> skipped, recorded in manifest."""

    async def test_missing_files_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-existent evidence file skipped and recorded in manifest."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path)

        fake_path = str(tmp_path / ".qai" / "nonexistent_evidence.bin")
        _seed_evidence(db_path, path=fake_path)

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        manifest_path = Path(config["output_dir"]) / "manifest.md"
        assert manifest_path.exists()
        manifest = manifest_path.read_text(encoding="utf-8")
        assert "file not found" in manifest.lower()
        assert "ev-1" in manifest


class TestEvidencePathOutsideQai:
    """Evidence path outside ~/.qai/ -> skipped with reason."""

    async def test_outside_qai_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """File outside ~/.qai/ is skipped with reason in manifest."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path)

        # Create a file outside the monkeypatched ~/.qai/
        outside_dir = tmp_path / "elsewhere"
        outside_dir.mkdir()
        outside_file = outside_dir / "outside.txt"
        outside_file.write_text("outside content", encoding="utf-8")

        _seed_evidence(db_path, path=str(outside_file))

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        manifest_path = Path(config["output_dir"]) / "manifest.md"
        manifest = manifest_path.read_text(encoding="utf-8")
        assert "outside ~/.qai/" in manifest


class TestFilenameCollision:
    """Filename collision -> evidence_id prefix prevents overwrite."""

    async def test_evidence_id_prefix_prevents_collision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two evidence files with same name get different prefixed names."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path)

        qai_dir = tmp_path / ".qai"
        qai_dir.mkdir(parents=True, exist_ok=True)
        file1 = qai_dir / "collision_test_1.txt"
        file2 = qai_dir / "collision_test_2.txt"
        file1.write_text("content 1", encoding="utf-8")
        file2.write_text("content 2", encoding="utf-8")

        _seed_evidence(db_path, ev_id="ev-a", path=str(file1))
        _seed_evidence(db_path, ev_id="ev-b", path=str(file2))

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True

        await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        zip_path = Path(config["output_dir"]) / "report.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            ev_entries = [n for n in zf.namelist() if n.startswith("evidence/")]
            assert len(ev_entries) == 2
            # Both prefixed with different IDs
            assert any("ev-a-" in n for n in ev_entries)
            assert any("ev-b-" in n for n in ev_entries)


class TestOutputDirCreationFailure:
    """Output dir creation failure at route level -> 500 response, no orphaned run."""

    def test_output_dir_failure_returns_500(self, tmp_path: Path) -> None:
        """OSError on mkdir -> 500, no run created."""
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from q_ai.core.schema import migrate as _migrate
        from q_ai.server.app import create_app

        # Set up test DB
        tmp_db = tmp_path / "route_test.db"
        conn = sqlite3.connect(str(tmp_db))
        try:
            _migrate(conn)
            conn.execute(
                "INSERT INTO targets (id, type, name, created_at) VALUES (?, ?, ?, ?)",
                ("tgt-1", "server", "test-target", "2026-01-01"),
            )
            conn.commit()
        finally:
            conn.close()

        app = create_app(db_path=tmp_db)

        body = {
            "workflow_id": "generate_report",
            "target_id": "tgt-1",
        }

        mock_entry = MagicMock()
        mock_entry.id = "generate_report"
        mock_entry.executor = AsyncMock()
        mock_entry.requires_provider = False

        error_response = JSONResponse(
            status_code=500,
            content={"detail": "Failed to prepare artifact output directory"},
        )

        with (
            TestClient(app) as test_client,
            patch("q_ai.server.routes.get_workflow", return_value=mock_entry),
            patch("q_ai.server.routes._prepare_output_dir", return_value=error_response),
        ):
            resp = test_client.post("/api/workflows/launch", json=body)

        assert resp.status_code == 500
        assert "output directory" in resp.json()["detail"].lower()

        # Verify no run was created
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM runs WHERE module = 'workflow'").fetchall()
            assert len(rows) == 0
        finally:
            conn.close()


# =====================================================================
# New tests for Phase 4
# =====================================================================


class TestRicherFindings:
    """Findings section shows full detail: description, frameworks, evidence."""

    async def test_findings_grouped_by_module_then_severity(self, tmp_path: Path) -> None:
        """Findings are grouped by module first, then by severity."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path, child_id="child-audit", module="audit")
        _seed_child_run(db_path, child_id="child-inject", module="inject")
        _seed_finding(
            db_path,
            run_id="child-audit",
            finding_id="f-audit-high",
            module="audit",
            severity=int(Severity.HIGH),
            title="Audit High Finding",
        )
        _seed_finding(
            db_path,
            run_id="child-inject",
            finding_id="f-inject-crit",
            module="inject",
            severity=int(Severity.CRITICAL),
            title="Inject Critical Finding",
        )

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        # Both module headers present
        assert "### audit" in content
        assert "### inject" in content
        # Severity subheadings present
        assert "#### HIGH" in content
        assert "#### CRITICAL" in content

    async def test_finding_detail_includes_description_and_frameworks(self, tmp_path: Path) -> None:
        """Finding detail shows description, framework IDs, source_ref."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path)
        _seed_finding(
            db_path,
            finding_id="f-detail",
            description="A detailed description of the vulnerability.",
            framework_ids='["OWASP-LLM-01", "MITRE-T1234"]',
            source_ref="scanner:tool_poisoning",
        )

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "A detailed description" in content
        assert "OWASP-LLM-01" in content
        assert "MITRE-T1234" in content
        assert "scanner:tool_poisoning" in content

    async def test_finding_evidence_pointers(self, tmp_path: Path) -> None:
        """Findings with evidence show evidence IDs in the report."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path)
        fid = _seed_finding(db_path, finding_id="f-ev")
        _seed_evidence_for_finding(db_path, fid, ev_id="ev-for-finding", mime_type="text/plain")

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "ev-for-finding" in content
        assert "text/plain" in content


class TestParseFrameworkIds:
    """Unit tests for _parse_framework_ids."""

    def test_valid_json_array(self) -> None:
        assert _parse_framework_ids('["A", "B"]') == ["A", "B"]

    def test_none_returns_empty(self) -> None:
        assert _parse_framework_ids(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert _parse_framework_ids("") == []

    def test_invalid_json_returns_empty(self) -> None:
        assert _parse_framework_ids("not json") == []

    def test_json_object_returns_empty(self) -> None:
        assert _parse_framework_ids('{"a": 1}') == []


class TestAuditSection:
    """Audit summary section with scan stats."""

    async def test_audit_section_populated(self, tmp_path: Path) -> None:
        """Audit summary shows scan count, transports, scanner categories."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path, module="audit")
        _seed_audit_scan(
            db_path,
            transport="stdio",
            scanners_run='["tool_poisoning", "rug_pull"]',
            scan_duration=12.5,
        )

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "## Audit Summary" in content
        assert "Scans:" in content
        assert "stdio" in content
        assert "tool_poisoning" in content
        assert "rug_pull" in content
        assert "12.5s" in content

    def test_render_audit_section_empty(self) -> None:
        """Empty audit scans -> empty message."""
        result = _render_audit_section([])
        assert _EMPTY_MSG in result


class TestInjectSection:
    """Inject summary section with technique x outcome matrix."""

    async def test_inject_section_matrix(self, tmp_path: Path) -> None:
        """Inject summary shows technique x outcome table."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path, module="inject")
        _seed_inject_result(db_path, result_id="i1", technique="tp", outcome="success")
        _seed_inject_result(db_path, result_id="i2", technique="tp", outcome="failed")
        _seed_inject_result(db_path, result_id="i3", technique="rug_pull", outcome="success")

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "## Inject Summary" in content
        assert "Total results:" in content
        assert "tp" in content
        assert "rug_pull" in content
        # Table structure
        assert "| Technique |" in content

    def test_render_inject_section_empty(self) -> None:
        """Empty inject results -> empty message."""
        result = _render_inject_section([])
        assert _EMPTY_MSG in result


class TestProxySection:
    """Proxy summary section with session stats."""

    async def test_proxy_section_populated(self, tmp_path: Path) -> None:
        """Proxy summary shows session count, messages, duration."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(db_path, module="proxy")
        _seed_proxy_session(db_path, message_count=15, duration_seconds=8.3)

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "## Proxy Summary" in content
        assert "Sessions:" in content
        assert "15" in content
        assert "8.3s" in content

    def test_render_proxy_section_empty(self) -> None:
        """Empty proxy sessions -> empty message."""
        result = _render_proxy_section([])
        assert _EMPTY_MSG in result


class TestNegativeResults:
    """Negative results section: modules that ran with zero findings."""

    async def test_negative_results_lists_modules(self, tmp_path: Path) -> None:
        """Negative results section shows all modules that ran."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        # audit ran with a finding, proxy ran with no findings
        _seed_child_run(db_path, child_id="c-audit", module="audit")
        _seed_child_run(db_path, child_id="c-proxy", module="proxy")
        _seed_finding(db_path, run_id="c-audit", finding_id="f-a")

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "## What Was Tested" in content
        assert "**audit:** 1 findings" in content
        assert "**proxy:** 0 findings" in content

    def test_render_negative_results_empty(self) -> None:
        """No child runs -> empty message."""
        result = _render_negative_results_section([], [], [])
        assert _EMPTY_MSG in result


class TestReproductionSection:
    """Reproduction section: CLI commands per child run."""

    async def test_reproduction_cli_commands(self, tmp_path: Path) -> None:
        """Reproduction section generates CLI commands from child run configs."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(
            db_path,
            child_id="c-audit",
            module="audit",
            config={"transport": "stdio", "command": "npx @mcp/server"},
        )
        _seed_child_run(
            db_path,
            child_id="c-inject",
            module="inject",
            config={"model": "openai/gpt-4o", "rounds": 3},
        )

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "## Reproduction" in content
        assert "qai audit scan" in content
        assert "--transport stdio" in content
        assert "qai inject campaign" in content
        assert "--model openai/gpt-4o" in content
        assert "--rounds 3" in content
        # Raw config JSON backup
        assert "### Raw Config" in content

    async def test_reproduction_includes_raw_json(self, tmp_path: Path) -> None:
        """Reproduction section includes raw config JSON for machine parsing."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _seed_target(db_path)
        _seed_workflow_run(db_path)
        _seed_child_run(
            db_path,
            child_id="c-proxy",
            module="proxy",
            config={"transport": "sse", "url": "http://example.com/mcp"},
        )

        runner = _make_runner(db_path=db_path)
        config = _base_config(tmp_path)
        await generate_report(runner, config)

        content = (Path(config["output_dir"]) / "report.md").read_text(encoding="utf-8")
        assert "```json" in content
        assert '"transport": "sse"' in content


class TestConfigToCli:
    """Unit tests for _config_to_cli mapping."""

    def test_audit_cli(self) -> None:
        result = _config_to_cli("audit", {"transport": "stdio", "command": "npx mcp"})
        assert result is not None
        assert "--transport stdio" in result
        assert "--command" in result
        assert "npx mcp" in result

    def test_inject_cli(self) -> None:
        result = _config_to_cli("inject", {"model": "openai/gpt-4o", "rounds": 3})
        assert result is not None
        assert "--model openai/gpt-4o" in result
        assert "--rounds 3" in result

    def test_proxy_cli(self) -> None:
        result = _config_to_cli("proxy", {"transport": "stdio", "command": "npx mcp"})
        assert result is not None
        assert "--transport stdio" in result
        assert "--target-command" in result
        assert "npx mcp" in result

    def test_unknown_module(self) -> None:
        assert _config_to_cli("unknown_mod", {}) is None

    def test_empty_config(self) -> None:
        result = _config_to_cli("audit", {})
        assert result == "qai audit scan"


class TestVisibleInLauncher:
    """visible_in_launcher flag on WorkflowEntry."""

    def test_generate_report_hidden_from_launcher(self) -> None:
        """generate_report workflow has visible_in_launcher=False."""
        from q_ai.orchestrator.registry import get_workflow

        wf = get_workflow("generate_report")
        assert wf is not None
        assert wf.visible_in_launcher is False

    def test_other_workflows_visible(self) -> None:
        """Other workflows default to visible_in_launcher=True."""
        from q_ai.orchestrator.registry import get_workflow

        for wf_id in ("assess", "test_docs", "test_assistant", "trace_path", "blast_radius"):
            wf = get_workflow(wf_id)
            assert wf is not None
            assert wf.visible_in_launcher is True, f"{wf_id} should be visible"

    def test_launcher_excludes_generate_report(self, tmp_path: Path) -> None:
        """Launcher route does not include generate_report workflow."""
        from fastapi.testclient import TestClient

        from q_ai.core.schema import migrate as _migrate
        from q_ai.server.app import create_app

        tmp_db = tmp_path / "launcher_test.db"
        conn = sqlite3.connect(str(tmp_db))
        try:
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()

        app = create_app(db_path=tmp_db)
        with TestClient(app) as client:
            resp = client.get("/launcher")
        assert resp.status_code == 200
        html = resp.text
        # "Generate Report" display name should not appear as a workflow card
        assert "Generate Report" not in html


class TestReportHtmlRendering:
    """Report markdown rendered as sanitized HTML in run results view."""

    def test_load_report_html(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_load_report_html converts markdown to sanitized HTML."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        from q_ai.server.routes import _load_report_html

        # Create the report file structure
        report_dir = tmp_path / ".qai" / "exports" / "generate_report" / "test-run-1"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_md = report_dir / "report.md"
        report_md.write_text(
            "# Test Report\n\n**Bold text** and `code`\n\n<script>alert('xss')</script>\n",
            encoding="utf-8",
        )

        html, has_zip = _load_report_html("test-run-1")

        # HTML is rendered
        assert "<h1>" in html or "Test Report" in html
        assert "<strong>" in html
        assert "<code>" in html
        # Script tag is sanitized
        assert "<script>" not in html
        assert "alert" not in html
        # No ZIP
        assert has_zip is False

    def test_load_report_html_with_zip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_load_report_html detects evidence ZIP existence."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        from q_ai.server.routes import _load_report_html

        report_dir = tmp_path / ".qai" / "exports" / "generate_report" / "test-run-2"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "report.md").write_text("# Report", encoding="utf-8")
        (report_dir / "report.zip").write_bytes(b"PK\x03\x04")

        html, has_zip = _load_report_html("test-run-2")
        assert html  # non-empty
        assert has_zip is True


class TestEvidenceDownload:
    """Download Evidence Pack endpoint."""

    def test_evidence_download_serves_zip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /api/exports/{run_id}/evidence returns the ZIP file."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        from fastapi.testclient import TestClient

        from q_ai.core.schema import migrate as _migrate
        from q_ai.server.app import create_app

        tmp_db = tmp_path / "ev_test.db"
        conn = sqlite3.connect(str(tmp_db))
        try:
            _migrate(conn)
            conn.execute(
                "INSERT INTO runs (id, module, name, status, started_at) VALUES (?, ?, ?, ?, ?)",
                ("run-ev-1", "workflow", "generate_report", int(RunStatus.COMPLETED), "2026-03-10"),
            )
            conn.commit()
        finally:
            conn.close()

        # Create ZIP file in the monkeypatched home
        report_dir = tmp_path / ".qai" / "exports" / "generate_report" / "run-ev-1"
        report_dir.mkdir(parents=True, exist_ok=True)
        zip_path = report_dir / "report.zip"
        import zipfile

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("report.md", "# Test")

        app = create_app(db_path=tmp_db)
        with TestClient(app) as client:
            resp = client.get("/api/exports/run-ev-1/evidence")
        assert resp.status_code == 200
        assert "application/zip" in resp.headers.get("content-type", "")

    def test_evidence_download_404_no_zip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /api/exports/{run_id}/evidence returns 404 when no ZIP exists."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        from fastapi.testclient import TestClient

        from q_ai.core.schema import migrate as _migrate
        from q_ai.server.app import create_app

        tmp_db = tmp_path / "ev_test2.db"
        conn = sqlite3.connect(str(tmp_db))
        try:
            _migrate(conn)
            conn.execute(
                "INSERT INTO runs (id, module, name, status, started_at) VALUES (?, ?, ?, ?, ?)",
                ("run-ev-2", "workflow", "generate_report", int(RunStatus.COMPLETED), "2026-03-10"),
            )
            conn.commit()
        finally:
            conn.close()

        app = create_app(db_path=tmp_db)
        with TestClient(app) as client:
            resp = client.get("/api/exports/run-ev-2/evidence")
        assert resp.status_code == 404
