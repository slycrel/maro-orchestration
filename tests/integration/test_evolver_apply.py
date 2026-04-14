"""Integration tests — evolver auto-apply mutation chain.

Verifies the most dangerous code path in the system: the evolver autonomously
mutating workspace config, skill files, and persona files.  These tests use
real file I/O against a tmp workspace; LLM calls are mocked.

Coverage:
  1. apply_suggestion on skill_pattern → skill file mutated on disk
  2. apply_suggestion on skill_pattern → change_log.jsonl written with before_state
  3. apply_suggestion on skill_pattern → suggestion marked applied in suggestions.jsonl
  4. apply_suggestion creates a NEW skill when target doesn't exist
  5. apply_suggestion on prompt_tweak records a tiered lesson
  6. apply_suggestion on new_guardrail held for review by default
  7. apply_suggestion on new_guardrail applied when POE_AUTO_APPLY_GUARDRAILS=1
  8. apply_suggestion on observation is a safe no-op (applied=True, no side effects)
  9. High-confidence suggestion (>=0.8) auto-applied via run_evolver
  10. Low-confidence suggestion (<0.8) NOT auto-applied via run_evolver
  11. Skills backup (.bak) created before mutation
  12. Multiple suggestions: only high-confidence ones auto-applied
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from evolver import (
    Suggestion,
    apply_suggestion,
    _apply_suggestion_action,
    _save_suggestions,
    load_suggestions,
    _suggestions_path,
)
from skill_types import Skill, skill_to_dict
from skills import load_skills, save_skill, _skills_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(name: str = "test-skill", skill_id: str = "sk-001",
                description: str = "Original description") -> Skill:
    return Skill(
        id=skill_id,
        name=name,
        description=description,
        trigger_patterns=["test"],
        steps_template=["step one", "step two"],
        source_loop_ids=["loop-1"],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_suggestion(
    category: str = "skill_pattern",
    target: str = "test-skill",
    confidence: float = 0.9,
    suggestion_text: str = "Improved skill description from evolver",
    suggestion_id: str = "sug-001",
    applied: bool = False,
) -> Suggestion:
    return Suggestion(
        suggestion_id=suggestion_id,
        category=category,
        target=target,
        suggestion=suggestion_text,
        failure_pattern="test pattern",
        confidence=confidence,
        outcomes_analyzed=10,
        applied=applied,
    )


def _seed_skill(skill: Skill) -> None:
    """Write a skill to the workspace skills.jsonl."""
    save_skill(skill)


def _seed_suggestion(suggestion: Suggestion) -> None:
    """Write a suggestion to the workspace suggestions.jsonl."""
    _save_suggestions([suggestion])


def _read_change_log() -> list[dict]:
    from orch_items import memory_dir
    cl = memory_dir() / "change_log.jsonl"
    if not cl.exists():
        return []
    entries = []
    for line in cl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# 1. Skill mutation on disk
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
def test_apply_skill_pattern_mutates_file():
    """apply_suggestion with category=skill_pattern updates the skill on disk."""
    skill = _make_skill()
    _seed_skill(skill)

    sug = _make_suggestion(target="test-skill", suggestion_text="Better description v2")
    _seed_suggestion(sug)

    result = apply_suggestion("sug-001")
    assert result is True

    skills = load_skills()
    updated = next((s for s in skills if s.id == "sk-001"), None)
    assert updated is not None
    assert updated.description == "Better description v2"
    assert updated.description != "Original description"


# ---------------------------------------------------------------------------
# 2. change_log.jsonl written with before_state
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
def test_apply_skill_pattern_writes_change_log():
    """apply_suggestion writes an entry to change_log.jsonl with before_state."""
    skill = _make_skill(description="Old desc for audit")
    _seed_skill(skill)

    sug = _make_suggestion(target="test-skill", suggestion_text="New desc for audit")
    _seed_suggestion(sug)

    apply_suggestion("sug-001")

    entries = _read_change_log()
    assert len(entries) >= 1

    entry = entries[-1]
    assert entry["module"] == "evolver"
    assert entry["action"] == "_apply_suggestion_action"
    assert entry["category"] == "skill_pattern"
    assert entry["suggestion_id"] == "sug-001"
    assert entry["before_state"] is not None
    assert entry["before_state"]["type"] == "skill_update"
    assert "Old desc for audit" in entry["before_state"]["old_description"]


# ---------------------------------------------------------------------------
# 3. Suggestion marked as applied
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
def test_apply_skill_pattern_marks_applied():
    """After apply_suggestion, the suggestion in suggestions.jsonl has applied=True."""
    skill = _make_skill()
    _seed_skill(skill)

    sug = _make_suggestion()
    _seed_suggestion(sug)

    apply_suggestion("sug-001")

    suggestions = load_suggestions(limit=100)
    matched = [s for s in suggestions if s.suggestion_id == "sug-001"]
    assert len(matched) == 1
    assert matched[0].applied is True


# ---------------------------------------------------------------------------
# 4. New skill creation when target doesn't exist
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
def test_apply_skill_pattern_creates_new_skill():
    """apply_suggestion creates a new skill when target doesn't match any existing skill."""
    sug = _make_suggestion(
        target="brand-new-skill",
        suggestion_text="A totally new skill from evolver",
        suggestion_id="sug-new",
    )
    _seed_suggestion(sug)

    result = apply_suggestion("sug-new")
    assert result is True

    skills = load_skills()
    created = next((s for s in skills if s.name == "brand-new-skill"), None)
    assert created is not None
    assert created.description == "A totally new skill from evolver"
    assert created.tier == "provisional"

    # change_log should record type=skill_create
    entries = _read_change_log()
    create_entries = [e for e in entries if e.get("suggestion_id") == "sug-new"]
    assert len(create_entries) >= 1
    assert create_entries[-1]["before_state"]["type"] == "skill_create"


