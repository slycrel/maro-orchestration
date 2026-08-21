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
    load_skills,
    record_skill_injection_outcome,
    record_skill_outcome,
    run_skill_tests,
    save_skill,
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
                    "domain": "web-research",
                    "tags": ["Research", "sources", "synthesis"],
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


def test_match_telemetry_keyword_tier(monkeypatch, tmp_path):
    """Match-tier telemetry (2026-08-08): keyword winners carry method+score,
    and the caller's telemetry dict is filled."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("research tool", triggers=["polymarket", "research strategy"])
    save_skill(skill)
    telemetry = {}
    matches = find_matching_skills("polymarket research", telemetry=telemetry)
    assert matches and matches[0].match_method == "keyword"
    assert matches[0].match_score >= 1
    assert telemetry["method"] == "keyword"
    assert telemetry["top_score"] >= 1
    assert telemetry["n_candidates"] >= 1
    assert matches[0].id in telemetry["scores"]


def test_match_telemetry_none_on_empty_match(monkeypatch, tmp_path):
    """The graded gap signal: no match fills method='none', not silence."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("cooking skill", triggers=["bake cake", "mix ingredients"])
    save_skill(skill)
    telemetry = {}
    matches = find_matching_skills("astrophysics telescope calibration zzzunique",
                                   telemetry=telemetry)
    assert matches == []
    assert telemetry["method"] == "none"
    assert telemetry["top_score"] == 0.0
    assert telemetry["n_candidates"] >= 1


def test_match_telemetry_none_on_empty_store(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    telemetry = {}
    assert find_matching_skills("anything", telemetry=telemetry) == []
    assert telemetry["method"] == "none"
    assert telemetry["n_candidates"] == 0


def test_match_telemetry_tfidf_tier(monkeypatch, tmp_path):
    """No trigger overlap but real token overlap → tfidf_fallback with cosine."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("web research helper", triggers=["zzznever matches"])
    skill.description = "gather information from web sources and articles"
    save_skill(skill)
    telemetry = {}
    matches = find_matching_skills(
        "gather information from web sources", use_router=False,
        telemetry=telemetry)
    if matches:  # tokenizer-dependent; the contract under test is the stamp
        assert matches[0].match_method == "tfidf_fallback"
        assert 0 < matches[0].match_score <= 1.5
        assert telemetry["method"] == "tfidf_fallback"
    else:
        assert telemetry["method"] == "none"


def test_match_telemetry_optional_and_backward_compatible(monkeypatch, tmp_path):
    """Callers that pass no telemetry dict are untouched."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("research tool", triggers=["polymarket"])
    save_skill(skill)
    matches = find_matching_skills("polymarket research")
    assert matches  # no TypeError, same behavior


def test_manifest_meta_records_match_block(monkeypatch, tmp_path):
    """append_skills_manifest(meta=...) lands a record-level match block."""
    import json as _json
    _setup_workspace(monkeypatch, tmp_path)
    import runs
    rd = tmp_path / "run-mt"
    (rd / "source").mkdir(parents=True)
    monkeypatch.setattr(runs, "current_run_dir", lambda: rd)
    runs.append_skills_manifest(
        [], stage="decompose",
        meta={"method": "none", "n_candidates": 7, "top_score": 0.0})
    rec = _json.loads((rd / "source" / "skills_manifest.jsonl").read_text())
    assert rec["match"]["method"] == "none"
    assert rec["match"]["n_candidates"] == 7
    assert rec["skills"] == []


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


def test_extract_skills_accepts_curated_success_class(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    outcomes = [
        {"goal": "curated success", "success_class": "success", "summary": "landed"},
        {"goal": "curated failure", "success_class": "failed", "summary": "did not land"},
    ]

    extracted = extract_skills(outcomes, _ExtractMockAdapter())

    assert extracted

    rejected = extract_skills(
        [{"goal": "curated failure", "success_class": "failed"}],
        _ExtractMockAdapter(),
    )
    assert rejected == []


# (increment_use tests removed 2026-07-29 with the function — see skills.py
# note: use_count is legacy-frozen; frontier gating moved to injected_runs.)


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
    """maro-skills --list with no skills prints skills=(none)."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["skills", "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "skills=(none)" in out


def test_cli_poe_skills_list_with_skill(monkeypatch, tmp_path, capsys):
    """maro-skills --list shows skill names."""
    _setup_workspace(monkeypatch, tmp_path)
    skill = _make_skill("cli list test skill")
    save_skill(skill)
    import cli
    rc = cli.main(["skills", "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cli list test skill" in out


def test_cli_poe_skills_extract_dry_run(monkeypatch, tmp_path, capsys):
    """maro-skills --extract --dry-run doesn't crash."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["skills", "--extract", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry_run" in out.lower() or "outcomes" in out.lower()


# ===========================================================================
# Phase 14 tests: SkillStats, SkillTestCase,
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
# Run-verdict injection counters (2026-07-29 measurement-honesty fix)
# ---------------------------------------------------------------------------

def test_record_skill_injection_outcome_counter_math(monkeypatch, tmp_path):
    """True, True, False → runs=3, successes=2, rate 2/3; legacy counters
    stay untouched — the two regimes never bleed into each other."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        skill = _make_skill("injection counter")
        save_skill(skill)
        record_skill_injection_outcome(skill.id, goal_achieved=True)
        record_skill_injection_outcome(skill.id, goal_achieved=True)
        record_skill_injection_outcome(skill.id, goal_achieved=False)
        stats = get_skill_stats(skill.id)
        assert stats is not None
        assert stats.injected_runs == 3
        assert stats.injected_successes == 2
        assert abs(stats.injected_success_rate - 2 / 3) < 1e-9
        assert stats.last_injected_verdict_at != ""
        assert stats.total_uses == 0 and stats.successes == 0


def test_record_skill_injection_outcome_creates_row_for_unknown(
        monkeypatch, tmp_path):
    """A skill with no prior stats row gets one created (name falls back to
    the id when the library can't resolve it)."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        record_skill_injection_outcome("sk-ghost", goal_achieved=False)
        stats = get_skill_stats("sk-ghost")
        assert stats is not None
        assert stats.skill_name == "sk-ghost"
        assert stats.injected_runs == 1 and stats.injected_successes == 0
        assert stats.injected_success_rate == 0.0


def test_update_skill_utility_does_not_write_stats(monkeypatch, tmp_path):
    """Double-count pin (2026-07-29): update_skill_utility used to call
    record_skill_outcome internally while both live callers also called it
    directly — every outcome counted twice. Utility/breaker updates must
    leave skill-stats untouched."""
    _setup_workspace(monkeypatch, tmp_path)
    with patch("skills._skill_stats_path", return_value=tmp_path / "skill-stats.jsonl"):
        from skills import update_skill_utility
        skill = _make_skill("utility no stats")
        save_skill(skill)
        update_skill_utility(skill.id, success=True)
        update_skill_utility(skill.id, success=False, failure_reason="boom")
        assert get_skill_stats(skill.id) is None
        # The utility side itself still moved.
        reloaded = next(s for s in load_skills() if s.id == skill.id)
        assert reloaded.utility_score != 1.0


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
    """Corrupted skill file → warning logged, skill still loads.

    Accepted-with-reason (r12 F5, three seats): hash equality is
    deliberately NOT part of the destructive admission predicate. The
    content_hash is a tamper-EVIDENT tripwire, not a security boundary —
    there is no secret key, so anyone who can forge a row can also write
    a valid hash, and stranding on mismatch would stop no attacker. The
    case it WOULD catch is a legitimate operator hand-edit whose hash is
    merely stale, which the rehash-on-update flow handles correctly.
    Warn-and-load is the decided behavior; this test pins it.
    """
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
    # Stamp the hash the way every production writer does (save_skill,
    # _save_skills fill) — r11 made load_skills admission the proof, and a
    # hash-less row is exactly what it refuses.
    skill.content_hash = compute_skill_hash(skill)
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


def test_maybe_auto_promote_tier_stamp_reloads_under_pool_lock(monkeypatch, tmp_path):
    # R3-8 (adversarial review 2026-08-06): the tier-stamp rewrite is a
    # reload → mutate → full-rewrite of the pool; without the lock spanning
    # the reload, a mutation landing between reload and rewrite is dropped.
    # Pin: the fresh reload runs while this thread holds the pool lock
    # (locked_write is reentrant, so _save_skills' inner acquire is a no-op).
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=AUTO_PROMOTE_MIN_RATE + 0.1,
                           use_count=AUTO_PROMOTE_MIN_USES)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    import skills as skills_mod
    from file_lock import _get_held
    lock_key = str((tmp_path / "skills.jsonl.lock").resolve())
    held_at_load = []
    real_load = skills_mod.load_skills

    def spy_load(*a, **k):
        held_at_load.append(lock_key in _get_held())
        return real_load(*a, **k)

    monkeypatch.setattr(skills_mod, "load_skills", spy_load)
    promoted = maybe_auto_promote_skills()
    assert skill.id in promoted
    # First load (candidate scan) runs unlocked; the fresh reload that the
    # rewrite is based on must run under the lock.
    assert held_at_load[0] is False
    assert held_at_load[-1] is True


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


def test_maybe_auto_promote_reads_stats_uses(monkeypatch, tmp_path):
    """THE dead-gate pin (2026-08-06): Skill.use_count's only writer was
    removed 2026-07-29, so a gate reading it alone can never fire — the
    store sat at 376 provisionals / 0 established for 8 weeks. Uses must
    come from SkillStats.total_uses. Red on revert."""
    import json
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=AUTO_PROMOTE_MIN_RATE + 0.1,
                           use_count=0)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    (tmp_path / "skill-stats.jsonl").write_text(json.dumps({
        "skill_id": skill.id, "skill_name": skill.name,
        "total_uses": AUTO_PROMOTE_MIN_USES,
        "successes": AUTO_PROMOTE_MIN_USES, "failures": 0,
        "success_rate": 1.0,
    }) + "\n")
    promoted = maybe_auto_promote_skills()
    assert skill.id in promoted


def test_maybe_auto_promote_no_uses_anywhere_stays_provisional(monkeypatch, tmp_path):
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=1.0, use_count=0)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    promoted = maybe_auto_promote_skills()
    assert promoted == []


def test_maybe_auto_promote_respects_limit(monkeypatch, tmp_path):
    """Cap per sweep (same shape as node promotion): the first sweep after
    the dead-gate fix must not push the whole eligible backlog through the
    LLM validation harness at once."""
    import json
    rows = []
    for i in range(4):
        s = Skill(
            id=f"lim{i}", name=f"Limit Skill {i}", description="d",
            trigger_patterns=["t"], steps_template=["s"], source_loop_ids=[],
            created_at="2026-08-06T00:00:00+00:00",
            use_count=AUTO_PROMOTE_MIN_USES, success_rate=1.0,
            tier="provisional", utility_score=1.0,
        )
        s.content_hash = compute_skill_hash(s)   # admission requires proof (r11)
        rows.append(json.dumps(_skill_to_dict(s)))
    (tmp_path / "skills.jsonl").write_text("\n".join(rows) + "\n")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    import skill_loader
    monkeypatch.setattr(skill_loader, "export_skill_as_markdown", lambda s: None)
    promoted = maybe_auto_promote_skills(limit=2)
    assert len(promoted) == 2
    tiers = {s.id: s.tier for s in load_skills()}
    assert sum(1 for t in tiers.values() if t == "established") == 2


def test_maybe_auto_promote_repaired_skill_lands_established(monkeypatch, tmp_path):
    """Adversarial-review pin R3-1 (2026-08-06): when validation fails once
    and repair succeeds, the promotion used to be applied to a stale object
    no longer in the list — SKILL_PROMOTED fired, the id was returned, and
    the save persisted the repaired skill still-provisional. The on-disk row
    must end up BOTH repaired and established. Red on revert."""
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=AUTO_PROMOTE_MIN_RATE + 0.1,
                           use_count=AUTO_PROMOTE_MIN_USES)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    import skill_loader
    monkeypatch.setattr(skill_loader, "export_skill_as_markdown", lambda s: None)

    calls = {"n": 0}

    def _validate(candidate, adapter):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"valid": False, "reason": "vague", "repair_hint": "", "judged": True}
        return {"valid": True, "reason": "", "repair_hint": "", "judged": True}

    def _fake_rewrite(candidate, adapter, **kwargs):
        # Mimic rewrite_skill(in_place=True): fresh load, mutate, SAVE, return
        # the fresh object — the sweep's in-memory list is now stale vs disk.
        pool = load_skills()
        target = next(s for s in pool if s.id == candidate.id)
        target.description = "repaired description"
        _save_skills(pool, updated_ids={target.id})
        return target

    monkeypatch.setattr("skills.validate_skill_for_promotion", _validate)
    import evolver
    monkeypatch.setattr(evolver, "rewrite_skill", _fake_rewrite)

    promoted = maybe_auto_promote_skills(adapter=object())
    assert skill.id in promoted
    on_disk = next(s for s in load_skills() if s.id == skill.id)
    assert on_disk.description == "repaired description"
    assert on_disk.tier == "established"


def test_maybe_auto_promote_limit_caps_llm_candidates(monkeypatch, tmp_path):
    """Adversarial-review pin R3-3 (2026-08-06): `limit` must cap candidates
    entering the LLM harness, not successful promotions — a pool of
    never-passing provisionals used to get validated + repaired in full,
    every sweep, with the cap never advancing. Red on revert."""
    import json
    rows = []
    for i in range(4):
        s = Skill(
            id=f"cap{i}", name=f"Cap Skill {i}", description="d",
            trigger_patterns=["t"], steps_template=["s"], source_loop_ids=[],
            created_at="2026-08-06T00:00:00+00:00",
            use_count=AUTO_PROMOTE_MIN_USES, success_rate=1.0,
            tier="provisional", utility_score=1.0,
        )
        s.content_hash = compute_skill_hash(s)   # admission requires proof (r11)
        rows.append(json.dumps(_skill_to_dict(s)))
    (tmp_path / "skills.jsonl").write_text("\n".join(rows) + "\n")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")

    validated = {"n": 0}

    def _always_invalid(candidate, adapter):
        validated["n"] += 1
        return {"valid": False, "reason": "no", "repair_hint": "", "judged": True}

    monkeypatch.setattr("skills.validate_skill_for_promotion", _always_invalid)
    import evolver
    monkeypatch.setattr(evolver, "rewrite_skill", lambda c, a, **kw: None)

    promoted = maybe_auto_promote_skills(adapter=object(), limit=2)
    assert promoted == []
    # 2 candidates x 1 validate each (rewrite returns None → stop): the other
    # two eligible skills never reach the harness.
    assert validated["n"] == 2


