"""Tests for list_evidence() DB function."""

from __future__ import annotations

from pathlib import Path

from q_ai.core.db import (
    create_evidence,
    create_finding,
    create_run,
    get_connection,
    list_evidence,
)
from q_ai.core.models import Severity


class TestListEvidence:
    """Tests for the list_evidence query function."""

    def test_returns_empty_when_no_evidence(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            result = list_evidence(conn)
        assert result == []

    def test_filter_by_finding_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit", name="test")
            finding_id = create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="test",
                severity=Severity.HIGH,
                title="Test finding",
            )
            ev_id = create_evidence(
                conn, type="response", finding_id=finding_id, content="evidence data"
            )
            # Create unrelated evidence
            create_evidence(conn, type="request", run_id=run_id, content="other")

            result = list_evidence(conn, finding_id=finding_id)

        assert len(result) == 1
        assert result[0].id == ev_id
        assert result[0].content == "evidence data"

    def test_filter_by_run_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit", name="test")
            other_run_id = create_run(conn, module="inject", name="other")
            create_evidence(conn, type="request", run_id=run_id, content="a")
            create_evidence(conn, type="response", run_id=run_id, content="b")
            create_evidence(conn, type="request", run_id=other_run_id, content="c")

            result = list_evidence(conn, run_id=run_id)

        assert len(result) == 2
        contents = {e.content for e in result}
        assert contents == {"a", "b"}

    def test_no_filter_returns_all(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="audit", name="test")
            create_evidence(conn, type="request", run_id=run_id, content="a")
            create_evidence(conn, type="response", run_id=run_id, content="b")

            result = list_evidence(conn)

        assert len(result) == 2
