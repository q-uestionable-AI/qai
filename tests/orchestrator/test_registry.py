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
        """All default workflows have executor=None (not yet implemented)."""
        for wf in list_workflows():
            assert wf.executor is None, f"{wf.id} should have executor=None"

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
