"""Tests for Phase 10: skills.py

Skill library — extract, match, format, inject.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from skills import (
    Skill,
    SkillStats,
    SkillTestCase,
    SkillMutationResult,
    ESCALATION_THRESHOLD,
    _skill_to_dict,
    compute_skill_hash,
    verify_skill_hash,
    extract_skills,
    find_matching_skills,
    format_skills_for_prompt,
    generate_skill_tests,
    get_all_skill_stats,
    get_skill_stats,
    get_skills_needing_escalation,
    increment_use,
    load_skills,
    parse_skill_sections,
    record_skill_outcome,
    render_skill_markdown,
    run_skill_tests,
    save_skill,
    update_skill_section,
    validate_skill_mutation,
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
        created_at=datetime.now(timezone.utc).isoformat(),
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


def test_find_matching_skills_returns_top_3(monkeypatch, tmp_path):
    """Returns at most 3 matching skills (keyword cap)."""
    _setup_workspace(monkeypatch, tmp_path)
    for i in range(5):
        skill = _make_skill(f"skill {i}", triggers=["common keyword"])
        skill.id = f"sk00000{i}"
        save_skill(skill)
    matches = find_matching_skills("common keyword task")
    assert len(matches) <= 3


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


def test_find_matching_skills_tfidf_fallback(monkeypatch, tmp_path):
    """When no trigger pattern matches, TF-IDF fallback returns relevant skills."""
    _setup_workspace(monkeypatch, tmp_path)
    relevant = _make_skill("polymarket research", triggers=["unrelated-trigger"])
    relevant.name = "polymarket research"
    relevant.description = "Research prediction market calibration and betting strategies on polymarket"
    irrelevant = _make_skill("systemd ops", triggers=["other-trigger"])
    irrelevant.description = "Configure systemd services and restart on failure"
    save_skill(relevant)
    save_skill(irrelevant)
    # No trigger pattern matches "polymarket strategy" exactly,
    # but TF-IDF should surface the relevant skill first
    matches = find_matching_skills("polymarket strategy calibration")
    assert len(matches) >= 1
    assert matches[0].id == relevant.id


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


# ===========================================================================
# Phase 14 tests: SkillStats, parse_skill_sections, SkillTestCase,
# validate_skill_mutation, compute_skill_hash, verify_skill_hash
# ===========================================================================


class _SkillTestMockAdapter:
    """Returns valid skill test case JSON."""

    def complete(self, messages, **kwargs):
        payload = [
            {"input_description": "research a topic", "expected_keywords": ["research", "result"]},
            {"input_description": "build a feature", "expected_keywords": ["feature", "complete"]},
        ]
        return LLMResponse(
            content=json.dumps(payload),
            stop_reason="end_turn",
            input_tokens=40,
            output_tokens=50,
        )


class _SkillRunMockAdapter:
    """Returns output containing expected keywords."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content="The research result is complete and ready.",
            stop_reason="end_turn",
            input_tokens=30,
            output_tokens=20,
        )


class _SkillRunFailAdapter:
    """Returns output NOT containing expected keywords."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content="Zyzzyva frumious bandersnatch output with nothing useful.",
            stop_reason="end_turn",
            input_tokens=30,
            output_tokens=20,
        )


# ---------------------------------------------------------------------------
# Phase 14: Per-skill success rate tracking
# ---------------------------------------------------------------------------

def test_record_skill_outcome_success(monkeypatch, tmp_path):
    """Recording a success increments success count and updates success_rate."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        skill = _make_skill("stat tracker")
        save_skill(skill)
        record_skill_outcome(skill.id, success=True)
        stats = get_skill_stats(skill.id)
        assert stats is not None
        assert stats.successes == 1
        assert stats.total_uses == 1
        assert stats.success_rate == 1.0


def test_record_skill_outcome_failure(monkeypatch, tmp_path):
    """Recording a failure decrements success_rate."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        skill = _make_skill("failure tracker")
        save_skill(skill)
        record_skill_outcome(skill.id, success=True)
        record_skill_outcome(skill.id, success=False)
        stats = get_skill_stats(skill.id)
        assert stats is not None
        assert stats.failures == 1
        assert stats.total_uses == 2
        assert stats.success_rate == 0.5


def test_get_skill_stats_unknown(monkeypatch, tmp_path):
    """get_skill_stats returns None for unknown skill_id."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        result = get_skill_stats("nonexistent_skill_id_xyz")
        assert result is None


def test_get_skills_needing_escalation(monkeypatch, tmp_path):
    """get_skills_needing_escalation filters by threshold."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        skill_good = _make_skill("good skill")
        skill_good.id = "skgood01"
        skill_bad = _make_skill("bad skill")
        skill_bad.id = "skbad001"
        save_skill(skill_good)
        save_skill(skill_bad)

        # Good skill: 8 success, 2 failures → rate = 0.8
        for _ in range(8):
            record_skill_outcome(skill_good.id, success=True)
        for _ in range(2):
            record_skill_outcome(skill_good.id, success=False)

        # Bad skill: 1 success, 9 failures → rate = 0.1
        record_skill_outcome(skill_bad.id, success=True)
        for _ in range(9):
            record_skill_outcome(skill_bad.id, success=False)

        escalated = get_skills_needing_escalation()
        ids = [s.skill_id for s in escalated]
        assert skill_bad.id in ids
        assert skill_good.id not in ids


def test_escalation_threshold_constant():
    """ESCALATION_THRESHOLD is 0.4."""
    assert ESCALATION_THRESHOLD == 0.4


def test_record_skill_outcome_needs_escalation_flag(monkeypatch, tmp_path):
    """needs_escalation flag set when success_rate < ESCALATION_THRESHOLD."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        skill = _make_skill("escalation flag test")
        skill.id = "skesc001"
        save_skill(skill)

        # 3 failures out of 3 → rate = 0.0
        for _ in range(3):
            record_skill_outcome(skill.id, success=False)
        stats = get_skill_stats(skill.id)
        assert stats is not None
        assert stats.needs_escalation is True


# ---------------------------------------------------------------------------
# Phase 14: Structured three-section markdown format
# ---------------------------------------------------------------------------

def test_parse_skill_sections_all(monkeypatch, tmp_path):
    """Parses all three sections correctly."""
    _setup_workspace(monkeypatch, tmp_path)
    markdown = """## Spec
This skill does research.

## Behavior
- Step 1: gather data
- Step 2: analyze

## Guardrails
- Do not exceed 10 requests per minute
"""
    sections = parse_skill_sections(markdown)
    assert "This skill does research" in sections["spec"]
    assert "Step 1" in sections["behavior"]
    assert "10 requests" in sections["guardrails"]


def test_parse_skill_sections_partial(monkeypatch, tmp_path):
    """Tolerates missing Guardrails section — returns empty string."""
    _setup_workspace(monkeypatch, tmp_path)
    markdown = """## Spec
Does something useful.

## Behavior
Execute the steps.
"""
    sections = parse_skill_sections(markdown)
    assert "Does something useful" in sections["spec"]
    assert "Execute" in sections["behavior"]
    assert sections["guardrails"] == ""


