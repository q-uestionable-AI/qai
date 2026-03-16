"""Tests for the workflow registry."""

from __future__ import annotations

from q_ai.orchestrator.registry import (
    WorkflowEntry,
    get_workflow,
    list_workflows,
    register_workflow,
)


class TestRegistry:
    """Workflow registry tests."""

    def test_list_workflows_returns_all(self) -> None:
        """All 6 default workflows are registered."""
        workflows = list_workflows()
        assert len(workflows) == 6

    def test_get_workflow_by_id(self) -> None:
        """Can look up a workflow by ID."""
        wf = get_workflow("assess")
        assert wf is not None
        assert wf.name == "Assess an MCP Server"

    def test_get_workflow_unknown_returns_none(self) -> None:
        """Unknown workflow ID returns None."""
        assert get_workflow("nonexistent") is None

    def test_assess_has_correct_modules(self) -> None:
        """Assess workflow uses audit, proxy, inject modules."""
        wf = get_workflow("assess")
        assert wf is not None
        assert wf.modules == ["audit", "proxy", "inject"]

    def test_unimplemented_workflows_have_none_executor(self) -> None:
        """Unimplemented workflows have executor=None."""
        implemented = {"assess", "test_docs", "test_assistant", "trace_path", "blast_radius"}
        for wf in list_workflows():
            if wf.id in implemented:
                continue
            assert wf.executor is None, f"{wf.id} should have executor=None"

    def test_assess_has_executor(self) -> None:
        """Assess workflow has a non-None executor after registration."""
        wf = get_workflow("assess")
        assert wf is not None
        assert wf.executor is not None

    def test_phase7_workflows_have_executors(self) -> None:
        """All four Phase 7 workflow executors are wired."""
        for wf_id in ("test_docs", "test_assistant", "trace_path", "blast_radius"):
            entry = get_workflow(wf_id)
            assert entry is not None
            assert entry.executor is not None, f"Executor not wired for {wf_id}"

    def test_register_workflow_overwrites(self) -> None:
        """Registering a workflow with an existing ID overwrites it."""
        original = get_workflow("assess")
        assert original is not None

        new_entry = WorkflowEntry(
            id="assess",
            name="Updated Assess",
            description="Updated",
            modules=["audit"],
        )
        register_workflow(new_entry)
        updated = get_workflow("assess")
        assert updated is not None
        assert updated.name == "Updated Assess"

        # Restore original
        register_workflow(original)

    def test_trace_path_is_fail_fast(self) -> None:
        """Trace an Attack Path uses fail_fast failure mode."""
        wf = get_workflow("trace_path")
        assert wf is not None
        assert wf.failure_mode == "fail_fast"

    def test_test_docs_has_rxp_as_optional_module(self) -> None:
        """test_docs workflow has rxp in optional_modules."""
        wf = get_workflow("test_docs")
        assert wf is not None
        assert "rxp" in wf.optional_modules

    def test_optional_modules_field_defaults_empty(self) -> None:
        """Workflows without explicit optional_modules have empty list."""
        for wf in list_workflows():
            if wf.id == "test_docs":
                continue
            assert wf.optional_modules == [], f"{wf.id} should have empty optional_modules"

    def test_requires_provider_default_true(self) -> None:
        """All workflows except test_assistant have requires_provider=True."""
        for wf in list_workflows():
            if wf.id == "test_assistant":
                continue
            assert wf.requires_provider is True, f"{wf.id} should have requires_provider=True"

    def test_test_assistant_requires_provider_false(self) -> None:
        """test_assistant does not require a provider."""
        wf = get_workflow("test_assistant")
        assert wf is not None
        assert wf.requires_provider is False
