"""Tests for the run results view (Phase 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.core.db import (
    create_finding,
    create_run,
    create_target,
    update_run_status,
)
from q_ai.core.models import RunStatus, Severity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_completed_assess_run(
    tmp_db: Path,
) -> tuple[str, str, str]:
    """Create a completed assess workflow with audit child run and findings.

    Returns (parent_run_id, audit_child_id, target_id).
    """
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        target_id = create_target(conn, type="server", name="Test Server", uri="stdio://test")
        parent_id = create_run(conn, module="workflow", name="assess", target_id=target_id)
        update_run_status(conn, parent_id, RunStatus.RUNNING)

        audit_child = create_run(conn, module="audit", name="audit-child", parent_run_id=parent_id)
        update_run_status(conn, audit_child, RunStatus.COMPLETED)

        create_finding(
            conn,
            run_id=audit_child,
            module="audit",
            category="command_injection",
            severity=Severity.CRITICAL,
            title="Shell command injection",
            description="The server executes unvalidated input.",
            framework_ids={"OWASP_MCP": ["MCP-01"], "CWE": ["CWE-78"]},
        )
        create_finding(
            conn,
            run_id=audit_child,
            module="audit",
            category="information_disclosure",
            severity=Severity.HIGH,
            title="Verbose error leaks",
            description="Error messages reveal internal paths.",
        )

        update_run_status(conn, parent_id, RunStatus.COMPLETED)
        conn.commit()
        return parent_id, audit_child, target_id
    finally:
        conn.close()


def _setup_completed_assess_with_proxy(
    tmp_db: Path,
) -> tuple[str, str]:
    """Create completed assess with proxy child run + proxy_session.

    Returns (parent_run_id, proxy_child_id).
    """
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        parent_id = create_run(conn, module="workflow", name="assess")
        update_run_status(conn, parent_id, RunStatus.RUNNING)
        proxy_child = create_run(conn, module="proxy", name="proxy-child", parent_run_id=parent_id)
        update_run_status(conn, proxy_child, RunStatus.COMPLETED)
        conn.execute(
            "INSERT INTO proxy_sessions"
            " (run_id, transport, server_name, message_count, duration_seconds)"
            " VALUES (?, ?, ?, ?, ?)",
            (proxy_child, "stdio", "test-server", 42, 12.5),
        )
        update_run_status(conn, parent_id, RunStatus.COMPLETED)
        conn.commit()
        return parent_id, proxy_child
    finally:
        conn.close()


def _setup_completed_assess_with_inject(
    tmp_db: Path,
) -> tuple[str, str, str]:
    """Create completed assess with inject child run + inject_results.

    Returns (parent_run_id, inject_child_id, target_id).
    """
    import uuid

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        target_id = create_target(conn, type="server", name="Test Server", uri="stdio://test")
        parent_id = create_run(conn, module="workflow", name="assess", target_id=target_id)
        update_run_status(conn, parent_id, RunStatus.RUNNING)
        inject_child = create_run(
            conn, module="inject", name="inject-child", parent_run_id=parent_id
        )
        update_run_status(conn, inject_child, RunStatus.COMPLETED)
        for payload, technique, outcome in [
            ("exfil_via_fetch", "description_poisoning", "full_compliance"),
            ("shadow_tool_call", "cross_tool_escalation", "refusal"),
            ("data_leak_prompt", "output_injection", "partial_compliance"),
        ]:
            conn.execute(
                "INSERT INTO inject_results"
                " (id, run_id, payload_name, technique, outcome, target_agent, evidence)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    inject_child,
                    payload,
                    technique,
                    outcome,
                    "claude-sonnet-4-6",
                    f"Model response for {payload}",
                ),
            )
        update_run_status(conn, parent_id, RunStatus.COMPLETED)
        conn.commit()
        return parent_id, inject_child, target_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rename: Operations → Runs
# ---------------------------------------------------------------------------


class TestOperationsRedirect:
    """GET /operations should 301 redirect to /runs."""

    def test_redirect_no_params(self, client: TestClient) -> None:
        resp = client.get("/operations", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/runs"

    def test_redirect_preserves_query_params(self, client: TestClient) -> None:
        resp = client.get("/operations?run_id=abc&foo=bar", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/runs?run_id=abc&foo=bar"


class TestOverviewHeader:
    """Overview header renders for terminal runs with run_id."""

    def test_overview_header_renders_workflow_name(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert resp.status_code == 200
        assert "Assess an MCP Server" in resp.text

    def test_overview_header_shows_target(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Test Server" in resp.text
        assert "stdio://test" in resp.text

    def test_overview_header_shows_status_badge(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Completed" in resp.text
        assert "badge-success" in resp.text

    def test_overview_header_shows_finding_counts(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "1 Critical" in resp.text
        assert "1 High" in resp.text

    def test_overview_header_generate_report_button(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Generate Report" in resp.text

    def test_overview_header_export_json_placeholder(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Export JSON" in resp.text

    def test_running_run_shows_status_bar_not_header(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "operations-status-bar" in resp.text
        assert "overview-header" not in resp.text


class TestRunsPage:
    """GET /runs basic behavior."""

    def test_runs_no_run_id_returns_200(self, client: TestClient) -> None:
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert "Run History" in resp.text

    def test_runs_nav_shows_runs_label(self, client: TestClient) -> None:
        resp = client.get("/runs")
        assert ">Runs<" in resp.text.replace(" ", "").replace("\n", "")

    def test_runs_page_passes_run_id(self, client: TestClient) -> None:
        resp = client.get("/runs?run_id=abc")
        assert resp.status_code == 200
        assert 'data-run-id="abc"' in resp.text


class TestScopedModuleTabs:
    """Only tabs for the workflow's modules should appear."""

    def test_assess_shows_only_audit_proxy_inject(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        text = resp.text
        assert "onclick=\"switchTab(this, 'audit')\"" in text
        assert "onclick=\"switchTab(this, 'proxy')\"" in text
        assert "onclick=\"switchTab(this, 'inject')\"" in text
        assert "onclick=\"switchTab(this, 'chain')\"" not in text
        assert "onclick=\"switchTab(this, 'ipi')\"" not in text
        assert "onclick=\"switchTab(this, 'cxp')\"" not in text
        assert "onclick=\"switchTab(this, 'rxp')\"" not in text

    def test_no_run_id_shows_history(self, client: TestClient) -> None:
        resp = client.get("/runs")
        text = resp.text
        assert "Run History" in text
        # Legacy 7-tab view replaced by run history in Phase 3
        assert "switchTab" not in text

    def test_module_did_not_execute_message(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.PARTIAL)
            audit_child = create_run(
                conn, module="audit", name="audit-child", parent_run_id=parent_id
            )
            update_run_status(conn, audit_child, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Module did not execute in this run" in resp.text


class TestAuditResultsTab:
    """Audit results tab renders findings, server info, and evidence."""

    def test_audit_tab_shows_findings(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Shell command injection" in resp.text
        assert "Verbose error leaks" in resp.text

    def test_audit_tab_shows_severity_badges(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "badge-critical" in resp.text
        assert "badge-high" in resp.text

    def test_audit_tab_shows_description(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "The server executes unvalidated input" in resp.text

    def test_audit_tab_shows_framework_ids(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "MCP-01" in resp.text
        assert "CWE-78" in resp.text

    def test_audit_tab_shows_server_info(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, audit_child, _ = _setup_completed_assess_run(tmp_db)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute(
                """INSERT INTO audit_scans
                   (run_id, transport, server_name, server_version,
                    scanners_run, finding_count, scan_duration_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (audit_child, "stdio", "my-mcp-server", "1.2.0", "tool_poisoning,rug_pull", 2, 4.5),
            )
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "my-mcp-server" in resp.text
        assert "stdio" in resp.text

    def test_audit_tab_expandable_evidence(self, client: TestClient, tmp_db: Path) -> None:
        from q_ai.core.db import create_evidence

        parent_id, audit_child, _ = _setup_completed_assess_run(tmp_db)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            row = conn.execute(
                "SELECT id FROM findings WHERE run_id = ? LIMIT 1",
                (audit_child,),
            ).fetchone()
            finding_id = row["id"]
            create_evidence(
                conn,
                type="response",
                finding_id=finding_id,
                content="Raw model response payload here",
            )
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Evidence" in resp.text
        assert "Raw model response payload here" in resp.text

    def test_audit_tab_shows_source_ref(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            audit_child = create_run(
                conn, module="audit", name="audit-child", parent_run_id=parent_id
            )
            update_run_status(conn, audit_child, RunStatus.COMPLETED)
            create_finding(
                conn,
                run_id=audit_child,
                module="audit",
                category="test",
                severity=Severity.MEDIUM,
                title="Test finding",
                source_ref="scanner:tool_poisoning",
            )
            update_run_status(conn, parent_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "scanner:tool_poisoning" in resp.text

    def test_audit_tab_scanner_errors(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.FAILED)
            audit_child = create_run(
                conn, module="audit", name="audit-child", parent_run_id=parent_id
            )
            update_run_status(conn, audit_child, RunStatus.FAILED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "failed" in resp.text.lower() or "error" in resp.text.lower()


class TestInjectResultsTab:
    """Inject results tab renders campaign summary and per-payload outcomes."""

    def test_inject_tab_shows_payload_names(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_with_inject(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "exfil_via_fetch" in resp.text
        assert "shadow_tool_call" in resp.text
        assert "data_leak_prompt" in resp.text

    def test_inject_tab_shows_techniques(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_with_inject(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "description_poisoning" in resp.text
        assert "cross_tool_escalation" in resp.text

    def test_inject_tab_shows_outcomes(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_with_inject(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "full_compliance" in resp.text
        assert "refusal" in resp.text

    def test_inject_tab_shows_model(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_with_inject(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "claude-sonnet-4-6" in resp.text

    def test_inject_tab_shows_campaign_summary(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_with_inject(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "3" in resp.text


class TestProxyResultsTab:
    def test_proxy_tab_shows_session_summary(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _ = _setup_completed_assess_with_proxy(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "test-server" in resp.text
        assert "stdio" in resp.text
        assert "42" in resp.text

    def test_proxy_tab_shows_duration(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _ = _setup_completed_assess_with_proxy(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "12.5" in resp.text

    def test_proxy_messages_endpoint_no_session_file(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        _, proxy_child = _setup_completed_assess_with_proxy(tmp_db)
        resp = client.get(f"/api/runs/proxy-messages/{proxy_child}?page=1")
        assert resp.status_code == 200
        assert "No messages captured" in resp.text


class TestFindingsSidebarNavigation:
    """Findings sidebar entries should have navigation data attributes."""

    def test_sidebar_findings_have_data_attributes(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _audit_child, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/api/operations/findings-sidebar?run_id={parent_id}")
        assert 'data-module="audit"' in resp.text
        assert "data-finding-id=" in resp.text
        assert "cursor-pointer" in resp.text

    def test_sidebar_findings_have_onclick(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/api/operations/findings-sidebar?run_id={parent_id}")
        assert "switchToFinding" in resp.text


class TestContentSanitization:
    """XSS regression: adversarial content must be HTML-escaped."""

    def test_xss_in_finding_title(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            audit_child = create_run(
                conn, module="audit", name="audit-child", parent_run_id=parent_id
            )
            update_run_status(conn, audit_child, RunStatus.COMPLETED)
            create_finding(
                conn,
                run_id=audit_child,
                module="audit",
                category="xss_test",
                severity=Severity.HIGH,
                title='<script>alert("xss")</script>',
                description='<img src=x onerror="alert(1)">',
            )
            update_run_status(conn, parent_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "<script>alert" not in resp.text
        assert "&lt;script&gt;" in resp.text or "\\u003c" in resp.text.lower()

    def test_xss_in_finding_description(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            audit_child = create_run(
                conn, module="audit", name="audit-child", parent_run_id=parent_id
            )
            update_run_status(conn, audit_child, RunStatus.COMPLETED)
            create_finding(
                conn,
                run_id=audit_child,
                module="audit",
                category="xss_test",
                severity=Severity.MEDIUM,
                title="Safe title",
                description='<iframe src="javascript:alert(1)">',
            )
            update_run_status(conn, parent_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "<iframe" not in resp.text
        assert "&lt;iframe" in resp.text or "\\u003c" in resp.text.lower()

    def test_xss_in_inject_evidence(self, client: TestClient, tmp_db: Path) -> None:
        import uuid

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            inject_child = create_run(
                conn, module="inject", name="inject-child", parent_run_id=parent_id
            )
            update_run_status(conn, inject_child, RunStatus.COMPLETED)
            conn.execute(
                "INSERT INTO inject_results"
                " (id, run_id, payload_name, technique, outcome, target_agent, evidence)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    inject_child,
                    "xss_payload",
                    "description_poisoning",
                    "full_compliance",
                    "test-model",
                    '<script>alert("xss")</script>',
                ),
            )
            update_run_status(conn, parent_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert '<script>alert("xss")</script>' not in resp.text

    def test_xss_in_sidebar_finding_title(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            run_id = create_run(conn, module="audit", name="audit-run")
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="xss_test",
                severity=Severity.HIGH,
                title='<script>alert("xss")</script>',
            )
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/api/operations/findings-sidebar?run_id={run_id}")
        assert "<script>alert" not in resp.text
        assert "&lt;script&gt;" in resp.text


# ---------------------------------------------------------------------------
# Phase 3: two-release redirect on /runs for target-bound probe runs
# ---------------------------------------------------------------------------


def _make_probe_run(tmp_db: Path, *, with_target: bool) -> tuple[str, str | None]:
    """Create a COMPLETED ipi-probe run; returns (run_id, target_id)."""
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        target_id: str | None = None
        if with_target:
            target_id = create_target(conn, type="server", name="redirect-target")
        run_id = create_run(conn, module="ipi-probe", target_id=target_id)
        update_run_status(conn, run_id, RunStatus.COMPLETED)
        conn.commit()
        return run_id, target_id
    finally:
        conn.close()


class TestRunsProbeTargetBound:
    """GET /runs renders target-bound probe runs in the runs view.

    Phase 6 removed the two-release redirect to the Intel detail page;
    every probe run — bound or unbound — now renders the single-run
    results view directly. The ``intel`` query parameter is also gone
    from the handler signature but is silently ignored by Starlette
    when passed, per the Phase 6 brief's tolerance guarantee.
    """

    def test_target_bound_probe_renders_runs_view(self, client: TestClient, tmp_db: Path) -> None:
        run_id, target_id = _make_probe_run(tmp_db, with_target=True)
        assert target_id is not None

        resp = client.get(f"/runs?run_id={run_id}", follow_redirects=False)
        assert resp.status_code == 200
        # runs.html renders a results-mode view for the single run —
        # the run_id appears in the rendered body. Matches the shape
        # used by ``test_null_target_probe_renders`` below.
        assert run_id in resp.text

    def test_intel_query_param_is_silently_ignored(self, client: TestClient, tmp_db: Path) -> None:
        run_id, _ = _make_probe_run(tmp_db, with_target=True)
        resp = client.get(f"/runs?run_id={run_id}&intel=1", follow_redirects=False)
        assert resp.status_code == 200
        # A plain 200 could also be the history view if ``run_id`` were
        # silently dropped. Asserting the id in the body pins down
        # single-run rendering under the tolerance guarantee.
        assert run_id in resp.text

    def test_null_target_probe_renders(self, client: TestClient, tmp_db: Path) -> None:
        run_id, _ = _make_probe_run(tmp_db, with_target=False)
        resp = client.get(f"/runs?run_id={run_id}", follow_redirects=False)
        assert resp.status_code == 200

    def test_sweep_run_renders(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            target_id = create_target(conn, type="server", name="sweep-target")
            run_id = create_run(conn, module="ipi-sweep", target_id=target_id)
            update_run_status(conn, run_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={run_id}", follow_redirects=False)
        assert resp.status_code == 200

    def test_workflow_run_renders(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}", follow_redirects=False)
        assert resp.status_code == 200

    def test_unknown_run_id_renders(self, client: TestClient) -> None:
        resp = client.get("/runs?run_id=does-not-exist-anywhere", follow_redirects=False)
        assert resp.status_code == 200


class TestRunsStatusBarRefreshLink:
    """Refresh link on the runs-view status bar has no ``intel=1`` suffix.

    Phase 6 removed the ``runs_link_suffix`` template variable and the
    Refresh link now emits a clean ``/runs?run_id=<id>`` URL regardless
    of the request's query string.
    """

    def _make_running_probe(self, tmp_db: Path, *, with_target: bool) -> tuple[str, str | None]:
        """Create a RUNNING ipi-probe run so status_bar.html is rendered.

        runs.html branches on ``is_terminal``; only non-terminal runs
        include the status bar with its Refresh link.
        """
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            target_id: str | None = None
            if with_target:
                target_id = create_target(conn, type="server", name="marker-target")
            run_id = create_run(conn, module="ipi-probe", target_id=target_id)
            update_run_status(conn, run_id, RunStatus.RUNNING)
            conn.commit()
            return run_id, target_id
        finally:
            conn.close()

    def test_refresh_link_has_no_intel_suffix_bound(self, client: TestClient, tmp_db: Path) -> None:
        run_id, _ = self._make_running_probe(tmp_db, with_target=True)
        resp = client.get(f"/runs?run_id={run_id}", follow_redirects=False)
        assert resp.status_code == 200
        assert f'href="/runs?run_id={run_id}"' in resp.text
        assert "intel=1" not in resp.text

    def test_refresh_link_has_no_intel_suffix_unbound(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        run_id, _ = self._make_running_probe(tmp_db, with_target=False)
        resp = client.get(f"/runs?run_id={run_id}", follow_redirects=False)
        assert resp.status_code == 200
        assert f'href="/runs?run_id={run_id}"' in resp.text
        assert "intel=1" not in resp.text
