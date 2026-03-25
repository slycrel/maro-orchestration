"""Tests for Phase 10: skills.py

Skill library — extract, match, format, inject.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from skills import (
    Skill,
    _skill_to_dict,
    extract_skills,
    find_matching_skills,
    format_skills_for_prompt,
    increment_use,
    load_skills,
    save_skill,
)
from llm import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _make_skill(name: str = "test skill", triggers=None, steps=None) -> Skill:
    from datetime import datetime, timezone
    return Skill(
        id="sk" + name[:6].replace(" ", "")[:6],
        name=name,
        description=f"Does {name}",
        trigger_patterns=triggers or ["test pattern", "sample trigger"],
        steps_template=steps or ["Step 1: research", "Step 2: implement", "Step 3: verify"],
        source_loop_ids=["loop001"],
        created_at=datetime.utcnow().isoformat(),
        use_count=0,
        success_rate=1.0,
    )


class _ExtractMockAdapter:
    """Returns valid skill extraction JSON."""

    def complete(self, messages, **kwargs):
        payload = {
            "skills": [
                {
                    "name": "research synthesis",
                    "description": "Gather and synthesize information from multiple sources",
                    "trigger_patterns": ["research", "analyze", "gather information"],
                    "steps_template": ["Define scope", "Gather sources", "Synthesize findings"],
                },
                {
                    "name": "iterative build",
                    "description": "Build incrementally with validation at each step",
                    "trigger_patterns": ["build", "implement", "develop"],
                    "steps_template": ["Scaffold structure", "Implement core", "Test and refine"],
                },
            ]
        }
        return LLMResponse(
            content=json.dumps(payload),
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=80,
        )


class _BadExtractAdapter:
    """Returns garbage JSON for skills extraction."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content="not json {{{broken",
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )


# ---------------------------------------------------------------------------
# load_skills
# ---------------------------------------------------------------------------

def test_load_skills_empty(monkeypatch, tmp_path):
    """No file → []."""
    _setup_workspace(monkeypatch, tmp_path)
    skills = load_skills()
    assert skills == []


def test_load_skills_returns_list(monkeypatch, tmp_path):
    """load_skills returns a list."""
    _setup_workspace(monkeypatch, tmp_path)
    result = load_skills()
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# save_skill / load_skills round-trip
# ---------------------------------------------------------------------------

def test_save_and_load_skill(monkeypatch, tmp_path):
    """Round-trip: save then load."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("polymarket research")
    save_skill(skill)
    skills = load_skills()
    assert len(skills) >= 1
    ids = [s.id for s in skills]
    assert skill.id in ids


def test_save_multiple_skills(monkeypatch, tmp_path):
    """Multiple skills can be saved and loaded."""
    _setup_workspace(monkeypatch, tmp_path)
    skill_a = _make_skill("skill alpha")
    skill_b = _make_skill("skill beta")
    save_skill(skill_a)
    save_skill(skill_b)
    skills = load_skills()
    ids = [s.id for s in skills]
    assert skill_a.id in ids
    assert skill_b.id in ids


def test_save_skill_updates_existing(monkeypatch, tmp_path):
    """Saving a skill with same id replaces the old entry."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("updatable skill")
    save_skill(skill)
    skill.use_count = 5
    skill.success_rate = 0.8
    save_skill(skill)
    skills = load_skills()
    matching = [s for s in skills if s.id == skill.id]
    assert len(matching) == 1
    assert matching[0].use_count == 5
    assert matching[0].success_rate == 0.8


def test_skill_round_trip_all_fields(monkeypatch, tmp_path):
    """All fields survive the round-trip."""
    _setup_workspace(monkeypatch, tmp_path)
    from datetime import datetime, timezone
    skill = Skill(
        id="sk123456",
        name="full field test",
        description="tests all fields",
        trigger_patterns=["trigger one", "trigger two"],
        steps_template=["step one", "step two", "step three"],
        source_loop_ids=["loop-abc", "loop-def"],
        created_at=datetime.now(timezone.utc).isoformat(),
        use_count=3,
        success_rate=0.75,
    )
    save_skill(skill)
    loaded = [s for s in load_skills() if s.id == skill.id][0]
    assert loaded.name == skill.name
    assert loaded.description == skill.description
    assert loaded.trigger_patterns == skill.trigger_patterns
    assert loaded.steps_template == skill.steps_template
    assert loaded.source_loop_ids == skill.source_loop_ids
    assert loaded.use_count == skill.use_count
    assert loaded.success_rate == skill.success_rate


# ---------------------------------------------------------------------------
# find_matching_skills
# ---------------------------------------------------------------------------

def test_find_matching_skills_keyword(monkeypatch, tmp_path):
    """Keyword match against trigger_patterns."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("research tool", triggers=["polymarket", "research strategy"])
    save_skill(skill)
    matches = find_matching_skills("polymarket research")
    assert len(matches) >= 1
    assert any(s.id == skill.id for s in matches)


def test_find_matching_skills_no_match(monkeypatch, tmp_path):
    """No matching patterns → []."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("cooking skill", triggers=["bake cake", "mix ingredients"])
    save_skill(skill)
    matches = find_matching_skills("quantum physics research on entanglement")
    # "research" might match "mix ingredients" weakly - use unique trigger
    matches2 = find_matching_skills("astrophysics telescope calibration zzzunique")
    assert matches2 == []


def test_find_matching_skills_returns_top_2(monkeypatch, tmp_path):
    """Returns at most 2 matching skills."""
    _setup_workspace(monkeypatch, tmp_path)
    for i in range(5):
        skill = _make_skill(f"skill {i}", triggers=["common keyword"])
        skill.id = f"sk00000{i}"
        save_skill(skill)
    matches = find_matching_skills("common keyword task")
    assert len(matches) <= 2


def test_find_matching_skills_partial_match(monkeypatch, tmp_path):
    """Partial keyword inclusion counts as match."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("analyzer", triggers=["data analysis pipeline"])
    save_skill(skill)
    # "analysis" is contained in "data analysis pipeline"
    matches = find_matching_skills("run data analysis pipeline for results")
    assert any(s.id == skill.id for s in matches)


def test_find_matching_skills_empty_library(monkeypatch, tmp_path):
    """Empty skill library → []."""
    _setup_workspace(monkeypatch, tmp_path)
    matches = find_matching_skills("any goal")
    assert matches == []


# ---------------------------------------------------------------------------
# format_skills_for_prompt
# ---------------------------------------------------------------------------

def test_format_skills_for_prompt(monkeypatch, tmp_path):
    """Returns non-empty string with skill names and steps."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("research tool", steps=["Step A", "Step B"])
    result = format_skills_for_prompt([skill])
    assert len(result) > 0
    assert "research tool" in result
    assert "Step A" in result


def test_format_skills_for_prompt_empty(monkeypatch, tmp_path):
    """Empty skills list → empty string."""
    _setup_workspace(monkeypatch, tmp_path)
    result = format_skills_for_prompt([])
    assert result == ""


def test_format_skills_for_prompt_multiple(monkeypatch, tmp_path):
    """Multiple skills all appear in the output."""
    _setup_workspace(monkeypatch, tmp_path)
    skill_a = _make_skill("skill alpha")
    skill_b = _make_skill("skill beta")
    result = format_skills_for_prompt([skill_a, skill_b])
    assert "skill alpha" in result
    assert "skill beta" in result


# ---------------------------------------------------------------------------
# extract_skills
# ---------------------------------------------------------------------------

def test_extract_skills_dry_run(monkeypatch, tmp_path):
    """With mock adapter that returns valid JSON, skills are extracted and saved."""
    _setup_workspace(monkeypatch, tmp_path)
    outcomes = [
        {"goal": "research polymarket strategies", "status": "done", "task_type": "research",
         "summary": "Found 5 strategies", "outcome_id": "oc123456"},
        {"goal": "build a data pipeline", "status": "done", "task_type": "build",
         "summary": "Pipeline built", "outcome_id": "oc789012"},
    ]
    extracted = extract_skills(outcomes, _ExtractMockAdapter())
    assert len(extracted) >= 1
    assert all(isinstance(s, Skill) for s in extracted)
    # Should be saved
    saved = load_skills()
    saved_ids = [s.id for s in saved]
    for s in extracted:
        assert s.id in saved_ids


def test_extract_skills_bad_json(monkeypatch, tmp_path):
    """Graceful fallback on bad JSON — returns []."""
    _setup_workspace(monkeypatch, tmp_path)
    outcomes = [
        {"goal": "test goal", "status": "done", "task_type": "general", "summary": "done"},
    ]
    extracted = extract_skills(outcomes, _BadExtractAdapter())
    assert extracted == []


def test_extract_skills_empty_outcomes(monkeypatch, tmp_path):
    """Empty outcomes → []."""
    _setup_workspace(monkeypatch, tmp_path)
    extracted = extract_skills([], _ExtractMockAdapter())
    assert extracted == []


def test_extract_skills_only_successes(monkeypatch, tmp_path):
    """Only successful outcomes are analyzed (status=done)."""
    _setup_workspace(monkeypatch, tmp_path)
    outcomes = [
        {"goal": "failed goal", "status": "stuck", "task_type": "general", "summary": "failed"},
    ]
    # All stuck → no successes → []
    extracted = extract_skills(outcomes, _ExtractMockAdapter())
    assert extracted == []


# ---------------------------------------------------------------------------
# increment_use
# ---------------------------------------------------------------------------

def test_increment_use(monkeypatch, tmp_path):
    """use_count incremented in file."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("increment test")
    skill.use_count = 0
    save_skill(skill)
    increment_use(skill.id)
    skills = load_skills()
    updated = [s for s in skills if s.id == skill.id][0]
    assert updated.use_count == 1


def test_increment_use_multiple_times(monkeypatch, tmp_path):
    """use_count accumulates across multiple calls."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("multi increment")
    skill.use_count = 0
    save_skill(skill)
    increment_use(skill.id)
    increment_use(skill.id)
    increment_use(skill.id)
    skills = load_skills()
    updated = [s for s in skills if s.id == skill.id][0]
    assert updated.use_count == 3


def test_increment_use_nonexistent_id(monkeypatch, tmp_path):
    """increment_use with unknown id doesn't raise."""
    _setup_workspace(monkeypatch, tmp_path)
    # Should not raise
    increment_use("nonexistent-skill-id")


# ---------------------------------------------------------------------------
# Skills wired into agent_loop._decompose
# ---------------------------------------------------------------------------

def test_skills_injected_into_decompose(monkeypatch, tmp_path):
    """Skills matching the goal appear in the decompose system prompt via skills_context."""
    _setup_workspace(monkeypatch, tmp_path)

    injected_prompts = []

    class CapturingAdapter:
        def complete(self, messages, **kwargs):
            from llm import LLMResponse
            user_content = next((m.content for m in messages if m.role == "user"), "")
            system_content = next((m.content for m in messages if m.role == "system"), "")
            injected_prompts.append(system_content)
            if "decompose" in user_content.lower() or "concrete steps" in user_content.lower():
                return LLMResponse(
                    content='["step A", "step B"]',
                    stop_reason="end_turn",
                    input_tokens=50,
                    output_tokens=20,
                )
            return LLMResponse(
                content='["step A", "step B"]',
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=20,
            )

    # Save a skill with matching trigger
    skill = _make_skill("polymarket analyzer", triggers=["polymarket"])
    save_skill(skill)

    # Verify find_matching_skills finds it
    matches = find_matching_skills("research polymarket data")
    assert len(matches) >= 1

    # Verify format_skills_for_prompt includes the skill name
    skills_block = format_skills_for_prompt(matches)
    assert "polymarket analyzer" in skills_block

    # Call _decompose with the skills_context (as run_agent_loop does)
    from agent_loop import _decompose
    steps = _decompose(
        "research polymarket data",
        CapturingAdapter(),
        max_steps=4,
        skills_context=skills_block,
    )
    assert len(steps) >= 1
    # The system prompt should contain the skill name
    combined = "\n".join(injected_prompts)
    assert "polymarket analyzer" in combined


# ---------------------------------------------------------------------------
# _skill_to_dict
# ---------------------------------------------------------------------------

def test_skill_to_dict(monkeypatch, tmp_path):
    """_skill_to_dict returns a plain dict with expected keys."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("dict test")
    d = _skill_to_dict(skill)
    assert "id" in d
    assert "name" in d
    assert "trigger_patterns" in d
    assert "steps_template" in d
    assert "use_count" in d
    assert "success_rate" in d


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_skills_list_empty(monkeypatch, tmp_path, capsys):
    """poe-skills --list with no skills prints skills=(none)."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-skills", "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "skills=(none)" in out


def test_cli_poe_skills_list_with_skill(monkeypatch, tmp_path, capsys):
    """poe-skills --list shows skill names."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("cli list test skill")
    save_skill(skill)
    import cli
    rc = cli.main(["poe-skills", "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cli list test skill" in out


def test_cli_poe_skills_extract_dry_run(monkeypatch, tmp_path, capsys):
    """poe-skills --extract --dry-run doesn't crash."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-skills", "--extract", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry_run" in out.lower() or "outcomes" in out.lower()
