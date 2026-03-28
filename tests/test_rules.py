"""Tests for Phase 22 Stage 5: rules.py — skill→rule graduation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rules import (
    Rule,
    load_rules,
    save_rule,
    find_matching_rule,
    record_rule_use,
    graduate_skill_to_rule,
    demote_rule_to_skill,
    record_rule_wrong_answer,
    get_rule_graduation_candidates,
    _rules_path,
    RULE_WRONG_ANSWER_DEMOTE_AT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rule(**kw) -> Rule:
    defaults = dict(
        id="rule0001",
        name="test-rule",
        description="A test rule",
        trigger_patterns=[r"research.*polymarket"],
        steps_template=["Step 1: research", "Step 2: summarise"],
        source_skill_id="skill0001",
        graduated_at="2026-03-27T00:00:00+00:00",
    )
    defaults.update(kw)
    return Rule(**defaults)


def _make_skill(**kw):
    """Return a minimal dict resembling a skills.Skill."""
    from skills import Skill
    defaults = dict(
        id="skill0001",
        name="polymarket-research",
        description="Research polymarket strategies",
        trigger_patterns=[r"research.*polymarket"],
        steps_template=["research", "analyse", "report"],
        source_loop_ids=[],
        created_at="2026-03-27T00:00:00+00:00",
        tier="established",
        use_count=5,
        success_rate=0.95,
    )
    defaults.update(kw)
    return Skill(**defaults)


# ---------------------------------------------------------------------------
# Storage roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_rule(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    rule = _make_rule()
    save_rule(rule)
    loaded = load_rules()
    assert len(loaded) == 1
    assert loaded[0].name == "test-rule"
    assert loaded[0].steps_template == ["Step 1: research", "Step 2: summarise"]


def test_load_rules_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "nonexistent.jsonl")
    assert load_rules() == []


def test_save_rule_updates_existing(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    rule = _make_rule()
    save_rule(rule)
    rule.use_count = 5
    save_rule(rule)
    loaded = load_rules()
    assert len(loaded) == 1
    assert loaded[0].use_count == 5


def test_load_rules_excludes_inactive(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    active = _make_rule(id="r1", name="active")
    inactive = _make_rule(id="r2", name="inactive", active=False)
    save_rule(active)
    save_rule(inactive)
    loaded = load_rules(active_only=True)
    assert len(loaded) == 1
    assert loaded[0].name == "active"


def test_load_rules_all_includes_inactive(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(id="r1"))
    save_rule(_make_rule(id="r2", active=False))
    all_rules = load_rules(active_only=False)
    assert len(all_rules) == 2


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def test_find_matching_rule_hits_regex(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(trigger_patterns=[r"research.*polymarket"]))
    rule = find_matching_rule("research winning polymarket strategies")
    assert rule is not None
    assert rule.name == "test-rule"


def test_find_matching_rule_misses_different_goal(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(trigger_patterns=[r"research.*polymarket"]))
    rule = find_matching_rule("build a slack bot")
    assert rule is None


def test_find_matching_rule_skips_inactive(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(active=False))
    rule = find_matching_rule("research winning polymarket strategies")
    assert rule is None


def test_find_matching_rule_fallback_substring_on_bad_regex(tmp_path, monkeypatch):
    """Invalid regex falls back to substring match."""
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(trigger_patterns=["[invalid"]))
    # "[invalid" is an invalid regex — should fall back to substring and miss
    rule = find_matching_rule("research polymarket")
    assert rule is None  # substring "[invalid" not in goal


def test_find_matching_rule_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(trigger_patterns=[r"research.*polymarket"]))
    rule = find_matching_rule("RESEARCH WINNING POLYMARKET STRATEGIES")
    assert rule is not None


def test_find_matching_rule_none_on_empty_steps(tmp_path, monkeypatch):
    """Rules with empty steps_template are skipped (nothing to inject)."""
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(steps_template=[]))
    rule = find_matching_rule("research polymarket")
    assert rule is None


# ---------------------------------------------------------------------------
# Use count
# ---------------------------------------------------------------------------

def test_record_rule_use_increments_count(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    rule = _make_rule()
    save_rule(rule)
    ok = record_rule_use(rule.id)
    assert ok
    loaded = load_rules()[0]
    assert loaded.use_count == 1


def test_record_rule_use_returns_false_when_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    ok = record_rule_use("nonexistent")
    assert not ok


# ---------------------------------------------------------------------------
# Graduation
# ---------------------------------------------------------------------------

def test_graduate_skill_to_rule_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    from skills import save_skill
    skill = _make_skill()
    save_skill(skill)

    rule = graduate_skill_to_rule(skill.id)
    assert rule is not None
    assert rule.name == skill.name
    assert rule.source_skill_id == skill.id
    assert rule.active is True

    # Verify persisted
    loaded = load_rules()
    assert len(loaded) == 1
    assert loaded[0].name == skill.name


def test_graduate_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    from skills import save_skill
    save_skill(_make_skill())
    rule = graduate_skill_to_rule("polymarket-research")
    assert rule is not None


def test_graduate_rejects_provisional_skill(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    from skills import save_skill
    save_skill(_make_skill(tier="provisional"))
    rule = graduate_skill_to_rule("polymarket-research")
    assert rule is None


def test_graduate_rejects_low_pass3(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    from skills import save_skill
    # success_rate=0.7: pass^3 = 0.343 < 0.70 threshold
    save_skill(_make_skill(success_rate=0.7, use_count=10))
    rule = graduate_skill_to_rule("polymarket-research")
    assert rule is None


def test_graduate_returns_none_for_unknown_skill(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    rule = graduate_skill_to_rule("ghost-skill")
    assert rule is None


# ---------------------------------------------------------------------------
# get_rule_graduation_candidates
# ---------------------------------------------------------------------------

def test_graduation_candidates_returns_eligible(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    from skills import save_skill
    save_skill(_make_skill())  # established, use_count=5, success_rate=0.95 → pass3=0.857
    candidates = get_rule_graduation_candidates()
    assert len(candidates) == 1
    assert candidates[0]["name"] == "polymarket-research"
    assert candidates[0]["pass3"] > 0.7


def test_graduation_candidates_excludes_provisional(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    from skills import save_skill
    save_skill(_make_skill(tier="provisional"))
    candidates = get_rule_graduation_candidates()
    assert candidates == []


def test_graduation_candidates_excludes_already_graduated(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    from skills import save_skill
    save_skill(_make_skill())
    graduate_skill_to_rule("polymarket-research")

    candidates = get_rule_graduation_candidates()
    assert candidates == []


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------

def test_demote_rule_to_skill(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    rule = _make_rule()
    save_rule(rule)
    ok = demote_rule_to_skill(rule.id)
    assert ok
    active = load_rules(active_only=True)
    assert len(active) == 0


def test_demote_returns_false_when_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    assert not demote_rule_to_skill("ghost-id")


def test_record_wrong_answer_increments(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule())
    updated = record_rule_wrong_answer("rule0001")
    assert updated is not None
    assert updated.wrong_answer_count == 1
    assert updated.active is True


def test_record_wrong_answer_auto_demotes_at_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule())
    for _ in range(RULE_WRONG_ANSWER_DEMOTE_AT):
        updated = record_rule_wrong_answer("rule0001")
    assert updated.active is False
    # Should not appear in active rules
    assert load_rules(active_only=True) == []


def test_record_wrong_answer_returns_none_when_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    result = record_rule_wrong_answer("ghost-id")
    assert result is None


# ---------------------------------------------------------------------------
# Integration: rule match bypasses _decompose in agent_loop
# ---------------------------------------------------------------------------

def test_build_loop_context_returns_matched_rule(tmp_path, monkeypatch):
    """When a rule matches, _build_loop_context returns it as the 5th element."""
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")
    save_rule(_make_rule(trigger_patterns=[r"research.*polymarket"]))

    from agent_loop import _build_loop_context
    _, _, _, _, matched = _build_loop_context("research winning polymarket strategies")
    assert matched is not None
    assert matched.name == "test-rule"


def test_build_loop_context_no_rule_returns_none(tmp_path, monkeypatch):
    """No rule match → 5th element is None."""
    monkeypatch.setattr("rules._rules_path", lambda: tmp_path / "rules.jsonl")

    from agent_loop import _build_loop_context
    _, _, _, _, matched = _build_loop_context("build a completely new feature")
    assert matched is None
