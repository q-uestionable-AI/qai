"""Tests for run history DB functions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from q_ai.core.db import (
    create_evidence,
    create_finding,
    create_run,
    create_target,
    delete_run_cascade,
    export_run_bundle,
    get_connection,
    list_runs,
    update_run_status,
)
from q_ai.core.models import RunStatus, Severity


class TestListRunsNameFilter:
    """Verify list_runs filtering by name and combinations with module."""

    def test_filter_by_name(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_run(conn, module="workflow", name="assess")
            create_run(conn, module="workflow", name="test_docs")
            create_run(conn, module="workflow", name="assess")
            runs = list_runs(conn, name="assess")
        assert len(runs) == 2
        assert all(r.name == "assess" for r in runs)

    def test_filter_by_name_no_match(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_run(conn, module="workflow", name="assess")
            runs = list_runs(conn, name="nonexistent")
        assert runs == []

    def test_filter_by_name_and_module(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_run(conn, module="workflow", name="assess")
            create_run(conn, module="audit", name="assess")
            runs = list_runs(conn, module="workflow", name="assess")
        assert len(runs) == 1
        assert runs[0].module == "workflow"


# ---------------------------------------------------------------------------
# Helpers for inserting module-specific data
# ---------------------------------------------------------------------------


def _insert_audit_scan(conn: sqlite3.Connection, run_id: str) -> str:
    scan_id = "audit_scan_001"
    conn.execute(
        "INSERT INTO audit_scans (id, run_id, transport) VALUES (?, ?, ?)",
        (scan_id, run_id, "stdio"),
    )
    return scan_id


def _insert_inject_result(conn: sqlite3.Connection, run_id: str) -> str:
    result_id = "inject_001"
    conn.execute(
        "INSERT INTO inject_results (id, run_id, payload_name, technique, outcome, target_agent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (result_id, run_id, "payload1", "tool_poisoning", "success", "agent1"),
    )
    return result_id


def _insert_proxy_session(
    conn: sqlite3.Connection,
    run_id: str,
    session_file: str | None = None,
) -> str:
    session_id = "proxy_001"
    conn.execute(
        "INSERT INTO proxy_sessions (id, run_id, transport, session_file) VALUES (?, ?, ?, ?)",
        (session_id, run_id, "stdio", session_file),
    )
    return session_id


def _insert_chain_execution(conn: sqlite3.Connection, run_id: str) -> str:
    exec_id = "chain_exec_001"
    conn.execute(
        "INSERT INTO chain_executions (id, run_id, chain_id) VALUES (?, ?, ?)",
        (exec_id, run_id, "chain1"),
    )
    return exec_id


def _insert_chain_step_output(conn: sqlite3.Connection, execution_id: str) -> str:
    step_id = "step_001"
    conn.execute(
        "INSERT INTO chain_step_outputs (id, execution_id, step_id, module, technique) "
        "VALUES (?, ?, ?, ?, ?)",
        (step_id, execution_id, "step1", "audit", "scan"),
    )
    return step_id


def _insert_ipi_payload(
    conn: sqlite3.Connection,
    run_id: str,
    uuid: str,
) -> str:
    payload_id = "ipi_payload_001"
    conn.execute(
        "INSERT INTO ipi_payloads (id, run_id, uuid, token, format, technique, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (payload_id, run_id, uuid, "tok123", "html", "callback"),
    )
    return payload_id


def _insert_ipi_hit(conn: sqlite3.Connection, uuid: str) -> str:
    hit_id = "ipi_hit_001"
    conn.execute(
        "INSERT INTO ipi_hits (id, uuid, confidence, timestamp) VALUES (?, ?, ?, datetime('now'))",
        (hit_id, uuid, "high"),
    )
    return hit_id


def _insert_cxp_result(conn: sqlite3.Connection, run_id: str) -> str:
    result_id = "cxp_001"
    conn.execute(
        "INSERT INTO cxp_test_results "
        "(id, run_id, campaign_id, technique_id, assistant, trigger_prompt, "
        "capture_mode, raw_output) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (result_id, run_id, "camp1", "tech1", "copilot", "prompt", "file", "output"),
    )
    return result_id


def _insert_rxp_validation(conn: sqlite3.Connection, run_id: str) -> str:
    val_id = "rxp_001"
    conn.execute(
        "INSERT INTO rxp_validations "
        "(id, run_id, model_id, total_queries, poison_retrievals, "
        "retrieval_rate, top_k) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (val_id, run_id, "model1", 10, 3, 0.3, 5),
    )
    return val_id


# ---------------------------------------------------------------------------
# delete_run_cascade tests
# ---------------------------------------------------------------------------


class TestDeleteRunCascade:
    def test_deletes_parent_and_children(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            target_id = create_target(conn, type="server", name="t1")
            parent_id = create_run(conn, module="workflow", name="assess", target_id=target_id)
            create_run(conn, module="audit", parent_run_id=parent_id)

            delete_run_cascade(conn, parent_id)

            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    def test_deletes_findings_and_evidence(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            child_id = create_run(conn, module="audit", parent_run_id=parent_id)
            finding_id = create_finding(
                conn,
                run_id=child_id,
                module="audit",
                category="test",
                severity=Severity.HIGH,
                title="Test Finding",
            )
            create_evidence(conn, type="response", finding_id=finding_id, run_id=child_id)

            delete_run_cascade(conn, parent_id)

            assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0

    def test_deletes_all_module_specific_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            child_audit = create_run(conn, module="audit", parent_run_id=parent_id)
            child_inject = create_run(conn, module="inject", parent_run_id=parent_id)
            child_proxy = create_run(conn, module="proxy", parent_run_id=parent_id)
            child_chain = create_run(conn, module="chain", parent_run_id=parent_id)
            child_ipi = create_run(conn, module="ipi", parent_run_id=parent_id)
            child_cxp = create_run(conn, module="cxp", parent_run_id=parent_id)
            child_rxp = create_run(conn, module="rxp", parent_run_id=parent_id)

            _insert_audit_scan(conn, child_audit)
            _insert_inject_result(conn, child_inject)
            _insert_proxy_session(conn, child_proxy)
            exec_id = _insert_chain_execution(conn, child_chain)
            _insert_chain_step_output(conn, exec_id)
            _insert_ipi_payload(conn, child_ipi, uuid="uuid-001")
            _insert_ipi_hit(conn, uuid="uuid-001")
            _insert_cxp_result(conn, child_cxp)
            _insert_rxp_validation(conn, child_rxp)

            delete_run_cascade(conn, parent_id)

            for table in [
                "audit_scans",
                "inject_results",
                "proxy_sessions",
                "chain_executions",
                "chain_step_outputs",
                "ipi_payloads",
                "ipi_hits",
                "cxp_test_results",
                "rxp_validations",
                "runs",
                "findings",
                "evidence",
            ]:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert count == 0, f"Table {table} still has {count} rows"

    def test_ipi_hits_deleted_via_uuid(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="test_docs")
            child_ipi = create_run(conn, module="ipi", parent_run_id=parent_id)
            _insert_ipi_payload(conn, child_ipi, uuid="my-uuid")
            _insert_ipi_hit(conn, uuid="my-uuid")

            delete_run_cascade(conn, parent_id)

            assert conn.execute("SELECT COUNT(*) FROM ipi_hits").fetchone()[0] == 0

    def test_cleans_up_evidence_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("q_ai.core.db._QAI_DATA_DIR", tmp_path)
        db_path = tmp_path / "qai.db"
        evidence_file = tmp_path / "evidence" / "capture.png"
        evidence_file.parent.mkdir(parents=True, exist_ok=True)
        evidence_file.write_text("data")

        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            create_evidence(
                conn,
                type="file",
                run_id=parent_id,
                storage="file",
                path=str(evidence_file),
            )

            files_to_delete = delete_run_cascade(conn, parent_id)

        # File cleanup happens after commit — caller deletes
        for f in files_to_delete:
            Path(f).unlink(missing_ok=True)

        assert not evidence_file.exists()

    def test_cleans_up_proxy_session_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("q_ai.core.db._QAI_DATA_DIR", tmp_path)
        db_path = tmp_path / "qai.db"
        session_file = tmp_path / "sessions" / "session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("{}")

        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            child_proxy = create_run(conn, module="proxy", parent_run_id=parent_id)
            _insert_proxy_session(conn, child_proxy, session_file=str(session_file))

            files_to_delete = delete_run_cascade(conn, parent_id)

        for f in files_to_delete:
            Path(f).unlink(missing_ok=True)

        assert not session_file.exists()

    def test_skips_paths_outside_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "safe"
        data_dir.mkdir()
        monkeypatch.setattr("q_ai.core.db._QAI_DATA_DIR", data_dir)
        db_path = tmp_path / "qai.db"
        outside_file = tmp_path / "outside" / "secret.txt"
        outside_file.parent.mkdir(parents=True, exist_ok=True)
        outside_file.write_text("sensitive")

        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            create_evidence(
                conn,
                type="file",
                run_id=parent_id,
                storage="file",
                path=str(outside_file),
            )

            files_to_delete = delete_run_cascade(conn, parent_id)

        assert files_to_delete == []
        assert outside_file.exists()

    def test_returns_empty_list_for_no_files(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            files = delete_run_cascade(conn, parent_id)
        assert files == []

    def test_nonexistent_run_raises(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn, pytest.raises(ValueError, match=r"Run .* not found"):
            delete_run_cascade(conn, "nonexistent")


# ---------------------------------------------------------------------------
# export_run_bundle tests
# ---------------------------------------------------------------------------


class TestExportRunBundle:
    def test_basic_export_structure(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            target_id = create_target(conn, type="server", name="test-srv")
            parent_id = create_run(
                conn,
                module="workflow",
                name="assess",
                target_id=target_id,
                config={"target_id": target_id},
            )
            update_run_status(conn, parent_id, RunStatus.COMPLETED)
            bundle = export_run_bundle(conn, parent_id)

        assert bundle["schema_version"] == "run-bundle-v1"
        assert bundle["run"]["id"] == parent_id
        assert bundle["run"]["module"] == "workflow"
        assert bundle["target"]["name"] == "test-srv"
        assert isinstance(bundle["child_runs"], list)
        assert isinstance(bundle["findings"], list)
        assert isinstance(bundle["evidence"], list)

    def test_export_includes_child_runs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            child_id = create_run(conn, module="audit", parent_run_id=parent_id)
            bundle = export_run_bundle(conn, parent_id)

        assert len(bundle["child_runs"]) == 1
        assert bundle["child_runs"][0]["id"] == child_id

    def test_export_includes_findings_and_evidence(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            child_id = create_run(conn, module="audit", parent_run_id=parent_id)
            finding_id = create_finding(
                conn,
                run_id=child_id,
                module="audit",
                category="test",
                severity=Severity.HIGH,
                title="Finding 1",
            )
            create_evidence(
                conn,
                type="response",
                finding_id=finding_id,
                run_id=child_id,
                mime_type="text/plain",
            )
            bundle = export_run_bundle(conn, parent_id)

        assert len(bundle["findings"]) == 1
        assert bundle["findings"][0]["title"] == "Finding 1"
        assert len(bundle["evidence"]) == 1
        assert bundle["evidence"][0]["type"] == "response"
        # Evidence export should NOT include inline content blobs
        assert "content" not in bundle["evidence"][0]

    def test_export_includes_module_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            child_id = create_run(conn, module="audit", parent_run_id=parent_id)
            _insert_audit_scan(conn, child_id)
            bundle = export_run_bundle(conn, parent_id)

        assert len(bundle["audit_scans"]) == 1

    def test_export_nonexistent_run_raises(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn, pytest.raises(ValueError, match=r"Run .* not found"):
            export_run_bundle(conn, "nonexistent")