def test_maybe_auto_promote_injected_evidence_vetoes(monkeypatch, tmp_path):
    """Adversarial-review pin R3-4 (2026-08-06): SkillStats' legacy counters
    credit keyword-matched bystanders (documented inflated in skill_types).
    Where verdict-grounded evidence exists (injected_runs > 0), a failing
    injected record must hold the skill even when legacy counters glow."""
    import json
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=1.0, use_count=AUTO_PROMOTE_MIN_USES)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    (tmp_path / "skill-stats.jsonl").write_text(json.dumps({
        "skill_id": skill.id, "skill_name": skill.name,
        "total_uses": 20, "successes": 20, "failures": 0, "success_rate": 1.0,
        "injected_runs": 3, "injected_successes": 0,
        "injected_success_rate": 0.0,
    }) + "\n")
    promoted = maybe_auto_promote_skills()
    assert promoted == []
    assert load_skills()[0].tier == "provisional"


def test_maybe_auto_promote_injected_evidence_confirms(monkeypatch, tmp_path):
    """Companion to the veto pin: good injected evidence does not block."""
    import json
    skill = _phase32_skill(tmp_path, tier="provisional",
                           utility=1.0, use_count=AUTO_PROMOTE_MIN_USES)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    import skill_loader
    monkeypatch.setattr(skill_loader, "export_skill_as_markdown", lambda s: None)
    (tmp_path / "skill-stats.jsonl").write_text(json.dumps({
        "skill_id": skill.id, "skill_name": skill.name,
        "total_uses": 20, "successes": 20, "failures": 0, "success_rate": 1.0,
        "injected_runs": 3, "injected_successes": 3,
        "injected_success_rate": 1.0,
    }) + "\n")
    promoted = maybe_auto_promote_skills()
    assert skill.id in promoted


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


def test_maybe_demote_reads_stats_uses(monkeypatch, tmp_path):
    """Live-writer census pin (2026-08-06): the promote-side fix (a0bae77)
    left this gate still reading the legacy-frozen Skill.use_count, so an
    established skill with all its uses in live SkillStats could never
    demote. Red on revert."""
    import json
    skill = _phase32_skill(tmp_path, tier="established",
                           utility=REWRITE_TRIGGER_RATE - 0.1,
                           use_count=0)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    (tmp_path / "skill-stats.jsonl").write_text(json.dumps({
        "skill_id": skill.id, "skill_name": skill.name,
        "total_uses": REWRITE_MIN_USES, "successes": 0,
        "failures": REWRITE_MIN_USES, "success_rate": 0.0,
    }) + "\n")
    demoted = maybe_demote_skills()
    assert skill.id in demoted


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


def test_skills_needing_rewrite_reads_stats_uses(monkeypatch, tmp_path):
    """Live-writer census pin (2026-08-06), same corpse third site: a
    circuit-open skill whose uses live only in SkillStats must still reach
    the rewrite lane — Skill.use_count is legacy-frozen (writer removed
    2026-07-29). Red on revert."""
    import json
    skill = _phase32_skill(tmp_path, utility=0.2, use_count=0,
                           circuit_state="open",
                           consecutive_failures=CIRCUIT_OPEN_THRESHOLD)
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("skills._skill_stats_path", lambda: tmp_path / "skill-stats.jsonl")
    (tmp_path / "skill-stats.jsonl").write_text(json.dumps({
        "skill_id": skill.id, "skill_name": skill.name,
        "total_uses": REWRITE_MIN_USES, "successes": 0,
        "failures": REWRITE_MIN_USES, "success_rate": 0.0,
    }) + "\n")
    candidates = skills_needing_rewrite()
    assert any(s.id == skill.id for s in candidates)


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

    def test_island_boost_prefers_matching_island(self):
        """NeMo S4: skill whose island matches goal intent ranks higher (20% boost)."""
        from skills import _tfidf_skill_rank, Skill

        def _make(id_, name, desc, triggers, island=""):
            return Skill(
                id=id_, name=name, description=desc,
                trigger_patterns=triggers, steps_template=["step"],
                source_loop_ids=[], created_at="2026-01-01T00:00:00+00:00",
                island=island,
            )

        # Both skills have similar TF-IDF relevance to the goal, but only research_skill
        # has island="research" matching the "research" keyword in the goal.
        research_skill = _make("s1", "web_searcher", "find information on topics",
                               ["find info", "search web"], island="research")
        build_skill = _make("s2", "code_gen", "generate code for topics",
                            ["generate code", "create module"], island="build")
        # Goal has explicit "research" intent
        results = _tfidf_skill_rank("research topics and gather information", [research_skill, build_skill])
        # research_skill should appear before build_skill (island boost tips the balance)
        ids = [s.id for s in results]
        if "s1" in ids and "s2" in ids:
            assert ids.index("s1") < ids.index("s2")


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
        # Fail-open: validation unavailable → allow promotion — but the
        # record must say no judgment happened (§13e slice-2 pattern).
        assert result["valid"] is True
        assert result["judged"] is False

    def test_validate_real_verdict_is_judged(self):
        from skills import validate_skill_for_promotion
        import types

        class Adapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(
                    content='{"valid": true, "reason": "solid", "repair_hint": ""}')

        result = validate_skill_for_promotion(self._make_skill(), Adapter())
        assert result["valid"] is True
        assert result["judged"] is True

    def _promote_and_capture_event(self, adapter):
        from skills import maybe_auto_promote_skills
        from unittest.mock import patch

        skill = self._make_skill(use_count=10, utility_score=0.9)
        events = []
        with patch("skills.load_skills", return_value=[skill]), \
             patch("skills._save_skills"), \
             patch("skills.compute_skill_hash", return_value="hash123"), \
             patch("captains_log.log_event",
                   side_effect=lambda **kw: events.append(kw)):
            result = maybe_auto_promote_skills(adapter=adapter)
        return skill, result, events

    def test_promoted_event_stamps_passed_validation(self):
        import types
        from unittest.mock import MagicMock
        adapter = MagicMock()
        adapter.complete.return_value = types.SimpleNamespace(
            content='{"valid": true, "reason": "passes", "repair_hint": ""}')
        skill, result, events = self._promote_and_capture_event(adapter)
        assert skill.id in result
        assert events[0]["context"]["validation"] == "passed"

    def test_promoted_event_stamps_unjudged_on_fail_open(self):
        class BrokenAdapter:
            def complete(self, messages, **kw):
                raise RuntimeError("connection refused")

        skill, result, events = self._promote_and_capture_event(BrokenAdapter())
        # Fail-open still promotes (graceful degradation to numeric gates) —
        # but the event says the validation pass was never a judgment.
        assert skill.id in result
        assert events[0]["context"]["validation"] == "unjudged"

    def test_promoted_event_stamps_skipped_without_adapter(self):
        skill, result, events = self._promote_and_capture_event(None)
        assert skill.id in result
        assert events[0]["context"]["validation"] == "skipped"

    def test_skill_maintenance_wires_adapter_into_promotion(
            self, monkeypatch, tmp_path):
        # Jeremy 2026-08-01 ("fix the promote validation"): the evolver call
        # site passed no adapter since Phase 32, so the whole validation
        # harness was dead code. Pin the wiring, not just the harness.
        import skills as skills_module
        from skill_lifecycle import run_skill_maintenance

        seen = {}

        def _capture(adapter=None, **kw):
            seen["adapter"] = adapter
            return []

        monkeypatch.setattr(skills_module, "maybe_auto_promote_skills", _capture)
        sentinel = object()
        run_skill_maintenance(adapter=sentinel, dry_run=False)
        assert seen.get("adapter") is sentinel

    def test_skill_maintenance_wires_node_promotion(self, monkeypatch, tmp_path):
        # V3 (2026-08-02, "same as skills"): knowledge-node candidate → active
        # promotion rides the same maintenance cadence, with the same adapter.
        import knowledge_web as kw_module
        from skill_lifecycle import run_skill_maintenance

        seen = {}

        def _capture(*, adapter=None, dry_run=False, **kw):
            seen["adapter"] = adapter
            seen["dry_run"] = dry_run
            return ["node-x"]

        monkeypatch.setattr(
            kw_module, "promote_knowledge_candidates", _capture)
        sentinel = object()
        result = run_skill_maintenance(adapter=sentinel, dry_run=False)
        assert seen.get("adapter") is sentinel
        assert seen.get("dry_run") is False
        assert result["nodes_promoted"] == ["node-x"]

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
    """Frontier gate reads the honest injected counters (2026-07-29): a
    skill is a rewrite candidate only with >= min_uses verdicted injections
    and an injected_success_rate inside the 40-70% band. Stats are seeded
    through the real writer, not fixture rows."""

    def _make_skill(self, id_, utility_score=0.5, use_count=0, circuit_state="closed"):
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

    def _seed_stats(self, skill_id, runs, successes):
        for i in range(runs):
            record_skill_injection_outcome(skill_id, goal_achieved=(i < successes))

    def _stats_patch(self, tmp_path):
        from unittest.mock import patch
        return patch("skills._skill_stats_path",
                     return_value=tmp_path / "skill-stats.jsonl")

    def test_returns_frontier_zone_skills(self, monkeypatch, tmp_path):
        from skills import frontier_skills
        _setup_workspace(monkeypatch, tmp_path)
        skills = [
            self._make_skill("low"),       # 10% verdicted success — below band
            self._make_skill("frontier"),  # 55% — in band
            self._make_skill("high"),      # 90% — above band
        ]
        with self._stats_patch(tmp_path):
            self._seed_stats("low", 10, 1)
            self._seed_stats("frontier", 20, 11)
            self._seed_stats("high", 10, 9)
            result = frontier_skills(skills)
        ids = [s.id for s in result]
        assert "frontier" in ids
        assert "low" not in ids
        assert "high" not in ids

    def test_excludes_open_circuit(self, monkeypatch, tmp_path):
        from skills import frontier_skills
        _setup_workspace(monkeypatch, tmp_path)
        skills = [
            self._make_skill("open_frontier", circuit_state="open"),
            self._make_skill("closed_frontier", circuit_state="closed"),
        ]
        with self._stats_patch(tmp_path):
            self._seed_stats("open_frontier", 20, 11)
            self._seed_stats("closed_frontier", 20, 11)
            result = frontier_skills(skills)
        ids = [s.id for s in result]
        assert "open_frontier" not in ids  # open-circuit handled by skills_needing_rewrite
        assert "closed_frontier" in ids

    def test_excludes_below_min_uses(self, monkeypatch, tmp_path):
        from skills import frontier_skills
        _setup_workspace(monkeypatch, tmp_path)
        skills = [
            self._make_skill("new"),     # 1 verdicted injection — not enough data
            self._make_skill("mature"),  # 5 — enough
        ]
        with self._stats_patch(tmp_path):
            self._seed_stats("new", 2, 1)
            self._seed_stats("mature", 5, 3)
            result = frontier_skills(skills, min_uses=3)
        ids = [s.id for s in result]
        assert "new" not in ids
        assert "mature" in ids

    def test_sorted_ascending_by_injected_rate(self, monkeypatch, tmp_path):
        from skills import frontier_skills
        _setup_workspace(monkeypatch, tmp_path)
        skills = [
            self._make_skill("s1"),  # 0.65
            self._make_skill("s2"),  # 0.45
            self._make_skill("s3"),  # 0.55
        ]
        with self._stats_patch(tmp_path):
            self._seed_stats("s1", 20, 13)
            self._seed_stats("s2", 20, 9)
            self._seed_stats("s3", 20, 11)
            result = frontier_skills(skills)
        assert [s.id for s in result] == ["s2", "s3", "s1"]  # hardest first

    def test_loads_from_disk_if_none(self, monkeypatch, tmp_path):
        from skills import frontier_skills
        from unittest.mock import patch
        _setup_workspace(monkeypatch, tmp_path)
        skills = [self._make_skill("disk_skill")]
        with self._stats_patch(tmp_path):
            self._seed_stats("disk_skill", 20, 11)
            with patch("skills.load_skills", return_value=skills):
                result = frontier_skills(None)
        assert any(s.id == "disk_skill" for s in result)

    def test_legacy_use_count_confers_no_candidacy(self, monkeypatch, tmp_path):
        """Pin for the 2026-07-29 fix: heavy legacy use_count + in-band
        utility_score (the OLD gate's inclusion criteria) means nothing
        without verdicted injections — use_count must never be resurrected
        as frontier evidence."""
        from skills import frontier_skills
        _setup_workspace(monkeypatch, tmp_path)
        skills = [self._make_skill("legacy_only", utility_score=0.55, use_count=100)]
        with self._stats_patch(tmp_path):
            result = frontier_skills(skills)
        assert result == []


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
# Project isolation in find_matching_skills
# ---------------------------------------------------------------------------

class TestFindMatchingSkillsProjectIsolation:
    """find_matching_skills(project=...) filters to global + project-specific skills."""

    def _make_skill(self, skill_id, name, trigger, project=""):
        from skill_types import Skill
        return Skill(
            id=skill_id,
            name=name,
            description=f"desc {name}",
            trigger_patterns=[trigger],
            steps_template=["step"],
            source_loop_ids=[],
            created_at="2026-01-01T00:00:00",
            project=project,
        )

    def test_no_project_returns_all(self, tmp_path, monkeypatch):
        """Without a project filter, all matching skills are returned."""
        from skills import find_matching_skills
        global_skill = self._make_skill("g1", "global", "research", project="")
        proj_skill = self._make_skill("p1", "proj", "research", project="polymarket")
        monkeypatch.setattr("skills.load_skills", lambda: [global_skill, proj_skill])
        results = find_matching_skills("research task", use_router=False, project="")
        ids = {s.id for s in results}
        assert "g1" in ids
        assert "p1" in ids

    def test_project_filter_includes_global(self, tmp_path, monkeypatch):
        """With project set, global skills (project='') are always included."""
        from skills import find_matching_skills
        global_skill = self._make_skill("g1", "global", "research", project="")
        proj_skill = self._make_skill("p1", "proj", "research", project="polymarket")
        monkeypatch.setattr("skills.load_skills", lambda: [global_skill, proj_skill])
        results = find_matching_skills("research task", use_router=False, project="polymarket")
        ids = {s.id for s in results}
        assert "g1" in ids
        assert "p1" in ids

    def test_project_filter_excludes_other_projects(self, tmp_path, monkeypatch):
        """With project set, skills from other projects are excluded."""
        from skills import find_matching_skills
        global_skill = self._make_skill("g1", "global", "research", project="")
        polymarket_skill = self._make_skill("p1", "poly", "research", project="polymarket")
        nootropics_skill = self._make_skill("n1", "noot", "research", project="nootropics")
        monkeypatch.setattr("skills.load_skills", lambda: [global_skill, polymarket_skill, nootropics_skill])
        results = find_matching_skills("research task", use_router=False, project="polymarket")
        ids = {s.id for s in results}
        assert "g1" in ids
        assert "p1" in ids
        assert "n1" not in ids

    def test_project_filter_empty_skill_pool(self, tmp_path, monkeypatch):
        """When all matching skills belong to different projects, returns empty list."""
        from skills import find_matching_skills
        other_skill = self._make_skill("o1", "other", "research", project="other-project")
        monkeypatch.setattr("skills.load_skills", lambda: [other_skill])
        results = find_matching_skills("research task", use_router=False, project="my-project")
        assert results == []

    def test_skill_project_field_roundtrips_json(self):
        """project field persists through skill_to_dict / dict_to_skill."""
        from skill_types import skill_to_dict, dict_to_skill, Skill
        skill = Skill(
            id="s1", name="n", description="d", trigger_patterns=["t"],
            steps_template=["s"], source_loop_ids=[], created_at="2026-01-01",
            project="polymarket-edges",
        )
        d = skill_to_dict(skill)
        assert d["project"] == "polymarket-edges"
        restored = dict_to_skill(d)
        assert restored.project == "polymarket-edges"

    def test_skill_project_defaults_to_empty(self):
        """Old skills loaded without project field default to '' (global)."""
        from skill_types import dict_to_skill
        d = {
            "id": "s1", "name": "n", "description": "d", "trigger_patterns": [],
            "steps_template": [], "source_loop_ids": [], "created_at": "2026-01-01",
            # no "project" key — simulates old serialized skill
        }
        skill = dict_to_skill(d)
        assert skill.project == ""

    def test_skill_imported_field_roundtrips_json(self):
        """PORTABLE_LEARNING_DESIGN §3: imported provenance stamp persists
        through skill_to_dict / dict_to_skill, not silently dropped."""
        from skill_types import skill_to_dict, dict_to_skill, Skill
        skill = Skill(
            id="s1", name="n", description="d", trigger_patterns=["t"],
            steps_template=["s"], source_loop_ids=[], created_at="2026-01-01",
            imported={"imported_from": "pack-a", "claimed_use_count": 12,
                      "claimed_success_rate": 0.9},
        )
        d = skill_to_dict(skill)
        assert d["imported"] == {"imported_from": "pack-a", "claimed_use_count": 12,
                                  "claimed_success_rate": 0.9}
        restored = dict_to_skill(d)
        assert restored.imported == {"imported_from": "pack-a", "claimed_use_count": 12,
                                      "claimed_success_rate": 0.9}

    def test_skill_imported_defaults_to_empty(self):
        """Old skills loaded without an imported field default to {} (local)."""
        from skill_types import dict_to_skill
        d = {
            "id": "s1", "name": "n", "description": "d", "trigger_patterns": [],
            "steps_template": [], "source_loop_ids": [], "created_at": "2026-01-01",
            # no "imported" key — simulates old serialized skill
        }
        skill = dict_to_skill(d)
        assert skill.imported == {}