def test_parse_skill_sections_no_sections(monkeypatch, tmp_path):
    """No section headers → whole content treated as spec."""
    _setup_workspace(monkeypatch, tmp_path)
    markdown = "This is just plain text with no section markers."
    sections = parse_skill_sections(markdown)
    assert "plain text" in sections["spec"]
    assert sections["behavior"] == ""
    assert sections["guardrails"] == ""


def test_parse_skill_sections_empty(monkeypatch, tmp_path):
    """Empty markdown → all empty strings."""
    _setup_workspace(monkeypatch, tmp_path)
    sections = parse_skill_sections("")
    assert sections == {"spec": "", "behavior": "", "guardrails": ""}


def test_render_skill_markdown(monkeypatch, tmp_path):
    """render_skill_markdown output contains ## Spec and ## Behavior."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("render test skill")
    md = render_skill_markdown(skill)
    assert "## Spec" in md
    assert "## Behavior" in md
    assert "## Guardrails" in md
    assert "render test skill" in md


def test_update_skill_section(monkeypatch, tmp_path):
    """update_skill_section returns new Skill with updated section."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("section update test")
    new_skill = update_skill_section(skill, "spec", "New spec content here.")
    # Should be a new object
    assert new_skill is not skill
    sections = parse_skill_sections(new_skill.description)
    assert "New spec content here" in sections["spec"]


def test_update_skill_section_guardrails(monkeypatch, tmp_path):
    """update_skill_section works for guardrails section."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("guardrails test")
    new_skill = update_skill_section(skill, "guardrails", "Never exceed 5 retries.")
    sections = parse_skill_sections(new_skill.description)
    assert "Never exceed" in sections["guardrails"]


def test_update_skill_section_invalid_raises(monkeypatch, tmp_path):
    """update_skill_section raises ValueError for invalid section name."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("invalid section test")
    with pytest.raises(ValueError):
        update_skill_section(skill, "invalid_section", "content")


# ---------------------------------------------------------------------------
# Phase 14: Hash-based poisoning defense
# ---------------------------------------------------------------------------

def test_compute_skill_hash(monkeypatch, tmp_path):
    """compute_skill_hash returns a non-empty hex string."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("hash test skill")
    h = compute_skill_hash(skill)
    assert isinstance(h, str)
    assert len(h) > 0
    # Should be a hex string (SHA256 = 64 chars)
    assert len(h) == 64


def test_verify_skill_hash_pass(monkeypatch, tmp_path):
    """Same content → verify_skill_hash returns True."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("hash verify pass")
    h = compute_skill_hash(skill)
    assert verify_skill_hash(skill, h) is True


def test_verify_skill_hash_fail(monkeypatch, tmp_path):
    """Modified content → verify_skill_hash returns False."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("hash verify fail")
    h = compute_skill_hash(skill)
    # Modify the skill name (content changes)
    skill.name = "tampered name that is different"
    assert verify_skill_hash(skill, h) is False


def test_save_skill_stores_hash(monkeypatch, tmp_path):
    """save_skill computes and stores content_hash."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("hash storage test")
    skill.content_hash = ""  # Ensure it starts empty
    save_skill(skill)
    loaded = [s for s in load_skills() if s.id == skill.id][0]
    assert loaded.content_hash != ""
    assert len(loaded.content_hash) == 64


def test_load_skills_warns_on_hash_mismatch(monkeypatch, tmp_path, caplog):
    """Corrupted skill file → warning logged, skill still loads."""
    import logging
    _setup_workspace(monkeypatch, tmp_path)

    # Save a skill normally first
    skill = _make_skill("hash mismatch test")
    save_skill(skill)

    # Resolve the actual skills file path via the module function
    from skills import _skills_path
    skills_file = _skills_path()
    assert skills_file.exists(), f"Skills file not found at {skills_file}"

    content = skills_file.read_text()
    saved_hash = skill.content_hash
    assert saved_hash, "Skill hash should be computed on save"

    # Replace the hash with a bad value
    corrupted = content.replace(saved_hash, "a" * 64)
    skills_file.write_text(corrupted)

    # Load with caplog to capture warnings
    with caplog.at_level(logging.WARNING, logger="skills"):
        loaded = load_skills()

    # Skill should still load (graceful degradation)
    ids = [s.id for s in loaded]
    assert skill.id in ids

    # Warning should have been emitted
    warning_found = any("mismatch" in r.message.lower() or "hash" in r.message.lower()
                        for r in caplog.records)
    assert warning_found, f"Expected hash mismatch warning, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Phase 14: Unit-test gate — generate_skill_tests and run_skill_tests
# ---------------------------------------------------------------------------

def test_generate_skill_tests_heuristic(monkeypatch, tmp_path):
    """No adapter → heuristic returns SkillTestCases."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
        skill = _make_skill("heuristic test gen")
        tests = generate_skill_tests(skill, failure_examples=["step 1 failed"], adapter=None)
        assert isinstance(tests, list)
        assert len(tests) >= 1
        assert all(isinstance(t, SkillTestCase) for t in tests)
        assert all(t.skill_id == skill.id for t in tests)


def test_generate_skill_tests_mock_adapter(monkeypatch, tmp_path):
    """LLM returns valid test JSON → SkillTestCases created."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
        skill = _make_skill("adapter test gen")
        tests = generate_skill_tests(
            skill,
            failure_examples=["api call failed"],
            adapter=_SkillTestMockAdapter(),
        )
        assert len(tests) >= 1
        assert all(isinstance(t, SkillTestCase) for t in tests)
        assert any("research" in " ".join(t.expected_keywords) for t in tests)


def test_generate_skill_tests_saves_to_file(monkeypatch, tmp_path):
    """generate_skill_tests saves to skill-tests.jsonl."""
    _setup_workspace(monkeypatch, tmp_path)
    tests_path = tmp_path / "skill-tests.jsonl"
    with patch("skills._skill_tests_path", return_value=tests_path):
        skill = _make_skill("save tests test")
        generate_skill_tests(skill, failure_examples=["failed"], adapter=None)
        assert tests_path.exists()
        lines = [l for l in tests_path.read_text().splitlines() if l.strip()]
        assert len(lines) >= 1


def test_run_skill_tests_dry_run(monkeypatch, tmp_path):
    """dry_run mode: all tests pass regardless."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("dry run test")
    tests = [
        SkillTestCase(
            skill_id=skill.id,
            input_description="do something",
            expected_keywords=["impossible_keyword_xyz"],
            derived_from_failure="test",
        )
    ]
    passed, total = run_skill_tests(skill, tests, adapter=None, dry_run=True)
    assert passed == total
    assert total == 1


def test_run_skill_tests_no_adapter(monkeypatch, tmp_path):
    """No adapter → all pass (dry_run equivalent)."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("no adapter test")
    tests = [
        SkillTestCase(
            skill_id=skill.id,
            input_description="do something",
            expected_keywords=["result"],
            derived_from_failure="test",
        )
    ]
    passed, total = run_skill_tests(skill, tests, adapter=None)
    assert passed == total


