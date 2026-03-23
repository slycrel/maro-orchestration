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