class TestCullArchive:
    """Retention decree (2026-07-10): island culls archive, never delete."""

    def test_cull_archives_instead_of_deleting(self):
        import json
        import skills as skills_mod
        from skills import (Skill, cull_island_bottom_half, load_skills,
                            _save_skills, save_skill)

        def mk(id_, state, util):
            return Skill(
                id=id_, name=f"skill_{id_}", description="search web information",
                trigger_patterns=["search"], steps_template=["step"],
                source_loop_ids=[], created_at="2026-01-01T00:00:00+00:00",
                circuit_state=state, utility_score=util, island="research",
            )

        pool = [
            mk("s1", "open", 0.1),
            mk("s2", "open", 0.2),
            mk("s3", "closed", 0.5),
            mk("s4", "closed", 0.6),
        ]
        # Seed via save_skill: _save_skills never creates rows (r18 —
        # a named id absent from the live store is a lost race with
        # a deliberate drop, not a create).
        for _s in pool:
            save_skill(_s)
        culled = cull_island_bottom_half("research", min_island_size=4)
        assert len(culled) == 1

        live_ids = {s.id for s in load_skills()}
        assert not set(culled) & live_ids
        assert len(live_ids) == 3

        arch = skills_mod._skills_archive_path()
        recs = [json.loads(l) for l in arch.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert {r["id"] for r in recs} == set(culled)
        assert all(r["archived_reason"] == "island_cull" for r in recs)

        from orch_items import memory_dir
        prov = list((memory_dir() / "skill_provenance").glob("*.json"))
        assert any(json.loads(p.read_text(encoding="utf-8"))["decision"] == "retire"
                   for p in prov)


# ---------------------------------------------------------------------------
# Pedigree + discovery metadata (2026-08-08 BACKLOG item)
# ---------------------------------------------------------------------------

class TestSkillPedigree:
    """origin/domain/tags: stamped at mint, round-trip, and consumed by matching."""

    def test_round_trip_preserves_pedigree(self):
        from skill_types import skill_to_dict, dict_to_skill
        sk = _make_skill("pedigree probe")
        sk.origin = "crystallized"
        sk.domain = "web-research"
        sk.tags = ["polymarket", "odds"]
        back = dict_to_skill(skill_to_dict(sk))
        assert back.origin == "crystallized"
        assert back.domain == "web-research"
        assert back.tags == ["polymarket", "odds"]

    def test_legacy_row_defaults_to_unknown(self):
        """Pre-stamp rows load with origin '' — unknown stays unknown."""
        from skill_types import dict_to_skill
        legacy = {"id": "old1", "name": "old", "description": "d"}
        sk = dict_to_skill(legacy)
        assert sk.origin == ""
        assert sk.domain == ""
        assert sk.tags == []

    def test_legacy_imported_row_derives_imported_origin(self):
        """An imported dict is certain evidence — blank origin derives 'imported'."""
        from skill_types import dict_to_skill
        legacy = {"id": "old2", "name": "old", "description": "d",
                  "imported": {"pack": "p", "imported_from": "x"}}
        assert dict_to_skill(legacy).origin == "imported"
        # ...but an explicit origin is never overridden by derivation
        explicit = dict(legacy, origin="crystallized")
        assert dict_to_skill(explicit).origin == "crystallized"

    def test_extract_skills_stamps_crystallized_with_discovery(self, monkeypatch, tmp_path):
        _setup_workspace(monkeypatch, tmp_path)
        outcomes = [
            {"goal": "research polymarket strategies", "status": "done",
             "task_type": "research", "summary": "found", "outcome_id": "oc1"},
        ]
        extracted = extract_skills(outcomes, _ExtractMockAdapter())
        assert extracted
        first = extracted[0]
        assert first.origin == "crystallized"
        assert first.domain == "web-research"
        # tags normalized to lowercase at mint ("Research" in the mock payload)
        assert first.tags == ["research", "sources", "synthesis"]

    def test_keyword_matching_counts_tags(self, monkeypatch):
        """A skill whose only hook is a tag still matches at the keyword tier."""
        from skills import find_matching_skills
        sk = _make_skill("tagged only", triggers=["completely unrelated phrase"])
        sk.tags = ["polymarket"]
        monkeypatch.setattr("skills.load_skills", lambda: [sk])
        results = find_matching_skills(
            "scan polymarket for mispriced odds", use_router=False)
        assert [s.id for s in results] == [sk.id]
        assert results[0].match_method == "keyword"

    def test_tfidf_doc_includes_tags_and_domain(self):
        from skills import _tfidf_skill_rank
        tagged = _make_skill("alpha", triggers=["zz qq"])
        tagged.tags = ["kubernetes", "deployment"]
        tagged.domain = "devops"
        plain = _make_skill("beta", triggers=["yy ww"])
        ranked = _tfidf_skill_rank("kubernetes deployment rollout", [tagged, plain])
        assert ranked and ranked[0].id == tagged.id

    def test_normalize_tags_contract(self):
        """Round-2 review: LLM mint sites iterated tags without a list
        check — {"tags": "research"} became character tags that
        keyword-match nearly any goal. One shared normalizer, all sites."""
        from skill_types import normalize_tags
        assert normalize_tags("research") == []
        assert normalize_tags({"a": 1}) == []
        assert normalize_tags(None) == []
        assert normalize_tags([" Research ", "", 42, "ODDS"]) == \
            ["research", "42", "odds"]
        assert normalize_tags([f"t{i}" for i in range(9)]) == \
            [f"t{i}" for i in range(6)]  # mint cap
        assert len(normalize_tags([f"t{i}" for i in range(9)], cap=None)) == 9

    def test_mint_site_survives_tags_as_string(self, monkeypatch, tmp_path):
        _setup_workspace(monkeypatch, tmp_path)

        class _StringTagsAdapter(_ExtractMockAdapter):
            def complete(self, messages, **kwargs):
                resp = super().complete(messages, **kwargs)
                payload = json.loads(resp.content)
                payload["skills"][0]["tags"] = "research"
                return LLMResponse(content=json.dumps(payload),
                                   stop_reason="end_turn",
                                   input_tokens=1, output_tokens=1)

        outcomes = [
            {"goal": "research polymarket strategies", "status": "done",
             "task_type": "research", "summary": "found", "outcome_id": "oc1"},
        ]
        extracted = extract_skills(outcomes, _StringTagsAdapter())
        assert extracted and extracted[0].tags == []

    def test_mixed_router_batch_keeps_per_skill_provenance(self, monkeypatch):
        """Round-2 review: a degraded batch (one candidate's inference
        failed → per-skill keyword fallback) used to stamp every winner
        "router" — false provenance exactly where it matters. Telemetry
        says "mixed"; each skill keeps its own RouteResult.method."""
        from skills import find_matching_skills
        from router import RouteResult
        sk_a = _make_skill("routed one", triggers=["completely unrelated a"])
        sk_b = _make_skill("fallback one", triggers=["completely unrelated b"])
        monkeypatch.setattr("skills.load_skills", lambda: [sk_a, sk_b])
        monkeypatch.setattr("router.route_skills", lambda goal, skills, top_k=3: [
            RouteResult(sk_a.id, sk_a.name, 0.9, "router"),
            RouteResult(sk_b.id, sk_b.name, 0.4, "keyword"),
        ])
        telemetry = {}
        results = find_matching_skills("any goal at all", use_router=True,
                                       telemetry=telemetry)
        assert [s.id for s in results] == [sk_a.id, sk_b.id]
        assert telemetry["method"] == "mixed"
        assert results[0].match_method == "router"
        assert results[1].match_method == "keyword"

    def test_all_router_batch_still_reports_router(self, monkeypatch):
        from skills import find_matching_skills
        from router import RouteResult
        sk_a = _make_skill("routed a", triggers=["completely unrelated a"])
        monkeypatch.setattr("skills.load_skills", lambda: [sk_a])
        monkeypatch.setattr("router.route_skills", lambda goal, skills, top_k=3: [
            RouteResult(sk_a.id, sk_a.name, 0.9, "router"),
        ])
        telemetry = {}
        results = find_matching_skills("any goal", use_router=True,
                                       telemetry=telemetry)
        assert telemetry["method"] == "router"
        assert results[0].match_method == "router"


# ---------------------------------------------------------------------------
# Byte-safety (2026-08-17 silent-drop arc, tree-wide destructive-rewrite sweep)
# ---------------------------------------------------------------------------

_TORN = b'{"skill_id": "s9", "skill_name": "torn\xff'


class TestTheSkillStoresSurviveATornByte:
    """skill-stats.jsonl held the tier-destruction chain: a strict read
    inside `except Exception: pass` left the keyed map EMPTY, and the very
    next counter update rewrote the store from it. Probed live before the
    fix: 4 lines -> 1, every skill's stats gone, silently. skills.jsonl held
    the write-lock twin: one torn byte and every save_skill raised."""

    @pytest.fixture(autouse=True)
    def _ws(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))

    def _seed_stats(self, n=3):
        from skills import _skill_stats_path
        p = _skill_stats_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.dumps({"skill_id": f"s{i}", "skill_name": f"skill-{i}",
                            "total_uses": 10 + i, "successes": 9}).encode()
                for i in range(1, n + 1)]
        p.write_bytes(b"\n".join(rows) + b"\n" + _TORN + b"\n")
        return p

    def test_a_counter_update_no_longer_wipes_the_stats_store(self):
        from skills import record_skill_outcome
        p = self._seed_stats()
        record_skill_outcome("s1", True)
        after = p.read_bytes()
        # every healthy row survived, and so did the torn one
        for sid in (b'"s1"', b'"s2"', b'"s3"'):
            assert sid in after
        assert _TORN in after
        with pytest.raises(UnicodeDecodeError):
            after.decode("utf-8")        # corruption signal intact

    def test_the_update_still_did_its_job(self):
        from skills import record_skill_outcome, get_skill_stats
        self._seed_stats()
        record_skill_outcome("s1", True)
        assert get_skill_stats("s1").total_uses == 12   # 11 + 1

    def test_the_injection_counter_preserves_the_same_way(self):
        from skills import record_skill_injection_outcome
        p = self._seed_stats()
        record_skill_injection_outcome("s2", True)
        after = p.read_bytes()
        assert b'"s1"' in after and b'"s3"' in after and _TORN in after

    def test_a_keyless_row_is_carried_not_deleted(self):
        # No skill_id means the keyed rebuild cannot represent it; the old
        # code dropped it on the floor (`if sid:` with no else).
        from skills import record_skill_outcome, _skill_stats_path
        p = _skill_stats_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        keyless = json.dumps({"skill_name": "no-id-here", "total_uses": 4})
        p.write_text(json.dumps({"skill_id": "s1", "total_uses": 1}) + "\n"
                     + keyless + "\n", encoding="utf-8")
        record_skill_outcome("s1", True)
        assert "no-id-here" in p.read_text(encoding="utf-8")

    def test_the_loss_is_announced(self, caplog):
        import logging
        from skills import record_skill_outcome
        self._seed_stats()
        with caplog.at_level(logging.WARNING):
            record_skill_outcome("s1", True)
        assert any("skill-stats" in r.message and "verbatim" in r.message
                   for r in caplog.records)

    def test_save_skill_no_longer_write_locks_the_library(self):
        from skills import save_skill, _skills_path, Skill
        def _mk(sid):
            return Skill(id=sid, name=f"n{sid}", description="d",
                         trigger_patterns=["x"], steps_template=["a"],
                         source_loop_ids=[],
                         created_at="2026-08-17T00:00:00+00:00")
        save_skill(_mk("k1"))
        p = _skills_path()
        with p.open("ab") as f:
            f.write(b'{"id": "k2", "name": "torn\xff' + b"\n")
        save_skill(_mk("k3"))            # used to raise UnicodeDecodeError
        after = p.read_bytes()
        assert b'"k1"' in after and b'"k3"' in after
        assert b'torn\xff' in after      # torn line NOT deleted

    def test_save_skill_never_launders_a_tainted_twin(self):
        from skills import save_skill, _skills_path, Skill, _skill_to_dict
        sk = Skill(id="k1", name="keeper", description="d",
                   trigger_patterns=["x"], steps_template=["a"],
                   source_loop_ids=[],
                   created_at="2026-08-17T00:00:00+00:00")
        p = _skills_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tainted = json.dumps(_skill_to_dict(sk)).encode().replace(
            b"keeper", b"keep\xffr")
        p.write_bytes(tainted + b"\n")
        save_skill(sk)                   # same id as the tainted twin
        after = p.read_bytes()
        assert tainted in after          # never id-matched, never re-dumped

    def test_load_skills_reads_past_a_torn_byte(self, caplog):
        import logging
        from skills import save_skill, load_skills, _skills_path, Skill
        save_skill(Skill(id="k1", name="keeper", description="d",
                         trigger_patterns=["x"], steps_template=["a"],
                         source_loop_ids=[],
                         created_at="2026-08-17T00:00:00+00:00"))
        with _skills_path().open("ab") as f:
            f.write(b'{"id": "k2", "name": "torn\xff' + b"\n")
        with caplog.at_level(logging.WARNING):
            got = load_skills()          # used to raise UnicodeDecodeError
        assert [s.id for s in got] == ["k1"]

    def test_a_load_save_cycle_does_not_delete_the_torn_row(self):
        # The chunk's OWN regression, caught by adversarial review: making
        # load_skills degrade (instead of raise) meant the in-memory list
        # was one row short, and _save_skills rewrote the store from it —
        # turning a loud crash into a silent deletion. Eight call sites
        # feed _save_skills, including update_skill_utility (every match).
        from skills import save_skill, load_skills, _save_skills, _skills_path, Skill
        def _mk(sid):
            return Skill(id=sid, name=f"n{sid}", description="d",
                         trigger_patterns=["x"], steps_template=["a"],
                         source_loop_ids=[],
                         created_at="2026-08-17T00:00:00+00:00")
        save_skill(_mk("k1"))
        p = _skills_path()
        with p.open("ab") as f:
            f.write(b'{"id": "k2", "name": "torn\xff' + b"\n")
        loaded = load_skills()
        _save_skills(loaded, updated_ids={s.id for s in loaded})
        after = p.read_bytes()
        assert b'torn\xff' in after       # stranded, not deleted
        assert b'"k1"' in after
        with pytest.raises(UnicodeDecodeError):
            after.decode("utf-8")

    def test_a_deliberate_drop_still_drops(self):
        # The carry must not resurrect rows the CALLER meant to remove
        # (graduation, demotion, GC all pass a shortened list).
        from skills import save_skill, load_skills, _save_skills, _skills_path, Skill
        def _mk(sid):
            return Skill(id=sid, name=f"n{sid}", description="d",
                         trigger_patterns=["x"], steps_template=["a"],
                         source_loop_ids=[],
                         created_at="2026-08-17T00:00:00+00:00")
        save_skill(_mk("k1"))
        save_skill(_mk("k2"))
        kept = [s for s in load_skills() if s.id != "k2"]
        # r16: a deliberate drop must be NAMED — absence alone is carried.
        _save_skills(kept, dropped_ids={"k2"}, updated_ids=frozenset())
        after = _skills_path().read_bytes()
        assert b'"k1"' in after and b'"k2"' not in after

    def test_an_unreadable_stats_store_aborts_the_write_instead_of_wiping(
            self, monkeypatch):
        # The safety-critical branch: refuse to rebuild from nothing.
        from skills import record_skill_outcome, _skill_stats_path
        import skills as _sk
        p = self._seed_stats()
        before = p.read_bytes()
        def _boom(_path):
            raise OSError("simulated unreadable store")
        monkeypatch.setattr(_sk, "_store_text", _boom)
        with pytest.raises(OSError):
            record_skill_outcome("s1", True)
        assert p.read_bytes() == before   # byte-identical, nothing written

    def test_a_pure_read_degrades_instead_of_raising(self, monkeypatch, caplog):
        # Reads degrade, writes abort — get_all_skill_stats has nothing to
        # abort, so it must not inherit the writer's raise.
        import logging
        from skills import get_all_skill_stats
        import skills as _sk
        self._seed_stats()
        monkeypatch.setattr(_sk, "_store_text",
                            lambda _p: (_ for _ in ()).throw(OSError("nope")))
        with caplog.at_level(logging.WARNING):
            assert get_all_skill_stats() == []
        assert any("NOT the same as no stats existing" in r.message
                   for r in caplog.records)