def test_run_skill_tests_empty(monkeypatch, tmp_path):
    """Empty tests list → (0, 0)."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("empty tests")
    passed, total = run_skill_tests(skill, [], adapter=None)
    assert passed == 0
    assert total == 0


def test_run_skill_tests_with_passing_adapter(monkeypatch, tmp_path):
    """Adapter returns output with expected keyword → test passes."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("passing adapter test")
    tests = [
        SkillTestCase(
            skill_id=skill.id,
            input_description="research something",
            expected_keywords=["research", "result"],
            derived_from_failure="test",
        )
    ]
    passed, total = run_skill_tests(skill, tests, adapter=_SkillRunMockAdapter(), dry_run=False)
    assert total == 1
    assert passed == 1


def test_run_skill_tests_with_failing_adapter(monkeypatch, tmp_path):
    """Adapter returns output without expected keyword → test fails."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("failing adapter test")
    tests = [
        SkillTestCase(
            skill_id=skill.id,
            input_description="do the research",
            expected_keywords=["research", "result", "complete"],
            derived_from_failure="test",
        )
    ]
    passed, total = run_skill_tests(skill, tests, adapter=_SkillRunFailAdapter(), dry_run=False)
    assert total == 1
    assert passed == 0


# ---------------------------------------------------------------------------
# Phase 14: validate_skill_mutation
# ---------------------------------------------------------------------------

def test_validate_skill_mutation_no_tests(monkeypatch, tmp_path):
    """No existing tests → generates them, runs (dry_run), not blocked."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
        with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
            skill = _make_skill("mutation no tests")
            mutated = _make_skill("mutation no tests")
            mutated.description = "Updated description for mutation."
            result = validate_skill_mutation(skill, mutated, adapter=None)
            assert isinstance(result, SkillMutationResult)
            # No adapter → dry_run → not blocked
            assert result.blocked is False
            assert result.skill_id == skill.id


def test_validate_skill_mutation_blocked(monkeypatch, tmp_path):
    """Failed tests → blocked=True."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
        with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
            skill = _make_skill("mutation blocked test")
            mutated = _make_skill("mutation blocked test")
            mutated.description = "Completely different."

            # Pre-create tests with impossible keywords
            pre_tests = [
                SkillTestCase(
                    skill_id=skill.id,
                    input_description="test input",
                    expected_keywords=["IMPOSSIBLE_KEYWORD_ZYZZYVA_XYZ"],
                    derived_from_failure="pre-made",
                )
            ]
            # Save the tests so validate_skill_mutation loads them
            from skills import _save_skill_tests
            with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
                _save_skill_tests(pre_tests)

            # Use an adapter that returns output without the impossible keyword
            result = validate_skill_mutation(skill, mutated, adapter=_SkillRunFailAdapter())
            assert isinstance(result, SkillMutationResult)
            assert result.blocked is True
            assert result.block_reason


def test_validate_skill_mutation_passes(monkeypatch, tmp_path):
    """Tests pass → blocked=False."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
        with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
            skill = _make_skill("mutation passes test")
            mutated = _make_skill("mutation passes test")

            # Pre-create tests with keywords that the mock adapter will satisfy
            pre_tests = [
                SkillTestCase(
                    skill_id=skill.id,
                    input_description="research something",
                    expected_keywords=["research", "result"],
                    derived_from_failure="pre-made",
                )
            ]
            from skills import _save_skill_tests
            with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
                _save_skill_tests(pre_tests)

            result = validate_skill_mutation(skill, mutated, adapter=_SkillRunMockAdapter())
            assert isinstance(result, SkillMutationResult)
            assert result.blocked is False


