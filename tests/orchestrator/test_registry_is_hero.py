"""Tests for the is_hero field on WorkflowEntry."""

from __future__ import annotations

import pytest

from q_ai.orchestrator.registry import (
    WorkflowEntry,
    get_workflow,
    list_workflows,
    register_workflow,
)


class TestIsHeroField:
    """WorkflowEntry.is_hero field tests."""

    def test_is_hero_defaults_false(self) -> None:
        """is_hero defaults to False for new WorkflowEntry instances."""
        entry = WorkflowEntry(id="x", name="X", description="X")
        assert entry.is_hero is False

    def test_assess_has_is_hero_true(self) -> None:
        """The assess workflow is the hero card."""
        wf = get_workflow("assess")
        assert wf is not None
        assert wf.is_hero is True

    def test_only_one_hero(self) -> None:
        """Exactly one registered workflow has is_hero=True."""
        heroes = [wf for wf in list_workflows() if wf.is_hero]
        assert len(heroes) == 1
        assert heroes[0].id == "assess"

    def test_non_hero_workflows(self) -> None:
        """All non-assess workflows have is_hero=False."""
        for wf in list_workflows():
            if wf.id == "assess":
                continue
            assert wf.is_hero is False, f"{wf.id} should have is_hero=False"

    def test_duplicate_hero_rejected(self) -> None:
        """Registering a second hero raises ValueError."""
        duplicate = WorkflowEntry(
            id="second_hero", name="Second Hero", description="X", is_hero=True
        )
        with pytest.raises(ValueError, match="already the hero"):
            register_workflow(duplicate)

    def test_re_registering_same_hero_allowed(self) -> None:
        """Re-registering the existing hero (same id) does not raise."""
        original = get_workflow("assess")
        assert original is not None
        updated = WorkflowEntry(
            id="assess",
            name=original.name,
            description=original.description,
            modules=list(original.modules),
            is_hero=True,
        )
        register_workflow(updated)
        # Restore original
        register_workflow(original)