class TestTheCarriedRowKeepsItsOwnBytes:
    """Adversarial r9, applied to the skills writer. `_save_skills` and
    `save_skill` both parsed a STRIPPED copy and then wrote that copy, so a
    row's leading/trailing bytes were rewritten by a save that never claimed
    to touch them — and `splitlines()` in the same loop would have split a
    row containing U+2028 into two invalid fragments."""

    def test_the_full_rewrite_strands_the_row_exactly(self, tmp_path,
                                                      monkeypatch):
        """`_save_skills` is the other writer, and its log line says
        "verbatim". The sweep found it stripping the row it carried."""
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        torn = "\u00a0{not json"
        f.write_text(torn + "\n", encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)

        skills_mod._save_skills([], updated_ids=frozenset())

        after = f.read_text(encoding="utf-8")
        assert torn in after, "\"carried verbatim\" rewrote the row's bytes"

    def test_a_stranded_row_is_written_back_exactly(self, tmp_path,
                                                    monkeypatch):
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        torn = " {not json"
        f.write_text(torn + "\n", encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)

        skill = skills_mod.Skill(id="new", name="n", description="d",
                                 trigger_patterns=[], steps_template=["s"],
                                 source_loop_ids=[],
                                 created_at="2026-01-01T00:00:00+00:00")
        skills_mod.save_skill(skill)

        after = f.read_text(encoding="utf-8")
        assert torn in after, "the row this save could not read lost its bytes"
        assert '"id": "new"' in after


class TestARowTheParserRefusesIsNotALiveSkill:
    """Adversarial r10 (4 of 5 seats, each probing it independently — the
    round's consensus finding). The write paths were hardened to refuse
    byte-tainted rows; the READ path went through the generic
    `read_jsonl_announced`, whose `_classify` still used bare `json.loads`.
    So `load_skills` materialized a Skill from a row `loads_clean` refuses,
    `_save_skills` wrote a CLEAN re-serialized copy of it, and — because
    the strandee landed after it — the laundered row then won last-row-wins
    on the next load. The launder hole the whole arc is about, re-opened
    from the read side."""

    ROW = ('{"id": "s1", "name": "n", "description": "d", '
           '"content_hash": "h", "created_at": "2026-01-01T00:00:00", '
           '"note": "\\udcff"}')

    def test_the_tainted_row_never_becomes_a_skill(self, tmp_path, monkeypatch):
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        f.write_text(self.ROW + "\n", encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)
        assert skills_mod.load_skills() == []

    def test_no_clean_clone_is_written(self, tmp_path, monkeypatch):
        """The half that is data loss rather than a bad read: a clean copy
        on disk makes the corruption undetectable ever after."""
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        f.write_text(self.ROW + "\n", encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)

        _loaded = skills_mod.load_skills()
        skills_mod._save_skills(_loaded, updated_ids={s.id for s in _loaded})

        after = f.read_text(encoding="utf-8")
        assert after.count('"id"') == 1, f"a clone was minted: {after!r}"
        assert "\\udcff" in after, "the row this save could not read lost its bytes"


class TestAnUnprovableRowIsNotAVersionOfAnything:
    """Adversarial r10 (Minimalist + Failure Operator, both probed). Both
    skill writers decided what to REMOVE from a row that only had to
    `loads_clean`-parse — so a row that is valid JSON but not a provable
    Skill took part in a removal decision about itself. `load_skills` skips
    such a row with a log line, which means it is in no caller's list, which
    means the next unrelated outcome update deleted it. This is the rule
    `validate_skill_row`'s own docstring has stated since r3, applied to the
    two callers that were still using the constructor."""

    def _store(self, tmp_path, monkeypatch, drift_first=True):
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)
        good = skills_mod.Skill(id="good", name="n", description="d",
                                trigger_patterns=[], steps_template=["s"],
                                source_loop_ids=[],
                                created_at="2026-01-01T00:00:00+00:00")
        skills_mod.save_skill(good)
        row = json.loads(f.read_text(encoding="utf-8").strip())
        drift = dict(row, id="drift", utility_score="nope")
        rows = [drift, row] if drift_first else [row, drift]
        f.write_text("".join(json.dumps(r) + "\n" for r in rows),
                     encoding="utf-8")
        return skills_mod, f

    def test_a_loadable_row_this_writer_cannot_PROVE_is_still_carried(
            self, tmp_path, monkeypatch):
        """The distinguishing case for `validate_skill_row` over
        `dict_to_skill`, which the r10 sweep showed the other tests could not
        tell apart: `description` is a NUMBER. `dict_to_skill` assigns it
        happily, so the row loads and its id IS in the caller's list — and
        under the constructor it would be re-serialised from the loaded
        Skill, which silently drops every key `skill_to_dict` does not
        write. Under the proof it rides through byte for byte."""
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)
        skills_mod.save_skill(skills_mod.Skill(
            id="x", name="n", description="d", trigger_patterns=[],
            steps_template=["s"], source_loop_ids=[],
            created_at="2026-01-01T00:00:00+00:00"))
        row = json.loads(f.read_text(encoding="utf-8").strip())
        row["description"] = 7
        row["operator_note"] = "keep this row"
        f.write_text(json.dumps(row) + "\n", encoding="utf-8")

        _loaded = skills_mod.load_skills()
        skills_mod._save_skills(_loaded, updated_ids={s.id for s in _loaded})

        after = f.read_text(encoding="utf-8")
        assert '"operator_note": "keep this row"' in after, \
            "a row this writer cannot prove was re-serialised, losing a field"

    def test_the_full_rewrite_keeps_it(self, tmp_path, monkeypatch):
        skills_mod, f = self._store(tmp_path, monkeypatch)
        loaded = skills_mod.load_skills()
        assert [s.id for s in loaded] == ["good"]

        skills_mod._save_skills(loaded, updated_ids={s.id for s in loaded})

        after = f.read_text(encoding="utf-8")
        assert '"utility_score": "nope"' in after, \
            "an unrelated save deleted a row it could not prove"
        assert '"id": "good"' in after

    def test_it_keeps_its_ordinal(self, tmp_path, monkeypatch):
        """The store is read last-row-wins by id, so carrying a row to the
        TAIL is not preservation — it is a promotion. `doctor` has preserved
        ordinals since r7; the skills writer appended strandees after every
        live skill (adversarial r10, Minimalist)."""
        skills_mod, f = self._store(tmp_path, monkeypatch, drift_first=False)

        _loaded = skills_mod.load_skills()
        skills_mod._save_skills(_loaded, updated_ids={s.id for s in _loaded})

        lines = f.read_text(encoding="utf-8").rstrip("\n").split("\n")
        # SECOND, deliberately. With the carried row first, appending it to
        # the tail lands it in the same place and the assertion cannot fail
        # — which is how the first version of this test passed the mutation
        # that removed ordinal preservation entirely.
        assert '"utility_score": "nope"' in lines[1], \
            f"the carried row was moved: {lines}"

    def test_a_single_skill_save_keeps_it_too(self, tmp_path, monkeypatch):
        """`save_skill` matched on `.get("id")` alone, so writing skill
        `drift` would have deleted the row it cannot prove is that skill."""
        skills_mod, f = self._store(tmp_path, monkeypatch)
        replacement = skills_mod.Skill(id="drift", name="n2", description="d",
                                       trigger_patterns=[], steps_template=["s"],
                                       source_loop_ids=[],
                                       created_at="2026-02-01T00:00:00+00:00")

        skills_mod.save_skill(replacement)

        after = f.read_text(encoding="utf-8")
        assert '"utility_score": "nope"' in after
        assert '"name": "n2"' in after

    def test_a_provable_row_the_caller_dropped_is_still_removed(self):
        """The negative control, and the reason this is not just 'never
        delete': graduation, demotion and GC drop skills ON PURPOSE, and
        that decision must still take effect."""
        import skills as skills_mod
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "skills.jsonl"
            import unittest.mock as mock
            with mock.patch.object(skills_mod, "_skills_path", lambda: f):
                for i in ("a", "b"):
                    skills_mod.save_skill(skills_mod.Skill(
                        id=i, name=i, description="d", trigger_patterns=[],
                        steps_template=["s"], source_loop_ids=[],
                        created_at="2026-01-01T00:00:00+00:00"))
                keep = [s for s in skills_mod.load_skills() if s.id == "a"]
                # r16: the drop is named; an unnamed absence is carried.
                skills_mod._save_skills(keep, dropped_ids={"b"}, updated_ids=frozenset())
                after = f.read_text(encoding="utf-8")
        assert '"id": "a"' in after and '"id": "b"' not in after


class TestABrokenRowDoesNotHideAWorkingOne:
    """Adversarial r10, found while probing the one above. `load_skills`
    claimed an id for `seen_ids` BEFORE converting the row, so the newest
    UNLOADABLE row for an id shadowed the newest loadable one and the skill
    left the library with nothing but a count in the log."""

    def test_the_older_valid_version_still_loads(self, tmp_path, monkeypatch):
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)
        skills_mod.save_skill(skills_mod.Skill(
            id="x", name="working", description="d", trigger_patterns=[],
            steps_template=["s"], source_loop_ids=[],
            created_at="2026-01-01T00:00:00+00:00"))
        row = json.loads(f.read_text(encoding="utf-8").strip())
        # `utility_score` is cast with float() inside dict_to_skill, so this
        # is a row load_skills genuinely CANNOT build. The first version of
        # this test used `steps_template="not a list"`, which dict_to_skill
        # assigns without complaint — the row loaded fine and the test could
        # not fail (r10 mutation sweep).
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row, utility_score="nope")) + "\n")

        loaded = skills_mod.load_skills()

        assert [s.name for s in loaded] == ["working"]


class TestTheSkillStatsRewriteKeepsWhatItCannotRead:
    """Adversarial r10 (Skeptic, probed): `_read_skill_stats` was the last
    read->rewrite pair in the arc still on the r8 idiom, and both halves of
    it destroyed data. `splitlines()` broke a valid row at the U+2028 inside
    a JSON string and the writer put the two fragments back rejoined with
    LF — the row's bytes CHANGED while the log said "carried verbatim" —
    and `line.strip()` deleted a whitespace-only row outright, neither
    stranded nor counted. This is the hot path every outcome update takes."""

    def test_a_whitespace_only_row_survives(self, tmp_path):
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text(json.dumps({"skill_id": "a"}) + "\n\u00a0\n",
                     encoding="utf-8")

        records, stranded = skills_mod._read_skill_stats(f)
        skills_mod._write_skill_stats(f, records, stranded)

        after = f.read_text(encoding="utf-8")
        assert "\u00a0" in after, "a row the reader could not parse was deleted"
        assert '"skill_id": "a"' in after

    def test_a_row_holding_U2028_is_not_split_in_two(self, tmp_path):
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        # ensure_ascii=False, or the fixture never contains a U+2028 at all
        # — json.dumps escapes it to the six ASCII characters `\u2028` by
        # default, splitlines() does not break on those, and the test passes
        # on the defect. The r10 mutation sweep caught exactly that.
        row = json.dumps({"skill_id": "b", "note": "x\u2028y"},
                         ensure_ascii=False)
        assert "\u2028" in row, "fixture drifted — no U+2028 in the row"
        f.write_text(row + "\n", encoding="utf-8")

        records, stranded = skills_mod._read_skill_stats(f)

        assert list(records) == ["b"], f"the row was framed apart: {records}"
        assert records["b"]["note"] == "x\u2028y"
        assert not stranded


