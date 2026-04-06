"""Tests for A/B skill variant system (Agent0 Rule A/B Variants steal)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from skills import (
    Skill,
    create_skill_variant,
    get_skill_variants,
    select_variant_for_task,
    record_variant_outcome,
    retire_losing_variants,
    MIN_VARIANT_USES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill(id, name="test", variant_of=None, utility_score=0.7, use_count=10,
           variant_wins=0, variant_losses=0):
    return Skill(
        id=id,
        name=name,
        description=f"desc-{id}",
        trigger_patterns=[f"trigger-{id}"],
        steps_template=[f"step-{id}"],
        source_loop_ids=[],
        created_at="2026-04-05T00:00:00Z",
        use_count=use_count,
        utility_score=utility_score,
        variant_of=variant_of,
        variant_wins=variant_wins,
        variant_losses=variant_losses,
    )


# ---------------------------------------------------------------------------
# create_skill_variant
# ---------------------------------------------------------------------------

class TestCreateSkillVariant:
    def test_sets_variant_of_to_parent_id(self):
        parent = _skill("parent-1")
        challenger = _skill("challenger-1")
        result = create_skill_variant(parent, challenger)
        assert result.variant_of == "parent-1"

    def test_resets_wins_and_losses(self):
        parent = _skill("p")
        challenger = _skill("c", variant_wins=5, variant_losses=3)
        result = create_skill_variant(parent, challenger)
        assert result.variant_wins == 0
        assert result.variant_losses == 0

    def test_returns_modified_challenger(self):
        parent = _skill("p")
        challenger = _skill("c")
        result = create_skill_variant(parent, challenger)
        assert result is challenger  # mutates in place and returns

    def test_parent_not_modified(self):
        parent = _skill("p")
        challenger = _skill("c")
        create_skill_variant(parent, challenger)
        assert parent.variant_of is None  # parent unchanged


# ---------------------------------------------------------------------------
# get_skill_variants
# ---------------------------------------------------------------------------

class TestGetSkillVariants:
    def test_returns_variants_for_parent(self):
        skills = [
            _skill("parent"),
            _skill("v1", variant_of="parent"),
            _skill("v2", variant_of="parent"),
            _skill("other"),
        ]
        variants = get_skill_variants("parent", skills)
        assert len(variants) == 2
        assert all(v.variant_of == "parent" for v in variants)

    def test_returns_empty_when_no_variants(self):
        skills = [_skill("parent"), _skill("other")]
        assert get_skill_variants("parent", skills) == []

    def test_does_not_return_parent_itself(self):
        skills = [_skill("parent"), _skill("v1", variant_of="parent")]
        variants = get_skill_variants("parent", skills)
        assert not any(v.id == "parent" for v in variants)


# ---------------------------------------------------------------------------
# select_variant_for_task
# ---------------------------------------------------------------------------

class TestSelectVariantForTask:
    def test_returns_parent_when_no_variants(self):
        parent = _skill("parent")
        skills = [parent, _skill("unrelated")]
        result = select_variant_for_task(parent, "task-001", skills)
        assert result.id == "parent"

    def test_routing_is_deterministic_per_task_id(self):
        parent = _skill("parent")
        v1 = _skill("v1", variant_of="parent")
        skills = [parent, v1]
        r1 = select_variant_for_task(parent, "task-abc", skills)
        r2 = select_variant_for_task(parent, "task-abc", skills)
        assert r1.id == r2.id  # same task → same variant

    def test_routing_can_select_parent_or_variant(self):
        """With many task IDs, both parent and variant should be selected."""
        parent = _skill("parent")
        v1 = _skill("v1", variant_of="parent")
        skills = [parent, v1]
        selected_ids = set()
        for i in range(100):
            r = select_variant_for_task(parent, f"task-{i:04d}", skills)
            selected_ids.add(r.id)
        # Both should appear over 100 trials
        assert "parent" in selected_ids
        assert "v1" in selected_ids

    def test_routing_with_multiple_variants(self):
        """All variants (including parent) should appear in routing."""
        parent = _skill("parent")
        v1 = _skill("v1", variant_of="parent")
        v2 = _skill("v2", variant_of="parent")
        skills = [parent, v1, v2]
        selected_ids = set()
        for i in range(300):
            r = select_variant_for_task(parent, f"task-{i:04d}", skills)
            selected_ids.add(r.id)
        assert "parent" in selected_ids
        assert "v1" in selected_ids
        assert "v2" in selected_ids

    def test_different_task_ids_can_pick_different_variants(self):
        parent = _skill("parent")
        v1 = _skill("v1", variant_of="parent")
        skills = [parent, v1]
        r1 = select_variant_for_task(parent, "task-000", skills)
        r2 = select_variant_for_task(parent, "task-999", skills)
        # Not required to be different, but selection should cover both over time
        # Just confirm it doesn't crash and returns a valid skill
        assert r1.id in {"parent", "v1"}
        assert r2.id in {"parent", "v1"}


# ---------------------------------------------------------------------------
# record_variant_outcome
# ---------------------------------------------------------------------------

class TestRecordVariantOutcome:
    def test_increments_wins_on_success(self):
        parent = _skill("parent")
        challenger = _skill("c1", variant_of="parent")
        skills = [parent, challenger]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                record_variant_outcome("c1", success=True)
                saved = mock_save.call_args[0][0]
                c_saved = next(s for s in saved if s.id == "c1")
                assert c_saved.variant_wins == 1
                assert c_saved.variant_losses == 0

    def test_increments_losses_on_failure(self):
        parent = _skill("parent")
        challenger = _skill("c1", variant_of="parent")
        skills = [parent, challenger]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                record_variant_outcome("c1", success=False)
                saved = mock_save.call_args[0][0]
                c_saved = next(s for s in saved if s.id == "c1")
                assert c_saved.variant_losses == 1
                assert c_saved.variant_wins == 0

    def test_noop_for_non_variant_skill(self):
        parent = _skill("parent")  # variant_of=None
        skills = [parent]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                record_variant_outcome("parent", success=True)
                mock_save.assert_not_called()  # no update for non-variant


# ---------------------------------------------------------------------------
# retire_losing_variants
# ---------------------------------------------------------------------------

class TestRetireLosingVariants:
    def _make_pair(self, parent_utility=0.6, challenger_wins=8, challenger_losses=2, use_count=10):
        parent = _skill("parent", utility_score=parent_utility, use_count=use_count)
        challenger = _skill(
            "challenger",
            variant_of="parent",
            variant_wins=challenger_wins,
            variant_losses=challenger_losses,
        )
        return parent, challenger

    def test_challenger_wins_promoted_parent_updated(self):
        """Challenger with better win-rate replaces parent's content."""
        parent = _skill("parent", utility_score=0.4, use_count=10)
        challenger = _skill("c1", variant_of="parent", variant_wins=8, variant_losses=2)
        challenger.description = "better description"
        challenger.steps_template = ["better step"]
        skills = [parent, challenger]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                result = retire_losing_variants(min_uses=MIN_VARIANT_USES)

        assert "parent" in result["promoted"]
        assert "c1" in result["retired"]
        # Parent content should be updated (in the saved list)
        saved = mock_save.call_args[0][0]
        parent_saved = next(s for s in saved if s.id == "parent")
        assert parent_saved.description == "better description"
        assert parent_saved.steps_template == ["better step"]

    def test_parent_wins_challenger_retired(self):
        """Parent with better utility_score causes challenger to be retired."""
        parent = _skill("parent", utility_score=0.85, use_count=10)
        challenger = _skill("c1", variant_of="parent", variant_wins=3, variant_losses=7)
        skills = [parent, challenger]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                result = retire_losing_variants(min_uses=MIN_VARIANT_USES)

        assert "c1" in result["retired"]
        assert "parent" not in result["promoted"]
        # Challenger should be removed from saved list
        saved = mock_save.call_args[0][0]
        assert not any(s.id == "c1" for s in saved)

    def test_insufficient_data_no_action(self):
        """Variants with fewer than min_uses trials are not retired."""
        parent = _skill("parent", utility_score=0.5, use_count=10)
        # Only 3 trials — below default MIN_VARIANT_USES
        challenger = _skill("c1", variant_of="parent", variant_wins=2, variant_losses=1)
        skills = [parent, challenger]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                result = retire_losing_variants(min_uses=MIN_VARIANT_USES)

        assert result["promoted"] == []
        assert result["retired"] == []
        mock_save.assert_not_called()

    def test_dry_run_no_save(self):
        parent = _skill("parent", utility_score=0.4, use_count=10)
        challenger = _skill("c1", variant_of="parent", variant_wins=8, variant_losses=2)
        skills = [parent, challenger]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                result = retire_losing_variants(dry_run=True, min_uses=MIN_VARIANT_USES)

        mock_save.assert_not_called()
        # Still reports what would happen
        assert "c1" in result["retired"]

    def test_no_variants_no_action(self):
        skills = [_skill("p1"), _skill("p2")]
        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills") as mock_save:
                result = retire_losing_variants()
        assert result == {"promoted": [], "retired": []}
        mock_save.assert_not_called()

    def test_missing_parent_skipped(self):
        """Variant pointing to non-existent parent should not crash."""
        orphan = _skill("orphan", variant_of="ghost-parent", variant_wins=8, variant_losses=2)
        skills = [orphan]
        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills"):
                # Should not raise
                result = retire_losing_variants(min_uses=MIN_VARIANT_USES)
        # No promotions — parent doesn't exist
        assert result["promoted"] == []

    def test_tie_goes_to_parent(self):
        """Equal win-rates: parent should not be replaced (challenger retired)."""
        parent = _skill("parent", utility_score=0.6, use_count=10)
        # Challenger also 0.6 win-rate
        challenger = _skill("c1", variant_of="parent", variant_wins=6, variant_losses=4)
        skills = [parent, challenger]

        with mock.patch("skills.load_skills", return_value=skills):
            with mock.patch("skills._save_skills"):
                result = retire_losing_variants(min_uses=MIN_VARIANT_USES)

        # Challenger wins 6/10 = 0.6; parent utility = 0.6; c_rate > parent_rate is False for equal
        # So challenger is retired (not promoted)
        assert "c1" in result["retired"]
        assert "parent" not in result["promoted"]