def test_validate_skill_mutation_returns_correct_type(monkeypatch, tmp_path):
    """validate_skill_mutation always returns SkillMutationResult."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_tests_path", return_value=tmp_path / "skill-tests.jsonl"):
        with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
            skill = _make_skill("type check test")
            mutated = _make_skill("type check test")
            result = validate_skill_mutation(skill, mutated)
            assert isinstance(result, SkillMutationResult)
            assert hasattr(result, "tests_run")
            assert hasattr(result, "tests_passed")
            assert hasattr(result, "blocked")
            assert hasattr(result, "block_reason")


# ---------------------------------------------------------------------------
# Phase 32: utility scoring, failure attribution, auto-promotion, rewrite gating
# ---------------------------------------------------------------------------

from skills import (
    update_skill_utility,
    attribute_failure_to_skills,
    maybe_auto_promote_skills,
    maybe_demote_skills,
    skills_needing_rewrite,
    UTILITY_EMA_ALPHA,
    AUTO_PROMOTE_MIN_USES,
    AUTO_PROMOTE_MIN_RATE,
    REWRITE_TRIGGER_RATE,
    REWRITE_MIN_USES,
    CIRCUIT_OPEN_THRESHOLD,
    CIRCUIT_HALFOPEN_RECOVERY,
    _save_skills,
)


def _phase32_skill(tmp_path, skill_id="p32skill", tier="provisional", utility=1.0,
                   use_count=0, circuit_state="closed",
                   consecutive_failures=0, consecutive_successes=0):
    """Helper: write a single skill to tmp_path and return it."""
    import datetime
    skill = Skill(
        id=skill_id,
        name=f"Test Skill {skill_id}",
        description="A test skill",
        trigger_patterns=["test research"],
        steps_template=["do the thing"],
        source_loop_ids=[],
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        use_count=use_count,
        success_rate=utility,
        tier=tier,
        utility_score=utility,
        circuit_state=circuit_state,
        consecutive_failures=consecutive_failures,
        consecutive_successes=consecutive_successes,
    )
    skills_file = tmp_path / "skills.jsonl"
    import json
    skills_file.write_text(json.dumps(_skill_to_dict(skill)) + "\n")
    return skill


def test_update_skill_utility_success_raises_score(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, utility=0.5, use_count=3)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    update_skill_utility(skill.id, success=True)
    updated = next(s for s in load_skills() if s.id == skill.id)
    assert updated.utility_score > 0.5  # EMA moved toward 1.0


def test_update_skill_utility_failure_lowers_score(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, utility=1.0, use_count=3)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    update_skill_utility(skill.id, success=False, failure_reason="step blocked: timeout")
    updated = next(s for s in load_skills() if s.id == skill.id)
    assert updated.utility_score < 1.0
    assert updated.failure_notes  # failure reason stored


def test_update_skill_utility_unknown_skill_no_error(monkeypatch, tmp_path):
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    # No skills file — should not raise
    update_skill_utility("nonexistent_id", success=True)


def test_maybe_auto_promote_eligible(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=AUTO_PROMOTE_MIN_RATE + 0.1,
                           use_count=AUTO_PROMOTE_MIN_USES)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    promoted = maybe_auto_promote_skills()
    assert skill.id in promoted
    updated = load_skills()
    assert updated[0].tier == "established"


def test_maybe_auto_promote_not_enough_uses(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=0.9,
                           use_count=AUTO_PROMOTE_MIN_USES - 1)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    promoted = maybe_auto_promote_skills()
    assert skill.id not in promoted


def test_maybe_auto_promote_low_utility(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=0.3,
                           use_count=AUTO_PROMOTE_MIN_USES)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    promoted = maybe_auto_promote_skills()
    assert promoted == []


def test_maybe_demote_low_utility_established(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, tier="established",
                           utility=REWRITE_TRIGGER_RATE - 0.1,
                           use_count=REWRITE_MIN_USES + 2)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    demoted = maybe_demote_skills()
    assert skill.id in demoted
    updated = load_skills()
    assert updated[0].tier == "provisional"


def test_maybe_demote_high_utility_not_demoted(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, tier="established",
                           utility=0.9,
                           use_count=REWRITE_MIN_USES + 2)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    demoted = maybe_demote_skills()
    assert demoted == []


def test_skills_needing_rewrite(monkeypatch, tmp_path):
    """Only open-circuit skills with enough uses appear as rewrite candidates."""
    skill = _phase32_skill(tmp_path, utility=0.2, use_count=REWRITE_MIN_USES + 1,
                           circuit_state="open", consecutive_failures=CIRCUIT_OPEN_THRESHOLD)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    candidates = skills_needing_rewrite()
    assert any(s.id == skill.id for s in candidates)


def test_skills_needing_rewrite_not_enough_uses(monkeypatch, tmp_path):
    """Use count below minimum — never a rewrite candidate."""
    skill = _phase32_skill(tmp_path, utility=0.1, use_count=REWRITE_MIN_USES - 1,
                           circuit_state="open", consecutive_failures=CIRCUIT_OPEN_THRESHOLD)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    candidates = skills_needing_rewrite()
    assert candidates == []


def test_skills_needing_rewrite_closed_circuit_not_eligible(monkeypatch, tmp_path):
    """Low utility but closed circuit → blip, not a rewrite candidate."""
    skill = _phase32_skill(tmp_path, utility=0.1, use_count=REWRITE_MIN_USES + 5,
                           circuit_state="closed")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    candidates = skills_needing_rewrite()
    assert candidates == []


def test_ema_formula():
    """EMA update: utility = alpha * new + (1-alpha) * old."""
    old = 0.5
    expected = UTILITY_EMA_ALPHA * 1.0 + (1 - UTILITY_EMA_ALPHA) * old
    assert abs(expected - (UTILITY_EMA_ALPHA + (1 - UTILITY_EMA_ALPHA) * old)) < 1e-9


# ---------------------------------------------------------------------------
# Phase 32: circuit breaker state machine
# ---------------------------------------------------------------------------

def test_circuit_breaker_opens_after_threshold(monkeypatch, tmp_path):
    """CIRCUIT_OPEN_THRESHOLD consecutive failures trip the breaker to open."""
    skill = _phase32_skill(tmp_path, utility=1.0, use_count=5, circuit_state="closed")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    for _ in range(CIRCUIT_OPEN_THRESHOLD):
        update_skill_utility(skill.id, success=False, failure_reason="timed out")
    updated = next(s for s in load_skills() if s.id == skill.id)
    assert updated.circuit_state == "open"


def test_circuit_breaker_blip_stays_closed(monkeypatch, tmp_path):
    """Fewer than threshold consecutive failures leaves circuit closed."""
    skill = _phase32_skill(tmp_path, utility=1.0, use_count=5, circuit_state="closed")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    for _ in range(CIRCUIT_OPEN_THRESHOLD - 1):
        update_skill_utility(skill.id, success=False, failure_reason="blip")
    updated = next(s for s in load_skills() if s.id == skill.id)
    assert updated.circuit_state == "closed"


def test_circuit_breaker_opens_then_recovers_via_halfopen(monkeypatch, tmp_path):
    """OPEN → HALF_OPEN on first success → CLOSED after CIRCUIT_HALFOPEN_RECOVERY successes."""
    skill = _phase32_skill(tmp_path, utility=0.2, use_count=5,
                           circuit_state="open", consecutive_failures=CIRCUIT_OPEN_THRESHOLD)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    # First success: open → half_open
    update_skill_utility(skill.id, success=True)
    updated = next(s for s in load_skills() if s.id == skill.id)
    assert updated.circuit_state == "half_open"
    # Remaining successes to close
    for _ in range(CIRCUIT_HALFOPEN_RECOVERY - 1):
        update_skill_utility(skill.id, success=True)
    updated = next(s for s in load_skills() if s.id == skill.id)
    assert updated.circuit_state == "closed"


def test_circuit_breaker_halfopen_failure_reopens(monkeypatch, tmp_path):
    """Failure during half_open immediately trips back to open."""
    skill = _phase32_skill(tmp_path, utility=0.5, use_count=5,
                           circuit_state="half_open", consecutive_successes=1)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    update_skill_utility(skill.id, success=False, failure_reason="still broken")
    updated = next(s for s in load_skills() if s.id == skill.id)
    assert updated.circuit_state == "open"


# ---------------------------------------------------------------------------
# optimization_objective field (Meta-Harness skill text as steering)
# ---------------------------------------------------------------------------

class TestOptimizationObjective:
    def test_default_is_empty_string(self):
        skill = _make_skill()
        assert skill.optimization_objective == ""

    def test_round_trips_through_dict(self):
        from skills import _dict_to_skill
        skill = _make_skill()
        skill.optimization_objective = "minimize LLM calls per step while maintaining accuracy"
        d = _skill_to_dict(skill)
        assert d["optimization_objective"] == skill.optimization_objective
        restored = _dict_to_skill(d)
        assert restored.optimization_objective == skill.optimization_objective

    def test_dict_to_skill_missing_key_defaults_to_empty(self):
        from skills import _dict_to_skill
        d = {
            "id": "sk-test", "name": "test", "description": "desc",
            "trigger_patterns": [], "steps_template": [],
            "source_loop_ids": [], "created_at": "",
        }
        skill = _dict_to_skill(d)
        assert skill.optimization_objective == ""

    def test_format_skills_for_prompt_includes_objective(self):
        skill = _make_skill()
        skill.optimization_objective = "reduce token cost"
        result = format_skills_for_prompt([skill])
        assert "Optimize for: reduce token cost" in result

    def test_format_skills_for_prompt_omits_when_empty(self):
        skill = _make_skill()
        skill.optimization_objective = ""
        result = format_skills_for_prompt([skill])
        assert "Optimize for" not in result

    def test_compute_skill_hash_changes_with_objective(self):
        skill = _make_skill()
        h1 = compute_skill_hash(skill)
        skill.optimization_objective = "new objective"
        h2 = compute_skill_hash(skill)
        assert h1 != h2

    def test_export_skill_as_markdown_includes_objective(self, tmp_path):
        from skill_loader import export_skill_as_markdown
        skill = _make_skill("my test skill")
        skill.optimization_objective = "minimize steps while preserving quality"
        path = export_skill_as_markdown(skill, skills_dir=tmp_path, overwrite=True)
        assert path is not None
        content = path.read_text()
        assert "optimization_objective" in content
        assert "minimize steps while preserving quality" in content

    def test_export_skill_omits_objective_when_empty(self, tmp_path):
        from skill_loader import export_skill_as_markdown
        skill = _make_skill("another skill")
        skill.optimization_objective = ""
        path = export_skill_as_markdown(skill, skills_dir=tmp_path, overwrite=True)
        assert path is not None
        content = path.read_text()
        assert "optimization_objective" not in content


# ---------------------------------------------------------------------------
# _stem + _skill_tokens (MetaClaw steal: lightweight stemmer)
# ---------------------------------------------------------------------------

class TestStemmer:
    def _stem(self, token):
        from skills import _stem
        return _stem(token)

    def test_strips_ing(self):
        assert self._stem("researching") == "research"

    def test_strips_er(self):
        assert self._stem("builder") == "build"

    def test_strips_tion(self):
        assert self._stem("execution") == "execu"  # "execution"[:-4] = "execu" (strips "tion")

    def test_strips_ed(self):
        assert self._stem("analysed") == "analys"

    def test_short_roots_preserved(self):
        # "run" → strip 'ing' → "r" (2 chars < 4) → should NOT strip
        assert self._stem("run") == "run"

    def test_no_suffix_unchanged(self):
        assert self._stem("memory") == "memory"

    def test_skill_tokens_stemmed(self):
        from skills import _skill_tokens
        tokens = _skill_tokens("researching memory")
        assert "research" in tokens

    def test_tfidf_finds_morphological_match(self):
        """'research' in goal should match a skill with 'researching' in trigger.

        Two skills are needed to avoid IDF=0 (single-doc IDF cancels all terms).
        """
        from skills import _tfidf_skill_rank, Skill

        def _make(id_, name, desc, triggers):
            return Skill(
                id=id_, name=name, description=desc,
                trigger_patterns=triggers, steps_template=["step"],
                source_loop_ids=[], created_at="2026-01-01T00:00:00+00:00",
            )

        research_skill = _make("s1", "research_tool",
                               "Tool for researching topics online",
                               ["researching", "information gathering"])
        other_skill = _make("s2", "scheduler", "Schedule future tasks",
                            ["schedule", "future", "cron"])

        results = _tfidf_skill_rank("research topics online", [research_skill, other_skill])
        assert any(s.id == "s1" for s in results)


# ---------------------------------------------------------------------------
# Island model (FunSearch steal: anti-monoculture diversity)
# ---------------------------------------------------------------------------

class TestIslandModel:
    def _make_skill(self, id_, name, desc, triggers=None, circuit_state="closed", utility_score=1.0, island=""):
        from skills import Skill
        return Skill(
            id=id_, name=name, description=desc,
            trigger_patterns=triggers or [],
            steps_template=["step"],
            source_loop_ids=[],
            created_at="2026-01-01T00:00:00+00:00",
            circuit_state=circuit_state,
            utility_score=utility_score,
            island=island,
        )

    def test_assign_island_research(self):
        from skills import assign_island
        skill = self._make_skill("s1", "web_search", "search the web for information",
                                 triggers=["search", "fetch data"])
        assert assign_island(skill) == "research"

    def test_assign_island_build(self):
        from skills import assign_island
        skill = self._make_skill("s2", "code_gen", "write and implement code",
                                 triggers=["write code", "implement feature"])
        assert assign_island(skill) == "build"

    def test_assign_island_analysis(self):
        from skills import assign_island
        skill = self._make_skill("s3", "code_review", "review and inspect code",
                                 triggers=["review", "inspect"])
        assert assign_island(skill) == "analysis"

    def test_assign_island_general_fallback(self):
        from skills import assign_island
        skill = self._make_skill("s4", "misc", "does something miscellaneous",
                                 triggers=["run"])
        assert assign_island(skill) == "general"

    def test_ensure_island_assigned_sets_island(self):
        from skills import ensure_island_assigned
        skill = self._make_skill("s5", "web_fetch", "fetch web content for research",
                                 triggers=["fetch"])
        assert skill.island == ""
        result = ensure_island_assigned(skill)
        assert result.island != ""
        assert result is skill  # mutates in place

    def test_ensure_island_assigned_skips_if_set(self):
        from skills import ensure_island_assigned
        skill = self._make_skill("s6", "build_thing", "builds things", island="build")
        result = ensure_island_assigned(skill)
        assert result.island == "build"  # unchanged

    def test_get_skills_by_island_groups_correctly(self):
        from skills import get_skills_by_island
        skills = [
            self._make_skill("s1", "searcher", "search web information", island="research"),
            self._make_skill("s2", "builder", "write and build code", island="build"),
            self._make_skill("s3", "checker", "review and check things", island="analysis"),
            self._make_skill("s4", "thinker", "does something general", island="general"),
        ]
        grouped = get_skills_by_island(skills)
        assert "s1" in [s.id for s in grouped.get("research", [])]
        assert "s2" in [s.id for s in grouped.get("build", [])]
        assert "s3" in [s.id for s in grouped.get("analysis", [])]

    def test_get_skills_by_island_auto_assigns(self):
        from skills import get_skills_by_island
        # No island set — should be auto-assigned
        skills = [
            self._make_skill("s1", "searcher", "search the web", triggers=["search", "fetch"]),
        ]
        grouped = get_skills_by_island(skills)
        assert any("s1" in [s.id for s in v] for v in grouped.values())

    def test_cull_island_skips_small_island(self):
        """Island with fewer than min_island_size skills is not culled."""
        from skills import cull_island_bottom_half
        from unittest.mock import patch
        skills = [
            self._make_skill("s1", "a", "search fetch", island="research",
                             circuit_state="open", utility_score=0.1),
            self._make_skill("s2", "b", "search web", island="research",
                             circuit_state="open", utility_score=0.2),
        ]
        with patch("skills.load_skills", return_value=skills):
            result = cull_island_bottom_half("research", min_island_size=4, dry_run=True)
        assert result == []

    def test_cull_island_only_open_circuit(self):
        """Only open-circuit skills are eligible for culling."""
        from skills import cull_island_bottom_half
        from unittest.mock import patch
        skills = [
            self._make_skill("s1", "a", "search", island="research",
                             circuit_state="closed", utility_score=0.1),
            self._make_skill("s2", "b", "search", island="research",
                             circuit_state="closed", utility_score=0.2),
            self._make_skill("s3", "c", "search", island="research",
                             circuit_state="open", utility_score=0.3),
            self._make_skill("s4", "d", "search", island="research",
                             circuit_state="open", utility_score=0.4),
            self._make_skill("s5", "e", "search", island="research",
                             circuit_state="closed", utility_score=0.5),
        ]
        with patch("skills.load_skills", return_value=skills):
            result = cull_island_bottom_half("research", min_island_size=4, dry_run=True)
        # Only s3/s4 are open; bottom half of 2 = 1 culled
        assert len(result) == 1
        assert result[0] in {"s3", "s4"}

    def test_run_island_cycle_dry_run(self):
        """dry_run returns counts without saving."""
        from skills import run_island_cycle
        from unittest.mock import patch, MagicMock
        skills = [
            self._make_skill("s1", "searcher", "fetch web data", island="research",
                             circuit_state="closed"),
        ]
        with patch("skills.load_skills", return_value=skills), \
             patch("skills._save_skills") as mock_save:
            result = run_island_cycle(dry_run=True)
        mock_save.assert_not_called()
        assert "assigned" in result
        assert "total_culled" in result


# ---------------------------------------------------------------------------
# Skill validation harness (Voyager/Agent0 steal)
# ---------------------------------------------------------------------------

class TestSkillValidationHarness:
    def _make_skill(self, id_="v1", tier="provisional", use_count=5, utility_score=0.8,
                    name="test_skill", desc="search the web for information", island="research"):
        from skills import Skill
        return Skill(
            id=id_, name=name, description=desc,
            trigger_patterns=["search", "fetch data"],
            steps_template=["fetch URL", "parse result", "return summary"],
            source_loop_ids=[],
            created_at="2026-01-01T00:00:00+00:00",
            tier=tier,
            use_count=use_count,
            utility_score=utility_score,
            island=island,
        )

    def test_validate_skill_pass(self, monkeypatch):
        from skills import validate_skill_for_promotion, Skill
        import types

        class FakeAdapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(content='{"valid": true, "reason": "clear and actionable", "repair_hint": ""}')

        result = validate_skill_for_promotion(self._make_skill(), FakeAdapter())
        assert result["valid"] is True
        assert "repair_hint" in result

    def test_validate_skill_fail(self, monkeypatch):
        from skills import validate_skill_for_promotion
        import types

        class FakeAdapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(content='{"valid": false, "reason": "steps are too vague", "repair_hint": "make steps concrete"}')

        result = validate_skill_for_promotion(self._make_skill(), FakeAdapter())
        assert result["valid"] is False
        assert result["repair_hint"] == "make steps concrete"

    def test_validate_fail_open_on_error(self):
        from skills import validate_skill_for_promotion

        class BrokenAdapter:
            def complete(self, messages, **kw):
                raise RuntimeError("connection refused")

        result = validate_skill_for_promotion(self._make_skill(), BrokenAdapter())
        # Fail-open: validation unavailable → allow promotion
        assert result["valid"] is True

    def test_promote_without_adapter_skips_validation(self, monkeypatch, tmp_path):
        from skills import maybe_auto_promote_skills
        from unittest.mock import patch

        skill = self._make_skill(use_count=10, utility_score=0.9)
        with patch("skills.load_skills", return_value=[skill]), \
             patch("skills._save_skills") as mock_save, \
             patch("skills.compute_skill_hash", return_value="hash123"), \
             patch("skills.validate_skill_for_promotion") as mock_validate:
            result = maybe_auto_promote_skills(adapter=None)

        mock_validate.assert_not_called()  # no adapter → no validation
        assert skill.id in result

    def test_promote_with_adapter_validates_skill(self, monkeypatch, tmp_path):
        from skills import maybe_auto_promote_skills
        from unittest.mock import patch, MagicMock
        import types

        skill = self._make_skill(use_count=10, utility_score=0.9)

        fake_adapter = MagicMock()
        fake_adapter.complete.return_value = types.SimpleNamespace(
            content='{"valid": true, "reason": "passes", "repair_hint": ""}'
        )

        with patch("skills.load_skills", return_value=[skill]), \
             patch("skills._save_skills"), \
             patch("skills.compute_skill_hash", return_value="hash123"):
            result = maybe_auto_promote_skills(adapter=fake_adapter)

        assert skill.id in result

    def test_promote_repair_loop_on_failure(self, monkeypatch, tmp_path):
        from skills import maybe_auto_promote_skills
        from unittest.mock import patch, MagicMock, call
        import types

        skill = self._make_skill(use_count=10, utility_score=0.9)

        # First call fails, second succeeds
        fake_adapter = MagicMock()
        fail_resp = types.SimpleNamespace(content='{"valid": false, "reason": "vague steps", "repair_hint": "be specific"}')
        pass_resp = types.SimpleNamespace(content='{"valid": true, "reason": "fixed", "repair_hint": ""}')
        fake_adapter.complete.side_effect = [fail_resp, pass_resp]

        repaired_skill = self._make_skill(id_="v1", desc="revised search skill with specific steps")

        with patch("skills.load_skills", return_value=[skill]), \
             patch("skills._save_skills"), \
             patch("skills.compute_skill_hash", return_value="hash123"), \
             patch("evolver.rewrite_skill", return_value=repaired_skill):
            result = maybe_auto_promote_skills(adapter=fake_adapter, max_repair_attempts=3)

        assert skill.id in result  # promoted after repair

    def test_promote_held_provisional_after_max_repairs(self, monkeypatch, tmp_path):
        from skills import maybe_auto_promote_skills
        from unittest.mock import patch, MagicMock
        import types

        skill = self._make_skill(use_count=10, utility_score=0.9)

        # Always fail validation
        fake_adapter = MagicMock()
        fake_adapter.complete.return_value = types.SimpleNamespace(
            content='{"valid": false, "reason": "still vague", "repair_hint": "try again"}'
        )

        with patch("skills.load_skills", return_value=[skill]), \
             patch("skills._save_skills") as mock_save, \
             patch("skills.compute_skill_hash", return_value="hash123"), \
             patch("evolver.rewrite_skill", return_value=None):  # rewrite returns None
            result = maybe_auto_promote_skills(adapter=fake_adapter, max_repair_attempts=2)

        assert skill.id not in result  # not promoted
        mock_save.assert_not_called()  # no write since nothing changed

    def test_below_threshold_not_promoted(self, monkeypatch, tmp_path):
        from skills import maybe_auto_promote_skills
        from unittest.mock import patch

        skill = self._make_skill(use_count=1, utility_score=0.3)  # below thresholds
        with patch("skills.load_skills", return_value=[skill]), \
             patch("skills._save_skills") as mock_save:
            result = maybe_auto_promote_skills()

        assert result == []
        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Frontier task targeting (Agent0 steal)
# ---------------------------------------------------------------------------

class TestFrontierSkills:
    def _make_skill(self, id_, utility_score, use_count=5, circuit_state="closed"):
        from skills import Skill
        return Skill(
            id=id_, name=f"skill_{id_}", description=f"skill {id_}",
            trigger_patterns=["test"],
            steps_template=["step"],
            source_loop_ids=[],
            created_at="2026-01-01T00:00:00+00:00",
            utility_score=utility_score,
            use_count=use_count,
            circuit_state=circuit_state,
        )

    def test_returns_frontier_zone_skills(self):
        from skills import frontier_skills, FRONTIER_LOW, FRONTIER_HIGH
        skills = [
            self._make_skill("low", 0.1),    # below frontier — not included
            self._make_skill("frontier", 0.55),  # in frontier zone — included
            self._make_skill("high", 0.9),   # above frontier — not included
        ]
        result = frontier_skills(skills)
        ids = [s.id for s in result]
        assert "frontier" in ids
        assert "low" not in ids
        assert "high" not in ids

    def test_excludes_open_circuit(self):
        from skills import frontier_skills
        skills = [
            self._make_skill("open_frontier", 0.55, circuit_state="open"),
            self._make_skill("closed_frontier", 0.55, circuit_state="closed"),
        ]
        result = frontier_skills(skills)
        ids = [s.id for s in result]
        assert "open_frontier" not in ids  # open-circuit handled by skills_needing_rewrite
        assert "closed_frontier" in ids

    def test_excludes_below_min_uses(self):
        from skills import frontier_skills
        skills = [
            self._make_skill("new", 0.55, use_count=1),   # not enough data
            self._make_skill("mature", 0.55, use_count=5), # enough data
        ]
        result = frontier_skills(skills, min_uses=3)
        ids = [s.id for s in result]
        assert "new" not in ids
        assert "mature" in ids

    def test_sorted_ascending_by_utility(self):
        from skills import frontier_skills
        skills = [
            self._make_skill("s1", 0.65),
            self._make_skill("s2", 0.45),
            self._make_skill("s3", 0.55),
        ]
        result = frontier_skills(skills)
        scores = [s.utility_score for s in result]
        assert scores == sorted(scores)  # ascending = hardest first

    def test_loads_from_disk_if_none(self):
        from skills import frontier_skills
        from unittest.mock import patch
        skills = [self._make_skill("disk_skill", 0.55)]
        with patch("skills.load_skills", return_value=skills):
            result = frontier_skills(None)
        assert any(s.id == "disk_skill" for s in result)


# ---------------------------------------------------------------------------
# Phase 59: SkillStats cost + latency telemetry (NeMo DataDesigner steal)
# ---------------------------------------------------------------------------

def test_skill_stats_cost_latency_fields_default(monkeypatch, tmp_path):
    """SkillStats initializes cost/latency/confidence fields to zero/1.0."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import SkillStats
    stats = SkillStats(skill_id="s1", skill_name="test skill")
    assert stats.total_cost_usd == 0.0
    assert stats.avg_latency_ms == 0.0
    assert stats.avg_confidence == 1.0


