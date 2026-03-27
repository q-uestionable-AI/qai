"""Tests for chain execution engine data models."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from q_ai.chain.executor_models import StepOutput, TargetConfig
from q_ai.chain.models import ChainResult, ChainStep, StepStatus


class TestStepOutput:
    """Tests for StepOutput dataclass."""

    def test_construction_minimal(self) -> None:
        """StepOutput can be created with required fields only."""
        output = StepOutput(
            step_id="scan-injection",
            module="audit",
            technique="injection",
            success=True,
            status=StepStatus.SUCCESS,
        )
        assert output.step_id == "scan-injection"
        assert output.module == "audit"
        assert output.technique == "injection"
        assert output.success is True
        assert output.status == StepStatus.SUCCESS
        assert output.scan_result is None
        assert output.campaign is None
        assert output.artifacts == {}
        assert output.error is None
        assert output.finished_at is None
        assert isinstance(output.started_at, datetime)

    def test_construction_full(self) -> None:
        """StepOutput can be created with all fields populated."""
        now = datetime.now(UTC)
        output = StepOutput(
            step_id="inject-poison",
            module="inject",
            technique="description_poisoning",
            success=False,
            status=StepStatus.FAILED,
            campaign={"id": "camp-1"},
            artifacts={"best_outcome": "full_compliance"},
            started_at=now,
            finished_at=now,
            error="Connection refused",
        )
        assert output.campaign == {"id": "camp-1"}
        assert output.artifacts["best_outcome"] == "full_compliance"
        assert output.error == "Connection refused"
        assert output.finished_at == now

    def test_to_dict_serialization(self) -> None:
        """to_dict produces a JSON-compatible dictionary."""
        now = datetime.now(UTC)
        output = StepOutput(
            step_id="scan-injection",
            module="audit",
            technique="injection",
            success=True,
            status=StepStatus.SUCCESS,
            artifacts={"vulnerable_tool": "exec_cmd"},
            started_at=now,
            finished_at=now,
        )
        d = output.to_dict()
        assert d["step_id"] == "scan-injection"
        assert d["module"] == "audit"
        assert d["success"] is True
        assert d["status"] == "success"
        assert d["artifacts"] == {"vulnerable_tool": "exec_cmd"}
        assert isinstance(d["started_at"], str)
        assert isinstance(d["finished_at"], str)
        assert d["error"] is None

    def test_to_dict_none_finished_at(self) -> None:
        """to_dict handles None finished_at."""
        output = StepOutput(
            step_id="s1",
            module="audit",
            technique="injection",
            success=True,
            status=StepStatus.SUCCESS,
        )
        d = output.to_dict()
        assert d["finished_at"] is None


class TestTargetConfig:
    """Tests for TargetConfig dataclass."""

    def test_defaults(self) -> None:
        """TargetConfig has sensible defaults."""
        config = TargetConfig()
        assert config.audit_transport is None
        assert config.audit_command is None
        assert config.audit_url is None
        assert config.inject_model is None

    def test_from_yaml_full(self, tmp_path: Path) -> None:
        """from_yaml loads a complete config file."""
        yaml_content = {
            "audit": {
                "transport": "stdio",
                "command": "python my_server.py",
            },
            "inject": {
                "model": "claude-haiku-4-5-20251001",
            },
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.audit_transport == "stdio"
        assert config.audit_command == ["python", "my_server.py"]
        assert config.inject_model == "claude-haiku-4-5-20251001"

    def test_from_yaml_list_command(self, tmp_path: Path) -> None:
        """from_yaml passes list-format command through unchanged."""
        yaml_content = {
            "audit": {
                "transport": "stdio",
                "command": ["python", "my_server.py"],
            },
            "inject": {
                "model": "test-model",
            },
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.audit_command == ["python", "my_server.py"]

    def test_from_yaml_audit_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_yaml handles missing inject section."""
        monkeypatch.delenv("QAI_MODEL", raising=False)
        yaml_content = {
            "audit": {
                "transport": "sse",
                "url": "http://localhost:8080/sse",
            },
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.audit_transport == "sse"
        assert config.audit_url == "http://localhost:8080/sse"
        assert config.inject_model is None

    def test_from_yaml_inject_only(self, tmp_path: Path) -> None:
        """from_yaml handles missing audit section."""
        yaml_content = {
            "inject": {
                "model": "claude-haiku-4-5-20251001",
            },
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.audit_transport is None
        assert config.inject_model == "claude-haiku-4-5-20251001"

    def test_from_yaml_empty_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_yaml handles an empty YAML file gracefully."""
        monkeypatch.delenv("QAI_MODEL", raising=False)
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        config = TargetConfig.from_yaml(config_file)
        assert config.audit_transport is None
        assert config.inject_model is None

    def test_from_yaml_file_not_found(self, tmp_path: Path) -> None:
        """from_yaml raises FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            TargetConfig.from_yaml(tmp_path / "nonexistent.yaml")

    def test_from_yaml_malformed(self, tmp_path: Path) -> None:
        """from_yaml raises ValueError for malformed YAML."""
        config_file = tmp_path / "bad.yaml"
        config_file.write_text(": : : not valid yaml [[[")

        with pytest.raises(ValueError, match="Failed to parse"):
            TargetConfig.from_yaml(config_file)

    def test_with_overrides(self) -> None:
        """with_overrides returns a new config with specified fields replaced."""
        config = TargetConfig(
            audit_transport="stdio",
            audit_command=["python", "server.py"],
        )
        new_config = config.with_overrides(
            audit_command=["python", "other.py"],
            inject_model="claude-haiku-4-5-20251001",
        )
        assert config.audit_command == ["python", "server.py"]
        assert config.inject_model is None
        assert new_config.audit_transport == "stdio"
        assert new_config.audit_command == ["python", "other.py"]
        assert new_config.inject_model == "claude-haiku-4-5-20251001"

    def test_from_yaml_env_var_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """from_yaml falls back to QAI_MODEL env var when inject.model is absent."""
        monkeypatch.setenv("QAI_MODEL", "claude-sonnet-4-6")
        yaml_content = {"audit": {"transport": "stdio"}}
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.inject_model == "claude-sonnet-4-6"

    def test_from_yaml_yaml_overrides_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML inject.model takes priority over QAI_MODEL env var."""
        monkeypatch.setenv("QAI_MODEL", "env-model")
        yaml_content = {"inject": {"model": "yaml-model"}}
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.inject_model == "yaml-model"

    def test_with_overrides_none_ignored(self) -> None:
        """with_overrides only replaces non-None kwargs."""
        config = TargetConfig(audit_transport="stdio")
        new_config = config.with_overrides(audit_transport=None)
        assert new_config.audit_transport == "stdio"

    def test_from_yaml_ipi_section(self, tmp_path: Path) -> None:
        """from_yaml reads ipi section."""
        yaml_content = {
            "ipi": {
                "callback_url": "http://localhost:8080",
                "output_dir": "/tmp/ipi",
                "format": "pdf",
            },
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.ipi_callback_url == "http://localhost:8080"
        assert config.ipi_output_dir == "/tmp/ipi"
        assert config.ipi_format == "pdf"

    def test_from_yaml_cxp_section(self, tmp_path: Path) -> None:
        """from_yaml reads cxp section with rule_ids list."""
        yaml_content = {
            "cxp": {
                "format_id": "cursorrules",
                "output_dir": "/tmp/cxp",
                "rule_ids": ["rule-1", "rule-2"],
            },
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.cxp_format_id == "cursorrules"
        assert config.cxp_output_dir == "/tmp/cxp"
        assert config.cxp_rule_ids == ["rule-1", "rule-2"]

    def test_from_yaml_rxp_section(self, tmp_path: Path) -> None:
        """from_yaml reads rxp section with top_k integer coercion."""
        yaml_content = {
            "rxp": {
                "model_id": "minilm-l6",
                "profile_id": "default",
                "top_k": 10,
            },
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.rxp_model_id == "minilm-l6"
        assert config.rxp_profile_id == "default"
        assert config.rxp_top_k == 10

    def test_from_yaml_all_sections(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_yaml reads all seven sections."""
        monkeypatch.delenv("QAI_MODEL", raising=False)
        yaml_content = {
            "audit": {"transport": "sse", "url": "http://localhost:8080/sse"},
            "inject": {"model": "test-model"},
            "ipi": {"callback_url": "http://cb", "format": "html"},
            "cxp": {"format_id": "claude-md"},
            "rxp": {"model_id": "bge-small", "top_k": 3},
        }
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml.dump(yaml_content))

        config = TargetConfig.from_yaml(config_file)
        assert config.audit_transport == "sse"
        assert config.inject_model == "test-model"
        assert config.ipi_callback_url == "http://cb"
        assert config.cxp_format_id == "claude-md"
        assert config.rxp_model_id == "bge-small"
        assert config.rxp_top_k == 3

    def test_with_overrides_new_fields(self) -> None:
        """with_overrides works for IPI/CXP/RXP fields."""
        config = TargetConfig(ipi_format="pdf")
        new_config = config.with_overrides(
            ipi_format="html",
            cxp_format_id="cursorrules",
            rxp_model_id="minilm-l6",
        )
        assert new_config.ipi_format == "html"
        assert new_config.cxp_format_id == "cursorrules"
        assert new_config.rxp_model_id == "minilm-l6"
        # Original unchanged
        assert config.ipi_format == "pdf"
        assert config.cxp_format_id is None

    def test_from_yaml_invalid_section_type(self, tmp_path: Path) -> None:
        """from_yaml raises ValueError for non-mapping sections."""
        yaml_content = "ipi: not_a_dict\n"
        config_file = tmp_path / "chain-targets.yaml"
        config_file.write_text(yaml_content)

        with pytest.raises(ValueError, match=r"ipi.*mapping"):
            TargetConfig.from_yaml(config_file)


class TestChainResult:
    """Tests for enhanced ChainResult."""

    def test_success_property_with_step_outputs(self) -> None:
        """success is True when last step_output succeeded."""
        result = ChainResult(
            chain_id="test-chain",
            step_outputs=[
                StepOutput(
                    step_id="s1",
                    module="audit",
                    technique="injection",
                    success=True,
                    status=StepStatus.SUCCESS,
                ),
            ],
        )
        assert result.success is True

    def test_success_property_last_step_failed(self) -> None:
        """success is False when last step_output failed."""
        result = ChainResult(
            chain_id="test-chain",
            step_outputs=[
                StepOutput(
                    step_id="s1",
                    module="audit",
                    technique="injection",
                    success=True,
                    status=StepStatus.SUCCESS,
                ),
                StepOutput(
                    step_id="s2",
                    module="inject",
                    technique="description_poisoning",
                    success=False,
                    status=StepStatus.FAILED,
                ),
            ],
        )
        assert result.success is False

    def test_success_property_no_step_outputs(self) -> None:
        """success is False when step_outputs is empty."""
        result = ChainResult(chain_id="test-chain")
        assert result.success is False

    def test_to_dict(self) -> None:
        """to_dict produces a complete JSON-compatible dict."""
        now = datetime.now(UTC)
        result = ChainResult(
            chain_id="test-chain",
            chain_name="Test Chain",
            target_config={"audit": {"transport": "stdio"}},
            step_outputs=[
                StepOutput(
                    step_id="s1",
                    module="audit",
                    technique="injection",
                    success=True,
                    status=StepStatus.SUCCESS,
                    started_at=now,
                    finished_at=now,
                ),
            ],
            trust_boundaries_crossed=["user\u2192agent"],
            started_at=now,
            finished_at=now,
            dry_run=False,
        )
        d = result.to_dict()
        assert d["chain_id"] == "test-chain"
        assert d["chain_name"] == "Test Chain"
        assert d["dry_run"] is False
        assert d["success"] is True
        assert len(d["step_outputs"]) == 1
        assert d["step_outputs"][0]["step_id"] == "s1"

    def test_to_dict_includes_prompt(self) -> None:
        """to_dict includes an interpret prompt."""
        result = ChainResult(
            chain_id="test-chain",
            chain_name="Test Chain",
            step_outputs=[
                StepOutput(
                    step_id="s1",
                    module="audit",
                    technique="injection",
                    success=True,
                    status=StepStatus.SUCCESS,
                ),
            ],
            trust_boundaries_crossed=["client-to-server"],
        )
        d = result.to_dict()
        assert "prompt" in d
        assert "Test Chain" in d["prompt"]
        assert "1-step" in d["prompt"]
        assert "injection" in d["prompt"]

    def test_interpret_prompt_empty_chain(self) -> None:
        """Interpret prompt handles chain with no step outputs."""
        result = ChainResult(chain_id="empty", chain_name="Empty Chain")
        prompt = result._build_interpret_prompt()
        assert "Empty Chain" in prompt
        assert "no step outputs" in prompt

    def test_interpret_prompt_mixed_results(self) -> None:
        """Interpret prompt summarizes mixed success/failure."""
        result = ChainResult(
            chain_id="mixed",
            chain_name="Mixed Chain",
            step_outputs=[
                StepOutput(
                    step_id="s1",
                    module="audit",
                    technique="injection",
                    success=True,
                    status=StepStatus.SUCCESS,
                ),
                StepOutput(
                    step_id="s2",
                    module="inject",
                    technique="description_poisoning",
                    success=False,
                    status=StepStatus.FAILED,
                ),
            ],
            trust_boundaries_crossed=["client-to-server", "agent-to-tool"],
        )
        prompt = result._build_interpret_prompt()
        assert "2-step" in prompt
        assert "1 audit" in prompt
        assert "1 inject" in prompt
        assert "1/2 steps succeeded" in prompt
        assert "1 failed" in prompt
        assert "client-to-server \u2192 agent-to-tool" in prompt

    def test_interpret_prompt_dry_run_with_steps(self) -> None:
        """Interpret prompt handles dry-run with tracer steps but no step_outputs."""
        result = ChainResult(
            chain_id="dry-run",
            chain_name="Dry Run Chain",
            steps=[
                ChainStep(
                    id="s1",
                    name="Scan",
                    module="audit",
                    technique="injection",
                ),
                ChainStep(
                    id="s2",
                    name="Inject",
                    module="inject",
                    technique="description_poisoning",
                ),
            ],
        )
        prompt = result._build_interpret_prompt()
        assert "2-step" in prompt
        assert "dry-run" in prompt.lower()
        assert "injection" in prompt
        assert "description_poisoning" in prompt
