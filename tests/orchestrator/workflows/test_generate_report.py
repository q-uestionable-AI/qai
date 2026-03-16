"""Tests for the generate_report workflow."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.core.models import Finding, RunStatus, Severity
from q_ai.orchestrator.workflows.generate_report import generate_report

_GET_CONN_PATCH = "q_ai.orchestrator.workflows.generate_report.get_connection"
_GET_TARGET_PATCH = "q_ai.orchestrator.workflows.generate_report.get_target"
_LIST_FINDINGS_PATCH = "q_ai.orchestrator.workflows.generate_report.list_findings"


def _make_runner(run_id: str = "run-1") -> MagicMock:
    """Create a mock WorkflowRunner."""
    runner = MagicMock()
    runner.run_id = run_id
    runner._db_path = None
    runner.emit_progress = AsyncMock()
    runner.emit_finding = AsyncMock()
    runner.complete = AsyncMock()
    runner.fail = AsyncMock()
    return runner


def _make_target(
    target_id: str = "target-1",
    name: str = "test-target",
    target_type: str = "server",
    uri: str | None = None,
) -> MagicMock:
    """Create a mock Target."""
    target = MagicMock()
    target.id = target_id
    target.name = name
    target.type = target_type
    target.uri = uri
    return target


def _base_config(tmp_path: Path) -> dict:
    """Create a minimal valid config."""
    return {
        "target_id": "target-1",
        "from_date": None,
        "to_date": None,
        "include_evidence_pack": False,
        "output_dir": str(tmp_path / "export"),
    }


def _make_conn_with_data(
    parent_rows: list[dict] | None = None,
    child_rows: list[dict] | None = None,
    ipi_payloads: int = 0,
    ipi_hits: int = 0,
    ipi_high_hits: int = 0,
    cxp_rows: list[dict] | None = None,
    rxp_rows: list[dict] | None = None,
    chain_total: int = 0,
    chain_successful: int = 0,
    chain_boundaries: list[str] | None = None,
    evidence_rows: list[dict] | None = None,
) -> MagicMock:
    """Build a mock connection that returns controlled data for all queries.

    Simulates SQLite Row objects with dict-like access via side_effect on
    execute(). The execute() mock is configured as a function that inspects
    the query string and returns appropriate cursors.
    """
    if parent_rows is None:
        parent_rows = []
    if child_rows is None:
        child_rows = []
    if cxp_rows is None:
        cxp_rows = []
    if rxp_rows is None:
        rxp_rows = []
    if chain_boundaries is None:
        chain_boundaries = []
    if evidence_rows is None:
        evidence_rows = []

    def _row(d: dict) -> dict:
        """Return a plain dict to represent a database row."""
        return d

    # NOTE: execute_side_effect should use `_row` to wrap any raw row
    # dictionaries it returns (e.g., via fetchall()/fetchone()). Since `_row`
    # now returns a plain dict, code under test can safely use subscripting
    # (row["col"]) and dict(row) without relying on MagicMock magic methods.

    def execute_side_effect(query: str, params: tuple | list = ()) -> MagicMock:
        cursor = MagicMock()
        q = query.strip().upper()

        if "JSON_EXTRACT" in q and "MODULE = 'WORKFLOW'" in q:
            rows = [_row(r) for r in parent_rows]
            cursor.fetchall.return_value = rows
        elif "FROM RUNS WHERE PARENT_RUN_ID" in q:
            rows = [_row(r) for r in child_rows]
            cursor.fetchall.return_value = rows
        elif "FROM IPI_PAYLOADS" in q:
            cursor.fetchone.return_value = (ipi_payloads,)
        elif "CONFIDENCE = 'HIGH'" in q:
            cursor.fetchone.return_value = (ipi_high_hits,)
        elif "FROM IPI_HITS" in q:
            cursor.fetchone.return_value = (ipi_hits,)
        elif "FROM CXP_TEST_RESULTS" in q:
            cursor.fetchall.return_value = [_row(r) for r in cxp_rows]
        elif "FROM RXP_VALIDATIONS" in q:
            cursor.fetchall.return_value = [_row(r) for r in rxp_rows]
        elif "SUCCESS = 1" in q and "CHAIN_EXECUTIONS" in q:
            cursor.fetchone.return_value = (chain_successful,)
        elif "TRUST_BOUNDARIES" in q and "CHAIN_EXECUTIONS" in q:
            boundary_rows = [_row({"trust_boundaries": json.dumps(chain_boundaries)})]
            cursor.fetchall.return_value = boundary_rows
        elif "FROM CHAIN_EXECUTIONS" in q and "COUNT" in q:
            cursor.fetchone.return_value = (chain_total,)
        elif "FROM EVIDENCE" in q:
            cursor.fetchall.return_value = [_row(r) for r in evidence_rows]
        else:
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = None

        return cursor

    conn = MagicMock()
    conn.execute = MagicMock(side_effect=execute_side_effect)

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestGenerateReport:
    """Unit tests for generate_report workflow executor."""

    async def test_target_not_found_fails(self, tmp_path: Path) -> None:
        """Target not found -> FAILED."""
        runner = _make_runner()
        config = _base_config(tmp_path)

        mock_ctx = _make_conn_with_data()
        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=None),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.FAILED)

    async def test_no_runs_produces_report_with_empty_sections(self, tmp_path: Path) -> None:
        """No runs -> report with all sections showing 'No data in scope.'"""
        runner = _make_runner()
        config = _base_config(tmp_path)
        target = _make_target()

        mock_ctx = _make_conn_with_data(parent_rows=[])
        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        report_path = Path(config["output_dir"]) / "report.md"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")

        # All sections must be present
        assert "## Runs Overview" in content
        assert "## Findings" in content
        assert "## IPI Campaign Summary" in content
        assert "## CXP Test Summary" in content
        assert "## RXP Validation Summary" in content
        assert "## Chain Execution Summary" in content
        assert "## Analyst Notes" in content

        # Empty sections show placeholder
        assert "_No data in scope._" in content

    async def test_runs_and_findings_in_report(self, tmp_path: Path) -> None:
        """Runs and findings appear in the report with workflow name."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        target = _make_target()

        parent_rows = [
            {
                "id": "parent-1",
                "name": "assess",
                "status": "COMPLETED",
                "started_at": "2026-03-15T10:00:00.000000+00:00",
            }
        ]
        child_rows = [{"id": "child-1"}]

        findings = [
            Finding(
                id="f-1",
                run_id="child-1",
                module="audit",
                category="test",
                severity=Severity.HIGH,
                title="Test finding",
                description="A test finding",
            ),
            Finding(
                id="f-2",
                run_id="child-1",
                module="inject",
                category="test",
                severity=Severity.LOW,
                title="Low finding",
            ),
        ]

        mock_ctx = _make_conn_with_data(
            parent_rows=parent_rows,
            child_rows=child_rows,
            ipi_payloads=5,
            ipi_hits=3,
            ipi_high_hits=1,
            chain_total=2,
            chain_successful=1,
            chain_boundaries=["net-to-internal"],
        )

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
            patch(_LIST_FINDINGS_PATCH, return_value=findings),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        report_path = Path(config["output_dir"]) / "report.md"
        content = report_path.read_text(encoding="utf-8")

        assert "Test finding" in content
        assert "Low finding" in content
        # Runs Overview shows workflow name, not "workflow" module
        assert "| assess |" in content
        assert "| Workflow |" in content
        assert "Payloads generated: 5" in content
        assert "Callback hits: 3" in content
        assert "High-confidence hits: 1" in content
        assert "Executions: 2 | Successful: 1" in content
        assert "net-to-internal" in content
        assert "HIGH: 1" in content
        assert "LOW: 1" in content

    async def test_run_query_uses_json_extract_for_target_id(self, tmp_path: Path) -> None:
        """Run query uses json_extract on config, not target_id column."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        target = _make_target()

        mock_ctx = _make_conn_with_data(parent_rows=[])

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
        ):
            await generate_report(runner, config)

        # Verify the SQL query uses json_extract
        conn = mock_ctx.__enter__.return_value
        calls = [str(c) for c in conn.execute.call_args_list]
        json_calls = [c for c in calls if "json_extract" in c.lower()]
        assert len(json_calls) > 0, "Should use json_extract to find target_id in config"

    async def test_date_filter_uses_exclusive_upper_bound(self, tmp_path: Path) -> None:
        """Date filter uses < next_day to include fractional seconds."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["from_date"] = "2026-03-01"
        config["to_date"] = "2026-03-15"
        target = _make_target()

        mock_ctx = _make_conn_with_data(parent_rows=[])

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        # Verify the SQL uses next-day exclusive bound (2026-03-16)
        conn = mock_ctx.__enter__.return_value
        calls = [str(c) for c in conn.execute.call_args_list]
        # Upper bound should be 2026-03-16 (next day), not 2026-03-15T23:59:59
        upper_calls = [c for c in calls if "2026-03-16" in c]
        assert len(upper_calls) > 0, "Should use next day as exclusive upper bound"
        # Lower bound should include timezone suffix
        lower_calls = [c for c in calls if "2026-03-01T00:00:00+00:00" in c]
        assert len(lower_calls) > 0, "Lower bound should include +00:00 suffix"

        # Check report header shows date scope
        report_path = Path(config["output_dir"]) / "report.md"
        content = report_path.read_text(encoding="utf-8")
        assert "2026-03-01" in content
        assert "2026-03-15" in content

    async def test_evidence_pack_zip_produced(self, tmp_path: Path) -> None:
        """Evidence pack produces ZIP with report and evidence."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True
        target = _make_target()

        # Create a fake evidence file inside .qai
        qai_dir = tmp_path / "fakehome" / ".qai" / "evidence"
        qai_dir.mkdir(parents=True)
        evidence_file = qai_dir / "screenshot.png"
        evidence_file.write_bytes(b"\x89PNG fake data")

        parent_rows = [
            {
                "id": "parent-1",
                "name": "assess",
                "status": "COMPLETED",
                "started_at": "2026-03-15T10:00:00.000000+00:00",
            }
        ]
        child_rows = [{"id": "child-1"}]

        evidence_rows = [
            {
                "id": "ev-1",
                "run_id": "child-1",
                "type": "screenshot",
                "storage": "file",
                "path": str(evidence_file),
                "mime_type": "image/png",
                "hash": None,
                "content": None,
                "finding_id": None,
                "created_at": "2026-03-15T10:00:00",
            }
        ]

        mock_ctx = _make_conn_with_data(
            parent_rows=parent_rows,
            child_rows=child_rows,
            evidence_rows=evidence_rows,
        )

        # Patch Path.home() to use our fake home
        fake_home = tmp_path / "fakehome"

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
            patch(_LIST_FINDINGS_PATCH, return_value=[]),
            patch("q_ai.orchestrator.workflows.generate_report.Path.home", return_value=fake_home),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        output = Path(config["output_dir"])
        assert (output / "report.md").exists()
        assert (output / "manifest.md").exists()
        assert (output / "report.zip").exists()

        with zipfile.ZipFile(output / "report.zip") as zf:
            names = zf.namelist()
            assert "report.md" in names
            assert "manifest.md" in names
            assert any("evidence/" in n for n in names)

    async def test_missing_evidence_files_skipped(self, tmp_path: Path) -> None:
        """Missing evidence files are skipped and noted in manifest."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True
        target = _make_target()

        fake_home = tmp_path / "fakehome"
        qai_dir = fake_home / ".qai"
        qai_dir.mkdir(parents=True)

        parent_rows = [
            {
                "id": "parent-1",
                "name": "assess",
                "status": "COMPLETED",
                "started_at": "2026-03-15T10:00:00.000000+00:00",
            }
        ]
        child_rows = [{"id": "child-1"}]

        evidence_rows = [
            {
                "id": "ev-1",
                "run_id": "child-1",
                "type": "screenshot",
                "storage": "file",
                "path": str(qai_dir / "nonexistent.png"),
                "mime_type": "image/png",
                "hash": None,
                "content": None,
                "finding_id": None,
                "created_at": "2026-03-15T10:00:00",
            }
        ]

        mock_ctx = _make_conn_with_data(
            parent_rows=parent_rows,
            child_rows=child_rows,
            evidence_rows=evidence_rows,
        )

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
            patch(_LIST_FINDINGS_PATCH, return_value=[]),
            patch("q_ai.orchestrator.workflows.generate_report.Path.home", return_value=fake_home),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        manifest = (Path(config["output_dir"]) / "manifest.md").read_text(encoding="utf-8")
        assert "skipped: file not found" in manifest

    async def test_evidence_path_outside_qai_skipped(self, tmp_path: Path) -> None:
        """Evidence files outside ~/.qai/ are skipped with reason in manifest."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True
        target = _make_target()

        fake_home = tmp_path / "fakehome"
        qai_dir = fake_home / ".qai"
        qai_dir.mkdir(parents=True)

        # Create a file outside .qai
        outside_file = tmp_path / "outside" / "secret.txt"
        outside_file.parent.mkdir(parents=True)
        outside_file.write_text("secret data")

        parent_rows = [
            {
                "id": "parent-1",
                "name": "assess",
                "status": "COMPLETED",
                "started_at": "2026-03-15T10:00:00.000000+00:00",
            }
        ]
        child_rows = [{"id": "child-1"}]

        evidence_rows = [
            {
                "id": "ev-1",
                "run_id": "child-1",
                "type": "screenshot",
                "storage": "file",
                "path": str(outside_file),
                "mime_type": "text/plain",
                "hash": None,
                "content": None,
                "finding_id": None,
                "created_at": "2026-03-15T10:00:00",
            }
        ]

        mock_ctx = _make_conn_with_data(
            parent_rows=parent_rows,
            child_rows=child_rows,
            evidence_rows=evidence_rows,
        )

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
            patch(_LIST_FINDINGS_PATCH, return_value=[]),
            patch("q_ai.orchestrator.workflows.generate_report.Path.home", return_value=fake_home),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        manifest = (Path(config["output_dir"]) / "manifest.md").read_text(encoding="utf-8")
        assert "skipped: path outside ~/.qai/" in manifest

    async def test_evidence_path_prefix_bypass_blocked(self, tmp_path: Path) -> None:
        """Paths like ~/.qai_backup/ must not pass the boundary check."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True
        target = _make_target()

        fake_home = tmp_path / "fakehome"
        qai_dir = fake_home / ".qai"
        qai_dir.mkdir(parents=True)

        # Create a file in a prefix-similar directory (.qai_backup)
        bypass_dir = fake_home / ".qai_backup"
        bypass_dir.mkdir(parents=True)
        bypass_file = bypass_dir / "stolen.txt"
        bypass_file.write_text("should not be copied")

        parent_rows = [
            {
                "id": "parent-1",
                "name": "assess",
                "status": "COMPLETED",
                "started_at": "2026-03-15T10:00:00.000000+00:00",
            }
        ]
        child_rows = [{"id": "child-1"}]

        evidence_rows = [
            {
                "id": "ev-1",
                "run_id": "child-1",
                "type": "file",
                "storage": "file",
                "path": str(bypass_file),
                "mime_type": "text/plain",
                "hash": None,
                "content": None,
                "finding_id": None,
                "created_at": "2026-03-15T10:00:00",
            }
        ]

        mock_ctx = _make_conn_with_data(
            parent_rows=parent_rows,
            child_rows=child_rows,
            evidence_rows=evidence_rows,
        )

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
            patch(_LIST_FINDINGS_PATCH, return_value=[]),
            patch("q_ai.orchestrator.workflows.generate_report.Path.home", return_value=fake_home),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        manifest = (Path(config["output_dir"]) / "manifest.md").read_text(encoding="utf-8")
        assert "skipped: path outside ~/.qai/" in manifest
        # File must NOT have been copied
        evidence_dir = Path(config["output_dir"]) / "evidence"
        if evidence_dir.exists():
            copied = list(evidence_dir.rglob("*"))
            assert not any(f.is_file() for f in copied), "No files should be copied"

    async def test_filename_collision_uses_evidence_id_prefix(self, tmp_path: Path) -> None:
        """Multiple evidence files with same name use evidence_id prefix to avoid collision."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["include_evidence_pack"] = True
        target = _make_target()

        fake_home = tmp_path / "fakehome"
        qai_dir = fake_home / ".qai" / "evidence"
        qai_dir.mkdir(parents=True)

        # Two files with the same name in different subdirs
        file1 = qai_dir / "dir1" / "screenshot.png"
        file1.parent.mkdir(parents=True)
        file1.write_bytes(b"file1-data")

        file2 = qai_dir / "dir2" / "screenshot.png"
        file2.parent.mkdir(parents=True)
        file2.write_bytes(b"file2-data")

        parent_rows = [
            {
                "id": "parent-1",
                "name": "assess",
                "status": "COMPLETED",
                "started_at": "2026-03-15T10:00:00.000000+00:00",
            }
        ]
        child_rows = [{"id": "child-1"}]

        evidence_rows = [
            {
                "id": "ev-aaa",
                "run_id": "child-1",
                "type": "screenshot",
                "storage": "file",
                "path": str(file1),
                "mime_type": "image/png",
                "hash": None,
                "content": None,
                "finding_id": None,
                "created_at": "2026-03-15T10:00:00",
            },
            {
                "id": "ev-bbb",
                "run_id": "child-1",
                "type": "screenshot",
                "storage": "file",
                "path": str(file2),
                "mime_type": "image/png",
                "hash": None,
                "content": None,
                "finding_id": None,
                "created_at": "2026-03-15T10:00:00",
            },
        ]

        mock_ctx = _make_conn_with_data(
            parent_rows=parent_rows,
            child_rows=child_rows,
            evidence_rows=evidence_rows,
        )

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
            patch(_LIST_FINDINGS_PATCH, return_value=[]),
            patch("q_ai.orchestrator.workflows.generate_report.Path.home", return_value=fake_home),
        ):
            await generate_report(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

        evidence_dir = Path(config["output_dir"]) / "evidence" / "child-1"
        files = list(evidence_dir.iterdir())
        names = [f.name for f in files]
        assert "ev-aaa-screenshot.png" in names
        assert "ev-bbb-screenshot.png" in names
        assert len(files) == 2

    async def test_output_dir_creation_failure_fails_runner(self, tmp_path: Path) -> None:
        """OSError on output_dir.mkdir -> runner.fail()."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        # Point output_dir to an impossible path
        config["output_dir"] = str(tmp_path / "export")
        target = _make_target()

        mock_ctx = _make_conn_with_data()

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_GET_TARGET_PATCH, return_value=target),
            patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")),
        ):
            await generate_report(runner, config)

        runner.fail.assert_awaited_once()
