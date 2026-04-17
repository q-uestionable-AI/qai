"""Tests for the workflow service: validators and config builders."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.services import workflow_service as svc
from q_ai.services.workflow_service import WorkflowValidationError


def _base_assess_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "transport": "stdio",
        "command": "python -m srv",
        "model": "openai/gpt-4",
        "rounds": 3,
    }
    body.update(overrides)
    return body


class TestValidateTransportAndModel:
    def test_stdio_with_command_passes(self) -> None:
        svc.validate_transport_and_model(_base_assess_body())

    def test_sse_with_url_passes(self) -> None:
        body = _base_assess_body(transport="sse", command="", url="http://localhost:8000")
        svc.validate_transport_and_model(body)

    def test_invalid_transport_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError, match="Invalid transport"):
            svc.validate_transport_and_model(_base_assess_body(transport="foo"))

    def test_stdio_missing_command(self) -> None:
        with pytest.raises(WorkflowValidationError, match="command is required"):
            svc.validate_transport_and_model(_base_assess_body(command=""))

    def test_sse_missing_url(self) -> None:
        body = _base_assess_body(transport="sse", command="", url="")
        with pytest.raises(WorkflowValidationError, match="url is required"):
            svc.validate_transport_and_model(body)

    def test_streamable_http_missing_url(self) -> None:
        body = _base_assess_body(transport="streamable-http", command="", url="")
        with pytest.raises(WorkflowValidationError, match="url is required"):
            svc.validate_transport_and_model(body)

    def test_model_missing_slash(self) -> None:
        with pytest.raises(WorkflowValidationError, match="provider/model"):
            svc.validate_transport_and_model(_base_assess_body(model="gpt4"))

    def test_model_empty(self) -> None:
        with pytest.raises(WorkflowValidationError, match="provider/model"):
            svc.validate_transport_and_model(_base_assess_body(model=""))


class TestValidateTransportAndCommand:
    def test_stdio_with_command_passes(self) -> None:
        svc.validate_transport_and_command({"transport": "stdio", "command": "node srv.js"})

    def test_sse_with_url_passes(self) -> None:
        svc.validate_transport_and_command({"transport": "sse", "url": "http://localhost"})

    def test_rejects_invalid_transport(self) -> None:
        with pytest.raises(WorkflowValidationError, match="Invalid transport"):
            svc.validate_transport_and_command({"transport": "weird"})

    def test_rejects_non_string_field(self) -> None:
        with pytest.raises(WorkflowValidationError, match="must be a string"):
            svc.validate_transport_and_command({"transport": ["stdio"]})


class TestValidateCampaignFields:
    def test_valid_passes(self) -> None:
        svc.validate_campaign_fields({"model": "openai/gpt-4", "rounds": 5})

    def test_model_format(self) -> None:
        with pytest.raises(WorkflowValidationError, match="provider/model"):
            svc.validate_campaign_fields({"model": "gpt-4", "rounds": 1})

    def test_rounds_must_be_int(self) -> None:
        with pytest.raises(WorkflowValidationError, match="rounds must be an integer"):
            svc.validate_campaign_fields({"model": "a/b", "rounds": "3"})

    def test_rounds_bool_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError, match="rounds must be an integer"):
            svc.validate_campaign_fields({"model": "a/b", "rounds": True})

    def test_rounds_out_of_range(self) -> None:
        with pytest.raises(WorkflowValidationError, match="between 1 and 10"):
            svc.validate_campaign_fields({"model": "a/b", "rounds": 11})


class TestBuildAssessConfig:
    def test_happy_path(self) -> None:
        cfg = svc.build_assess_config(_base_assess_body(rxp_enabled=True), "tgt-1")
        assert cfg["target_id"] == "tgt-1"
        assert cfg["transport"] == "stdio"
        assert cfg["command"] == "python -m srv"
        assert cfg["url"] is None
        assert cfg["rxp_enabled"] is True
        assert cfg["inject"]["model"] == "openai/gpt-4"
        assert cfg["inject"]["rounds"] == 3
        assert cfg["audit"]["checks"] is None
        assert cfg["proxy"]["intercept"] is False

    def test_techniques_list_normalized(self) -> None:
        cfg = svc.build_assess_config(_base_assess_body(techniques=["a", "b"]), "tgt")
        assert cfg["inject"]["techniques"] == ["a", "b"]

    def test_payloads_list_normalized(self) -> None:
        cfg = svc.build_assess_config(_base_assess_body(payload_names=["x"]), "tgt")
        assert cfg["inject"]["payloads"] == ["x"]

    def test_checks_list_normalized(self) -> None:
        cfg = svc.build_assess_config(_base_assess_body(checks=["c1"]), "tgt")
        assert cfg["audit"]["checks"] == ["c1"]

    def test_validation_error_propagates(self) -> None:
        with pytest.raises(WorkflowValidationError):
            svc.build_assess_config(_base_assess_body(transport="bad"), "tgt")

    def test_rounds_must_be_int(self) -> None:
        with pytest.raises(WorkflowValidationError, match="rounds must be an integer"):
            svc.build_assess_config(_base_assess_body(rounds="3"), "tgt")

    def test_rounds_bool_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError, match="rounds must be an integer"):
            svc.build_assess_config(_base_assess_body(rounds=True), "tgt")

    def test_rounds_out_of_range(self) -> None:
        with pytest.raises(WorkflowValidationError, match="between 1 and 10"):
            svc.build_assess_config(_base_assess_body(rounds=99), "tgt")


class TestBuildTestDocsConfig:
    def test_happy_path(self) -> None:
        cfg = svc.build_test_docs_config({"callback_url": "http://cb/", "format": "pdf"}, "tgt")
        assert cfg["callback_url"] == "http://cb/"
        assert cfg["format"] == "pdf"
        assert cfg["base_name"] == "report"
        assert cfg["rxp"]["target_id"] == "tgt"

    def test_callback_required(self) -> None:
        with pytest.raises(WorkflowValidationError, match="callback_url is required"):
            svc.build_test_docs_config({}, "tgt")

    def test_missing_template_defaults_to_generic(self) -> None:
        """No ``template_id`` in body -> cfg carries the GENERIC string."""
        cfg = svc.build_test_docs_config({"callback_url": "http://cb/", "format": "pdf"}, "tgt")
        assert cfg["template_id"] == "generic"

    def test_valid_named_template(self) -> None:
        """A known ``template_id`` is preserved in the returned config."""
        cfg = svc.build_test_docs_config(
            {"callback_url": "http://cb/", "format": "pdf", "template_id": "whois"},
            "tgt",
        )
        assert cfg["template_id"] == "whois"
        assert cfg["format"] == "pdf"

    def test_unknown_template_raises(self) -> None:
        """Unknown template_id -> WorkflowValidationError with the value."""
        with pytest.raises(WorkflowValidationError, match="not-real"):
            svc.build_test_docs_config(
                {"callback_url": "http://cb/", "format": "pdf", "template_id": "not-real"},
                "tgt",
            )

    def test_incompatible_format_template_raises(self) -> None:
        """Incompatible format+template -> WorkflowValidationError naming both."""
        with pytest.raises(WorkflowValidationError, match="whois"):
            svc.build_test_docs_config(
                {"callback_url": "http://cb/", "format": "html", "template_id": "whois"},
                "tgt",
            )

    def test_unknown_format_raises(self) -> None:
        """Unknown format value -> WorkflowValidationError with the value."""
        with pytest.raises(WorkflowValidationError, match="xyz"):
            svc.build_test_docs_config(
                {"callback_url": "http://cb/", "format": "xyz"},
                "tgt",
            )


class TestBuildTestAssistantConfig:
    def test_happy_path(self) -> None:
        cfg = svc.build_test_assistant_config(
            {"format_id": "cursor", "rule_ids": ["r1"], "repo_name": "myrepo"},
            "tgt",
        )
        assert cfg["format_id"] == "cursor"
        assert cfg["rule_ids"] == ["r1"]
        assert cfg["repo_name"] == "myrepo"

    def test_format_id_required(self) -> None:
        with pytest.raises(WorkflowValidationError, match="format_id is required"):
            svc.build_test_assistant_config({}, "tgt")


class TestBuildBlastRadiusConfig:
    def test_chain_execution_id_required(self, tmp_path: Any) -> None:
        with pytest.raises(WorkflowValidationError, match="chain_execution_id is required"):
            svc.build_blast_radius_config({}, None)

    def test_unknown_execution(self, tmp_path: Any) -> None:
        # No DB entry exists; ensure not-found path raises validation error
        import sqlite3

        from q_ai.core.schema import migrate

        db_path = tmp_path / "x.db"
        with sqlite3.connect(str(db_path)) as c:
            c.row_factory = sqlite3.Row
            migrate(c)
        with pytest.raises(WorkflowValidationError, match="Chain execution not found"):
            svc.build_blast_radius_config({"chain_execution_id": "missing"}, db_path)


class TestBuildGenerateReportConfig:
    def test_target_id_required(self) -> None:
        with pytest.raises(WorkflowValidationError, match="target_id is required"):
            svc.build_generate_report_config({}, None)

    def test_target_not_found(self, tmp_path: Any) -> None:
        import sqlite3

        from q_ai.core.schema import migrate

        db_path = tmp_path / "x.db"
        with sqlite3.connect(str(db_path)) as c:
            c.row_factory = sqlite3.Row
            migrate(c)
        with pytest.raises(WorkflowValidationError, match="Target not found"):
            svc.build_generate_report_config({"target_id": "missing"}, db_path)


class TestBuildQuickActionConfig:
    def test_scan_includes_checks(self) -> None:
        cfg = svc.build_quick_action_config(
            "scan",
            {"transport": "stdio", "command": "x", "checks": ["a"]},
            "tgt",
        )
        assert cfg["checks"] == ["a"]
        assert "model" not in cfg

    def test_campaign_includes_model_rounds(self) -> None:
        cfg = svc.build_quick_action_config(
            "campaign",
            {
                "transport": "stdio",
                "command": "x",
                "model": "openai/gpt-4",
                "rounds": 2,
                "techniques": ["t1"],
            },
            "tgt",
        )
        assert cfg["model"] == "openai/gpt-4"
        assert cfg["rounds"] == 2
        assert cfg["techniques"] == ["t1"]

    def test_intercept_minimal(self) -> None:
        cfg = svc.build_quick_action_config(
            "intercept", {"transport": "sse", "url": "http://x"}, "tgt"
        )
        assert cfg["transport"] == "sse"
        assert cfg["url"] == "http://x"
        assert cfg["command"] is None


class TestBuildWorkflowConfig:
    def test_unknown_workflow(self) -> None:
        with pytest.raises(WorkflowValidationError, match="No builder for"):
            svc.build_workflow_config("unknown", {}, None)

    def test_assess_dispatch(self) -> None:
        cfg = svc.build_workflow_config("assess", _base_assess_body(), None)
        assert cfg["inject"]["model"] == "openai/gpt-4"

    def test_test_docs_dispatch(self) -> None:
        cfg = svc.build_workflow_config("test_docs", {"callback_url": "http://cb"}, None)
        assert cfg["callback_url"] == "http://cb"

    def test_validation_error_propagates(self) -> None:
        with pytest.raises(WorkflowValidationError):
            svc.build_workflow_config("assess", _base_assess_body(transport="x"), None)


class TestValidateProviderModel:
    @pytest.mark.asyncio
    async def test_provider_required(self) -> None:
        with pytest.raises(WorkflowValidationError, match="provider is required"):
            await svc.validate_provider_model({"model": ""}, None)

    @pytest.mark.asyncio
    async def test_unknown_provider(self) -> None:
        with pytest.raises(WorkflowValidationError, match="Unknown provider"):
            await svc.validate_provider_model({"provider": "nope", "model": "nope/x"}, None)

    @pytest.mark.asyncio
    async def test_unconfigured_cloud_provider(self) -> None:
        # openai is CLOUD; no credential -> "not configured"
        with (
            patch.object(svc, "_read_provider_config", return_value=(None, "")),
            pytest.raises(WorkflowValidationError, match="not configured"),
        ):
            await svc.validate_provider_model({"provider": "openai", "model": "openai/gpt-4"}, None)

    @pytest.mark.asyncio
    async def test_no_model_selected(self) -> None:
        with (
            patch.object(svc, "_read_provider_config", return_value=("apikey", "")),
            pytest.raises(WorkflowValidationError, match="No model selected"),
        ):
            await svc.validate_provider_model({"provider": "openai", "model": ""}, None)

    @pytest.mark.asyncio
    async def test_local_unreachable(self) -> None:
        # ollama is LOCAL; simulate unreachable fetch_models
        from q_ai.core.providers import ModelListResponse

        with (
            patch.object(svc, "_read_provider_config", return_value=(None, "http://localhost")),
            patch.object(
                svc,
                "fetch_models",
                new=AsyncMock(
                    return_value=ModelListResponse(
                        models=[], supports_custom=False, error="connection refused"
                    )
                ),
            ),
            pytest.raises(WorkflowValidationError, match="connection refused"),
        ):
            await svc.validate_provider_model({"provider": "ollama", "model": "ollama/llama"}, None)

    @pytest.mark.asyncio
    async def test_local_reachable_passes(self) -> None:
        from q_ai.core.providers import ModelListResponse

        with (
            patch.object(svc, "_read_provider_config", return_value=(None, "http://localhost")),
            patch.object(
                svc,
                "fetch_models",
                new=AsyncMock(
                    return_value=ModelListResponse(models=[], supports_custom=False, error=None)
                ),
            ),
        ):
            await svc.validate_provider_model({"provider": "ollama", "model": "ollama/llama"}, None)

    @pytest.mark.asyncio
    async def test_provider_inferred_from_model(self) -> None:
        # When provider is omitted but model is "provider/model", infer it.
        # openai is CLOUD so fetch_models is not called; only configured-credential
        # path is exercised.
        with patch.object(svc, "_read_provider_config", return_value=("apikey", "")):
            await svc.validate_provider_model({"model": "openai/gpt-4"}, None)


class TestWorkflowValidationError:
    def test_carries_detail(self) -> None:
        exc = WorkflowValidationError("bad input")
        assert exc.detail == "bad input"
        assert str(exc) == "bad input"