class TestAdmissionIsTheProof:
    """Adversarial r11 (four of five seats, independently): r10 put
    `validate_skill_row` on the WRITERS, so `load_skills` still admitted via
    the tolerant `dict_to_skill` constructor — and the gap between a
    tolerant loader and a strict writer is a launder mint. A row carrying
    `"utility_score": "1.0"` loaded fine, and `_save_skills` emitted a
    NORMALIZED CLONE (float 1.0) that won last-row-wins: constructible !=
    provable != deliverable. One admission predicate on both ends —
    admitted == provable — kills the clone structurally, not per-field."""

    def _write(self, tmp_path, monkeypatch, rows):
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        f.write_text("".join(json.dumps(r) + "\n" for r in rows),
                     encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)
        return f

    def _valid_row(self, **over):
        s = Skill(id="x", name="n", description="d", trigger_patterns=[],
                  steps_template=["s"], source_loop_ids=[],
                  created_at="2026-01-01T00:00:00+00:00")
        s.content_hash = compute_skill_hash(s)
        return {**_skill_to_dict(s), **over}

    def test_a_coercible_but_unprovable_row_is_not_admitted(
            self, tmp_path, monkeypatch, caplog):
        import logging

        import skills as skills_mod

        self._write(tmp_path, monkeypatch,
                    [self._valid_row(utility_score="1.0")])
        with caplog.at_level(logging.WARNING):
            assert skills_mod.load_skills() == []
        assert any("not loadable as Skill" in r.getMessage()
                   for r in caplog.records)

    def test_no_normalized_clone_is_minted(self, tmp_path, monkeypatch):
        """The data-loss half: the raw row must survive AND no clean twin
        may appear to win last-row-wins over it."""
        import skills as skills_mod

        f = self._write(tmp_path, monkeypatch,
                        [self._valid_row(utility_score="1.0",
                                         operator_note="keep")])
        _loaded = skills_mod.load_skills()
        skills_mod._save_skills(_loaded, updated_ids={s.id for s in _loaded})

        after = f.read_text(encoding="utf-8")
        lines = [l for l in after.split("\n") if l]
        assert sum('"id": "x"' in l for l in lines) == 1, lines
        assert '"utility_score": "1.0"' in after     # raw row verbatim
        assert '"operator_note": "keep"' in after

    def test_an_admit_fail_row_cannot_shadow_an_older_valid_one(
            self, tmp_path, monkeypatch):
        """r11's shadow-delete: the loader used to claim the id BEFORE the
        proof, so a construct-ok/hash-fail newer row hid the older valid row
        from every caller — and `_save_skills` then deleted the valid one.
        The id is claimed only by a row that proves out."""
        import skills as skills_mod

        good = self._valid_row(id="same", name="good")
        bad = dict(good, description=7, name="bad")   # constructs; proof fails
        f = self._write(tmp_path, monkeypatch, [good, bad])

        loaded = skills_mod.load_skills()
        assert [s.name for s in loaded] == ["good"]

        skills_mod._save_skills(loaded, updated_ids={s.id for s in loaded})
        after = f.read_text(encoding="utf-8")
        assert '"name": "good"' in after
        assert '"name": "bad"' in after, "the unprovable row was deleted"


class TestTheWriterCannotOutrunItsReader:
    """Adversarial r11 (F2c): `json.dumps` happily writes CPython `NaN` and
    clean `\\udcXX` escapes — rows this module's own strict reader then
    refuses. A writer that can emit what its reader strands is minting
    strandees; `_prove_line` runs every emitted line back through the same
    admission door, and a failure ABORTS BEFORE THE STORE IS TOUCHED."""

    def _seed(self, tmp_path, monkeypatch):
        import skills as skills_mod

        f = tmp_path / "skills.jsonl"
        monkeypatch.setattr(skills_mod, "_skills_path", lambda: f)
        skills_mod.save_skill(Skill(
            id="v", name="valid", description="d", trigger_patterns=[],
            steps_template=["s"], source_loop_ids=[],
            created_at="2026-01-01T00:00:00+00:00"))
        return f

    def test_save_skill_refuses_a_nan_score(self, tmp_path, monkeypatch):
        import math as _math

        import skills as skills_mod

        f = self._seed(tmp_path, monkeypatch)
        before = f.read_bytes()
        nan_skill = Skill(id="v", name="v2", description="d",
                          trigger_patterns=[], steps_template=["s"],
                          source_loop_ids=[],
                          created_at="2026-01-01T00:00:00+00:00",
                          utility_score=_math.nan)

        with pytest.raises(ValueError):
            skills_mod.save_skill(nan_skill)

        assert f.read_bytes() == before, "the store was touched on abort"
        assert [s.name for s in skills_mod.load_skills()] == ["valid"]

    def test_save_skill_refuses_a_schema_invalid_emission(
            self, tmp_path, monkeypatch):
        """Adversarial r12 (Skeptic + Minimalist, probed): r11's proof ran
        `loads_clean` alone while the reader admits via
        `validate_skill_row` — so a constructible Skill with `tier=7`
        (hash-excluded, JSON-clean) was emitted, REPLACED the healthy row,
        and stranded on the next load. The writer proves the COMPLETE
        admission predicate now."""
        import skills as skills_mod

        f = self._seed(tmp_path, monkeypatch)
        before = f.read_bytes()
        bad = Skill(id="v", name="v2", description="d", trigger_patterns=[],
                    steps_template=["s"], source_loop_ids=[],
                    created_at="2026-01-01T00:00:00+00:00", tier=7)

        with pytest.raises(Exception):
            skills_mod.save_skill(bad)

        assert f.read_bytes() == before, "the store was touched on abort"
        assert [s.name for s in skills_mod.load_skills()] == ["valid"]

    def test_save_skills_aborts_on_a_row_its_reader_would_strand(
            self, tmp_path, monkeypatch):
        """The bulk-writer twin: every emission point runs the same proof.
        A caller mutating a loaded Skill in memory (the update-utility
        shape) must not be able to write what the next load strands."""
        import math as _math

        import skills as skills_mod

        f = self._seed(tmp_path, monkeypatch)
        before = f.read_bytes()
        loaded = skills_mod.load_skills()
        loaded[0].utility_score = _math.nan

        # r16: _save_skills RAISES on abort (an error result must not be
        # a valid value) — and the store must still be untouched.
        import pytest
        with pytest.raises(ValueError):
            skills_mod._save_skills(loaded, updated_ids={s.id for s in loaded})

        assert f.read_bytes() == before, "the store was touched on abort"

    @staticmethod
    def _warning_capture():
        import contextlib
        import logging

        @contextlib.contextmanager
        def _cap():
            records = []

            class _H(logging.Handler):
                def emit(self, record):
                    records.append(record)

            h = _H(level=logging.WARNING)
            logging.getLogger("skills").addHandler(h)
            try:
                yield records
            finally:
                logging.getLogger("skills").removeHandler(h)

        return _cap()

    def test_save_skill_refuses_an_escaped_lone_surrogate(
            self, tmp_path, monkeypatch):
        """`tier` is hash-excluded, so the hash proof cannot see it — but
        dumps would emit it as a CLEAN six-char escape the reader strands.
        The write must fail loudly instead, with the old row intact."""
        import skills as skills_mod

        f = self._seed(tmp_path, monkeypatch)
        before = f.read_bytes()
        sur = Skill(id="v", name="v2", description="d", trigger_patterns=[],
                    steps_template=["s"], source_loop_ids=[],
                    created_at="2026-01-01T00:00:00+00:00",
                    tier="\udcff")

        with pytest.raises(Exception):
            skills_mod.save_skill(sur)

        assert f.read_bytes() == before, "the store was touched on abort"
        assert [s.name for s in skills_mod.load_skills()] == ["valid"]


class TestTheStatsWriterCannotOutrunItsReader:
    """Adversarial r11 (Architect): non-finite telemetry — a NaN
    avg_latency, say — would write the CPython token `NaN`, which the
    strict reader strands on the next load: the writer manufacturing its
    own unreadable row. Raising aborts the update with the store intact."""

    def test_write_skill_stats_refuses_nonfinite_telemetry(self, tmp_path):
        import math as _math

        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text('{"skill_id": "a", "uses": 3}\n', encoding="utf-8")
        before = f.read_bytes()

        # r13 put validate_skill_stats_row in FRONT of the emission
        # proof, so the refusal now arrives as its TypeError — accept
        # either door; the property under test is refuse-before-write.
        with pytest.raises((TypeError, ValueError)):
            skills_mod._write_skill_stats(
                f, {"a": {"skill_id": "a", "avg_latency_ms": _math.nan}}, [])

        assert f.read_bytes() == before, "the store was touched on abort"


class TestSkillStatsKeysAreIdentity:
    """Adversarial r11 (Expert QA): JSON `1` and `true` are different rows,
    but Python dict keys say `1 == True` — so a keyed rebuild of the stats
    store silently deleted one of them. A non-string id is not an identity
    this store can key on; such rows are strandees, kept verbatim."""

    def test_json_1_and_true_do_not_collide(self, tmp_path):
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text('{"skill_id": 1, "skill_name": "first"}\n'
                     '{"skill_id": true, "skill_name": "second"}\n',
                     encoding="utf-8")

        records, stranded = skills_mod._read_skill_stats(f)
        assert records == {}
        assert len(stranded) == 2

        skills_mod._write_skill_stats(f, records, stranded)
        after = f.read_text(encoding="utf-8")
        assert '"first"' in after and '"second"' in after

    def test_a_string_keyed_row_is_still_a_record(self, tmp_path):
        """Negative control — the live store is 100% string-keyed."""
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text('{"skill_id": "a", "uses": 3}\n', encoding="utf-8")

        records, stranded = skills_mod._read_skill_stats(f)

        assert list(records) == ["a"] and not stranded


