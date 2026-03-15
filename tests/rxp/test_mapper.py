"""Tests for RXP mapper (persist_validation)."""

from __future__ import annotations

from pathlib import Path

from q_ai.core.db import create_run, get_connection
from q_ai.rxp.mapper import persist_validation
from q_ai.rxp.models import ValidationResult


class TestPersistValidation:
    """Tests for persist_validation."""

    def test_creates_run_and_validation(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        result = ValidationResult(
            model_id="minilm-l6",
            total_queries=5,
            poison_retrievals=3,
            retrieval_rate=0.6,
            mean_poison_rank=2.5,
            query_results=[],
        )

        run_id = persist_validation(
            result=result,
            profile_id="hr-policy",
            top_k=5,
            db_path=db_path,
        )

        assert isinstance(run_id, str)
        assert len(run_id) > 0

        # Verify the run record
        with get_connection(db_path) as conn:
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert run_row is not None
            assert run_row["module"] == "rxp"
            assert run_row["name"] == "validate-minilm-l6"

            # Verify the validation record
            val_row = conn.execute(
                "SELECT * FROM rxp_validations WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert val_row is not None
            assert val_row["model_id"] == "minilm-l6"
            assert val_row["profile_id"] == "hr-policy"
            assert val_row["total_queries"] == 5
            assert val_row["poison_retrievals"] == 3

    def test_persist_validation_uses_supplied_run_id(self, tmp_path: Path) -> None:
        """Verify persist_validation uses provided run_id instead of creating one."""
        db_path = tmp_path / "test.db"
        # Pre-create a run
        with get_connection(db_path) as conn:
            pre_run_id = create_run(conn, module="rxp", name="pre-created")

        result = ValidationResult(
            model_id="minilm-l6",
            total_queries=3,
            poison_retrievals=1,
            retrieval_rate=0.33,
            mean_poison_rank=3.0,
            query_results=[],
        )

        run_id = persist_validation(
            result=result,
            profile_id="hr-policy",
            top_k=5,
            db_path=db_path,
            run_id=pre_run_id,
        )
        assert run_id == pre_run_id

        with get_connection(db_path) as conn:
            all_runs = conn.execute("SELECT * FROM runs").fetchall()
            assert len(all_runs) == 1
            assert all_runs[0]["id"] == pre_run_id

            # Validation row should reference the pre-created run
            val_row = conn.execute(
                "SELECT * FROM rxp_validations WHERE run_id = ?", (pre_run_id,)
            ).fetchone()
            assert val_row is not None