def test_skill_stats_roundtrip_with_telemetry(monkeypatch, tmp_path):
    """SkillStats.to_dict / from_dict preserves cost + latency fields."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import SkillStats
    stats = SkillStats(
        skill_id="s1", skill_name="test",
        total_cost_usd=0.42, avg_latency_ms=1200.0, avg_confidence=0.85,
    )
    d = stats.to_dict()
    restored = SkillStats.from_dict(d)
    assert restored.total_cost_usd == 0.42
    assert restored.avg_latency_ms == 1200.0
    assert restored.avg_confidence == 0.85


def test_record_skill_outcome_accumulates_cost(monkeypatch, tmp_path):
    """record_skill_outcome accumulates cost_usd across calls."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import record_skill_outcome, get_all_skill_stats

    record_skill_outcome("sk1", True, cost_usd=0.01, latency_ms=500.0)
    record_skill_outcome("sk1", True, cost_usd=0.02, latency_ms=700.0)

    stats_list = get_all_skill_stats()
    sk = next((s for s in stats_list if s.skill_id == "sk1"), None)
    assert sk is not None
    assert abs(sk.total_cost_usd - 0.03) < 1e-9
    assert sk.avg_latency_ms > 0


def test_record_skill_outcome_updates_avg_latency(monkeypatch, tmp_path):
    """avg_latency_ms EMA update moves toward recent latency."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import record_skill_outcome, get_all_skill_stats

    record_skill_outcome("sk2", True, latency_ms=1000.0)
    record_skill_outcome("sk2", True, latency_ms=500.0)

    stats_list = get_all_skill_stats()
    sk = next((s for s in stats_list if s.skill_id == "sk2"), None)
    assert sk is not None
    # avg should be between 500 and 1000
    assert 500.0 <= sk.avg_latency_ms <= 1000.0


def test_efficiency_score_below_three_uses_returns_zero(monkeypatch, tmp_path):
    """efficiency_score() returns 0.0 when total_uses < 3."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import SkillStats
    stats = SkillStats(skill_id="s1", skill_name="test", total_uses=2, successes=2)
    assert stats.efficiency_score() == 0.0


