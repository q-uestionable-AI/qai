"""Tests for web UI route handlers."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


class TestLauncherRoute:
    """GET / returns the launcher page."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_workflow_cards(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "Assess an MCP Server" in resp.text
        assert "Test Document Ingestion" in resp.text
        assert "Test a Coding Assistant" in resp.text
        assert "Trace an Attack Path" in resp.text
        assert "Measure Blast Radius" in resp.text
        assert "Generate Report" in resp.text

    def test_contains_nav_links(self, client: TestClient) -> None:
        resp = client.get("/")
        assert 'href="/"' in resp.text
        assert 'href="/operations"' in resp.text
        assert 'href="/research"' in resp.text


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


class TestResearchRoute:
    """GET /research returns the research workspace."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/research")
        assert resp.status_code == 200

    def test_contains_table_sections(self, client: TestClient) -> None:
        resp = client.get("/research")
        assert "Runs" in resp.text
        assert "Findings" in resp.text
        assert "Targets" in resp.text


class TestResearchAPIRoutes:
    """HTMX partial endpoints for research table filtering."""

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


class TestLauncherRxpAvailable:
    """Launcher route passes rxp_available to template context."""

    def test_launcher_passes_rxp_available_false(self, client: TestClient) -> None:
        """When rxp deps unavailable, template shows install hint and disabled toggle."""
        with patch("q_ai.server.routes.rxp_is_available", return_value=False):
            resp = client.get("/")
        assert resp.status_code == 200
        assert 'pip install "q-uestionable-ai[rxp]"' in resp.text
        assert "disabled" in resp.text

    def test_launcher_rxp_available_true(self, client: TestClient) -> None:
        """When rxp deps available, no install hint shown."""
        with patch("q_ai.server.routes.rxp_is_available", return_value=True):
            resp = client.get("/")
        assert resp.status_code == 200
        assert "RXP pre-validation requires additional dependencies" not in resp.text

    def test_launcher_rxp_toggle_has_name_attribute(self, client: TestClient) -> None:
        """RXP toggle has name='rxp_enabled' for FormData inclusion."""
        with patch("q_ai.server.routes.rxp_is_available", return_value=True):
            resp = client.get("/")
        assert 'name="rxp_enabled"' in resp.text
