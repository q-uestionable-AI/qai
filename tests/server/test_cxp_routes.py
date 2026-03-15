"""Tests for CXP web UI routes."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestCxpTabInOperations:
    def test_operations_contains_cxp_tab(self, client: TestClient) -> None:
        resp = client.get("/operations")
        assert resp.status_code == 200
        assert "cxp" in resp.text.lower()

    def test_cxp_tab_api_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/cxp/tab")
        assert resp.status_code == 200

    def test_cxp_results_api_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/cxp/results")
        assert resp.status_code == 200
