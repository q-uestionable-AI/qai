"""Tests for RXP database CRUD operations."""

from __future__ import annotations

from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.rxp.db import get_validation, list_validations, save_validation
from q_ai.rxp.models import ValidationResult


def _make_result(model_id: str = "minilm-l6") -> ValidationResult:
    """Create a minimal ValidationResult for testing."""
    return ValidationResult(
        model_id=model_id,
        total_queries=5,
        poison_retrievals=3,
        retrieval_rate=0.6,
        mean_poison_rank=2.5,
        query_results=[],
    )


class TestSaveValidation:
    """Tests for save_validation."""

    def test_save_and_get_roundtrip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        # Create a run record first
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
                ("run1", "rxp", 0),
            )

        result = _make_result()
        vid = save_validation("run1", result, "hr-policy", 5, db_path=db_path)

        retrieved = get_validation(vid, db_path=db_path)
        assert retrieved is not None
        assert retrieved["run_id"] == "run1"
        assert retrieved["model_id"] == "minilm-l6"
        assert retrieved["profile_id"] == "hr-policy"
        assert retrieved["total_queries"] == 5
        assert retrieved["poison_retrievals"] == 3
        assert retrieved["retrieval_rate"] == 0.6
        assert retrieved["mean_poison_rank"] == 2.5
        assert retrieved["top_k"] == 5
        assert retrieved["results_json"] is not None

    def test_save_with_no_profile(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
                ("run2", "rxp", 0),
            )

        result = _make_result()
        vid = save_validation("run2", result, None, 10, db_path=db_path)

        retrieved = get_validation(vid, db_path=db_path)
        assert retrieved is not None
        assert retrieved["profile_id"] is None


class TestGetValidation:
    """Tests for get_validation."""

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path):
            pass  # ensure schema
        assert get_validation("nonexistent", db_path=db_path) is None


class TestListValidations:
    """Tests for list_validations."""

    def test_list_all(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
                ("run1", "rxp", 0),
            )
            conn.execute(
                "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
                ("run2", "rxp", 0),
            )

        save_validation("run1", _make_result("minilm-l6"), "hr-policy", 5, db_path=db_path)
        save_validation("run2", _make_result("bge-small"), None, 3, db_path=db_path)

        rows = list_validations(db_path=db_path)
        assert len(rows) == 2

    def test_list_filtered_by_model(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
                ("run1", "rxp", 0),
            )
            conn.execute(
                "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
                ("run2", "rxp", 0),
            )

        save_validation("run1", _make_result("minilm-l6"), "hr-policy", 5, db_path=db_path)
        save_validation("run2", _make_result("bge-small"), None, 3, db_path=db_path)

        rows = list_validations(model_id="minilm-l6", db_path=db_path)
        assert len(rows) == 1
        assert rows[0]["model_id"] == "minilm-l6"

    def test_list_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path):
            pass
        rows = list_validations(db_path=db_path)
        assert rows == []
