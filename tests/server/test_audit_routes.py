"""Tests for audit-related web UI routes."""

from fastapi.testclient import TestClient


class TestAuditOperationsTab:
    """Tests for the audit tab in the operations page."""

    def test_operations_has_audit_tab(self, client: TestClient) -> None:
        """Verify operations page includes the transport selector."""
        resp = client.get("/operations")
        assert resp.status_code == 200
        assert "transport" in resp.text.lower()

    def test_audit_tab_has_form(self, client: TestClient) -> None:
        """Verify operations page includes the scan form."""
        resp = client.get("/operations")
        assert resp.status_code == 200
        assert "scan" in resp.text.lower()


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
