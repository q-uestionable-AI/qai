"""Tests for the Intel page routes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from q_ai.core.db import create_target


class TestIntelPage:
    """GET /intel renders the Intel page."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert resp.status_code == 200

    def test_contains_nav_link(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert 'href="/intel"' in resp.text

    def test_nav_link_present_on_other_pages(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        assert 'href="/intel"' in resp.text

    def test_contains_import_card(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert "Import Results" in resp.text
        assert "form-import" in resp.text

    def test_contains_probe_card(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert "Probe Model" in resp.text
        assert "form-probe" in resp.text

    def test_format_options_present(self, client: TestClient) -> None:
        resp = client.get("/intel")
        for fmt in ["garak", "pyrit", "sarif", "scored", "bipia"]:
            assert fmt in resp.text.lower()

    def test_target_selector_populated(self, tmp_db: Path, client: TestClient) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            create_target(conn, type="server", name="intel-test-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/intel")
        assert "intel-test-target" in resp.text


def _garak_jsonl() -> str:
    """Build a minimal valid Garak JSONL report."""
    lines = [
        json.dumps(
            {
                "entry_type": "start_run setup",
                "garak_version": "0.9.0.13",
                "run_id": "test-run",
                "model_type": "openai",
                "model_name": "gpt-4",
            }
        ),
        json.dumps(
            {
                "entry_type": "eval",
                "eval_id": "eval-001",
                "probe": "promptinject.HijackHateHumansMini",
                "detector": "toxicity.ToxicityClassifier",
                "passed": 2,
                "total": 10,
                "owasp_llm": "LLM01",
            }
        ),
    ]
    return "\n".join(lines) + "\n"


class TestImportPreview:
    """POST /api/intel/import/preview parses without persisting."""

    def test_preview_garak(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/preview",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["finding_count"] >= 1
        assert isinstance(data["findings"], list)
        assert "severity" in data["findings"][0]
        assert "category" in data["findings"][0]
        assert "title" in data["findings"][0]

    def test_preview_unknown_format(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "data.json"
        file_path.write_text("{}", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/preview",
                files={"file": ("data.json", f, "application/json")},
                data={"format": "unknown"},
            )

        assert resp.status_code == 422
        assert "Unknown format" in resp.json()["detail"]


class TestImportCommit:
    """POST /api/intel/import/commit parses and persists."""

    def test_commit_garak(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["finding_count"] >= 1
        assert "run_id" in data

    def test_commit_unknown_format(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "data.json"
        file_path.write_text("{}", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("data.json", f, "application/json")},
                data={"format": "unknown"},
            )

        assert resp.status_code == 422


class TestProbeLaunch:
    """POST /api/intel/probe/launch validates and accepts."""

    def test_missing_endpoint(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"model": "gpt-4o-mini"},
        )
        assert resp.status_code == 422
        assert "endpoint" in resp.json()["detail"]

    def test_missing_model(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1"},
        )
        assert resp.status_code == 422
        assert "model" in resp.json()["detail"]

    def test_invalid_json_body(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["detail"]

    def test_non_dict_json_body(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json=["a", "list"],
        )
        assert resp.status_code == 422
        assert "object" in resp.json()["detail"]

    def test_non_numeric_temperature(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1", "model": "m", "temperature": "hot"},
        )
        assert resp.status_code == 422
        assert "temperature" in resp.json()["detail"]

    def test_non_numeric_concurrency(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1", "model": "m", "concurrency": "many"},
        )
        assert resp.status_code == 422
        assert "concurrency" in resp.json()["detail"]

    def test_concurrency_below_one(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1", "model": "m", "concurrency": 0},
        )
        assert resp.status_code == 422
        assert "concurrency" in resp.json()["detail"]

    def test_happy_path_launches(self, client: TestClient) -> None:
        mock_probes = [MagicMock()]
        mock_result = MagicMock()
        with (
            patch("q_ai.ipi.probe_service.load_probes", return_value=mock_probes) as m_load,
            patch(
                "q_ai.ipi.probe_service.run_probes",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("q_ai.ipi.probe_service.persist_probe_run"),
        ):
            resp = client.post(
                "/api/intel/probe/launch",
                json={
                    "endpoint": "http://localhost:8000/v1",
                    "model": "gpt-4o-mini",
                },
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "launched"
        assert data["redirect"] == "/runs"
        m_load.assert_called_once()


class TestImportCommitTargetValidation:
    """POST /api/intel/import/commit validates target_id."""

    def test_invalid_target_id_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak", "target_id": "nonexistent-target-id"},
            )

        assert resp.status_code == 422
        assert "Target not found" in resp.json()["detail"]


class TestErrorMessagesAreGeneric:
    """Error responses must not leak exception text."""

    def test_preview_error_is_generic(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "bad.jsonl"
        file_path.write_text("not valid garak data\n", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/preview",
                files={"file": ("bad.jsonl", f, "application/jsonl")},
                data={"format": "garak"},
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # Must be generic — no internal paths or parser class names.
        assert "Parse failed:" not in detail
        assert "Traceback" not in detail

    def test_commit_error_is_generic(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "bad.jsonl"
        file_path.write_text("not valid garak data\n", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("bad.jsonl", f, "application/jsonl")},
                data={"format": "garak"},
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "Import failed:" not in detail
        assert "Traceback" not in detail
