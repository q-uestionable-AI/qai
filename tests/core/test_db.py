"""Tests for q-ai core database service."""

from __future__ import annotations

from pathlib import Path

from q_ai.core.db import (
    create_evidence,
    create_finding,
    create_run,
    create_target,
    get_connection,
    get_run,
    get_setting,
    get_target,
    list_findings,
    list_runs,
    list_targets,
    set_setting,
    update_run_status,
)
from q_ai.core.models import RunStatus, Severity


class TestConnection:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path):
            assert db_path.exists()

    def test_wal_mode(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1

    def test_user_version_set(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            assert ver == 13
            # V9: verify mitigation column exists on findings table
            columns = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
            assert "mitigation" in columns
            # V10: verify guidance column exists on runs table
            run_columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            assert "guidance" in run_columns
            # V11: verify source column exists on runs table
            assert "source" in run_columns

    def test_schema_tables_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            expected = {
                "runs",
                "targets",
                "findings",
                "evidence",
                "settings",
            }
            assert expected <= tables

    def test_creates_parent_directory(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "subdir" / "qai.db"
        with get_connection(db_path):
            assert db_path.exists()


class TestRunCRUD:
    def test_create_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(
                conn,
                module="audit",
                name="scan1",
            )
            assert len(run_id) == 32

    def test_create_child_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="chain")
            child_id = create_run(
                conn,
                module="audit",
                parent_run_id=parent_id,
            )
            children = list_runs(
                conn,
                parent_run_id=parent_id,
            )
            assert len(children) == 1
            assert children[0].id == child_id

    def test_update_run_status(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            update_run_status(
                conn,
                run_id,
                RunStatus.COMPLETED,
            )
            runs = list_runs(conn, module="audit")
            assert runs[0].status == RunStatus.COMPLETED

    def test_update_run_status_sets_finished_at(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            update_run_status(
                conn,
                run_id,
                RunStatus.COMPLETED,
            )
            runs = list_runs(conn, module="audit")
            assert runs[0].finished_at is not None

    def test_list_runs_filter_module(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_run(conn, module="audit")
            create_run(conn, module="inject")
            runs = list_runs(conn, module="audit")
            assert len(runs) == 1
            assert runs[0].module == "audit"

    def test_list_runs_filter_status(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            r1 = create_run(conn, module="audit")
            create_run(conn, module="audit")
            update_run_status(
                conn,
                r1,
                RunStatus.COMPLETED,
            )
            runs = list_runs(
                conn,
                status=RunStatus.COMPLETED,
            )
            assert len(runs) == 1

    def test_list_runs_default_pending(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_run(conn, module="audit")
            runs = list_runs(conn)
            assert runs[0].status == RunStatus.PENDING

    def test_create_run_with_config(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            cfg = {"transport": "stdio", "timeout": 30}
            create_run(
                conn,
                module="audit",
                config=cfg,
            )
            runs = list_runs(conn, module="audit")
            assert runs[0].config == cfg

    def test_get_run_returns_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit", name="scan1")
            run = get_run(conn, run_id)
            assert run is not None
            assert run.id == run_id
            assert run.module == "audit"
            assert run.name == "scan1"

    def test_get_run_not_found(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run = get_run(conn, "nonexistent")
            assert run is None


class TestTargetCRUD:
    def test_create_and_get_target(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            tid = create_target(
                conn,
                type="server",
                name="test-mcp",
                uri="http://localhost",
            )
            target = get_target(conn, tid)
            assert target is not None
            assert target.name == "test-mcp"
            assert target.uri == "http://localhost"

    def test_get_target_not_found(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            target = get_target(conn, "nonexistent")
            assert target is None

    def test_list_targets(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_target(
                conn,
                type="server",
                name="s1",
            )
            create_target(
                conn,
                type="endpoint",
                name="e1",
            )
            targets = list_targets(conn)
            assert len(targets) == 2

    def test_create_target_with_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            meta = {"version": "1.0", "protocol": "mcp"}
            tid = create_target(
                conn,
                type="server",
                name="meta-srv",
                metadata=meta,
            )
            target = get_target(conn, tid)
            assert target is not None
            assert target.metadata == meta


class TestFindingCRUD:
    def test_create_finding(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            fid = create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="command_injection",
                severity=Severity.HIGH,
                title="Injection found",
            )
            assert len(fid) == 32

    def test_list_findings_min_severity(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="info_disclosure",
                severity=Severity.LOW,
                title="Low finding",
            )
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="command_injection",
                severity=Severity.HIGH,
                title="High finding",
            )
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="auth",
                severity=Severity.CRITICAL,
                title="Critical finding",
            )
            findings = list_findings(
                conn,
                min_severity=Severity.HIGH,
            )
            assert len(findings) == 2
            assert all(f.severity >= Severity.HIGH for f in findings)

    def test_list_findings_filter_module(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            r1 = create_run(conn, module="audit")
            r2 = create_run(conn, module="inject")
            create_finding(
                conn,
                run_id=r1,
                module="audit",
                category="a",
                severity=Severity.HIGH,
                title="f1",
            )
            create_finding(
                conn,
                run_id=r2,
                module="inject",
                category="b",
                severity=Severity.HIGH,
                title="f2",
            )
            findings = list_findings(conn, module="audit")
            assert len(findings) == 1

    def test_list_findings_filter_run_id(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            r1 = create_run(conn, module="audit")
            r2 = create_run(conn, module="audit")
            create_finding(
                conn,
                run_id=r1,
                module="audit",
                category="a",
                severity=Severity.MEDIUM,
                title="f1",
            )
            create_finding(
                conn,
                run_id=r2,
                module="audit",
                category="b",
                severity=Severity.LOW,
                title="f2",
            )
            findings = list_findings(conn, run_id=r1)
            assert len(findings) == 1
            assert findings[0].title == "f1"

    def test_list_findings_filter_target_id(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            tid = create_target(
                conn,
                type="server",
                name="srv1",
            )
            r1 = create_run(
                conn,
                module="audit",
                target_id=tid,
            )
            r2 = create_run(conn, module="audit")
            create_finding(
                conn,
                run_id=r1,
                module="audit",
                category="a",
                severity=Severity.HIGH,
                title="target-finding",
            )
            create_finding(
                conn,
                run_id=r2,
                module="audit",
                category="b",
                severity=Severity.HIGH,
                title="other-finding",
            )
            findings = list_findings(
                conn,
                target_id=tid,
            )
            assert len(findings) == 1
            assert findings[0].title == "target-finding"

    def test_list_findings_ordered_by_severity(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="a",
                severity=Severity.LOW,
                title="low",
            )
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="b",
                severity=Severity.CRITICAL,
                title="crit",
            )
            findings = list_findings(conn)
            assert findings[0].severity == Severity.CRITICAL
            assert findings[1].severity == Severity.LOW


class TestEvidenceCRUD:
    def test_create_evidence_inline(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            fid = create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="test",
                severity=Severity.INFO,
                title="test",
            )
            eid = create_evidence(
                conn,
                type="request",
                finding_id=fid,
                run_id=run_id,
                content='{"tool": "ls"}',
                mime_type="application/json",
            )
            assert len(eid) == 32

    def test_create_evidence_file(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            eid = create_evidence(
                conn,
                type="screenshot",
                run_id=run_id,
                storage="file",
                path="/tmp/screenshot.png",
            )
            assert len(eid) == 32

    def test_create_evidence_without_finding(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit")
            eid = create_evidence(
                conn,
                type="log",
                run_id=run_id,
                content="some log output",
            )
            assert len(eid) == 32


class TestSettings:
    def test_get_setting_default(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            val = get_setting(
                conn,
                "nonexistent",
                default="fallback",
            )
            assert val == "fallback"

    def test_get_setting_default_none(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            val = get_setting(conn, "nonexistent")
            assert val is None

    def test_set_and_get_setting(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            set_setting(
                conn,
                "audit.transport",
                "stdio",
            )
            val = get_setting(conn, "audit.transport")
            assert val == "stdio"

    def test_set_setting_updates_existing(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            set_setting(conn, "key", "v1")
            set_setting(conn, "key", "v2")
            assert get_setting(conn, "key") == "v2"
