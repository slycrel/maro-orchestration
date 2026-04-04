"""Tests for evolver.py — meta-evolution / self-improvement (§19)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evolver import (
    Suggestion,
    EvolverReport,
    load_suggestions,
    _save_suggestions,
    _build_outcomes_summary,
    _llm_analyze,
    run_evolver,
    list_pending_suggestions,
    apply_suggestion,
    _apply_suggestion_action,
    _dynamic_constraints_path,
    BusinessSignal,
    scan_outcomes_for_signals,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def test_suggestion_roundtrip():
    s = Suggestion(
        suggestion_id="abc-00",
        category="prompt_tweak",
        target="research",
        suggestion="Be more concise",
        failure_pattern="research tasks drift",
        confidence=0.8,
        outcomes_analyzed=20,
    )
    d = s.to_dict()
    restored = Suggestion.from_dict(d)
    assert restored.suggestion_id == s.suggestion_id
    assert restored.confidence == 0.8


def test_evolver_report_summary_skipped():
    r = EvolverReport(run_id="r1", outcomes_reviewed=0, skipped=True, skip_reason="too few outcomes")
    assert "skipped" in r.summary()
    assert "too few" in r.summary()


def test_evolver_report_summary_with_suggestions():
    r = EvolverReport(
        run_id="r1",
        outcomes_reviewed=10,
        suggestions=[
            Suggestion(
                suggestion_id="r1-00",
                category="prompt_tweak",
                target="all",
                suggestion="Always verify step output",
                failure_pattern="steps claimed done without verification",
                confidence=0.9,
                outcomes_analyzed=10,
            )
        ],
    )
    s = r.summary()
    assert "suggestions=1" in s
    assert "prompt_tweak" in s


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_save_and_load_suggestions(tmp_path):
    with patch("evolver._suggestions_path", return_value=tmp_path / "suggestions.jsonl"):
        suggestions = [
            Suggestion(
                suggestion_id="t1-00",
                category="new_guardrail",
                target="build",
                suggestion="Always run tests after build",
                failure_pattern="builds claimed complete without test verification",
                confidence=0.85,
                outcomes_analyzed=15,
            )
        ]
        _save_suggestions(suggestions)
        loaded = load_suggestions()

    assert len(loaded) == 1
    assert loaded[0].suggestion_id == "t1-00"
    assert loaded[0].category == "new_guardrail"


def test_load_suggestions_empty(tmp_path):
    with patch("evolver._suggestions_path", return_value=tmp_path / "nope.jsonl"):
        result = load_suggestions()
    assert result == []


def test_load_suggestions_newest_first(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="old", category="observation", target="all",
                    suggestion="old one", failure_pattern="x", confidence=0.5, outcomes_analyzed=5)
    s2 = Suggestion(suggestion_id="new", category="prompt_tweak", target="all",
                    suggestion="new one", failure_pattern="y", confidence=0.7, outcomes_analyzed=10)
    path.write_text(
        json.dumps(s1.to_dict()) + "\n" + json.dumps(s2.to_dict()) + "\n",
        encoding="utf-8",
    )
    with patch("evolver._suggestions_path", return_value=path):
        loaded = load_suggestions()
    assert loaded[0].suggestion_id == "new"


# ---------------------------------------------------------------------------
# _build_outcomes_summary
# ---------------------------------------------------------------------------

def _make_outcome(status="done", task_type="research", goal="test goal", summary="worked"):
    from memory import Outcome
    return Outcome(
        outcome_id="x",
        goal=goal,
        task_type=task_type,
        status=status,
        summary=summary,
        lessons=[],
    )


def test_build_outcomes_summary_empty():
    result = _build_outcomes_summary([])
    assert "no outcomes" in result.lower()


def test_build_outcomes_summary_counts():
    outcomes = [
        _make_outcome(status="done"),
        _make_outcome(status="stuck", summary="got confused"),
        _make_outcome(status="done"),
    ]
    result = _build_outcomes_summary(outcomes)
    assert "3" in result
    assert "2 done" in result
    assert "1 stuck" in result


def test_build_outcomes_summary_includes_stuck_details():
    outcomes = [_make_outcome(status="stuck", summary="LLM kept repeating the same step")]
    result = _build_outcomes_summary(outcomes)
    assert "LLM kept repeating" in result


# ---------------------------------------------------------------------------
# _llm_analyze
# ---------------------------------------------------------------------------

def test_llm_analyze_dry_run():
    patterns, suggestions = _llm_analyze([_make_outcome()], dry_run=True)
    assert patterns == []
    assert suggestions == []


def test_llm_analyze_empty_outcomes():
    patterns, suggestions = _llm_analyze([])
    assert patterns == []
    assert suggestions == []


def test_llm_analyze_parses_llm_response():
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "failure_patterns": ["tasks drift without ancestry context"],
        "suggestions": [
            {
                "category": "prompt_tweak",
                "target": "agenda",
                "suggestion": "Inject ancestry prompt in all AGENDA steps",
                "failure_pattern": "tasks drift without ancestry context",
                "confidence": 0.85,
            }
        ]
    })
    mock_adapter.complete.return_value = mock_resp

    with patch("evolver.build_adapter", return_value=mock_adapter):
        patterns, suggestions = _llm_analyze([_make_outcome()] * 5)

    assert len(patterns) == 1
    assert "drift" in patterns[0]
    assert len(suggestions) == 1
    assert suggestions[0]["category"] == "prompt_tweak"


def test_llm_analyze_handles_bad_json():
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = "this is not JSON"
    mock_adapter.complete.return_value = mock_resp

    with patch("evolver.build_adapter", return_value=mock_adapter):
        patterns, suggestions = _llm_analyze([_make_outcome()] * 5)

    assert patterns == []
    assert suggestions == []


# ---------------------------------------------------------------------------
# run_evolver
# ---------------------------------------------------------------------------

def test_run_evolver_skips_too_few_outcomes():
    with patch("evolver.load_outcomes", return_value=[]):
        report = run_evolver(min_outcomes=3, dry_run=True, verbose=False)
    assert report.skipped is True
    assert "0 outcomes" in report.skip_reason


def test_run_evolver_dry_run():
    outcomes = [_make_outcome()] * 10
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], [])):
        report = run_evolver(dry_run=True, verbose=False)
    assert report.outcomes_reviewed == 10
    assert report.skipped is False


def test_run_evolver_generates_suggestions():
    outcomes = [_make_outcome()] * 10
    raw_suggestions = [
        {"category": "prompt_tweak", "target": "all",
         "suggestion": "Be concise", "failure_pattern": "verbose output",
         "confidence": 0.8}
    ]
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=(["pattern 1"], raw_suggestions)), \
         patch("evolver.scan_outcomes_for_signals", return_value=[]), \
         patch("evolver._save_suggestions") as mock_save:
        report = run_evolver(dry_run=False, verbose=False, notify=False)

    assert len(report.suggestions) == 1
    assert report.suggestions[0].category == "prompt_tweak"
    mock_save.assert_called_once()


def test_run_evolver_saves_suggestions(tmp_path):
    outcomes = [_make_outcome()] * 5
    raw_suggestions = [
        {"category": "observation", "target": "research",
         "suggestion": "Check sources", "failure_pattern": "hallucination",
         "confidence": 0.7}
    ]
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], raw_suggestions)), \
         patch("evolver.scan_outcomes_for_signals", return_value=[]), \
         patch("evolver._suggestions_path", return_value=tmp_path / "suggestions.jsonl"):
        report = run_evolver(dry_run=False, verbose=False, notify=False)

    saved = (tmp_path / "suggestions.jsonl").read_text()
    assert "observation" in saved


def test_run_evolver_load_outcomes_failure():
    with patch("evolver.load_outcomes", side_effect=Exception("disk full")):
        report = run_evolver(dry_run=True, verbose=False)
    assert report.skipped is True


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_evolver_skips_no_outcomes(capsys):
    with patch("evolver.load_outcomes", return_value=[]):
        import cli
        rc = cli.main(["poe-evolver", "--dry-run", "--min-outcomes", "1"])
    # Should succeed (just skip with message)
    assert rc == 0
    out = capsys.readouterr().out
    assert "evolver" in out


def test_cli_poe_evolver_json(capsys):
    outcomes = [_make_outcome()] * 5
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], [])):
        import cli
        rc = cli.main(["poe-evolver", "--dry-run", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "outcomes_reviewed" in data


# ---------------------------------------------------------------------------
# Phase 8: list_pending_suggestions + apply_suggestion
# ---------------------------------------------------------------------------

def test_list_pending_suggestions_empty(tmp_path):
    with patch("evolver._suggestions_path", return_value=tmp_path / "nope.jsonl"):
        result = list_pending_suggestions()
    assert result == []


def test_list_pending_suggestions_filters_applied(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="observation", target="all",
                    suggestion="pending one", failure_pattern="x", confidence=0.5,
                    outcomes_analyzed=5, applied=False)
    s2 = Suggestion(suggestion_id="s2", category="observation", target="all",
                    suggestion="applied one", failure_pattern="y", confidence=0.7,
                    outcomes_analyzed=10, applied=True)
    s3 = Suggestion(suggestion_id="s3", category="prompt_tweak", target="all",
                    suggestion="pending two", failure_pattern="z", confidence=0.6,
                    outcomes_analyzed=8, applied=False)
    path.write_text(
        "\n".join(json.dumps(s.to_dict()) for s in [s1, s2, s3]) + "\n",
        encoding="utf-8",
    )
    with patch("evolver._suggestions_path", return_value=path):
        result = list_pending_suggestions()
    assert len(result) == 2
    ids = {s.suggestion_id for s in result}
    assert "s1" in ids
    assert "s3" in ids
    assert "s2" not in ids


def test_apply_suggestion_marks_applied(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="observation", target="all",
                    suggestion="test", failure_pattern="x", confidence=0.5,
                    outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")

    with patch("evolver._suggestions_path", return_value=path):
        ok = apply_suggestion("s1")
    assert ok is True

    # Verify it's now applied
    with patch("evolver._suggestions_path", return_value=path):
        pending = list_pending_suggestions()
    assert len(pending) == 0


def test_apply_suggestion_not_found(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="observation", target="all",
                    suggestion="test", failure_pattern="x", confidence=0.5,
                    outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")

    with patch("evolver._suggestions_path", return_value=path):
        ok = apply_suggestion("nonexistent")
    assert ok is False


def test_apply_suggestion_no_file(tmp_path):
    with patch("evolver._suggestions_path", return_value=tmp_path / "nope.jsonl"):
        ok = apply_suggestion("s1")
    assert ok is False


def test_cli_poe_evolver_list(capsys, tmp_path):
    s1 = Suggestion(suggestion_id="s1", category="prompt_tweak", target="all",
                    suggestion="Be more concise", failure_pattern="verbose output",
                    confidence=0.8, outcomes_analyzed=10, applied=False)
    with patch("evolver.list_pending_suggestions", return_value=[s1]):
        import cli
        rc = cli.main(["poe-evolver", "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "s1" in out
    assert "prompt_tweak" in out


def test_cli_poe_evolver_list_json(capsys, tmp_path):
    s1 = Suggestion(suggestion_id="s1", category="prompt_tweak", target="all",
                    suggestion="Be more concise", failure_pattern="verbose output",
                    confidence=0.8, outcomes_analyzed=10, applied=False)
    with patch("evolver.list_pending_suggestions", return_value=[s1]):
        import cli
        rc = cli.main(["poe-evolver", "--list", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["suggestion_id"] == "s1"


def test_cli_poe_evolver_apply(capsys):
    with patch("evolver.apply_suggestion", return_value=True):
        import cli
        rc = cli.main(["poe-evolver", "--apply", "s1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "applied=s1" in out


def test_cli_poe_evolver_apply_not_found(capsys):
    with patch("evolver.apply_suggestion", return_value=False):
        import cli
        rc = cli.main(["poe-evolver", "--apply", "nonexistent"])
    assert rc == 2


# ---------------------------------------------------------------------------
# Phase 12 — receive_inspector_tickets
# ---------------------------------------------------------------------------

from evolver import receive_inspector_tickets


def test_receive_inspector_tickets_saves_suggestions(tmp_path):
    """receive_inspector_tickets saves each valid ticket as a Suggestion."""
    tickets = [
        {
            "id": "insp-abc123",
            "title": "Fix recurring errors",
            "pattern": "error_events: repeated failures",
            "suggested_fix": "Add retry logic to tool calls",
            "priority": "high",
            "auto_evolver": True,
        },
        {
            "id": "insp-def456",
            "title": "Reduce context churn",
            "pattern": "context_churn: lessons not applied",
            "suggested_fix": "Summarize lessons before each step",
            "priority": "medium",
            "auto_evolver": True,
        },
    ]
    suggestions_path = tmp_path / "suggestions.jsonl"
    with patch("evolver._suggestions_path", return_value=suggestions_path):
        count = receive_inspector_tickets(tickets)

    assert count == 2
    assert suggestions_path.exists()
    lines = [json.loads(l) for l in suggestions_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    categories = {l["category"] for l in lines}
    assert "inspection_finding" in categories
    # High priority → confidence 0.9
    high = next(l for l in lines if "retry" in l["suggestion"])
    assert high["confidence"] == pytest.approx(0.9)


def test_receive_inspector_tickets_empty_list():
    """Empty ticket list returns 0 and writes nothing."""
    with patch("evolver._save_suggestions") as mock_save:
        count = receive_inspector_tickets([])
    assert count == 0
    mock_save.assert_not_called()


# ===========================================================================
# Phase 14 tests: apply_suggestion skill_pattern test gate
# ===========================================================================

from unittest.mock import MagicMock


def _make_skill_pattern_suggestion(
    suggestion_id="gate-test-00",
    target="my-skill",
    suggestion="Updated behavior description",
    applied=False,
):
    """Create a skill_pattern suggestion dict."""
    return {
        "suggestion_id": suggestion_id,
        "category": "skill_pattern",
        "target": target,
        "suggestion": suggestion,
        "failure_pattern": "skill keeps failing",
        "confidence": 0.7,
        "outcomes_analyzed": 5,
        "generated_at": "2026-03-25T00:00:00+00:00",
        "applied": applied,
    }


def _write_suggestion(path, suggestion_dict):
    """Write a suggestion dict to a jsonl file."""
    import json as _json
    with path.open("w", encoding="utf-8") as f:
        f.write(_json.dumps(suggestion_dict) + "\n")


def test_apply_suggestion_skill_pattern_gate_blocked(tmp_path):
    """skill_pattern suggestion where mutation fails test gate → status=gate_blocked."""
    from unittest.mock import patch as _patch
    import json as _json

    sugg = _make_skill_pattern_suggestion()
    suggestions_path = tmp_path / "suggestions.jsonl"
    _write_suggestion(suggestions_path, sugg)

    # Create a mock gate result that says blocked=True
    mock_gate_result = {"blocked": True, "block_reason": "Tests failed: 2/2 tests blocked"}

    with _patch("evolver._suggestions_path", return_value=suggestions_path):
        with _patch("evolver._run_skill_test_gate", return_value=mock_gate_result):
            found = apply_suggestion("gate-test-00")

    assert found is True
    lines = [_json.loads(l) for l in suggestions_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    updated = lines[0]
    assert updated["applied"] is False
    assert updated.get("status") == "gate_blocked"
    assert "block_reason" in updated


def test_apply_suggestion_skill_pattern_gate_passes(tmp_path):
    """skill_pattern suggestion where mutation passes test gate → status=applied."""
    from unittest.mock import patch as _patch
    import json as _json

    sugg = _make_skill_pattern_suggestion(suggestion_id="gate-pass-00")
    suggestions_path = tmp_path / "suggestions.jsonl"
    _write_suggestion(suggestions_path, sugg)

    # Create a mock gate result that says not blocked
    mock_gate_result = {"blocked": False, "block_reason": ""}

    with _patch("evolver._suggestions_path", return_value=suggestions_path):
        with _patch("evolver._run_skill_test_gate", return_value=mock_gate_result):
            found = apply_suggestion("gate-pass-00")

    assert found is True
    lines = [_json.loads(l) for l in suggestions_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    updated = lines[0]
    assert updated["applied"] is True
    # "status" key should not be "gate_blocked"
    assert updated.get("status") != "gate_blocked"


def test_apply_suggestion_non_skill_pattern_not_gated(tmp_path):
    """Non-skill_pattern suggestions apply directly without test gate."""
    from unittest.mock import patch as _patch
    import json as _json

    sugg = {
        "suggestion_id": "no-gate-00",
        "category": "prompt_tweak",
        "target": "all",
        "suggestion": "Be more concise",
        "failure_pattern": "drift",
        "confidence": 0.8,
        "outcomes_analyzed": 5,
        "generated_at": "2026-03-25T00:00:00+00:00",
        "applied": False,
    }
    suggestions_path = tmp_path / "suggestions.jsonl"
    _write_suggestion(suggestions_path, sugg)

    gate_called = []

    def fake_gate(d):
        gate_called.append(d)
        return {"blocked": True, "block_reason": "should not be called"}

    with _patch("evolver._suggestions_path", return_value=suggestions_path):
        with _patch("evolver._run_skill_test_gate", side_effect=fake_gate):
            found = apply_suggestion("no-gate-00")

    # Gate should NOT have been called for prompt_tweak
    assert len(gate_called) == 0
    assert found is True
    lines = [_json.loads(l) for l in suggestions_path.read_text().splitlines() if l.strip()]
    assert lines[0]["applied"] is True


# ===========================================================================
# Phase 32: synthesize_skill tests
# ===========================================================================

from evolver import synthesize_skill


class _SynthesisAdapter:
    """Returns a well-formed skill JSON."""
    def complete(self, messages, **kwargs):
        result = MagicMock()
        result.content = json.dumps({
            "name": "web_search_summarize",
            "description": "Search the web and summarize results for a given topic.",
            "trigger_patterns": ["search and summarize", "web research", "look up"],
            "steps_template": [
                "Search for the topic using a web search tool",
                "Extract the top 3 relevant results",
                "Summarize the findings in 2-3 sentences",
            ],
        })
        return result


def test_synthesize_skill_returns_skill(tmp_path):
    """synthesize_skill returns a Skill with correct fields."""
    with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
        skill = synthesize_skill(
            goal="search and summarize recent news on AI",
            outcome_summary="Found 3 articles and summarized them.",
            source_loop_id="abc123",
            adapter=_SynthesisAdapter(),
            dry_run=True,
        )
    assert skill is not None
    assert skill.name == "web_search_summarize"
    assert "Search" in skill.steps_template[0]
    assert skill.circuit_state == "closed"
    assert skill.tier == "provisional"


def test_synthesize_skill_saves_when_not_dry_run(tmp_path):
    """synthesize_skill writes to skills.jsonl when dry_run=False."""
    skills_path = tmp_path / "skills.jsonl"
    with patch("skills._skills_path", return_value=skills_path):
        skill = synthesize_skill(
            goal="search and summarize recent news on AI",
            outcome_summary="Found 3 articles and summarized them.",
            source_loop_id="abc123",
            adapter=_SynthesisAdapter(),
            dry_run=False,
        )
    assert skill is not None
    assert skills_path.exists()
    data = json.loads(skills_path.read_text().strip().splitlines()[-1])
    assert data["name"] == "web_search_summarize"


def test_synthesize_skill_skips_duplicate_name(tmp_path):
    """synthesize_skill returns None if a skill with the same name already exists."""
    import json as _json
    skills_path = tmp_path / "skills.jsonl"
    # Pre-populate with the same name
    existing = {
        "id": "existing1",
        "name": "web_search_summarize",
        "description": "existing skill",
        "trigger_patterns": ["web research"],
        "steps_template": ["do stuff"],
        "source_loop_ids": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "use_count": 0,
        "tier": "provisional",
        "circuit_state": "closed",
    }
    skills_path.write_text(_json.dumps(existing) + "\n", encoding="utf-8")
    with patch("skills._skills_path", return_value=skills_path):
        skill = synthesize_skill(
            goal="search and summarize recent news on AI",
            outcome_summary="Found 3 articles.",
            adapter=_SynthesisAdapter(),
            dry_run=False,
        )
    assert skill is None


def test_synthesize_skill_no_adapter_returns_none():
    """synthesize_skill returns None when adapter is None."""
    skill = synthesize_skill(
        goal="some goal",
        outcome_summary="done",
        adapter=None,
    )
    assert skill is None


def test_synthesize_skill_bad_json_returns_none(tmp_path):
    """synthesize_skill returns None when LLM returns unparseable content."""
    class _BadAdapter:
        def complete(self, messages, **kwargs):
            result = MagicMock()
            result.content = "not json at all"
            return result

    with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
        skill = synthesize_skill(
            goal="some goal",
            outcome_summary="done",
            adapter=_BadAdapter(),
            dry_run=True,
        )
    assert skill is None


def test_synthesize_skill_sets_source_loop_id(tmp_path):
    """synthesize_skill records the source loop id."""
    with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
        skill = synthesize_skill(
            goal="search and summarize",
            outcome_summary="done",
            source_loop_id="loop42",
            adapter=_SynthesisAdapter(),
            dry_run=True,
        )
    assert skill is not None
    assert "loop42" in skill.source_loop_ids


# ---------------------------------------------------------------------------
# Feedback loop: _apply_suggestion_action
# ---------------------------------------------------------------------------

def test_apply_action_prompt_tweak_writes_lesson(tmp_path, monkeypatch):
    """prompt_tweak action writes a TieredLesson to memory."""
    captured = {}

    def fake_record(lesson_text, task_type, outcome, source_goal, *, tier, confidence, **kw):
        captured["lesson_text"] = lesson_text
        captured["task_type"] = task_type
        captured["tier"] = tier

    monkeypatch.setattr("evolver.record_tiered_lesson", fake_record)
    monkeypatch.setattr("evolver.MemoryTier", type("MT", (), {"MEDIUM": "medium"})())

    _apply_suggestion_action({
        "category": "prompt_tweak",
        "target": "research",
        "suggestion": "Be more concise in decompose steps",
        "suggestion_id": "test-00",
        "confidence": 0.85,
    })

    assert captured["lesson_text"] == "Be more concise in decompose steps"
    assert captured["task_type"] == "research"
    assert captured["tier"] == "medium"


def test_apply_action_new_guardrail_writes_dynamic_constraint(tmp_path, monkeypatch):
    """new_guardrail action appends to dynamic-constraints.jsonl."""
    monkeypatch.setattr("evolver._dynamic_constraints_path", lambda: tmp_path / "dynamic-constraints.jsonl")

    _apply_suggestion_action({
        "category": "new_guardrail",
        "target": "all",
        "suggestion": r"\bdrop\s+database\b",
        "suggestion_id": "test-01",
        "confidence": 0.9,
    })

    content = (tmp_path / "dynamic-constraints.jsonl").read_text()
    entry = json.loads(content.strip())
    assert entry["pattern"] == r"\bdrop\s+database\b"
    assert "test-01" in entry["source"]


def test_apply_action_skill_pattern_creates_skill(tmp_path, monkeypatch):
    """skill_pattern action writes a new Skill to the skill library."""
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    _apply_suggestion_action({
        "category": "skill_pattern",
        "target": "new-skill-from-evolver",
        "suggestion": "Step 1: research; Step 2: synthesize; Step 3: report",
        "suggestion_id": "test-02",
        "confidence": 0.82,
    })

    skills_data = (tmp_path / "skills.jsonl").read_text()
    skill = json.loads(skills_data.strip())
    assert skill["name"] == "new-skill-from-evolver"


def test_apply_action_observation_is_noop(tmp_path, monkeypatch):
    """observation category has no side effects."""
    monkeypatch.setattr("evolver._dynamic_constraints_path", lambda: tmp_path / "dc.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")

    _apply_suggestion_action({
        "category": "observation",
        "target": "all",
        "suggestion": "Poe seems to work well on research tasks",
        "suggestion_id": "test-03",
        "confidence": 0.6,
    })

    assert not (tmp_path / "dc.jsonl").exists()
    assert not (tmp_path / "skills.jsonl").exists()


def test_dynamic_constraint_loaded_by_check(tmp_path, monkeypatch):
    """Patterns written to dynamic-constraints.jsonl are picked up by check_step_constraints."""
    from constraint import check_step_constraints

    dc_path = tmp_path / "memory" / "dynamic-constraints.jsonl"
    dc_path.parent.mkdir(parents=True)
    dc_path.write_text(json.dumps({
        "pattern": r"\bevil_command\b",
        "risk": "HIGH",
        "detail": "evolver guardrail: evil_command",
        "source": "test-04",
        "added_at": "2026-03-27T00:00:00+00:00",
    }) + "\n")

    monkeypatch.setattr("constraint._load_dynamic_constraints",
                        lambda: [("dynamic_guardrail", [(r"\bevil_command\b", "HIGH", "evil blocked")])])

    result = check_step_constraints("run evil_command now", goal="test")
    assert result.blocked
    assert any(f.name == "dynamic_guardrail" for f in result.flags)


def test_run_evolver_auto_applies_high_confidence(tmp_path, monkeypatch):
    """run_evolver auto-applies suggestions with confidence >= 0.8."""
    from unittest.mock import MagicMock

    monkeypatch.setattr("evolver._suggestions_path", lambda: tmp_path / "suggestions.jsonl")

    applied_ids = []

    def fake_apply(sid):
        applied_ids.append(sid)
        return True

    monkeypatch.setattr("evolver.apply_suggestion", fake_apply)
    monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock()] * 10)
    monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, dry_run=False: (
        ["pattern1"],
        [
            {"category": "prompt_tweak", "target": "research", "suggestion": "be concise",
             "failure_pattern": "drift", "confidence": 0.9},
            {"category": "observation", "target": "all", "suggestion": "all good",
             "failure_pattern": "", "confidence": 0.5},
        ],
    ))

    report = run_evolver(dry_run=False, verbose=False, min_outcomes=1)

    assert report.outcomes_reviewed == 10
    # Only the high-confidence suggestion should be auto-applied
    assert len(applied_ids) == 1


# ---------------------------------------------------------------------------
# BusinessSignal + scan_outcomes_for_signals
# ---------------------------------------------------------------------------

class TestBusinessSignal:
    def test_to_dict(self):
        s = BusinessSignal(
            signal_type="opportunity",
            description="Unusual market odds",
            suggested_goal="Analyze top Polymarket markets for mispriced odds",
            confidence=0.85,
            source_outcome="polymarket research run",
        )
        d = s.to_dict()
        assert d["signal_type"] == "opportunity"
        assert d["confidence"] == 0.85
        assert "suggested_goal" in d


class TestScanOutcomesForSignals:
    def _make_outcome(self, status="done", goal="research goal", summary="found useful pattern"):
        o = MagicMock()
        o.status = status
        o.goal = goal
        o.summary = summary
        o.task_type = "research"
        return o

    def test_dry_run_returns_empty(self):
        outcomes = [self._make_outcome()]
        result = scan_outcomes_for_signals(outcomes, dry_run=True)
        assert result == []

    def test_no_done_outcomes_returns_empty(self):
        outcomes = [self._make_outcome(status="stuck")]
        with patch("evolver.build_adapter") as mock_build:
            result = scan_outcomes_for_signals(outcomes)
        assert result == []

    def test_valid_signal_returned(self):
        signal_json = json.dumps({
            "signals": [{
                "signal_type": "opportunity",
                "description": "Top wallets show consistent pattern",
                "suggested_goal": "Analyze Polymarket top wallet strategies",
                "confidence": 0.85,
                "source_outcome": "polymarket run",
            }]
        })
        mock_adapter = MagicMock()
        mock_adapter.complete.return_value = MagicMock(
            content=signal_json, input_tokens=20, output_tokens=50
        )
        outcomes = [self._make_outcome()]
        with patch("evolver.build_adapter", return_value=mock_adapter):
            signals = scan_outcomes_for_signals(outcomes, min_confidence=0.7)
        assert len(signals) == 1
        assert signals[0].signal_type == "opportunity"
        assert "Polymarket" in signals[0].suggested_goal

    def test_low_confidence_signal_filtered(self):
        signal_json = json.dumps({
            "signals": [{
                "signal_type": "lead",
                "description": "Weak lead",
                "suggested_goal": "Maybe look into this",
                "confidence": 0.4,
                "source_outcome": "some run",
            }]
        })
        mock_adapter = MagicMock()
        mock_adapter.complete.return_value = MagicMock(content=signal_json, input_tokens=10, output_tokens=20)
        outcomes = [self._make_outcome()]
        with patch("evolver.build_adapter", return_value=mock_adapter):
            signals = scan_outcomes_for_signals(outcomes, min_confidence=0.7)
        assert signals == []

    def test_adapter_error_returns_empty(self):
        mock_adapter = MagicMock()
        mock_adapter.complete.side_effect = RuntimeError("network error")
        outcomes = [self._make_outcome()]
        with patch("evolver.build_adapter", return_value=mock_adapter):
            signals = scan_outcomes_for_signals(outcomes)
        assert signals == []

    def test_empty_suggested_goal_filtered(self):
        signal_json = json.dumps({
            "signals": [{
                "signal_type": "follow_up",
                "description": "something",
                "suggested_goal": "",
                "confidence": 0.9,
                "source_outcome": "run",
            }]
        })
        mock_adapter = MagicMock()
        mock_adapter.complete.return_value = MagicMock(content=signal_json, input_tokens=10, output_tokens=20)
        outcomes = [self._make_outcome()]
        with patch("evolver.build_adapter", return_value=mock_adapter):
            signals = scan_outcomes_for_signals(outcomes)
        assert signals == []


class TestRunEvolverSignalScan:
    def test_signals_become_sub_mission_suggestions(self, monkeypatch, tmp_path):
        """run_evolver converts business signals into sub_mission Suggestion entries."""
        monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock(status="done")] * 5)
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, dry_run=False: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda s: None)

        fake_signal = BusinessSignal(
            signal_type="opportunity",
            description="Test signal",
            suggested_goal="Run deeper analysis",
            confidence=0.85,
            source_outcome="test outcome",
        )
        monkeypatch.setattr("evolver.scan_outcomes_for_signals", lambda outcomes, dry_run=False: [fake_signal])

        # Prevent graduation pass from running
        monkeypatch.setattr("evolver.run_graduation", lambda verbose=False: 0, raising=False)

        report = run_evolver(dry_run=False, verbose=False, min_outcomes=1, scan_signals=True)
        sub_missions = [s for s in report.suggestions if s.category == "sub_mission"]
        assert len(sub_missions) == 1
        assert "deeper analysis" in sub_missions[0].suggestion

    def test_scan_signals_false_skips_scan(self, monkeypatch):
        scan_called = []
        monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock(status="done")] * 5)
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, dry_run=False: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda s: None)
        monkeypatch.setattr("evolver.scan_outcomes_for_signals",
                            lambda outcomes, dry_run=False: scan_called.append(True) or [])

        run_evolver(dry_run=False, verbose=False, min_outcomes=1, scan_signals=False)
        assert scan_called == []


# ---------------------------------------------------------------------------
# scan_calibration_log
# ---------------------------------------------------------------------------

from evolver import scan_calibration_log, CalibrationFinding


def _write_cal_entries(path: Path, entries: list) -> None:
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestScanCalibrationLog:
    def test_empty_file_returns_no_findings(self, tmp_path):
        cal = tmp_path / "calibration.jsonl"
        cal.write_text("")
        assert scan_calibration_log(cal_path=cal) == []

    def test_missing_file_returns_no_findings(self, tmp_path):
        assert scan_calibration_log(cal_path=tmp_path / "nonexistent.jsonl") == []

    def test_insufficient_entries_skipped(self, tmp_path):
        cal = tmp_path / "calibration.jsonl"
        _write_cal_entries(cal, [
            {"decision_class": "mechanical", "confidence": 8, "action_raw": "close", "action_final": "close"},
            {"decision_class": "mechanical", "confidence": 7, "action_raw": "close", "action_final": "surface"},
        ])
        # min_entries defaults to 5; only 2 entries → no finding
        findings = scan_calibration_log(cal_path=cal)
        assert findings == []

    def test_high_override_rate_generates_finding(self, tmp_path):
        cal = tmp_path / "calibration.jsonl"
        entries = [
            {"decision_class": "taste", "confidence": 7, "action_raw": "close", "action_final": "surface"},
            {"decision_class": "taste", "confidence": 6, "action_raw": "continue", "action_final": "surface"},
            {"decision_class": "taste", "confidence": 7, "action_raw": "close", "action_final": "surface"},
            {"decision_class": "taste", "confidence": 8, "action_raw": "close", "action_final": "surface"},
            {"decision_class": "taste", "confidence": 6, "action_raw": "close", "action_final": "surface"},
        ]
        _write_cal_entries(cal, entries)
        findings = scan_calibration_log(cal_path=cal, min_entries=5, high_override_threshold=0.4)
        assert len(findings) == 1
        assert findings[0].decision_class == "taste"
        assert findings[0].override_rate == 1.0
        assert "override rate" in findings[0].suggestion

    def test_no_override_no_finding(self, tmp_path):
        cal = tmp_path / "calibration.jsonl"
        entries = [
            {"decision_class": "mechanical", "confidence": 8, "action_raw": "close", "action_final": "close"},
            {"decision_class": "mechanical", "confidence": 9, "action_raw": "close", "action_final": "close"},
            {"decision_class": "mechanical", "confidence": 8, "action_raw": "continue", "action_final": "continue"},
            {"decision_class": "mechanical", "confidence": 9, "action_raw": "close", "action_final": "close"},
            {"decision_class": "mechanical", "confidence": 8, "action_raw": "close", "action_final": "close"},
        ]
        _write_cal_entries(cal, entries)
        findings = scan_calibration_log(cal_path=cal, min_entries=5)
        assert findings == []

    def test_low_confidence_generates_finding(self, tmp_path):
        cal = tmp_path / "calibration.jsonl"
        entries = [
            {"decision_class": "user_challenge", "confidence": 3, "action_raw": "surface", "action_final": "surface"},
            {"decision_class": "user_challenge", "confidence": 4, "action_raw": "surface", "action_final": "surface"},
            {"decision_class": "user_challenge", "confidence": 3, "action_raw": "surface", "action_final": "surface"},
            {"decision_class": "user_challenge", "confidence": 4, "action_raw": "surface", "action_final": "surface"},
            {"decision_class": "user_challenge", "confidence": 3, "action_raw": "surface", "action_final": "surface"},
        ]
        _write_cal_entries(cal, entries)
        findings = scan_calibration_log(cal_path=cal, min_entries=5, low_confidence_threshold=6.0)
        assert len(findings) == 1
        assert "mean confidence" in findings[0].suggestion
        assert findings[0].mean_confidence < 6.0

    def test_multiple_classes_independent(self, tmp_path):
        cal = tmp_path / "calibration.jsonl"
        # mechanical: fine (no overrides, high confidence)
        mech = [{"decision_class": "mechanical", "confidence": 9, "action_raw": "close", "action_final": "close"}] * 5
        # taste: high override rate
        taste = [{"decision_class": "taste", "confidence": 7, "action_raw": "close", "action_final": "surface"}] * 5
        _write_cal_entries(cal, mech + taste)
        findings = scan_calibration_log(cal_path=cal, min_entries=5, high_override_threshold=0.4)
        classes = {f.decision_class for f in findings}
        assert "taste" in classes
        assert "mechanical" not in classes

    def test_malformed_lines_skipped(self, tmp_path):
        cal = tmp_path / "calibration.jsonl"
        with open(cal, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"decision_class": "mechanical", "confidence": 8,
                                "action_raw": "close", "action_final": "close"}) + "\n")
        # Only 1 valid entry — below min_entries → no finding, no crash
        findings = scan_calibration_log(cal_path=cal)
        assert isinstance(findings, list)

    def test_run_evolver_wires_calibration_scan(self, monkeypatch, tmp_path):
        """scan_calibration=True causes calibration suggestions to appear in report."""
        monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock(status="done")] * 5)
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, dry_run=False: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda s: None)
        monkeypatch.setattr("evolver.scan_outcomes_for_signals", lambda outcomes, dry_run=False: [])
        monkeypatch.setattr("evolver.run_graduation", lambda verbose=False: 0, raising=False)

        fake_finding = CalibrationFinding(
            decision_class="taste",
            entry_count=10,
            override_count=5,
            override_rate=0.5,
            mean_confidence=5.5,
            suggestion="add examples for taste decisions",
        )
        monkeypatch.setattr("evolver.scan_calibration_log", lambda: [fake_finding])

        report = run_evolver(dry_run=False, verbose=False, min_outcomes=1, scan_calibration=True)
        cal_suggestions = [s for s in report.suggestions if s.category == "prompt_tweak" and s.target == "escalation"]
        assert len(cal_suggestions) == 1
        assert "taste" in cal_suggestions[0].suggestion

    def test_run_evolver_scan_calibration_false_skips(self, monkeypatch):
        scan_called = []
        monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock(status="done")] * 5)
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, dry_run=False: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda s: None)
        monkeypatch.setattr("evolver.scan_outcomes_for_signals", lambda outcomes, dry_run=False: [])
        monkeypatch.setattr("evolver.scan_calibration_log",
                            lambda: scan_called.append(True) or [])

        run_evolver(dry_run=False, verbose=False, min_outcomes=1, scan_calibration=False)
        assert scan_called == []


# ---------------------------------------------------------------------------
# _build_outcomes_summary — step trace enrichment (Meta-Harness steal)
# ---------------------------------------------------------------------------

from evolver import _build_outcomes_summary


class TestBuildOutcomesSummaryTraceEnrichment:
    def _make_outcome(self, status="done", goal="test goal", summary="summary text",
                      task_type="research", outcome_id="o-001"):
        return MagicMock(
            status=status,
            goal=goal,
            summary=summary,
            task_type=task_type,
            outcome_id=outcome_id,
        )

    def test_stuck_outcome_without_traces_still_works(self, monkeypatch):
        monkeypatch.setattr("memory.load_step_traces", lambda ids: {}, raising=False)
        outcomes = [self._make_outcome("stuck", outcome_id="o-stuck")]
        result = _build_outcomes_summary(outcomes)
        assert "stuck" in result
        assert "o-stuck" in result or "stuck outcome" in result.lower()

    def test_stuck_outcome_with_traces_includes_step_detail(self, monkeypatch):
        trace = {
            "goal": "the stuck goal",
            "steps": [
                {"step": "fetch data", "status": "done", "result": "ok", "summary": ""},
                {"step": "analyze", "status": "stuck", "stuck_reason": "LLM timed out"},
            ],
        }
        monkeypatch.setattr("memory.load_step_traces", lambda ids: {"o-stuck": trace})
        outcomes = [self._make_outcome("stuck", outcome_id="o-stuck")]
        result = _build_outcomes_summary(outcomes)
        assert "trace:o-stuck" in result
        assert "LLM timed out" in result
        assert "analyze" in result

    def test_done_outcomes_no_trace_fetch(self, monkeypatch):
        called = []
        monkeypatch.setattr("memory.load_step_traces", lambda ids: called.append(ids) or {})
        outcomes = [self._make_outcome("done")]
        _build_outcomes_summary(outcomes)
        # load_step_traces should not be called when there are no stuck outcomes
        assert called == []

    def test_load_traces_exception_does_not_crash(self, monkeypatch):
        def _raise(ids):
            raise RuntimeError("disk error")
        monkeypatch.setattr("memory.load_step_traces", _raise)
        outcomes = [self._make_outcome("stuck", outcome_id="o-stuck")]
        # Should not raise
        result = _build_outcomes_summary(outcomes)
        assert isinstance(result, str)
