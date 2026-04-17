"""Tests for RunGuidance and GuidanceBlock data models."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from q_ai.core.guidance import BlockKind, GuidanceBlock, RunGuidance


class TestBlockKind:
    """BlockKind StrEnum validation."""

    def test_valid_values(self) -> None:
        assert BlockKind.INVENTORY == "inventory"
        assert BlockKind.TRIGGER_PROMPTS == "trigger_prompts"
        assert BlockKind.DEPLOYMENT_STEPS == "deployment_steps"
        assert BlockKind.MONITORING == "monitoring"
        assert BlockKind.INTERPRETATION == "interpretation"
        assert BlockKind.FACTORS == "factors"

    def test_invalid_value_rejected(self) -> None:
        with pytest.raises(ValueError):
            BlockKind("nonexistent")

    def test_all_values_count(self) -> None:
        assert len(BlockKind) == 6


class TestGuidanceBlock:
    """GuidanceBlock serialization and validation."""

    def test_construction(self) -> None:
        block = GuidanceBlock(
            kind=BlockKind.INVENTORY,
            label="File Inventory",
            items=["file1.pdf", "file2.md"],
            metadata={"count": 2},
        )
        assert block.kind == BlockKind.INVENTORY
        assert block.label == "File Inventory"
        assert len(block.items) == 2
        assert block.metadata["count"] == 2

    def test_to_dict(self) -> None:
        block = GuidanceBlock(
            kind=BlockKind.DEPLOYMENT_STEPS,
            label="Steps",
            items=["Step 1", "Step 2"],
            metadata={"key": "val"},
        )
        d = block.to_dict()
        assert d["kind"] == "deployment_steps"
        assert d["label"] == "Steps"
        assert d["items"] == ["Step 1", "Step 2"]
        assert d["metadata"] == {"key": "val"}

    def test_from_dict(self) -> None:
        data = {
            "kind": "monitoring",
            "label": "Monitor",
            "items": ["Watch logs"],
            "metadata": {},
        }
        block = GuidanceBlock.from_dict(data)
        assert block.kind == BlockKind.MONITORING
        assert block.label == "Monitor"
        assert block.items == ["Watch logs"]

    def test_round_trip(self) -> None:
        original = GuidanceBlock(
            kind=BlockKind.TRIGGER_PROMPTS,
            label="Prompts",
            items=["prompt1", "prompt2"],
            metadata={"profile": "aggressive"},
        )
        restored = GuidanceBlock.from_dict(original.to_dict())
        assert restored.kind == original.kind
        assert restored.label == original.label
        assert restored.items == original.items
        assert restored.metadata == original.metadata

    def test_from_dict_missing_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required keys"):
            GuidanceBlock.from_dict({"label": "x", "items": []})

    def test_from_dict_missing_label_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required keys"):
            GuidanceBlock.from_dict({"kind": "inventory", "items": []})

    def test_from_dict_invalid_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid BlockKind"):
            GuidanceBlock.from_dict({"kind": "bogus", "label": "x"})

    def test_from_dict_items_wrong_type_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a list"):
            GuidanceBlock.from_dict({"kind": "inventory", "label": "x", "items": "not a list"})

    def test_from_dict_metadata_wrong_type_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a dict"):
            GuidanceBlock.from_dict(
                {"kind": "inventory", "label": "x", "items": [], "metadata": "bad"}
            )

    def test_from_dict_defaults(self) -> None:
        block = GuidanceBlock.from_dict({"kind": "factors", "label": "Factors"})
        assert block.items == []
        assert block.metadata == {}


class TestRunGuidance:
    """RunGuidance serialization, fail-soft, and round-trip."""

    def _make_guidance(self) -> RunGuidance:
        return RunGuidance(
            blocks=[
                GuidanceBlock(
                    kind=BlockKind.INVENTORY,
                    label="Files",
                    items=["a.pdf"],
                ),
                GuidanceBlock(
                    kind=BlockKind.DEPLOYMENT_STEPS,
                    label="Deploy",
                    items=["step1", "step2"],
                    metadata={"env": "prod"},
                ),
            ],
            schema_version=1,
            generated_at="2026-03-21T00:00:00+00:00",
            module="ipi",
        )

    def test_to_dict(self) -> None:
        g = self._make_guidance()
        d = g.to_dict()
        assert d["schema_version"] == 1
        assert d["module"] == "ipi"
        assert d["generated_at"] == "2026-03-21T00:00:00+00:00"
        assert len(d["blocks"]) == 2

    def test_round_trip(self) -> None:
        original = self._make_guidance()
        restored = RunGuidance.from_dict(original.to_dict())
        assert len(restored.blocks) == len(original.blocks)
        assert restored.schema_version == original.schema_version
        assert restored.generated_at == original.generated_at
        assert restored.module == original.module
        for orig_b, rest_b in zip(original.blocks, restored.blocks, strict=True):
            assert rest_b.kind == orig_b.kind
            assert rest_b.label == orig_b.label
            assert rest_b.items == orig_b.items
            assert rest_b.metadata == orig_b.metadata

    def test_json_round_trip(self) -> None:
        """Full JSON serialize/deserialize round-trip."""
        original = self._make_guidance()
        json_str = json.dumps(original.to_dict())
        restored = RunGuidance.from_dict(json.loads(json_str))
        assert restored.module == original.module
        assert len(restored.blocks) == len(original.blocks)

    def test_fail_soft_unknown_schema_version(self) -> None:
        data = {
            "schema_version": 99,
            "generated_at": "2026-01-01T00:00:00",
            "module": "cxp",
            "blocks": [],
        }
        result = RunGuidance.from_dict(data)
        assert result.schema_version == 99
        assert result.module == "cxp"
        assert len(result.blocks) == 1
        assert "not supported" in result.blocks[0].items[0]

    def test_fail_soft_preserves_metadata(self) -> None:
        data = {"schema_version": 42, "generated_at": "ts", "module": "rxp"}
        result = RunGuidance.from_dict(data)
        assert result.generated_at == "ts"
        assert result.module == "rxp"

    def test_from_dict_blocks_wrong_type_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a list"):
            RunGuidance.from_dict({"schema_version": 1, "blocks": "bad"})

    def test_from_dict_defaults(self) -> None:
        g = RunGuidance.from_dict({})
        assert g.blocks == []
        assert g.schema_version == 1
        assert g.generated_at == ""
        assert g.module == ""

    def test_create_factory(self) -> None:
        blocks = [
            GuidanceBlock(kind=BlockKind.FACTORS, label="F", items=["f1"]),
        ]
        g = RunGuidance.create(blocks, module="ipi")
        assert g.module == "ipi"
        assert g.schema_version == 1
        assert g.generated_at  # non-empty
        assert len(g.blocks) == 1


class TestRunGuidanceDBPersistence:
    """Test guidance persistence via DB helpers."""

    def test_save_and_retrieve_guidance(self, tmp_path: Path) -> None:
        from q_ai.core.db import (
            create_run,
            get_connection,
            get_run_guidance,
            save_run_guidance,
        )

        db_path = tmp_path / "test.db"
        guidance = RunGuidance.create(
            blocks=[
                GuidanceBlock(kind=BlockKind.INVENTORY, label="Files", items=["a.pdf"]),
            ],
            module="ipi",
        )
        guidance_json = json.dumps(guidance.to_dict())

        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="ipi")
            save_run_guidance(conn, run_id, guidance_json)

        with get_connection(db_path) as conn:
            result = get_run_guidance(conn, run_id)
            assert result is not None
            restored = RunGuidance.from_dict(json.loads(result))
            assert restored.module == "ipi"
            assert len(restored.blocks) == 1

    def test_get_guidance_returns_none_when_unset(self, tmp_path: Path) -> None:
        from q_ai.core.db import create_run, get_connection, get_run_guidance

        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="ipi")

        with get_connection(db_path) as conn:
            assert get_run_guidance(conn, run_id) is None

    def test_save_guidance_raises_on_missing_run(self, tmp_path: Path) -> None:
        from q_ai.core.db import get_connection, save_run_guidance

        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn, pytest.raises(ValueError, match="not found"):
            save_run_guidance(conn, "nonexistent", '{"blocks": []}')

    def test_get_guidance_raises_on_missing_run(self, tmp_path: Path) -> None:
        from q_ai.core.db import get_connection, get_run_guidance

        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn, pytest.raises(ValueError, match="not found"):
            get_run_guidance(conn, "nonexistent")

    def test_guidance_in_run_model(self, tmp_path: Path) -> None:
        from q_ai.core.db import create_run, get_connection, get_run, save_run_guidance

        db_path = tmp_path / "test.db"
        guidance_json = json.dumps({"schema_version": 1, "blocks": [], "module": "cxp"})

        with get_connection(db_path) as conn:
            run_id = create_run(conn, module="cxp")
            save_run_guidance(conn, run_id, guidance_json)

        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
            assert run is not None
            assert run.guidance is not None
            parsed = json.loads(run.guidance)
            assert parsed["module"] == "cxp"


class TestSchemaV10Migration:
    """Test V10 migration adds guidance column to runs table."""

    def test_migration_adds_guidance_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        try:
            from q_ai.core.schema import migrate

            migrate(conn)
            conn.commit()
            columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            assert "guidance" in columns
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 13
        finally:
            conn.close()
