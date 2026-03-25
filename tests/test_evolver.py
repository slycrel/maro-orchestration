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