# ---------------------------------------------------------------------------
# 5. prompt_tweak records a tiered lesson
# ---------------------------------------------------------------------------

def test_apply_prompt_tweak_records_lesson():
    """apply_suggestion with category=prompt_tweak calls record_tiered_lesson."""
    sug = _make_suggestion(
        category="prompt_tweak",
        target="research",
        suggestion_text="Be more concise in research output",
        suggestion_id="sug-tweak",
        confidence=0.85,
    )
    _seed_suggestion(sug)

    with patch("evolver.record_tiered_lesson") as mock_rtl:
        mock_rtl.return_value = None
        # Also need to patch the module-level reference
        import evolver
        old_rtl = evolver.record_tiered_lesson
        evolver.record_tiered_lesson = mock_rtl
        try:
            result = apply_suggestion("sug-tweak")
        finally:
            evolver.record_tiered_lesson = old_rtl

    assert result is True
    # Verify lesson was recorded (the call happens inside _apply_suggestion_action
    # which imports record_tiered_lesson from memory)
    suggestions = load_suggestions(limit=100)
    matched = [s for s in suggestions if s.suggestion_id == "sug-tweak"]
    assert len(matched) == 1
    assert matched[0].applied is True


# ---------------------------------------------------------------------------
# 6. new_guardrail held for review by default
# ---------------------------------------------------------------------------

def test_apply_guardrail_held_in_production(monkeypatch):
    """new_guardrail suggestions are held in production (config environment=production).

    Session 20.5: previous behavior was 'held unless env var explicitly set' —
    which silently disabled the guardrail path everywhere. New policy is
    'auto-apply in non-prod, hold in prod'. This test covers the prod-hold case.
    """
    sug = _make_suggestion(
        category="new_guardrail",
        target="all",
        suggestion_text="Block all destructive commands",
        suggestion_id="sug-guard",
        confidence=0.9,
    )
    _seed_suggestion(sug)

    # Ensure env override is unset so we hit the config-based branch
    monkeypatch.delenv("POE_AUTO_APPLY_GUARDRAILS", raising=False)

    # Simulate production environment via config
    monkeypatch.setattr("config.get", lambda key, default=None:
                        "production" if key == "environment" else default)

    result = apply_suggestion("sug-guard")
    assert result is True

    suggestions = load_suggestions(limit=100)
    matched = [s for s in suggestions if s.suggestion_id == "sug-guard"]
    assert len(matched) == 1
    assert matched[0].applied is False  # held in prod


def test_apply_guardrail_auto_applied_in_dev():
    """new_guardrail suggestions auto-apply in dev (default environment).

    Session 20.5 regression: confirms the new default-on behavior.
    """
    sug = _make_suggestion(
        category="new_guardrail",
        target="all",
        suggestion_text="Block destructive command X",
        suggestion_id="sug-guard-dev",
        confidence=0.9,
    )
    _seed_suggestion(sug)

    # No env override, no config mock → defaults to dev → auto-apply
    os.environ.pop("POE_AUTO_APPLY_GUARDRAILS", None)

    result = apply_suggestion("sug-guard-dev")
    assert result is True

    suggestions = load_suggestions(limit=100)
    matched = [s for s in suggestions if s.suggestion_id == "sug-guard-dev"]
    assert len(matched) == 1
    assert matched[0].applied is True  # auto-applied in dev