# ---------------------------------------------------------------------------
# Skill dataclass fields
# ---------------------------------------------------------------------------

class TestSkillVariantFields:
    def test_variant_fields_default_none_and_zero(self):
        s = _skill("test")
        assert s.variant_of is None
        assert s.variant_wins == 0
        assert s.variant_losses == 0

    def test_variant_fields_serialized(self):
        from skills import _skill_to_dict, _dict_to_skill
        s = _skill("test", variant_of="parent-123", variant_wins=5, variant_losses=2)
        d = _skill_to_dict(s)
        assert d["variant_of"] == "parent-123"
        assert d["variant_wins"] == 5
        assert d["variant_losses"] == 2
        s2 = _dict_to_skill(d)
        assert s2.variant_of == "parent-123"
        assert s2.variant_wins == 5
        assert s2.variant_losses == 2

    def test_legacy_skill_without_variant_fields_loads_safely(self):
        from skills import _dict_to_skill
        d = {
            "id": "legacy",
            "name": "legacy",
            "description": "old skill",
            "trigger_patterns": [],
            "steps_template": [],
            "source_loop_ids": [],
            "created_at": "2026-01-01",
        }
        s = _dict_to_skill(d)
        assert s.variant_of is None
        assert s.variant_wins == 0
        assert s.variant_losses == 0