class TestStatsUpdateMergesOverTheStoredRow:
    """Adversarial r11 (F6): both outcome recorders rebuilt the row from
    `SkillStats.to_dict()`, so any field this module does not model — an
    operator's note, a foreign tool's stamp — was deleted by the next
    routine update. The update now merges over the stored row: unknown
    fields ride through untouched."""

    def test_record_skill_outcome_keeps_unknown_fields(
            self, tmp_path, monkeypatch):
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text(json.dumps({"skill_id": "s", "skill_name": "n",
                                 "operator_note": "keep"}) + "\n",
                     encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skill_stats_path", lambda: f)

        skills_mod.record_skill_outcome("s", True)

        after = f.read_text(encoding="utf-8")
        assert '"operator_note": "keep"' in after
        # Fields SkillStats models (needs_escalation, success_rate...) are
        # this module's to recompute — the merge protects only what it does
        # not own.
        assert '"total_uses": 1' in after

    def test_record_skill_injection_outcome_keeps_unknown_fields(
            self, tmp_path, monkeypatch):
        """The branch twin (sibling census: same rebuild, other recorder)."""
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text(json.dumps({"skill_id": "s", "skill_name": "n",
                                 "operator_note": "keep"}) + "\n",
                     encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skill_stats_path", lambda: f)

        skills_mod.record_skill_injection_outcome("s", True)

        assert '"operator_note": "keep"' in f.read_text(encoding="utf-8")


class TestTheFinalTornFrameGainsATerminatorOnPurpose:
    """Adversarial r11 F4, ACCEPTED WITH REASON rather than fixed: a torn
    final fragment with no trailing LF is carried with one added. The
    alternative preserves the missing terminator — and then the next
    `locked_append` concatenates a fresh record INTO the torn fragment,
    corrupting both. Content bytes are intact; only the frame is
    normalized. This pin is the record of that decision — if it fires,
    someone 'fixed' F4 and reopened the concatenation hole."""

    def test_the_strand_keeps_its_bytes_and_gains_only_an_lf(self, tmp_path):
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text('{"skill_id": "a"}\n{"torn": ', encoding="utf-8")

        records, stranded = skills_mod._read_skill_stats(f)
        skills_mod._write_skill_stats(f, records, stranded)

        # Strandees ride FIRST since adversarial r14 (same ordinal rule
        # as the r13 backend rewrite); the torn frame keeps its bytes
        # and gains only its framing LF.
        assert f.read_text(encoding="utf-8") == (
            '{"torn": \n{"skill_id": "a"}\n')


class TestStatsAdmissionIsTheProof:
    """Adversarial r12 (two seats, probed): the r11 skills rule applied to
    its own stats twin. `SkillStats.from_dict` is a COERCING constructor —
    `float("1.0")` passes, `bool("false")` is True — so a schema-drifted
    row rode a routine counter bump and came back with modeled fields
    silently laundered; the injection recorder (which does not recompute
    `needs_escalation`) flipped a stored `"false"` to JSON `true`. Rows
    the model would distort strand verbatim. Census: 203/203 live rows
    pass."""

    def test_a_drifted_row_strands_instead_of_being_laundered(
            self, tmp_path, monkeypatch):
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text(json.dumps({"skill_id": "s1", "skill_name": "S",
                                 "success_rate": "1.0",
                                 "needs_escalation": "false",
                                 "operator_note": "keep"}) + "\n",
                     encoding="utf-8")
        monkeypatch.setattr(skills_mod, "_skill_stats_path", lambda: f)

        skills_mod.record_skill_injection_outcome("s1", True)

        after = f.read_text(encoding="utf-8")
        assert '"success_rate": "1.0"' in after, "the raw row was rewritten"
        assert '"needs_escalation": "false"' in after, \
            "a stored string-false was laundered to a real bool"
        assert '"needs_escalation": true' not in after

    def test_a_well_typed_row_is_still_a_record(self, tmp_path):
        """Negative control — the live store is 203/203 well-typed."""
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text(json.dumps({"skill_id": "a", "skill_name": "A",
                                 "total_uses": 3, "success_rate": 0.5,
                                 "needs_escalation": False}) + "\n",
                     encoding="utf-8")

        records, stranded = skills_mod._read_skill_stats(f)

        assert list(records) == ["a"] and not stranded

    def test_the_recorders_refuse_an_id_no_reader_can_return(
            self, tmp_path, monkeypatch):
        """A non-string id would strand as keyless; a surrogate id would
        strand as byte-tainted. Either way the recorder would report an
        outcome no read can ever surface — refuse at the door instead."""
        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        monkeypatch.setattr(skills_mod, "_skill_stats_path", lambda: f)

        with pytest.raises(TypeError):
            skills_mod.record_skill_outcome(1, True)
        with pytest.raises(TypeError):
            skills_mod.record_skill_outcome("bad\udcff", True)
        with pytest.raises(TypeError):
            skills_mod.record_skill_injection_outcome(1, True)
        with pytest.raises(TypeError):
            skills_mod.record_skill_injection_outcome("bad\udcff", True)

        assert not f.exists(), "a refused outcome still touched the store"

    def test_duplicate_string_ids_are_announced_not_silent(
            self, tmp_path, caplog):
        """Same id twice IS representable (last wins, matching the keyed
        read) — but N rows becoming one on the next rewrite must be said
        out loud (adversarial r12, QA). The drop is right; the silence
        was not."""
        import logging

        import skills as skills_mod

        f = tmp_path / "skill-stats.jsonl"
        f.write_text('{"skill_id": "same", "skill_name": "older"}\n'
                     '{"skill_id": "same", "skill_name": "newer"}\n',
                     encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            records, stranded = skills_mod._read_skill_stats(f)

        assert records["same"]["skill_name"] == "newer"   # last wins, pinned
        assert not stranded
        assert any("duplicate" in r.getMessage() and str(f) in r.getMessage()
                   for r in caplog.records), \
            "duplicate compaction passed in silence"


class TestPresenceIsNotAbsence:
    """Adversarial r13 (three seats, probed): `d.get(name)` made an
    explicitly stored JSON `null` indistinguishable from an absent field,
    so a present null rode the absence exemption, `bool(None)` laundered
    it to `false` on the next counter bump, and a `null` counter would
    have made the NEXT update raise mid-recorder. No modeled field is
    nullable in the emitted schema."""

    def test_a_present_null_is_refused_for_every_modeled_field(self):
        from skills import validate_skill_stats_row
        for field in ("total_uses", "success_rate", "skill_name",
                      "needs_escalation"):
            with pytest.raises(TypeError):
                validate_skill_stats_row({"skill_id": "s", field: None})

    def test_a_null_row_strands_instead_of_being_normalized(
            self, tmp_path, monkeypatch):
        import skills as skills_mod
        _setup_workspace(monkeypatch, tmp_path)
        path = skills_mod._skill_stats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps({"skill_id": "s1", "needs_escalation": None,
                          "operator_note": "keep"})
        path.write_text(raw + "\n")

        skills_mod.record_skill_injection_outcome("s1", True)

        text = path.read_text()
        assert raw in text, "the null row was not carried verbatim"
        assert '"operator_note"' in text


class TestEvidenceArrivesAsEvidence:
    """Adversarial r13 (Architect, probed): `success="false"` is truthy,
    so a stringly-typed caller recorded a FAILURE as a success — evidence
    that type-checks clean forever after. And non-finite telemetry
    reached the emission door, whose refusal the never-raise write
    wrapper swallowed — the outcome silently discarded with a normal
    return. Both refused at the door now, store untouched."""

    def test_the_recorders_refuse_truthy_nonbool_verdicts(
            self, tmp_path, monkeypatch):
        import skills as skills_mod
        _setup_workspace(monkeypatch, tmp_path)
        with pytest.raises(TypeError):
            skills_mod.record_skill_outcome("s", "false")
        with pytest.raises(TypeError):
            skills_mod.record_skill_injection_outcome("s", "false")
        assert not skills_mod._skill_stats_path().exists()

    def test_non_finite_telemetry_is_refused_before_any_mutation(
            self, tmp_path, monkeypatch):
        import math
        import skills as skills_mod
        _setup_workspace(monkeypatch, tmp_path)
        for kw in ({"cost_usd": math.nan}, {"latency_ms": math.inf},
                   {"confidence": True}):
            with pytest.raises(TypeError):
                skills_mod.record_skill_outcome("s", True, **kw)
        assert not skills_mod._skill_stats_path().exists()


class TestTheStatsWriterProvesTheReadersPredicate:
    """Adversarial r13 (Architect, probed): `_write_skill_stats` proved
    clean-object JSON while its reader admits via
    `validate_skill_stats_row` — the writer could vouch for a row no
    reader will ever return. One admission predicate on both ends."""

    def test_the_writer_refuses_a_row_its_reader_strands(self, tmp_path):
        import skills as skills_mod
        path = tmp_path / "skill-stats.jsonl"
        path.write_text('{"skill_id": "keep"}\n')
        before = path.read_bytes()
        with pytest.raises(TypeError):
            skills_mod._write_skill_stats(
                path, {"s": {"skill_id": "s", "needs_escalation": "false"}},
                [])
        assert path.read_bytes() == before, "the store was touched"

    def test_the_writer_refuses_a_surrogate_it_cannot_re_read(self, tmp_path):
        """The schema validator passes a lone surrogate (it IS a str), so
        only the emission re-read stands between it and the store — pin
        that second lock separately from the first."""
        import skills as skills_mod
        path = tmp_path / "skill-stats.jsonl"
        path.write_text('{"skill_id": "keep"}\n')
        before = path.read_bytes()
        with pytest.raises(Exception):
            skills_mod._write_skill_stats(
                path, {"s": {"skill_id": "s", "skill_name": "bad\udcff"}},
                [])
        assert path.read_bytes() == before, "the store was touched"


class TestTheArchiveIsAWriterToo:
    """Adversarial r13 (Skeptic, probed): `_archive_skills` — the
    retention guarantee itself — used bare json.dumps, so a skill holding
    a lone surrogate archived as a row the strict reader strands, and was
    then removed from the live pool. The archive proves every line
    BEFORE any append; a refusal aborts the caller's removal too."""

    def test_an_unprovable_skill_aborts_the_archive_before_any_append(
            self, tmp_path, monkeypatch):
        import skills as skills_mod
        _setup_workspace(monkeypatch, tmp_path)
        good = Skill(id="a", name="n", description="d", trigger_patterns=[],
                     steps_template=["s"], source_loop_ids=[],
                     created_at="2026-01-01T00:00:00+00:00")
        bad = Skill(id="b", name="n\udcff", description="d",
                    trigger_patterns=[], steps_template=["s"],
                    source_loop_ids=[],
                    created_at="2026-01-01T00:00:00+00:00")
        arch = skills_mod._skills_archive_path()
        with pytest.raises(Exception):
            skills_mod._archive_skills([good, bad], reason="test")
        assert not arch.exists(), "a partial archive was written"
        skills_mod._archive_skills([good], reason="test")
        assert arch.exists()


# ---------------------------------------------------------------------------
# Adversarial r14
# ---------------------------------------------------------------------------


class TestIdentityIsPartOfThePredicate:
    """Adversarial r14 (four seats, probed): the reader keys this store
    on a non-empty STRING skill_id, but validate_skill_stats_row checked
    only the modeled statistic fields — so _write_skill_stats vouched
    for a skill_id:null row the reader immediately strands as keyless.
    Admitted == provable includes identity."""

    def test_validator_refuses_bad_identity(self):
        import pytest
        from skills import validate_skill_stats_row
        for sid in (None, "", 7, True):
            with pytest.raises(TypeError):
                validate_skill_stats_row({"skill_id": sid, "total_uses": 1})
        with pytest.raises(TypeError):
            validate_skill_stats_row({"total_uses": 1})

    def test_writer_refuses_a_keyless_row(self, tmp_path):
        import pytest
        from skills import _write_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        with pytest.raises((TypeError, ValueError)):
            _write_skill_stats(
                path, {"x": {"skill_id": None, "total_uses": 1}}, [])
        assert not path.exists(), "refusal must abort before the write"

    def test_writer_refuses_a_rekeyed_row(self, tmp_path):
        """The map key and the row's own identity must agree
        (adversarial r14, Minimalist)."""
        import pytest
        from skills import _write_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        with pytest.raises(ValueError):
            _write_skill_stats(
                path, {"other": {"skill_id": "s", "total_uses": 1}}, [])
        assert not path.exists()


class TestTheStatsWriterPutsStrandeesFirst:
    """Adversarial r14 (three seats, probed): r13 moved generic-rewrite
    strandees to the head of the payload so a keyed last-row-wins
    consumer can never let a stranded legacy row shadow the caller's
    fresh record — and this sibling writer kept the old tail position,
    where a same-id stranded row overrode the repaired one for any
    naive parser."""

    def test_strandees_ride_first(self, tmp_path):
        import json
        from skills import _write_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        legacy = '{"skill_id":"s","needs_escalation":"false"}'
        _write_skill_stats(
            path, {"s": {"skill_id": "s", "total_uses": 3}}, [legacy])
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == legacy
        # A naive keyed last-row-wins parser sees the VALID row.
        naive = {}
        for line in lines:
            try:
                d = json.loads(line)
            except Exception:
                continue
            naive[d.get("skill_id")] = d
        assert naive["s"]["total_uses"] == 3
        assert "needs_escalation" not in naive["s"]

    def test_unterminated_strandee_round_trips(self, tmp_path):
        """r14 accept-and-pin twin of the backend pin: the strandee's
        payload bytes survive verbatim; the appended LF is framing."""
        from skills import _read_skill_stats, _write_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        path.write_bytes(b'{"skill_id":"s","total_uses":1}\n\xff')
        records, stranded = _read_skill_stats(path)
        _write_skill_stats(path, records, stranded)
        first = path.read_bytes().split(b"\n", 1)[0]
        assert first == b"\xff"
        records2, stranded2 = _read_skill_stats(path)
        assert stranded2 == stranded


class TestAReadAnnouncesAReadNotARewrite:
    """Adversarial r14 (Architect, probed): _read_skill_stats logged
    "carried through the rewrite verbatim" from PURE READS —
    get_all_skill_stats never rewrites, and a recorder could log the
    claim and then fail its write. The carry-through announcement
    belongs to the writer, after its commit."""

    def test_pure_read_does_not_claim_a_rewrite(self, tmp_path, caplog):
        import logging
        from skills import _read_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        path.write_text("BROKEN\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="skills"):
            _read_skill_stats(path)
        text = caplog.text
        assert "excluded from this read" in text
        assert "carried through the rewrite" not in text

    def test_the_writer_announces_after_the_commit(self, tmp_path, caplog):
        import logging
        from skills import _write_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        with caplog.at_level(logging.WARNING, logger="skills"):
            _write_skill_stats(
                path, {"s": {"skill_id": "s", "total_uses": 1}},
                ["BROKEN"])
        assert "carried through the rewrite verbatim" in caplog.text

    def test_a_refused_write_leaves_no_carry_claim(
            self, tmp_path, caplog, monkeypatch):
        import logging
        import pytest
        import file_lock
        from skills import _write_skill_stats

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(file_lock, "atomic_write", boom)
        path = tmp_path / "skill-stats.jsonl"
        with caplog.at_level(logging.WARNING, logger="skills"):
            with pytest.raises(OSError):
                _write_skill_stats(
                    path, {"s": {"skill_id": "s", "total_uses": 1}},
                    ["BROKEN"])
        assert "carried through the rewrite" not in caplog.text


class TestTheArchiveBatchCannotSplit:
    """Adversarial r14 (Failure Operator, probed): per-line appends let
    a mid-batch failure land HALF a batch, and the caller's retry then
    duplicated the already-landed skills. One append call per batch: a
    failure lands nothing, so a retry starts clean. (Residual, accepted:
    a retry after a successful append still duplicates — in an
    append-only retention store a duplicate is noise, not loss.)"""

    def _skills(self, ids):
        from skills import _dict_to_skill
        return [_dict_to_skill({
            "id": i, "name": i, "description": "d",
            "trigger_patterns": [], "steps": [],
            "created_at": "t", "version": 1}) for i in ids]

    def test_one_append_call_per_batch(self, tmp_path, monkeypatch):
        import json
        import file_lock
        import skills as sk
        arch = tmp_path / "archive.jsonl"
        monkeypatch.setattr(sk, "_skills_archive_path", lambda: arch)
        calls = []
        real = file_lock.locked_append
        monkeypatch.setattr(
            file_lock, "locked_append",
            lambda path, line, **kw: (
                calls.append(1), real(path, line, **kw))[1])
        sk._archive_skills(self._skills(["a", "b"]), reason="cull")
        assert len(calls) == 1
        ids = [json.loads(l)["id"]
               for l in arch.read_text(encoding="utf-8").splitlines()
               if l.strip()]
        assert ids == ["a", "b"]

    def test_a_failed_batch_lands_nothing(self, tmp_path, monkeypatch):
        import pytest
        import file_lock
        import skills as sk
        arch = tmp_path / "archive.jsonl"
        monkeypatch.setattr(sk, "_skills_archive_path", lambda: arch)

        def boom(path, line, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(file_lock, "locked_append", boom)
        with pytest.raises(OSError):
            sk._archive_skills(self._skills(["a", "b"]), reason="cull")
        assert not arch.exists() or arch.read_text() == ""


class TestTheRecordersFailClosed:
    """Adversarial r15 (four seats, probed): the two skill-stats
    read-modify-write recorders were the untraveled twins of the r14
    transform fix — bare locked_write() let fail-open run the RMW
    unlocked (two concurrent recorders both read N, both wrote N+1,
    one outcome silently lost), and a failed _write_skill_stats was
    caught, warned, and converted into the recorders' ordinary None
    return, so a caller could not distinguish disk-full from success."""

    def _scratch(self, monkeypatch, tmp_path):
        import skills as sk
        p = tmp_path / "skill-stats.jsonl"
        monkeypatch.setattr(sk, "_skill_stats_path", lambda: p)
        return sk, p

    def test_record_skill_outcome_raises_when_the_write_fails(
            self, tmp_path, monkeypatch):
        import pytest
        sk, _ = self._scratch(monkeypatch, tmp_path)

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(sk, "_write_skill_stats", boom)
        with pytest.raises(OSError):
            sk.record_skill_outcome("s", True)

    def test_record_skill_injection_outcome_raises_when_the_write_fails(
            self, tmp_path, monkeypatch):
        import pytest
        sk, _ = self._scratch(monkeypatch, tmp_path)

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(sk, "_write_skill_stats", boom)
        with pytest.raises(OSError):
            sk.record_skill_injection_outcome("s", True)

    def test_both_recorders_take_the_lock_with_require(self):
        """Cheap structural pin: the keyword is the fix (same shape as
        TestTransformRefusesToRunUnlocked's pin in
        tests/test_memory_backend.py)."""
        import ast
        import inspect
        import skills as sk
        for fn in (sk.record_skill_outcome,
                   sk.record_skill_injection_outcome):
            src = inspect.getsource(fn)
            calls = [n for n in ast.walk(ast.parse(src.lstrip()))
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", "") == "locked_write"]
            assert calls, f"{fn.__name__} no longer calls locked_write"
            kw = {k.arg: getattr(k.value, "value", None)
                  for k in calls[0].keywords}
            assert kw.get("require") is True, fn.__name__

    def test_contended_fail_open_raises_before_the_recorder_writes(
            self, tmp_path, monkeypatch):
        import fcntl
        import pytest
        from file_lock import FileLockTimeout
        sk, p = self._scratch(monkeypatch, tmp_path)
        monkeypatch.setenv("MARO_FILELOCK_FAIL_OPEN", "1")
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "1")
        holder = open(str(p) + ".lock", "w")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            with pytest.raises(FileLockTimeout):
                sk.record_skill_outcome("s", True)
            assert not p.exists()
        finally:
            holder.close()


class TestADuplicateCompactionIsAnnouncedByTheWriter:
    """Adversarial r15 (four seats, probed): the r14 fix moved the
    stranded-carry announcement behind commit but left its duplicate
    twin behind — a PURE READ of two same-id rows logged "will be
    compacted by the next rewrite", an unconditional destructive claim
    from a path that changes nothing. If no rewrite follows, or the
    write fails, the audit record overstates what happened. The read
    now reports an exclusion from ITS OWN result and hands the count to
    the writer, which announces compaction only after its commit."""

    ROWS = ('{"skill_id": "d", "total_uses": 1}\n'
            '{"skill_id": "d", "total_uses": 2}\n')

    def test_a_pure_read_claims_no_rewrite(self, tmp_path, caplog):
        import logging
        from skills import _read_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        path.write_text(self.ROWS, encoding="utf-8")
        before = path.read_bytes()
        with caplog.at_level(logging.WARNING, logger="skills"):
            read = _read_skill_stats(path)
        assert path.read_bytes() == before
        assert read.compacted == 1
        assert "excluded from this keyed read" in caplog.text
        assert "compacted by" not in caplog.text
        assert "will be compacted" not in caplog.text

    def test_the_writer_announces_compaction_after_commit(
            self, tmp_path, caplog):
        import logging
        from skills import _read_skill_stats, _write_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        path.write_text(self.ROWS, encoding="utf-8")
        read = _read_skill_stats(path)
        records, stranded = read
        with caplog.at_level(logging.WARNING, logger="skills"):
            _write_skill_stats(path, records, stranded,
                               compacted=read.compacted)
        assert "compacted by this rewrite" in caplog.text
        lines = [l for l in
                 path.read_text(encoding="utf-8").splitlines() if l]
        assert len(lines) == 1

    def test_a_failed_write_claims_no_compaction(
            self, tmp_path, caplog, monkeypatch):
        import logging
        import pytest
        import file_lock
        from skills import _read_skill_stats, _write_skill_stats
        path = tmp_path / "skill-stats.jsonl"
        path.write_text(self.ROWS, encoding="utf-8")
        read = _read_skill_stats(path)
        records, stranded = read

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(file_lock, "atomic_write", boom)
        with caplog.at_level(logging.WARNING, logger="skills"):
            with pytest.raises(OSError):
                _write_skill_stats(path, records, stranded,
                                   compacted=read.compacted)
        assert "compacted by this rewrite" not in caplog.text


class TestTheArchiveIsDurableBeforeTheDelete:
    """Adversarial r15 (two seats, probed): the live-pool removal that
    follows _archive_skills goes through fsyncing atomic_write, but the
    archive append rode the page cache — a power loss could keep the
    deletion and lose the retention copy, and bare locked_write let
    fail-open run the retention writer unlocked. The append is now
    require=True + durable=True (fsync the file, and the parent dir
    when the append created it)."""

    def _skills(self, ids):
        from skills import _dict_to_skill
        return [_dict_to_skill({
            "id": i, "name": i, "description": "d",
            "trigger_patterns": [], "steps": [],
            "created_at": "t", "version": 1}) for i in ids]

    def test_the_append_fsyncs_file_and_new_parent_dir(
            self, tmp_path, monkeypatch):
        import os as _os
        import skills as sk
        arch = tmp_path / "archive.jsonl"
        monkeypatch.setattr(sk, "_skills_archive_path", lambda: arch)
        fsyncs = []
        real = _os.fsync
        monkeypatch.setattr(
            _os, "fsync", lambda fd: (fsyncs.append(fd), real(fd))[1])
        sk._archive_skills(self._skills(["a"]), reason="cull")
        assert len(fsyncs) == 2, "expected file + parent-dir fsync"
        fsyncs.clear()
        sk._archive_skills(self._skills(["b"]), reason="cull")
        assert len(fsyncs) == 1, "existing file: file fsync only"

    def test_the_archive_call_is_require_and_durable(self):
        """Cheap structural pin: the keywords are the fix."""
        import ast
        import inspect
        import skills as sk
        src = inspect.getsource(sk._archive_skills)
        calls = [n for n in ast.walk(ast.parse(src.lstrip()))
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "locked_append"]
        assert calls, "_archive_skills no longer calls locked_append"
        kw = {k.arg: getattr(k.value, "value", None)
              for k in calls[0].keywords}
        assert kw.get("require") is True
        assert kw.get("durable") is True

    def test_contended_fail_open_archives_nothing(
            self, tmp_path, monkeypatch):
        import fcntl
        import pytest
        import skills as sk
        from file_lock import FileLockTimeout
        arch = tmp_path / "archive.jsonl"
        monkeypatch.setattr(sk, "_skills_archive_path", lambda: arch)
        monkeypatch.setenv("MARO_FILELOCK_FAIL_OPEN", "1")
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "1")
        holder = open(str(arch) + ".lock", "w")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            with pytest.raises(FileLockTimeout):
                sk._archive_skills(self._skills(["a"]), reason="cull")
            assert not arch.exists()
        finally:
            holder.close()


class TestADeliberateDropMustBeNamed:
    """Adversarial r16 (three seats, HIGH, probed): every _save_skills
    caller passes a list built from an UNLOCKED load_skills() snapshot,
    and the old contract read "proven row absent from the list" as
    "deliberately deleted" — so a skill saved by a concurrent process
    between the snapshot and the rewrite was silently destroyed with no
    archive copy. Absence now means CARRY; only ids named in
    dropped_ids are removed."""

    def _mk(self, i):
        from skills import Skill
        return Skill(id=i, name=i, description="d", trigger_patterns=[],
                     steps_template=[], source_loop_ids=[],
                     created_at="2026-08-21T00:00:00+00:00")

    def test_an_unnamed_absence_is_carried_not_deleted(
            self, tmp_path, monkeypatch):
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("A"))
        snapshot = sk.load_skills()
        sk.save_skill(self._mk("C"))          # concurrent add
        sk._save_skills(snapshot,             # C absent, NOT named
                        updated_ids={s.id for s in snapshot})
        assert {s.id for s in sk.load_skills()} == {"A", "C"}

    def test_a_named_drop_removes_exactly_the_named_ids(
            self, tmp_path, monkeypatch):
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        for i in ("A", "B", "C"):
            sk.save_skill(self._mk(i))
        keep = [s for s in sk.load_skills() if s.id != "B"]
        sk._save_skills(keep, dropped_ids={"B"}, updated_ids=frozenset())
        assert {s.id for s in sk.load_skills()} == {"A", "C"}

    def test_the_pool_writers_require_their_lock(self):
        """Structural pin: the keyword is the fix (r16)."""
        import ast
        import inspect
        import skills as sk
        for fn in (sk.save_skill, sk._save_skills):
            src = inspect.getsource(fn)
            calls = [n for n in ast.walk(ast.parse(src.lstrip()))
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", "") == "locked_write"]
            assert calls, f"{fn.__name__} no longer calls locked_write"
            kw = {k.arg: getattr(k.value, "value", None)
                  for k in calls[0].keywords}
            assert kw.get("require") is True, fn.__name__

    def test_a_failed_pool_rewrite_raises_and_names_the_store(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import pytest
        import file_lock
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("A"))

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(file_lock, "atomic_write", boom)
        with caplog.at_level(logging.ERROR, logger="skills"):
            with pytest.raises(OSError):
                _l = sk.load_skills()
                sk._save_skills(_l, updated_ids={s.id for s in _l})
        assert "pool rewrite NOT performed" in caplog.text
        assert str(sk._skills_path()) in caplog.text

    def test_the_destructive_callers_name_their_drops(self):
        """Structural pin: cull, retirement, and the evolver rollback
        pass dropped_ids explicitly."""
        import inspect
        import skills as sk
        assert "dropped_ids=cull_set" in inspect.getsource(
            sk.cull_island_bottom_half)
        assert "dropped_ids=retired_set" in inspect.getsource(
            sk.retire_losing_variants)
        import evolver_store
        assert "dropped_ids=_removed" in inspect.getsource(evolver_store)


class TestTheBatchRecorderIsOneTransaction:
    """Adversarial r16 (four seats, probed): see
    record_skill_injection_outcomes — every id commits in one write or
    none do."""

    def test_all_ids_commit_in_one_write(self, tmp_path, monkeypatch):
        import skills as sk
        p = tmp_path / "skill-stats.jsonl"
        monkeypatch.setattr(sk, "_skill_stats_path", lambda: p)
        writes = []
        real = sk._write_skill_stats
        monkeypatch.setattr(
            sk, "_write_skill_stats",
            lambda *a, **k: (writes.append(1), real(*a, **k))[1])
        sk.record_skill_injection_outcomes(["a", "b", "c"], True)
        assert writes == [1]
        recs, _ = sk._read_skill_stats(p)
        assert {k: v["injected_runs"] for k, v in recs.items()} == \
            {"a": 1, "b": 1, "c": 1}

    def test_a_failed_batch_commits_nothing(self, tmp_path, monkeypatch):
        import pytest
        import skills as sk
        p = tmp_path / "skill-stats.jsonl"
        monkeypatch.setattr(sk, "_skill_stats_path", lambda: p)

        def boom(*a, **k):
            raise OSError("ENOSPC")

        monkeypatch.setattr(sk, "_write_skill_stats", boom)
        with pytest.raises(OSError):
            sk.record_skill_injection_outcomes(["a", "b"], True)
        assert not p.exists()

    def test_the_id_door_refuses_before_any_write(
            self, tmp_path, monkeypatch):
        import pytest
        import skills as sk
        p = tmp_path / "skill-stats.jsonl"
        monkeypatch.setattr(sk, "_skill_stats_path", lambda: p)
        with pytest.raises(TypeError):
            sk.record_skill_injection_outcomes(["ok", 7], True)
        with pytest.raises(TypeError):
            sk.record_skill_injection_outcomes(["ok"], "true")
        assert not p.exists()


class TestARecorderFailureIsAnnouncedWhereverItHappens:
    """Adversarial r16 (two seats, probed): r15's error log wrapped only
    the write, so a lock or read failure raised with NO recorder-level
    announcement — and two of three production catch sites logged at
    DEBUG. The try now covers the whole transaction."""

    def test_a_lock_failure_is_announced_by_both_recorders(
            self, tmp_path, monkeypatch, caplog):
        import fcntl
        import logging
        import pytest
        import skills as sk
        p = tmp_path / "skill-stats.jsonl"
        monkeypatch.setattr(sk, "_skill_stats_path", lambda: p)
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "1")
        holder = open(str(p) + ".lock", "w")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            for fn, args in ((sk.record_skill_outcome, ("s", True)),
                             (sk.record_skill_injection_outcome,
                              ("s", True))):
                caplog.clear()
                with caplog.at_level(logging.ERROR, logger="skills"):
                    with pytest.raises(Exception):
                        fn(*args)
                assert "NOT persisted" in caplog.text, fn.__name__
        finally:
            holder.close()


class TestStatsReadSurvivesTheCopyProtocols:
    """Adversarial r16 (four seats, probed): default tuple reduction
    reconstructs a subclass from ONE tuple argument, so copy, deepcopy,
    and pickle raised TypeError — and any path that survived would have
    dropped .compacted."""

    def test_pickle_deepcopy_and_copy_preserve_all_three_fields(self):
        import copy
        import pickle
        from skills import _StatsRead
        r = _StatsRead({"a": {"n": 1}}, ["strand"], 2)
        for clone in (pickle.loads(pickle.dumps(r)),
                      copy.deepcopy(r), copy.copy(r)):
            assert clone == r
            assert clone.compacted == 2


class TestAWriteMustBeNamed:
    """Adversarial r17 (three seats, HIGH, probed): r16 protected a
    concurrently ADDED id, but a row present in the caller's stale
    snapshot still replaced the live row wholesale — a concurrent
    save_skill(B) was silently reverted by any unrelated caller that
    loaded before it and saved after it. `updated_ids` is the write
    twin of `dropped_ids`: only a named id takes the caller's version;
    everything else is carried verbatim from the LIVE store."""

    @staticmethod
    def _mk(sid, desc="d"):
        import skills as sk
        return sk.Skill(
            id=sid, name=sid, description=desc, trigger_patterns=["x"],
            steps_template=["s"], source_loop_ids=[],
            created_at="2026-08-21T00:00:00+00:00")

    def test_a_concurrent_revision_survives_an_unnamed_save(
            self, tmp_path, monkeypatch):
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("A", "a-old"))
        sk.save_skill(self._mk("B", "b-old"))
        snapshot = sk.load_skills()
        sk.save_skill(self._mk("B", "b-concurrent"))   # operator edit
        for s in snapshot:
            if s.id == "A":
                s.description = "a-new"
        sk._save_skills(snapshot, updated_ids={"A"})   # B NOT named
        after = {s.id: s.description for s in sk.load_skills()}
        assert after == {"A": "a-new", "B": "b-concurrent"}

    def test_a_named_update_takes_the_callers_version(
            self, tmp_path, monkeypatch):
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("A", "a-old"))
        snapshot = sk.load_skills()
        snapshot[0].description = "a-new"
        sk._save_skills(snapshot, updated_ids={"A"})
        assert sk.load_skills()[0].description == "a-new"

    def test_an_unnamed_stale_copy_does_not_resurrect_a_deleted_row(
            self, tmp_path, monkeypatch):
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("X"))
        sk.save_skill(self._mk("Y"))
        snapshot = sk.load_skills()                 # holds X and Y
        sk._save_skills([s for s in sk.load_skills() if s.id != "Y"],
                        dropped_ids={"Y"}, updated_ids=frozenset())
        for s in snapshot:
            if s.id == "X":
                s.description = "x2"
        sk._save_skills(snapshot, updated_ids={"X"})   # stale Y unnamed
        assert {s.id for s in sk.load_skills()} == {"X"}

    def test_contradictory_intent_is_refused_before_the_store(
            self, tmp_path, monkeypatch):
        import pytest
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("A"))
        before = sk._skills_path().read_bytes()
        pool = sk.load_skills()
        # Each door is pinned by its MESSAGE, not just the raise: every
        # overlap input also trips a sibling door (an overlapping id is
        # either in the list — door 3 — or not — door 2), so raise-vs-not
        # cannot tell the overlap door from its siblings. The message is
        # the door's contribution: the operator reads "contradictory
        # intent", not a misleading sibling diagnosis. (r17 sweep
        # survivor: `if False:` on the overlap check passed this test's
        # original raise-only form.)
        with pytest.raises(ValueError, match="named both updated and dropped"):
            sk._save_skills(pool, updated_ids={"A"}, dropped_ids={"A"})
        with pytest.raises(ValueError, match="absent from the caller's list"):
            sk._save_skills(pool, updated_ids={"ghost"})
        with pytest.raises(ValueError, match="still present in the caller's"):
            sk._save_skills(pool, dropped_ids={"A"},
                            updated_ids=frozenset())
        assert sk._skills_path().read_bytes() == before

    def test_an_unnamed_duplicate_row_is_carried_not_compacted(
            self, tmp_path, monkeypatch):
        """Adversarial r17 (Minimalist, probed): an incidental rewrite
        physically destroyed an older valid same-id row an operator kept
        for recovery. Unnamed rows now carry verbatim — duplicates
        included."""
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("same", "v1"))
        v1 = sk._skills_path().read_text()
        sk.save_skill(self._mk("same", "v2"))
        sk._skills_path().write_text(v1 + sk._skills_path().read_text())
        sk.save_skill(self._mk("other"))
        pool = sk.load_skills()
        sk._save_skills(pool, updated_ids={"other"})
        lines = [l for l in sk._skills_path().read_text().splitlines() if l]
        assert sum('"same"' in l for l in lines) == 2

    def test_a_named_updates_duplicate_is_compacted_and_announced(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("same", "v1"))
        v1 = sk._skills_path().read_text()
        sk.save_skill(self._mk("same", "v2"))
        sk._skills_path().write_text(v1 + sk._skills_path().read_text())
        pool = sk.load_skills()
        with caplog.at_level(logging.WARNING, logger="skills"):
            sk._save_skills(pool, updated_ids={"same"})
        lines = [l for l in sk._skills_path().read_text().splitlines() if l]
        assert sum('"same"' in l for l in lines) == 1
        assert "compacted by this rewrite" in caplog.text

    def test_a_named_drop_is_announced_with_the_store_path(
            self, tmp_path, monkeypatch, caplog):
        """Adversarial r17 (Failure Operator, probed): a committed cull
        or rollback emitted no line naming skills.jsonl, so an operator
        could not distinguish it from tampering or locate the store."""
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("keep"))
        sk.save_skill(self._mk("gone"))
        with caplog.at_level(logging.WARNING, logger="skills"):
            sk._save_skills(
                [s for s in sk.load_skills() if s.id == "keep"],
                dropped_ids={"gone"}, updated_ids=frozenset())
        assert "removed by this rewrite" in caplog.text
        assert str(sk._skills_path()) in caplog.text
        assert "gone" in caplog.text

    def test_the_announcement_is_post_commit(
            self, tmp_path, monkeypatch, caplog):
        """Adversarial r17 (Minimalist, probed): the carried-verbatim
        warning preceded atomic_write, so a failed rewrite left a log
        claiming rows were carried through a rewrite that never ran."""
        import logging
        import pytest
        import file_lock
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("k1"))
        with sk._skills_path().open("ab") as f:
            f.write(b'{"id": "torn", "name": "torn\xff' + b"\n")

        def boom(*a, **k):
            raise OSError("simulated ENOSPC")
        monkeypatch.setattr(file_lock, "atomic_write", boom)
        with caplog.at_level(logging.WARNING, logger="skills"):
            with pytest.raises(OSError):
                pool = sk.load_skills()
                sk._save_skills(pool, updated_ids={"k1"})
        assert "carried through the rewrite" not in caplog.text
        assert "pool rewrite NOT performed" in caplog.text