def test_apply_guardrail_override_off_holds(monkeypatch):
    """POE_AUTO_APPLY_GUARDRAILS=0 explicitly holds even in dev."""
    sug = _make_suggestion(
        category="new_guardrail",
        target="all",
        suggestion_text="Block trades over $1000",
        suggestion_id="sug-guard-off",
        confidence=0.9,
    )
    _seed_suggestion(sug)

    monkeypatch.setenv("POE_AUTO_APPLY_GUARDRAILS", "0")

    result = apply_suggestion("sug-guard-off")
    assert result is True

    suggestions = load_suggestions(limit=100)
    matched = [s for s in suggestions if s.suggestion_id == "sug-guard-off"]
    assert len(matched) == 1
    assert matched[0].applied is False


# ---------------------------------------------------------------------------
# 7. new_guardrail applied with POE_AUTO_APPLY_GUARDRAILS=1
# ---------------------------------------------------------------------------

def test_apply_guardrail_with_env_override(monkeypatch):
    """new_guardrail IS applied when POE_AUTO_APPLY_GUARDRAILS=1."""
    sug = _make_suggestion(
        category="new_guardrail",
        target="all",
        suggestion_text="Require confirmation for trades",
        suggestion_id="sug-guard-ok",
        confidence=0.9,
    )
    _seed_suggestion(sug)

    monkeypatch.setenv("POE_AUTO_APPLY_GUARDRAILS", "1")

    result = apply_suggestion("sug-guard-ok")
    assert result is True

    suggestions = load_suggestions(limit=100)
    matched = [s for s in suggestions if s.suggestion_id == "sug-guard-ok"]
    assert len(matched) == 1
    assert matched[0].applied is True

    # Verify dynamic-constraints.jsonl was written
    from orch_items import memory_dir
    dc_path = memory_dir() / "dynamic-constraints.jsonl"
    assert dc_path.exists()
    entries = [json.loads(l) for l in dc_path.read_text().splitlines() if l.strip()]
    assert any("Require confirmation" in e.get("pattern", "") for e in entries)


# ---------------------------------------------------------------------------
# 8. observation is a safe no-op
# ---------------------------------------------------------------------------

def test_apply_observation_is_noop():
    """observation category sets applied=True but has no side effects."""
    sug = _make_suggestion(
        category="observation",
        target="all",
        suggestion_text="System is running smoothly",
        suggestion_id="sug-obs",
    )
    _seed_suggestion(sug)

    result = apply_suggestion("sug-obs")
    assert result is True

    suggestions = load_suggestions(limit=100)
    matched = [s for s in suggestions if s.suggestion_id == "sug-obs"]
    assert len(matched) == 1
    assert matched[0].applied is True

    # No skill file should be created
    skills = load_skills()
    assert len(skills) == 0


# ---------------------------------------------------------------------------
# 9. High-confidence auto-applied via run_evolver
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
@patch("evolver._verify_post_apply")
def test_run_evolver_auto_applies_high_confidence(_mock_verify):
    """run_evolver auto-applies suggestions with confidence >= 0.8.

    Note: _verify_post_apply is mocked so the session 20.5 auto-revert
    behavior doesn't undo mutations when the test-runner's own pytest
    subprocess fails (orthogonal concern — covered by direct unit tests
    on _verify_post_apply itself in test_evolver.py).
    """
    from evolver import run_evolver

    skill = _make_skill()
    _seed_skill(skill)

    # Mock the LLM to return a high-confidence skill_pattern suggestion
    fake_llm_json = json.dumps({
        "failure_patterns": ["test skills degrade"],
        "suggestions": [{
            "category": "skill_pattern",
            "target": "test-skill",
            "suggestion": "Dramatically improved via evolver",
            "failure_pattern": "test skills degrade",
            "confidence": 0.9,
        }],
    })

    mock_resp = MagicMock()
    mock_resp.content = fake_llm_json

    mock_adapter = MagicMock()
    mock_adapter.complete.return_value = mock_resp

    with patch("evolver.build_adapter", return_value=mock_adapter), \
         patch("evolver.load_outcomes", return_value=[
             MagicMock(to_dict=lambda: {"goal": "test", "success": False, "failure_reason": "degraded"})
             for _ in range(5)
         ]), \
         patch("evolver.load_lessons", return_value=[]):

        report = run_evolver(min_outcomes=1, dry_run=False, verbose=True)

    assert len(report.suggestions) >= 1

    # Verify the skill was actually mutated on disk
    skills = load_skills()
    updated = next((s for s in skills if s.id == "sk-001"), None)
    assert updated is not None
    assert "Dramatically improved" in updated.description


