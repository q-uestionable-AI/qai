"""Tests for web UI route handlers."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestLauncherRoute:
    """GET / returns the launcher page."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_workflow_rows(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "Assess an MCP Server" in resp.text
        assert "Test Document Ingestion" in resp.text
        assert "Test Context Poisoning" in resp.text
        assert "Trace an Attack Path" in resp.text
        assert "Measure Blast Radius" in resp.text
        # Generate Report is hidden from launcher (visible_in_launcher=False)
        assert "Generate Report" not in resp.text

    def test_contains_nav_links(self, client: TestClient) -> None:
        resp = client.get("/")
        assert 'href="/"' in resp.text
        assert 'href="/runs"' in resp.text

    def test_contains_docs_pill(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "docs.q-uestionable.ai" in resp.text
        assert "docs-pill" in resp.text


class TestOperationsRoute:
    """GET /operations returns the operations skeleton."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/operations")
        assert resp.status_code == 200

    def test_contains_status_bar(self, client: TestClient) -> None:
        resp = client.get("/operations")
        assert "status" in resp.text.lower()

    def test_contains_tabs(self, client: TestClient) -> None:
        resp = client.get("/operations")
        assert "tab" in resp.text.lower()


class TestResearchRouteRemoved:
    """GET /research is removed — returns 404."""

    def test_returns_404(self, client: TestClient) -> None:
        resp = client.get("/research")
        assert resp.status_code == 404


class TestTableAPIRoutes:
    """HTMX partial endpoints for table filtering."""

    def test_api_runs_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/runs")
        assert resp.status_code == 200

    def test_api_findings_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/findings")
        assert resp.status_code == 200

    def test_api_targets_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/targets")
        assert resp.status_code == 200

    def test_api_runs_empty_state(self, client: TestClient) -> None:
        resp = client.get("/api/runs")
        assert "No runs" in resp.text

    def test_api_findings_empty_state(self, client: TestClient) -> None:
        resp = client.get("/api/findings")
        assert "No findings" in resp.text

    def test_api_targets_empty_state(self, client: TestClient) -> None:
        resp = client.get("/api/targets")
        assert "No targets" in resp.text

    def test_api_runs_with_invalid_filter_ignored(self, client: TestClient) -> None:
        resp = client.get("/api/runs?status=invalid")
        assert resp.status_code == 200

    def test_api_findings_with_invalid_severity_ignored(self, client: TestClient) -> None:
        resp = client.get("/api/findings?severity=invalid")
        assert resp.status_code == 200


class TestLauncherRxpAlwaysAvailable:
    """RXP is always available — no gating in launcher."""

    def test_rxp_toggle_present_and_enabled(self, client: TestClient) -> None:
        """RXP toggle is always present and not disabled."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="rxp_enabled"' in resp.text
        assert "RXP pre-validation requires additional dependencies" not in resp.text