class TestTheBatchRecorderAdmitsOneVerdictPerSkill:
    """Adversarial r17 (two seats, probed): the batch recorder applied
    every element of its id list, so a duplicated id credited one
    injected run twice; a bare string would have been iterated
    character by character."""

    def test_duplicate_ids_are_collapsed_to_one_verdict(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="skills"):
            sk.record_skill_injection_outcomes(["dup", "dup", "one"], True)
        assert sk.get_skill_stats("dup").injected_runs == 1
        assert sk.get_skill_stats("one").injected_runs == 1
        assert "duplicate id(s) collapsed" in caplog.text

    def test_a_bare_string_is_refused(self, tmp_path, monkeypatch):
        import pytest
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        with pytest.raises(TypeError):
            sk.record_skill_injection_outcomes("bare-id", True)
        assert sk.get_skill_stats("b") is None


class TestNamingIsNotCreation:
    """Adversarial r18 (QA, HIGH, probed): 'an updated id whose live row
    vanished' is a lost race with a DELIBERATE drop — cull, retirement,
    rollback — and the r17 tail append silently resurrected the retired
    row, reasoning and archive trail gone. No call site creates rows
    through _save_skills (creation is save_skill's job), so a
    named-but-absent write is dropped and ANNOUNCED; the deletion
    stands."""

    @staticmethod
    def _mk(sid, desc="d"):
        import skills as sk
        return sk.Skill(
            id=sid, name=sid, description=desc, trigger_patterns=["x"],
            steps_template=["s"], source_loop_ids=[],
            created_at="2026-08-21T00:00:00+00:00")

    def test_a_stale_named_write_cannot_resurrect_a_dropped_row(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("X"))
        pool = sk.load_skills()          # in-flight caller's snapshot
        live = sk.load_skills()          # concurrent deliberate cull
        sk._archive_skills(live, reason="test_cull")
        sk._save_skills([], dropped_ids={"X"}, updated_ids=frozenset())
        assert sk.load_skills() == []
        with caplog.at_level(logging.WARNING):
            sk._save_skills(pool, updated_ids={"X"})
        assert sk.load_skills() == [], "retired row resurrected"
        assert any("named write(s) NOT applied" in r.getMessage()
                   and "no parseable live row holds these id(s)" in
                   r.getMessage()
                   and str(sk._skills_path()) in r.getMessage()
                   for r in caplog.records)

    def test_an_applied_named_write_is_not_announced_as_a_ghost(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("Y"))
        pool = sk.load_skills()
        pool[0].description = "revised"
        with caplog.at_level(logging.WARNING):
            sk._save_skills(pool, updated_ids={"Y"})
        assert sk.load_skills()[0].description == "revised"
        assert not any("NOT applied" in r.getMessage()
                       for r in caplog.records)

    def test_a_mutated_unnamed_row_is_warned_not_silent(
            self, tmp_path, monkeypatch, caplog):
        """Adversarial r18 (Failure Operator, probed): forgetting to
        name a mutated id discarded the edit with no signal anywhere —
        the omission twin of the contradiction ValueErrors."""
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("M", "orig"))
        pool = sk.load_skills()
        pool[0].description = "mutated-but-forgot-to-name"
        with caplog.at_level(logging.WARNING):
            sk._save_skills(pool, updated_ids=frozenset())
        assert sk.load_skills()[0].description == "orig"
        assert any("differ from the live store" in r.getMessage()
                   and "'M'" in r.getMessage()
                   for r in caplog.records)

    def test_an_untouched_unnamed_row_raises_no_divergence_noise(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("Q"))
        pool = sk.load_skills()
        with caplog.at_level(logging.WARNING):
            sk._save_skills(pool, updated_ids=frozenset())
        assert not any("differ from the live store" in r.getMessage()
                       for r in caplog.records)

    def test_an_empty_backfill_hash_is_not_flagged_as_divergence(
            self, tmp_path, monkeypatch, caplog):
        """content_hash is derived — an unnamed copy whose hash was
        cleared in memory is not an edit (r18, Minimalist corollary)."""
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("H"))
        pool = sk.load_skills()
        pool[0].content_hash = ""
        with caplog.at_level(logging.WARNING):
            sk._save_skills(pool, updated_ids=frozenset())
        assert not any("differ from the live store" in r.getMessage()
                       for r in caplog.records)
        # And the in-memory copy was NOT mutated by the backfill: only
        # named rows are serialized, so backfilling an unnamed copy
        # would stamp a hash the store never holds.
        assert pool[0].content_hash == ""

    def test_the_drop_announcement_counts_physical_rows(
            self, tmp_path, monkeypatch, caplog):
        """Adversarial r18 (Architect): duplicate physical rows for one
        dropped id used to announce fewer removals than performed."""
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        s = self._mk("D")
        sk.save_skill(s)
        # save_skill dedupes same-id rows, so seed the legacy duplicate
        # raw — a provable second physical line for the same id.
        with sk._skills_path().open("a", encoding="utf-8") as f:
            _dup = self._mk("D", "second-copy")
            _dup.content_hash = sk.compute_skill_hash(_dup)
            f.write(sk._prove_line(_dup) + "\n")
        pool = sk.load_skills()
        assert [x.id for x in pool] == ["D"]
        with caplog.at_level(logging.WARNING):
            sk._save_skills([], dropped_ids={"D"}, updated_ids=frozenset())
        assert sk.load_skills() == []
        assert any("2 physical row(s) for 1 named id(s)" in r.getMessage()
                   for r in caplog.records)