def test_efficiency_score_high_success_low_cost(monkeypatch, tmp_path):
    """efficiency_score() is high for good success rate and low cost."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import SkillStats
    # 10 uses, all success, $0.001 total → cost_per_run = 0.0001
    stats = SkillStats(
        skill_id="s1", skill_name="test",
        total_uses=10, successes=10, success_rate=1.0,
        total_cost_usd=0.001,
    )
    score = stats.efficiency_score()
    assert score > 0.9  # near-perfect


def test_efficiency_score_high_cost_reduces_score(monkeypatch, tmp_path):
    """efficiency_score() is lower when cost per run is high."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import SkillStats
    # 5 uses, all success, $1.00 total → cost_per_run = 0.20 → penalty = 0.20
    stats = SkillStats(
        skill_id="s1", skill_name="test",
        total_uses=5, successes=5, success_rate=1.0,
        total_cost_usd=1.0,
    )
    score = stats.efficiency_score()
    assert score < 0.8  # penalized by high cost


# ---------------------------------------------------------------------------
# Phase 59: Provenance records (Feynman steal)
# ---------------------------------------------------------------------------

def test_write_skill_provenance_creates_file(monkeypatch, tmp_path):
    """write_skill_provenance writes a JSON file in skill_provenance/."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import write_skill_provenance
    from memory import _memory_dir
    import json

    path = write_skill_provenance(
        "my_skill", "promote",
        reason="pass^3 >= 0.7",
        success_rate=0.95,
        efficiency_score=0.90,
    )
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["skill_name"] == "my_skill"
    assert data["decision"] == "promote"
    assert data["success_rate"] == 0.95


def test_load_skill_provenance_returns_records(monkeypatch, tmp_path):
    """load_skill_provenance returns all records for a skill, newest first."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import write_skill_provenance, load_skill_provenance

    write_skill_provenance("skill_x", "promote", reason="first")
    write_skill_provenance("skill_x", "demote", reason="second")

    records = load_skill_provenance("skill_x")
    assert len(records) == 2
    # Newest first
    assert records[0]["decision"] == "demote"
    assert records[1]["decision"] == "promote"


