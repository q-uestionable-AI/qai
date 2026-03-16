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
from q_ai.orchestrator.workflows.generate_report import generate_report

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
) -> str:
    """Insert a child run."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO runs (id, module, parent_run_id, status, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (child_id, module, parent_id, int(RunStatus.COMPLETED), "2026-03-10T10:01:00"),
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
) -> str:
    """Insert a finding."""
    finding_id = f"finding-{run_id}"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO findings (id, run_id, module, category, severity, title, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (finding_id, run_id, "audit", "test_cat", severity, title, "2026-03-10"),
        )
        conn.commit()
    finally:
        conn.close()
    return finding_id


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
        assert "No data in scope." not in content


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