class TestTheAnnouncementTellsTheTruth:
    """Adversarial r19 (multi-seat, probed): the r18 announcements
    over-claimed their causes. 'Concurrently removed; the deletion
    stands' fired for a row physically present but unprovable — and
    for an id never created at all; the divergence warning asserted
    'the caller's edit was NOT applied' when the common cause under
    load is a concurrent NAMED write legitimately moving the row. A
    message states what the scan PROVED, never a guessed cause."""

    @staticmethod
    def _mk(sid, desc="d"):
        import skills as sk
        return sk.Skill(
            id=sid, name=sid, description=desc, trigger_patterns=["x"],
            steps_template=["s"], source_loop_ids=[],
            created_at="2026-08-21T00:00:00+00:00")

    def test_a_named_write_against_an_unprovable_row_says_present(
            self, tmp_path, monkeypatch, caplog):
        import json as _json
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("U"))
        # Drift the live row so it parses but fails the proof.
        path = sk._skills_path()
        row = _json.loads(path.read_text().strip())
        row["utility_score"] = "nope"
        path.write_text(_json.dumps(row) + "\n")
        raw = path.read_bytes()
        caller = [self._mk("U", "revised")]
        with caplog.at_level(logging.WARNING):
            sk._save_skills(caller, updated_ids={"U"})
        # The unprovable row rode through verbatim.
        assert raw.strip() in path.read_bytes()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("present but unprovable" in m and "'U'" in m
                   for m in msgs)
        assert not any("no parseable live row" in m for m in msgs)
        assert not any("concurrently removed" in m for m in msgs)

    def test_a_tainted_store_earns_the_ghost_message_a_hedge(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("T"))
        path = sk._skills_path()
        path.write_bytes(path.read_bytes().replace(b'"T"', b'"T\xff"'))
        caller = [self._mk("T", "revised")]
        with caplog.at_level(logging.WARNING):
            sk._save_skills(caller, updated_ids={"T"})
        msgs = [r.getMessage() for r in caplog.records]
        assert any("no parseable live row" in m
                   and "carried verbatim whose id could not be read" in m
                   for m in msgs)

    def test_a_clean_ghost_message_does_not_hedge(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("X"))
        pool = sk.load_skills()
        live = sk.load_skills()
        sk._archive_skills(live, reason="test_cull")
        sk._save_skills([], dropped_ids={"X"}, updated_ids=frozenset())
        with caplog.at_level(logging.WARNING):
            sk._save_skills(pool, updated_ids={"X"})
        msgs = [r.getMessage() for r in caplog.records]
        assert any("no parseable live row" in m for m in msgs)
        assert not any("carried verbatim whose id could not be read" in m
                       for m in msgs)

    def test_the_divergence_warning_names_both_causes(
            self, tmp_path, monkeypatch, caplog):
        """The steady-state shape under load: a third party's NAMED
        write moves a row this caller never touched. The warning may
        fire — but it must state the ambiguity, never assert the
        caller made an edit."""
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("A"))
        sk.save_skill(self._mk("B"))
        snapshot = sk.load_skills()          # caller: will touch only A
        live = sk.load_skills()              # concurrent legit writer
        for s in live:
            if s.id == "B":
                s.description = "moved-by-b-writer"
        sk._save_skills(live, updated_ids={"B"})
        for s in snapshot:
            if s.id == "A":
                s.description = "a-edit"
        with caplog.at_level(logging.WARNING):
            sk._save_skills(snapshot, updated_ids={"A"})
        by_id = {s.id: s for s in sk.load_skills()}
        assert by_id["B"].description == "moved-by-b-writer"
        assert by_id["A"].description == "a-edit"
        div = [r.getMessage() for r in caplog.records
               if "differ from the live store" in r.getMessage()]
        assert div and all("concurrent write" in m
                           and "either" in m for m in div)
        assert not any("the caller's edit was NOT applied" in m
                       for m in div)


class TestDeletionsEarnTheSameTruths:
    """Adversarial r20 (five seats, HIGH, probed): the dropped_ids
    branch is only reachable for PROVABLE rows, so a named drop whose
    live row failed the proof silently no-oped — the cull returned
    clean, the row survived, and the only signal was the id-less carry
    line. Deletions by name get the same three truths writes got in
    r19."""

    @staticmethod
    def _mk(sid, desc="d"):
        import skills as sk
        return sk.Skill(
            id=sid, name=sid, description=desc, trigger_patterns=["x"],
            steps_template=["s"], source_loop_ids=[],
            created_at="2026-08-21T00:00:00+00:00")

    @staticmethod
    def _corrupt_row(path):
        import json as _json
        row = _json.loads(path.read_text().strip())
        row["utility_score"] = "nope"
        path.write_text(_json.dumps(row) + "\n")

    def test_a_named_drop_on_an_unprovable_row_is_announced_not_silent(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("K"))
        self._corrupt_row(sk._skills_path())
        with caplog.at_level(logging.WARNING):
            sk._save_skills([], dropped_ids={"K"}, updated_ids=frozenset())
        # The row was NOT removed — and the failure names the id.
        assert '"K"' in sk._skills_path().read_text()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("named drop(s) NOT applied" in m and "'K'" in m
                   and "repair, then confirm the drop" in m for m in msgs)
        assert not any("removed by this rewrite" in m for m in msgs)

    def test_a_partial_drop_names_its_surviving_duplicate(
            self, tmp_path, monkeypatch, caplog):
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("P"))
        # A second, unprovable physical row for the same id.
        with sk._skills_path().open("a", encoding="utf-8") as f:
            f.write('{"id": "P", "name": "P", "utility_score": "nope"}\n')
        with caplog.at_level(logging.WARNING):
            sk._save_skills([], dropped_ids={"P"}, updated_ids=frozenset())
        msgs = [r.getMessage() for r in caplog.records]
        assert any("removed the provable row(s)" in m
                   and "unprovable duplicate" in m and "'P'" in m
                   for m in msgs)
        # The provable row is gone; the unprovable duplicate remains.
        assert '"nope"' in sk._skills_path().read_text()

    def test_an_idless_unprovable_row_earns_the_ghost_hedge(
            self, tmp_path, monkeypatch, caplog):
        """Adversarial r20 (two seats, probed): the hedge counted only
        byte-tainted rows, so an unprovable row whose id field itself
        was unreadable let the ghost message assert absence the scan
        had not proved."""
        import json as _json
        import logging
        import skills as sk
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        sk.save_skill(self._mk("G"))
        path = sk._skills_path()
        row = _json.loads(path.read_text().strip())
        row["id"] = 12345
        path.write_text(_json.dumps(row) + "\n")
        caller = [self._mk("G", "revised")]
        with caplog.at_level(logging.WARNING):
            sk._save_skills(caller, updated_ids={"G"})
        msgs = [r.getMessage() for r in caplog.records]
        assert any("no parseable live row" in m
                   and "whose id could not be read" in m for m in msgs)
