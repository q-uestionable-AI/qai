"""Tests for audit-related web UI routes."""

from fastapi.testclient import TestClient


class TestAuditOperationsTab:
    """Tests for the audit tab — accessible via API partial."""

    def test_audit_tab_api_returns_200(self, client: TestClient) -> None:
        """Verify audit tab partial endpoint works."""
        resp = client.get("/api/audit/scan/nonexistent/status")
        assert resp.status_code == 200

    def test_operations_redirects_to_runs(self, client: TestClient) -> None:
        """GET /operations redirects to /runs (run history view)."""
        resp = client.get("/operations")
        assert resp.status_code == 200
        assert "Run History" in resp.text


class TestAuditApiRoutes:
    """Tests for the audit API endpoints."""

    def test_scan_status_returns_200(self, client: TestClient) -> None:
        """Verify scan status endpoint returns 200 for nonexistent run."""
        resp = client.get("/api/audit/scan/nonexistent/status")
        assert resp.status_code == 200

    def test_audit_findings_empty(self, client: TestClient) -> None:
        """Verify findings endpoint returns 200 for nonexistent run."""
        resp = client.get("/api/audit/findings/nonexistent")
        assert resp.status_code == 200