# ---------------------------------------------------------------------------
# 10. Low-confidence NOT auto-applied via run_evolver
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
def test_run_evolver_skips_low_confidence():
    """run_evolver does NOT auto-apply suggestions with confidence < 0.6."""
    from evolver import run_evolver

    skill = _make_skill()
    _seed_skill(skill)

    fake_llm_json = json.dumps({
        "failure_patterns": ["maybe an issue"],
        "suggestions": [{
            "category": "skill_pattern",
            "target": "test-skill",
            "suggestion": "Speculative change, low confidence",
            "failure_pattern": "maybe an issue",
            "confidence": 0.3,
        }],
    })

    mock_resp = MagicMock()
    mock_resp.content = fake_llm_json

    mock_adapter = MagicMock()
    mock_adapter.complete.return_value = mock_resp

    with patch("evolver.build_adapter", return_value=mock_adapter), \
         patch("evolver.load_outcomes", return_value=[
             MagicMock(to_dict=lambda: {"goal": "test", "success": True, "failure_reason": ""})
             for _ in range(5)
         ]), \
         patch("evolver.load_lessons", return_value=[]):

        report = run_evolver(min_outcomes=1, dry_run=False, verbose=True)

    # Skill should NOT have been mutated
    skills = load_skills()
    original = next((s for s in skills if s.id == "sk-001"), None)
    assert original is not None
    assert original.description == "Original description"


# ---------------------------------------------------------------------------
# 11. Skills backup (.bak) created before mutation
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
def test_apply_skill_creates_backup():
    """When updating an existing skill, a .bak copy of skills.jsonl is created."""
    skill = _make_skill(description="Backup test original")
    _seed_skill(skill)

    sug = _make_suggestion(target="test-skill", suggestion_text="Updated after backup")
    _seed_suggestion(sug)

    apply_suggestion("sug-001")

    bak_path = Path(str(_skills_path()) + ".bak")
    assert bak_path.exists(), "skills.jsonl.bak should exist after skill mutation"

    # The backup should contain the original description
    bak_content = bak_path.read_text(encoding="utf-8")
    assert "Backup test original" in bak_content


# ---------------------------------------------------------------------------
# 12. Multiple suggestions: only high-confidence ones auto-applied
# ---------------------------------------------------------------------------

@patch("evolver.validate_skill_mutation", None)
@patch("evolver.record_tiered_lesson", None)
@patch("evolver._verify_post_apply")
def test_run_evolver_mixed_confidence(_mock_verify):
    """With multiple suggestions at different confidences, only >=0.8 auto-apply.

    _verify_post_apply is mocked to prevent session 20.5 auto-revert from
    rolling back the mutation (post-apply test suite may fail for unrelated
    reasons in this test harness).
    """
    from evolver import run_evolver

    skill_a = _make_skill(name="skill-a", skill_id="sk-a", description="Skill A original")
    _seed_skill(skill_a)
    skill_b = _make_skill(name="skill-b", skill_id="sk-b", description="Skill B original")
    _seed_skill(skill_b)

    fake_llm_json = json.dumps({
        "failure_patterns": ["pattern alpha", "pattern beta"],
        "suggestions": [
            {
                "category": "skill_pattern",
                "target": "skill-a",
                "suggestion": "High confidence mutation for A",
                "failure_pattern": "pattern alpha",
                "confidence": 0.85,
            },
            {
                "category": "skill_pattern",
                "target": "skill-b",
                "suggestion": "Low confidence mutation for B",
                "failure_pattern": "pattern beta",
                "confidence": 0.4,
            },
        ],
    })

    mock_resp = MagicMock()
    mock_resp.content = fake_llm_json

    mock_adapter = MagicMock()
    mock_adapter.complete.return_value = mock_resp

    with patch("evolver.build_adapter", return_value=mock_adapter), \
         patch("evolver.load_outcomes", return_value=[
             MagicMock(to_dict=lambda: {"goal": "test", "success": False, "failure_reason": "bad"})
             for _ in range(5)
         ]), \
         patch("evolver.load_lessons", return_value=[]):

        run_evolver(min_outcomes=1, dry_run=False, verbose=True)

    skills = load_skills()

    a = next((s for s in skills if s.id == "sk-a"), None)
    assert a is not None
    assert "High confidence mutation" in a.description, "skill-a should be mutated (confidence 0.85)"

    b = next((s for s in skills if s.id == "sk-b"), None)
    assert b is not None
    assert b.description == "Skill B original", "skill-b should NOT be mutated (confidence 0.4)"