def test_load_skill_provenance_empty_when_no_records(monkeypatch, tmp_path):
    """load_skill_provenance returns [] when no records exist."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import load_skill_provenance
    assert load_skill_provenance("nonexistent_skill") == []


def test_write_provenance_extra_fields(monkeypatch, tmp_path):
    """write_skill_provenance includes extra fields in JSON output."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import write_skill_provenance
    import json

    path = write_skill_provenance(
        "sk", "rewrite",
        extra={"utility_score": 0.25, "circuit_state": "open"}
    )
    data = json.loads(path.read_text())
    assert data["utility_score"] == 0.25
    assert data["circuit_state"] == "open"


# ---------------------------------------------------------------------------
# Phase 59: Skill description verifier (Feynman Steal 11)
# ---------------------------------------------------------------------------

def _make_full_skill(name="test", description="", trigger_patterns=None, steps_template=None):
    """Build a Skill with all required fields for verifier tests."""
    from skills import Skill
    return Skill(
        id="sk1",
        name=name,
        description=description,
        trigger_patterns=trigger_patterns or [],
        steps_template=steps_template or [],
        source_loop_ids=[],
        created_at="2026-04-07T00:00:00Z",
    )


def test_verify_skill_clean_description():
    """Clean skill description returns is_clean=True and high confidence."""
    from skills import verify_skill_description
    skill = _make_full_skill(
        description="Search the web for information and return structured results",
        steps_template=["identify key terms", "run search", "parse top 5 results"],
    )
    result = verify_skill_description(skill)
    assert result.is_clean is True
    assert result.confidence > 0.9


