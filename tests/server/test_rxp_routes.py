"""Tests for RXP web UI routes."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestRxpTabInOperations:
    def test_operations_contains_rxp_tab(self, client: TestClient) -> None:
        resp = client.get("/operations")
        assert resp.status_code == 200
        assert "rxp" in resp.text.lower()

    def test_rxp_tab_api_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/rxp/tab")
        assert resp.status_code == 200

    def test_rxp_validations_api_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/rxp/validations")
        assert resp.status_code == 200