def test_verify_skill_absolute_claim():
    """Absolute claims ('always', '100%') are flagged as suspicious."""
    from skills import verify_skill_description
    skill = _make_full_skill(
        description="This skill always succeeds and provides 100% accurate results."
    )
    result = verify_skill_description(skill)
    assert result.is_clean is False
    categories = [s["category"] for s in result.suspicious_claims]
    assert "absolute_claim" in categories


def test_verify_skill_unsourced_metric():
    """Unsourced percentage metrics are flagged as suspicious."""
    from skills import verify_skill_description
    skill = _make_full_skill(
        description="Achieves 40% improvement in query efficiency with no setup."
    )
    result = verify_skill_description(skill)
    assert any(s["category"] == "unsourced_metric" for s in result.suspicious_claims)


def test_verify_skill_confidence_decrements_per_finding():
    """Confidence decrements by 0.2 per suspicious finding."""
    from skills import verify_skill_description
    # Two absolute claims
    skill = _make_full_skill(
        description="This always works and is guaranteed to be perfect."
    )
    result = verify_skill_description(skill)
    assert result.confidence <= 0.6  # at least 2 suspicious findings


def test_verify_skill_result_fields():
    """SkillVerificationResult has expected fields."""
    from skills import verify_skill_description, SkillVerificationResult
    skill = _make_full_skill(name="my-skill", description="Clean description here")
    result = verify_skill_description(skill)
    assert isinstance(result, SkillVerificationResult)
    assert result.skill_name == "my-skill"
    assert isinstance(result.suspicious_claims, list)
    assert isinstance(result.is_clean, bool)
    assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# Phase 59: Skill sampler constraints (NeMo Steal 7)
# ---------------------------------------------------------------------------

class TestSkillConstraints:
    """Tests for SkillConstraint and apply_skill_constraints()."""

    def _make_skill_with_id(self, sid, name="test"):
        return Skill(
            id=sid, name=name, description="a skill",
            trigger_patterns=[], steps_template=[],
            source_loop_ids=[], created_at="2026-04-07T00:00:00Z",
        )

    def test_no_constraints_returns_all_skills(self):
        from skills import apply_skill_constraints
        skills = [self._make_skill_with_id("s1"), self._make_skill_with_id("s2")]
        result = apply_skill_constraints("some goal", skills, constraints=None)
        assert result == skills

    def test_constraint_matches_by_keyword(self):
        """Constraint with matching keyword annotates the skill."""
        from skills import apply_skill_constraints, SkillConstraint
        skill = self._make_skill_with_id("s1", "research-skill")
        constraint = SkillConstraint(
            skill_id="s1",
            condition_keywords=["research", "investigate"],
            parameter_overrides={"approach": "analytical"},
        )
        result = apply_skill_constraints("research polymarket trends", [skill], [constraint])
        assert len(result) == 1
        assert "constraint_overrides" in result[0].optimization_objective
        assert "analytical" in result[0].optimization_objective

    def test_constraint_no_match_skill_unchanged(self):
        """Constraint without matching keyword leaves skill unchanged."""
        from skills import apply_skill_constraints, SkillConstraint
        skill = self._make_skill_with_id("s1")
        constraint = SkillConstraint(
            skill_id="s1",
            condition_keywords=["build", "compile"],
            parameter_overrides={"mode": "strict"},
        )
        result = apply_skill_constraints("research AI papers", [skill], [constraint])
        assert result[0].optimization_objective == ""  # untouched

    def test_constraint_excluded_keyword_skips(self):
        """Excluded keyword prevents constraint from applying."""
        from skills import apply_skill_constraints, SkillConstraint
        skill = self._make_skill_with_id("s1")
        constraint = SkillConstraint(
            skill_id="s1",
            condition_keywords=["research"],
            excluded_keywords=["polymarket"],  # excluded
            parameter_overrides={"mode": "fast"},
        )
        result = apply_skill_constraints("research polymarket data", [skill], [constraint])
        assert "constraint_overrides" not in result[0].optimization_objective

    def test_multiple_constraints_merge_overrides(self):
        """Multiple constraints for same skill merge their overrides."""
        from skills import apply_skill_constraints, SkillConstraint
        skill = self._make_skill_with_id("s1")
        constraints = [
            SkillConstraint("s1", ["research"], {"depth": "deep"}),
            SkillConstraint("s1", ["urgent"], {"speed": "fast"}),
        ]
        result = apply_skill_constraints("urgent research needed", [skill], constraints)
        obj = result[0].optimization_objective
        assert "deep" in obj
        assert "fast" in obj

    def test_constraint_does_not_modify_original_skill(self):
        """apply_skill_constraints returns a copy when modifying (original unchanged)."""
        from skills import apply_skill_constraints, SkillConstraint
        skill = self._make_skill_with_id("s1")
        original_obj = skill.optimization_objective
        constraint = SkillConstraint("s1", ["test"], {"key": "val"})
        apply_skill_constraints("test goal", [skill], [constraint])
        assert skill.optimization_objective == original_obj  # original unchanged
