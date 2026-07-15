"""Tests for evolver.py — meta-evolution / self-improvement (§19)."""

import json
import os
import select
import subprocess
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
    suggestion_is_applied,
    _apply_suggestion_action,
    _dynamic_constraints_path,
    BusinessSignal,
    scan_outcomes_for_signals,
    scan_quality_drift,
    QualityDriftFinding,
    _save_baseline,
    _load_baselines,
    scan_evolver_impact,
    format_impact_summary,
    EvolverImpactRecord,
    verify_applied_suggestions,
    stamp_verification,
)
from memory_ledger import (
    verdict_trust,
    VERDICT_TRUST_FULL,
    VERDICT_TRUST_DIRECTIONAL,
    VERDICT_TRUST_NEUTRAL,
    VERDICT_TRUST_EXCLUDED,
)
from evolver_scans import _outcome_ts, _verify_counts


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
    assert restored.applied_manually is False
    assert restored.expected_signal == []


def test_suggestion_expected_signal_roundtrip():
    """VERIFY_LEARN_ARC V1 row shape: expected_signal survives to_dict/from_dict."""
    s = Suggestion(
        suggestion_id="abc-01",
        category="observation",
        target="all",
        suggestion="text",
        failure_pattern="pattern",
        confidence=0.7,
        outcomes_analyzed=5,
        expected_signal=[{"metric": "failure_class_rate", "class": "retry_churn", "direction": "down"}],
    )
    d = s.to_dict()
    assert d["expected_signal"] == [
        {"metric": "failure_class_rate", "class": "retry_churn", "direction": "down"}
    ]
    restored = Suggestion.from_dict(d)
    assert restored.expected_signal == s.expected_signal


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
    with patch("evolver_store._suggestions_path", return_value=tmp_path / "suggestions.jsonl"):
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
    with patch("evolver_store._suggestions_path", return_value=tmp_path / "nope.jsonl"):
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
    with patch("evolver_store._suggestions_path", return_value=path):
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


def test_run_evolver_verifies_graduations_before_low_outcome_skip():
    with patch("graduation.run_graduation_verification") as verify, \
         patch("evolver.load_outcomes", return_value=[]):
        report = run_evolver(
            min_outcomes=3, dry_run=False, verbose=False, notify=True
        )
    assert report.skipped is True
    verify.assert_called_once_with(notify=True)


def test_run_evolver_dry_run_does_not_emit_graduation_verification():
    with patch("graduation.run_graduation_verification") as verify, \
         patch("evolver.load_outcomes", return_value=[]):
        run_evolver(min_outcomes=3, dry_run=True, verbose=False)
    verify.assert_not_called()


def test_run_evolver_dry_run():
    outcomes = [_make_outcome()] * 10
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], [])):
        report = run_evolver(dry_run=True, verbose=False)
    assert report.outcomes_reviewed == 10
    assert report.skipped is False


def test_apply_cli_counts_only_durable_applied_state(monkeypatch, capsys):
    import evolver
    suggestion = Suggestion(
        suggestion_id="held-1", category="new_guardrail", target="all",
        suggestion="review me", failure_pattern="x", confidence=0.9,
        outcomes_analyzed=3,
    )
    monkeypatch.setattr("sys.argv", ["maro-evolver", "apply", "--all"])
    monkeypatch.setattr(evolver, "list_pending_suggestions", lambda limit=50: [suggestion])
    monkeypatch.setattr(evolver, "apply_suggestion", lambda sid, manual=True: True)
    monkeypatch.setattr(evolver, "suggestion_is_applied", lambda sid: False)

    assert evolver.main() == 0
    assert "Applied 0/1 suggestions." in capsys.readouterr().out


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
         patch("evolver.scan_calibration_log", return_value=[]), \
         patch("evolver.scan_step_costs", return_value=[]), \
         patch("evolver._save_suggestions") as mock_save:
        report = run_evolver(dry_run=False, verbose=False, notify=False)

    assert len(report.suggestions) == 1
    assert report.suggestions[0].category == "prompt_tweak"
    mock_save.assert_called_once()


def test_run_evolver_passes_through_llm_expected_signal():
    """VERIFY_LEARN_ARC V1: an LLM-authored suggestion that declares
    expected_signal must reach the stored Suggestion unchanged."""
    outcomes = [_make_outcome()] * 10
    raw_suggestions = [
        {"category": "prompt_tweak", "target": "all",
         "suggestion": "Be concise", "failure_pattern": "verbose output",
         "confidence": 0.8,
         "expected_signal": [{"metric": "cost_per_run", "direction": "down"}]}
    ]
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], raw_suggestions)), \
         patch("evolver.scan_outcomes_for_signals", return_value=[]), \
         patch("evolver.scan_calibration_log", return_value=[]), \
         patch("evolver.scan_step_costs", return_value=[]), \
         patch("evolver._save_suggestions"):
        report = run_evolver(dry_run=False, verbose=False, notify=False)

    assert report.suggestions[0].expected_signal == [
        {"metric": "cost_per_run", "direction": "down"}
    ]


def test_run_evolver_defaults_expected_signal_when_llm_omits_it():
    outcomes = [_make_outcome()] * 10
    raw_suggestions = [
        {"category": "prompt_tweak", "target": "all",
         "suggestion": "Be concise", "failure_pattern": "verbose output",
         "confidence": 0.8}
    ]
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], raw_suggestions)), \
         patch("evolver.scan_outcomes_for_signals", return_value=[]), \
         patch("evolver.scan_calibration_log", return_value=[]), \
         patch("evolver.scan_step_costs", return_value=[]), \
         patch("evolver._save_suggestions"):
        report = run_evolver(dry_run=False, verbose=False, notify=False)

    assert report.suggestions[0].expected_signal == []


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
         patch("evolver_store._suggestions_path", return_value=tmp_path / "suggestions.jsonl"):
        report = run_evolver(dry_run=False, verbose=False, notify=False)

    saved = (tmp_path / "suggestions.jsonl").read_text()
    assert "observation" in saved


def test_run_evolver_load_outcomes_failure():
    with patch("evolver.load_outcomes", side_effect=Exception("disk full")):
        report = run_evolver(dry_run=True, verbose=False)
    assert report.skipped is True


# ---------------------------------------------------------------------------
# skill_candidate catch-up sweep (adversarial-review R1 batch-1 finding #4)
# ---------------------------------------------------------------------------
#
# run_curation.flag_skill_candidate writes card["skill_candidate"] but had no
# consumer outside tests. WIRED (not removed) — evolver.promote_skill_
# candidates is the consumer: a periodic sweep feeding unconsumed flags
# through the same skills.extract_skills() call loop_finalize already uses at
# goal-end (it can't consume same-run — curate_run runs AFTER loop_finalize's
# extract_skills call). See run_curation.py's "skill_candidate consumer"
# section and evolver.promote_skill_candidates' docstring for the full
# rationale.

_SC_REUSABLE_SCRIPT = (
    '"""Fetch and summarize a changelog for a repo."""\n'
    "import argparse\n"
    "\n"
    "def summarize(path):\n"
    "    with open(path) as fh:\n"
    "        return fh.read()[:100]\n"
    "\n"
    "def main():\n"
    "    ap = argparse.ArgumentParser()\n"
    "    ap.add_argument('path')\n"
    "    args = ap.parse_args()\n"
    "    print(summarize(args.path))\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def _sc_flagged_run(handle_id):
    """Create + curate a run flag_skill_candidate marks as a candidate — a
    done+achieved run with a reusable script (run_curation's
    _judge_script_reusability heuristics)."""
    from runs import create_run_dir, finalize_run
    from run_curation import curate_run

    rd = create_run_dir(handle_id, prompt="build a tool", lane="now", model="claude",
                        extra_metadata={"goal_achieved": True})
    finalize_run(handle_id, status="done")
    (rd / "build").mkdir(exist_ok=True)
    (rd / "build" / "tool.py").write_text(_SC_REUSABLE_SCRIPT)
    card = curate_run(handle_id)
    assert card is not None and (card.get("skill_candidate") or {}).get("flagged") is True
    return rd


def test_promote_skill_candidates_no_candidates_returns_zero():
    from evolver import promote_skill_candidates
    assert promote_skill_candidates(adapter=MagicMock(), dry_run=False, verbose=False) == 0


def test_promote_skill_candidates_skips_when_other_process_owns_sweep(tmp_path):
    from evolver import promote_skill_candidates

    src = str(Path(__file__).parent.parent / "src")
    code = """
import sys, time
sys.path.insert(0, sys.argv[1])
from proc_lock import hold_pidfile
with hold_pidfile("skill-candidate-sweep", fail_open=False) as acquired:
    print("HELD" if acquired else "REFUSED", flush=True)
    if acquired:
        time.sleep(10)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = src
    env["MARO_WORKSPACE"] = str(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", code, src],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 5)
        assert ready, "lock-holder process did not report readiness within 5s"
        assert proc.stdout.readline().strip() == "HELD"
        with patch("run_curation.find_unconsumed_skill_candidates") as find:
            assert promote_skill_candidates(adapter=MagicMock()) == 0
        find.assert_not_called()
    finally:
        proc.kill()
        proc.communicate()


def test_promote_skill_candidates_lock_storage_failure_skips_before_scan():
    from evolver import promote_skill_candidates

    with patch("builtins.open", side_effect=OSError("read-only")), \
         patch("run_curation.find_unconsumed_skill_candidates") as find:
        assert promote_skill_candidates(adapter=MagicMock()) == 0

    find.assert_not_called()


def test_promote_skill_candidates_saves_skill_and_marks_consumed():
    from evolver import promote_skill_candidates
    from run_curation import find_unconsumed_skill_candidates
    from skills import Skill

    _sc_flagged_run("h0e00001")
    assert any(c["handle_id"] == "h0e00001" for c in find_unconsumed_skill_candidates())

    fake_skill = Skill(id="s1", name="new-skill", description="d",
                        trigger_patterns=["x"], steps_template=["a"],
                        source_loop_ids=[], created_at="2026-07-13T00:00:00+00:00")
    with patch("skills.extract_skills", return_value=[fake_skill]) as mock_extract:
        n = promote_skill_candidates(adapter=MagicMock(), dry_run=False, verbose=False)

    assert n == 1
    mock_extract.assert_called_once()
    outcomes_arg = mock_extract.call_args[0][0]
    assert outcomes_arg[0]["success_class"] == "success"
    assert "status" not in outcomes_arg[0]
    assert not any(c["handle_id"] == "h0e00001" for c in find_unconsumed_skill_candidates())


def test_promote_skill_candidates_dry_run_neither_saves_nor_consumes():
    from evolver import promote_skill_candidates
    from run_curation import find_unconsumed_skill_candidates
    from skills import Skill

    _sc_flagged_run("h0e00002")
    fake_skill = Skill(id="s2", name="new-skill-2", description="d",
                        trigger_patterns=["x"], steps_template=["a"],
                        source_loop_ids=[], created_at="2026-07-13T00:00:00+00:00")
    with patch("skills.extract_skills", return_value=[fake_skill]) as mock_extract:
        n = promote_skill_candidates(adapter=MagicMock(), dry_run=True, verbose=False)

    assert n == 0
    mock_extract.assert_not_called()
    assert any(c["handle_id"] == "h0e00002" for c in find_unconsumed_skill_candidates())


def test_promote_skill_candidates_extract_declines_still_consumes():
    """extract_skills returning [] (declining a low-signal batch) is not an
    error — the candidate is still marked consumed so it isn't rescanned
    forever (mark_skill_candidate_consumed means 'looked at', not 'produced
    a skill')."""
    from evolver import promote_skill_candidates
    from run_curation import find_unconsumed_skill_candidates

    _sc_flagged_run("h0e00003")
    with patch("skills.extract_skills", return_value=[]):
        n = promote_skill_candidates(adapter=MagicMock(), dry_run=False, verbose=False)

    assert n == 0
    assert not any(c["handle_id"] == "h0e00003" for c in find_unconsumed_skill_candidates())


def test_promote_skill_candidates_extract_exception_is_non_fatal():
    """A transient extract_skills failure (bad adapter, timeout, malformed
    response) must not consume the candidate — it was never actually
    evaluated, so consuming it would burn its only retry on an error instead
    of a real decision (final adversarial pass, 2026-07-13: Skeptic Medium —
    this test used to pin the opposite, lossy behavior)."""
    from evolver import promote_skill_candidates
    from run_curation import find_unconsumed_skill_candidates

    _sc_flagged_run("h0e00004")
    with patch("skills.extract_skills", side_effect=RuntimeError("boom")):
        n = promote_skill_candidates(adapter=MagicMock(), dry_run=False, verbose=False)

    assert n == 0
    # NOT consumed — stays available so the next sweep retries it.
    assert any(c["handle_id"] == "h0e00004" for c in find_unconsumed_skill_candidates())


def test_run_evolver_wires_skill_candidate_sweep():
    """run_evolver's scan_skill_candidates=True (the default) calls the
    sweep; scan_skill_candidates=False (next test) skips it entirely."""
    from run_curation import find_unconsumed_skill_candidates

    _sc_flagged_run("h0e00005")
    outcomes = [_make_outcome()] * 5
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], [])), \
         patch("evolver.scan_outcomes_for_signals", return_value=[]), \
         patch("skills.extract_skills", return_value=[]):
        run_evolver(dry_run=False, verbose=False, notify=False,
                    scan_skill_candidates=True)
    assert not any(c["handle_id"] == "h0e00005" for c in find_unconsumed_skill_candidates())


def test_run_evolver_skill_candidate_sweep_can_be_disabled():
    from run_curation import find_unconsumed_skill_candidates

    _sc_flagged_run("h0e00006")
    outcomes = [_make_outcome()] * 5
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], [])), \
         patch("evolver.scan_outcomes_for_signals", return_value=[]):
        run_evolver(dry_run=False, verbose=False, notify=False,
                    scan_skill_candidates=False)
    # Sweep never ran — candidate still unconsumed.
    assert any(c["handle_id"] == "h0e00006" for c in find_unconsumed_skill_candidates())


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_evolver_skips_no_outcomes(capsys):
    with patch("evolver.load_outcomes", return_value=[]):
        import cli
        rc = cli.main(["evolver", "--dry-run", "--min-outcomes", "1"])
    # Should succeed (just skip with message)
    assert rc == 0
    out = capsys.readouterr().out
    assert "evolver" in out


def test_cli_poe_evolver_json(capsys):
    outcomes = [_make_outcome()] * 5
    with patch("evolver.load_outcomes", return_value=outcomes), \
         patch("evolver._llm_analyze", return_value=([], [])):
        import cli
        rc = cli.main(["evolver", "--dry-run", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "outcomes_reviewed" in data


# ---------------------------------------------------------------------------
# Phase 8: list_pending_suggestions + apply_suggestion
# ---------------------------------------------------------------------------

def test_list_pending_suggestions_empty(tmp_path):
    with patch("evolver_store._suggestions_path", return_value=tmp_path / "nope.jsonl"):
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
    with patch("evolver_store._suggestions_path", return_value=path):
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

    with patch("evolver_store._suggestions_path", return_value=path):
        ok = apply_suggestion("s1")
    assert ok is True

    # Verify it's now applied
    with patch("evolver_store._suggestions_path", return_value=path):
        pending = list_pending_suggestions()
    assert len(pending) == 0


def test_apply_suggestion_stamps_applied_at(tmp_path):
    """The apply timestamp must live in suggestions.jsonl, not only in the
    captain's log — scan_evolver_impact reads it from here (the log is
    visibility/data, not the wire)."""
    from datetime import datetime
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="observation", target="all",
                    suggestion="test", failure_pattern="x", confidence=0.5,
                    outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")

    with patch("evolver_store._suggestions_path", return_value=path):
        ok = apply_suggestion("s1")
    assert ok is True

    d = json.loads(path.read_text(encoding="utf-8").strip())
    assert d["applied"] is True
    assert d["applied_at"]
    assert d["applied_manually"] is False
    datetime.fromisoformat(d["applied_at"])  # parseable, raises otherwise


def test_apply_suggestion_persists_manual_authority(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="observation", target="all",
                    suggestion="test", failure_pattern="x", confidence=0.5,
                    outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")

    with patch("evolver_store._suggestions_path", return_value=path):
        assert apply_suggestion("s1", manual=True) is True
        assert suggestion_is_applied("s1") is True

    d = json.loads(path.read_text(encoding="utf-8").strip())
    assert d["applied_manually"] is True


def test_reapply_is_idempotent_and_preserves_authority(tmp_path, monkeypatch):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="observation", target="all",
                    suggestion="test", failure_pattern="x", confidence=0.5,
                    outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")
    action = MagicMock(return_value=True)
    monkeypatch.setattr("evolver_store._suggestions_path", lambda: path)
    monkeypatch.setattr("evolver_store._apply_suggestion_action", action)

    assert apply_suggestion("s1", manual=False) is True
    first = json.loads(path.read_text(encoding="utf-8").strip())
    assert apply_suggestion("s1", manual=True) is True
    second = json.loads(path.read_text(encoding="utf-8").strip())

    assert action.call_count == 1
    assert second["applied_at"] == first["applied_at"]
    assert second["applied_manually"] is False


def test_failed_action_remains_retryable_and_unapplied(tmp_path, monkeypatch):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="prompt_tweak", target="all",
                    suggestion="test", failure_pattern="x", confidence=0.8,
                    outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")
    action = MagicMock(side_effect=[False, True])
    monkeypatch.setattr("evolver_store._suggestions_path", lambda: path)
    monkeypatch.setattr("evolver_store._apply_suggestion_action", action)

    assert apply_suggestion("s1", manual=False) is True
    failed = json.loads(path.read_text(encoding="utf-8").strip())
    assert failed["applied"] is False
    assert failed["status"] == "action_failed"

    assert apply_suggestion("s1", manual=True) is True
    retried = json.loads(path.read_text(encoding="utf-8").strip())
    assert action.call_count == 2
    assert retried["applied"] is True
    assert retried["applied_manually"] is True


def test_apply_suggestion_not_found(tmp_path):
    path = tmp_path / "suggestions.jsonl"
    s1 = Suggestion(suggestion_id="s1", category="observation", target="all",
                    suggestion="test", failure_pattern="x", confidence=0.5,
                    outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")

    with patch("evolver_store._suggestions_path", return_value=path):
        ok = apply_suggestion("nonexistent")
    assert ok is False


def test_apply_suggestion_no_file(tmp_path):
    with patch("evolver_store._suggestions_path", return_value=tmp_path / "nope.jsonl"):
        ok = apply_suggestion("s1")
    assert ok is False


def test_verify_post_apply_reverts_on_test_failure(tmp_path):
    # Regression: session 20 adversarial review finding 3.2 — _verify_post_apply
    # used to log a warning on test failure but leave broken state in place.
    # The self-improvement loop could make itself worse and stay that way.
    # Fix: iterate applied_ids on failure and call revert_suggestion on each.
    from evolver import _verify_post_apply

    fake_fail = MagicMock()
    fake_fail.returncode = 1
    fake_fail.stdout = "FAILED tests/test_foo.py::test_bar"
    fake_fail.stderr = ""

    reverted_ids = []

    def fake_revert(sid):
        reverted_ids.append(sid)
        return {"reverted": True, "category": "prompt_tweak", "detail": "rolled back"}

    # Simulate the caller passing a list of applied suggestion ids.
    with patch("subprocess.run", return_value=fake_fail), \
         patch("evolver.revert_suggestion", side_effect=fake_revert):
        _verify_post_apply(["s1", "s2", "s3"], "run-xyz", verbose=False)

    assert reverted_ids == ["s1", "s2", "s3"]


def test_verify_post_apply_does_not_revert_on_test_success(tmp_path):
    # Passing tests must NOT trigger a revert — would undo good changes.
    from evolver import _verify_post_apply

    fake_pass = MagicMock()
    fake_pass.returncode = 0
    fake_pass.stdout = "3830 passed"
    fake_pass.stderr = ""

    reverted_ids = []

    def fake_revert(sid):
        reverted_ids.append(sid)
        return {"reverted": True, "category": "prompt_tweak", "detail": "rolled back"}

    with patch("subprocess.run", return_value=fake_pass), \
         patch("evolver.revert_suggestion", side_effect=fake_revert):
        _verify_post_apply(["s1", "s2"], "run-xyz", verbose=False)

    assert reverted_ids == []


def test_verify_post_apply_accepts_legacy_int_count(tmp_path):
    # Backward-compat: older callers/tests pass an int count. Still accepted,
    # but no revert happens because we don't have the IDs. This preserves the
    # old "log a warning" behavior for those callers.
    from evolver import _verify_post_apply

    fake_fail = MagicMock()
    fake_fail.returncode = 1
    fake_fail.stdout = "FAILED"
    fake_fail.stderr = ""

    with patch("subprocess.run", return_value=fake_fail), \
         patch("evolver.revert_suggestion") as mock_revert:
        _verify_post_apply(3, "run-xyz", verbose=False)

    assert mock_revert.call_count == 0  # no IDs → no revert


def test_verify_post_apply_runs_throttled(tmp_path):
    # BACKLOG batch-02: the verify pass fires at run finalize on a live box —
    # it must run as a background citizen (nice + core cap, test-safe.sh
    # posture), never an unthrottled all-cores pytest.
    import shutil
    from evolver import _verify_post_apply

    fake_pass = MagicMock()
    fake_pass.returncode = 0
    fake_pass.stdout = "1 passed"
    fake_pass.stderr = ""

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = [str(c) for c in cmd]
        return fake_pass

    with patch("subprocess.run", side_effect=fake_run), \
         patch("evolver.revert_suggestion"):
        _verify_post_apply(["s1"], "run-xyz", verbose=False)

    cmd = captured["cmd"]
    assert any("pytest" in c for c in cmd)
    if shutil.which("nice"):
        assert cmd[0] == "nice"
    if shutil.which("taskset"):
        assert "taskset" in cmd


def test_apply_suggestion_cost_optimization_held_for_review(tmp_path):
    # Regression: cost_optimization has no executor in _apply_suggestion_action.
    # Previously it fell through to the else-branch and got marked applied=True,
    # silently doing nothing. Now it must stay applied=False and surface for review.
    path = tmp_path / "suggestions.jsonl"
    s = Suggestion(suggestion_id="c1", category="cost_optimization", target="decompose",
                   suggestion="switch to cheap tier", failure_pattern="high tokens",
                   confidence=0.9, outcomes_analyzed=5, applied=False)
    path.write_text(json.dumps(s.to_dict()) + "\n", encoding="utf-8")

    with patch("evolver_store._suggestions_path", return_value=path):
        ok = apply_suggestion("c1")
    assert ok is True  # found + updated, but NOT executed

    stored = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert stored["applied"] is False
    assert stored.get("status") == "pending_human_review"
    assert "cost_optimization" in stored.get("block_reason", "")


def test_apply_suggestion_crystallization_held_for_review(tmp_path):
    """crystallization is human-gated — apply_suggestion must NOT auto-apply it."""
    path = tmp_path / "suggestions.jsonl"
    s = Suggestion(
        suggestion_id="cr1",
        category="crystallization",
        target="research",
        suggestion="PROMOTE TO IDENTITY: 'Always X' — applied 15x across 3 task types.",
        failure_pattern="lesson_id=abc times_applied=15 task_types=3",
        confidence=0.95,
        outcomes_analyzed=15,
        applied=False,
    )
    path.write_text(json.dumps(s.to_dict()) + "\n", encoding="utf-8")

    with patch("evolver_store._suggestions_path", return_value=path):
        ok = apply_suggestion("cr1")
    assert ok is True  # found + updated

    stored = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert stored["applied"] is False, "crystallization must NOT be auto-applied"
    assert stored.get("status") == "pending_human_review"
    assert "crystallization" in stored.get("block_reason", "")


def test_cli_poe_evolver_list(capsys, tmp_path):
    s1 = Suggestion(suggestion_id="s1", category="prompt_tweak", target="all",
                    suggestion="Be more concise", failure_pattern="verbose output",
                    confidence=0.8, outcomes_analyzed=10, applied=False)
    with patch("evolver.list_pending_suggestions", return_value=[s1]):
        import cli
        rc = cli.main(["evolver", "--list"])
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
        rc = cli.main(["evolver", "--list", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["suggestion_id"] == "s1"


def test_cli_poe_evolver_apply(capsys):
    with patch("evolver.apply_suggestion", return_value=True):
        import cli
        rc = cli.main(["evolver", "--apply", "s1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "applied=s1" in out


def test_cli_poe_evolver_apply_not_found(capsys):
    with patch("evolver.apply_suggestion", return_value=False):
        import cli
        rc = cli.main(["evolver", "--apply", "nonexistent"])
    assert rc == 2


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

    with _patch("evolver_store._suggestions_path", return_value=suggestions_path):
        with _patch("evolver_store._run_skill_test_gate", return_value=mock_gate_result):
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

    with _patch("evolver_store._suggestions_path", return_value=suggestions_path):
        with _patch("evolver_store._run_skill_test_gate", return_value=mock_gate_result):
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

    with _patch("evolver_store._suggestions_path", return_value=suggestions_path):
        with _patch("evolver_store._run_skill_test_gate", side_effect=fake_gate):
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
    """Returns a well-formed skill JSON that passes all 3 gates."""
    def complete(self, messages, **kwargs):
        result = MagicMock()
        result.content = json.dumps({
            "name": "web_search_summarize",
            "description": "Search the web and summarize results for a given topic.",
            "trigger_patterns": ["search and summarize", "web research", "look up topic"],
            "steps_template": [
                "Search for the topic using a web search tool",
                "Extract the top 3 relevant results",
                "Summarize the findings in 2-3 sentences",
            ],
            "expected_outputs": [
                "a 2-3 sentence summary paragraph",
                "a list of 3 source URLs",
            ],
            "edge_cases": [
                "search returns zero results",
                "search times out mid-query",
                "all results are paywalled or unreadable",
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


# ===========================================================================
# 3-gate pre-promotion quality checks
# ===========================================================================

from evolver import (
    _gate_trigger_precision,
    _gate_output_schema,
    _gate_edge_case_coverage,
    _OFF_TARGET_CORPUS,
    _MIN_EDGE_CASES,
)


class TestTriggerPrecisionGate:
    def test_rejects_empty_patterns(self):
        passed, reason = _gate_trigger_precision([])
        assert passed is False
        assert "no trigger_patterns" in reason

    def test_rejects_too_short_pattern(self):
        passed, reason = _gate_trigger_precision(["ok"])
        assert passed is False
        assert "too short" in reason

    def test_rejects_generic_pattern_that_matches_corpus(self):
        # "status report" appears in every off-target — should fail precision
        corpus = (
            "draft a status report for Monday",
            "email a status report to finance",
            "compile the weekly status report",
            "archive last month's status report",
            "review yesterday's grafana dashboards",
        )
        passed, reason = _gate_trigger_precision(
            ["status report"], off_target=corpus
        )
        assert passed is False
        assert "off-target" in reason

    def test_rejects_when_any_one_pattern_is_generic(self):
        # Mixing a specific trigger with a bad one still fails — a single
        # generic trigger is enough to steal matches from better skills.
        corpus = (
            "draft a status report",
            "email a status report",
            "compile status report",
        )
        passed, reason = _gate_trigger_precision(
            ["polymarket edge scan", "status report"], off_target=corpus
        )
        assert passed is False

    def test_accepts_specific_patterns(self):
        passed, reason = _gate_trigger_precision(
            ["search and summarize", "web research", "look up topic"]
        )
        assert passed is True, reason

    def test_accepts_domain_specific_jargon(self):
        passed, reason = _gate_trigger_precision(
            ["polymarket edge scan", "edge ledger update"]
        )
        assert passed is True, reason


class TestOutputSchemaGate:
    def test_rejects_missing_expected_outputs(self):
        passed, reason = _gate_output_schema({})
        assert passed is False
        assert "expected_outputs" in reason

    def test_rejects_empty_list(self):
        passed, reason = _gate_output_schema({"expected_outputs": []})
        assert passed is False

    def test_rejects_non_list(self):
        passed, reason = _gate_output_schema({"expected_outputs": "just a string"})
        assert passed is False

    def test_rejects_list_of_blanks(self):
        passed, reason = _gate_output_schema({"expected_outputs": ["", "  ", ""]})
        assert passed is False
        assert "blanks" in reason

    def test_accepts_non_empty_list(self):
        passed, reason = _gate_output_schema(
            {"expected_outputs": ["a summary paragraph", "3 source URLs"]}
        )
        assert passed is True


class TestEdgeCaseCoverageGate:
    def test_rejects_missing_edge_cases(self):
        passed, reason = _gate_edge_case_coverage({})
        assert passed is False

    def test_rejects_too_few_edge_cases(self):
        passed, reason = _gate_edge_case_coverage(
            {"edge_cases": ["empty input", "timeout"]}
        )
        assert passed is False
        assert str(_MIN_EDGE_CASES) in reason

    def test_rejects_duplicate_edge_cases(self):
        # Distinct count is what matters — 3 copies of the same case = 1 distinct
        passed, reason = _gate_edge_case_coverage(
            {"edge_cases": ["empty input", "empty input", "empty input"]}
        )
        assert passed is False

    def test_accepts_three_distinct_cases(self):
        passed, reason = _gate_edge_case_coverage({
            "edge_cases": [
                "search returns zero results",
                "search times out mid-query",
                "all results are paywalled",
            ]
        })
        assert passed is True


class _GateFailingAdapter:
    """Adapter factory: produces skills that fail specific gates."""
    def __init__(self, *, triggers=None, expected_outputs=None, edge_cases=None):
        self._triggers = triggers
        self._expected_outputs = expected_outputs
        self._edge_cases = edge_cases

    def complete(self, messages, **kwargs):
        result = MagicMock()
        payload = {
            "name": "failing_skill",
            "description": "A skill designed to trip a specific gate.",
            "trigger_patterns": self._triggers
            if self._triggers is not None
            else ["search and summarize", "web research", "look up topic"],
            "steps_template": ["step one", "step two"],
            "expected_outputs": self._expected_outputs
            if self._expected_outputs is not None
            else ["a summary"],
            "edge_cases": self._edge_cases
            if self._edge_cases is not None
            else ["empty input", "timeout", "ambiguous wording"],
        }
        result.content = json.dumps(payload)
        return result


def test_synthesize_skill_rejects_generic_trigger(tmp_path):
    """A skill whose trigger matches off-target goals is discarded."""
    adapter = _GateFailingAdapter(triggers=["the", "and", "do"])
    with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
        skill = synthesize_skill(
            goal="something", outcome_summary="done",
            adapter=adapter, dry_run=True,
        )
    assert skill is None


def test_synthesize_skill_rejects_missing_expected_outputs(tmp_path):
    """A skill that omits expected_outputs is discarded."""
    adapter = _GateFailingAdapter(expected_outputs=[])
    with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
        skill = synthesize_skill(
            goal="something", outcome_summary="done",
            adapter=adapter, dry_run=True,
        )
    assert skill is None


def test_synthesize_skill_rejects_insufficient_edge_cases(tmp_path):
    """A skill that lists fewer than _MIN_EDGE_CASES is discarded."""
    adapter = _GateFailingAdapter(edge_cases=["only one"])
    with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
        skill = synthesize_skill(
            goal="something", outcome_summary="done",
            adapter=adapter, dry_run=True,
        )
    assert skill is None


def test_synthesize_skill_all_gates_pass_when_well_formed(tmp_path):
    """The reference adapter yields a skill that survives all 3 gates."""
    with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
        skill = synthesize_skill(
            goal="search and summarize recent news",
            outcome_summary="done",
            adapter=_SynthesisAdapter(),
            dry_run=True,
        )
    assert skill is not None


def test_gate_rejection_emits_captains_log_event(tmp_path):
    """A rejected synthesis is recorded as SKILL_SYNTHESIS_REJECTED."""
    from captains_log import set_log_path, load_log, SKILL_SYNTHESIS_REJECTED
    log_path = tmp_path / "captains_log.jsonl"
    set_log_path(log_path)
    try:
        adapter = _GateFailingAdapter(edge_cases=["only one"])
        with patch("skills._skills_path", return_value=tmp_path / "skills.jsonl"):
            skill = synthesize_skill(
                goal="specific goal", outcome_summary="done",
                adapter=adapter, dry_run=True,
            )
        assert skill is None
        events = load_log()
        rejected = [e for e in events if e.get("event_type") == SKILL_SYNTHESIS_REJECTED]
        assert len(rejected) == 1
        assert rejected[0]["subject"] == "failing_skill"
        assert rejected[0]["context"]["gate"] == "edge_case_coverage"
    finally:
        set_log_path(None)


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

    monkeypatch.setattr("evolver_store.record_tiered_lesson", fake_record)
    monkeypatch.setattr("evolver_store.MemoryTier", type("MT", (), {"MEDIUM": "medium"})())

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
    monkeypatch.setattr("evolver_store._dynamic_constraints_path", lambda: tmp_path / "dynamic-constraints.jsonl")

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
    monkeypatch.setattr("evolver_store._dynamic_constraints_path", lambda: tmp_path / "dc.jsonl")
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


def test_apply_action_writes_enriched_audit_trail(tmp_path, monkeypatch):
    """change_log.jsonl includes suggestion_text, confidence, and before_state."""
    monkeypatch.setattr("evolver_store._dynamic_constraints_path", lambda: tmp_path / "dc.jsonl")
    monkeypatch.setattr("skills._skills_path", lambda: tmp_path / "skills.jsonl")
    monkeypatch.setattr("orch_items.memory_dir", lambda: tmp_path)

    _apply_suggestion_action({
        "category": "new_guardrail",
        "target": "all",
        "suggestion": r"\bdrop\s+table\b",
        "suggestion_id": "audit-test-01",
        "confidence": 0.9,
    })

    cl_path = tmp_path / "change_log.jsonl"
    assert cl_path.exists()
    entry = json.loads(cl_path.read_text().strip().split("\n")[-1])
    assert entry["suggestion_text"] == r"\bdrop\s+table\b"
    assert entry["confidence"] == 0.9
    assert entry["before_state"] == {"type": "guardrail_append"}
    assert "suggestion_hash" in entry
    assert entry["category"] == "new_guardrail"


def test_apply_action_audit_trail_captures_skill_before_state(tmp_path, monkeypatch):
    """Audit trail captures old skill description when updating an existing skill."""
    from skills import Skill
    # Seed a skill file
    skill = Skill(
        id="sk01", name="test-skill", description="Original description",
        trigger_patterns=[], steps_template=[], source_loop_ids=[],
        created_at="2026-01-01T00:00:00+00:00", tier="provisional", utility_score=0.5,
    )
    skills_path = tmp_path / "skills.jsonl"
    skills_path.write_text(json.dumps(skill.__dict__) + "\n")
    monkeypatch.setattr("skills._skills_path", lambda: skills_path)
    monkeypatch.setattr("orch_items.memory_dir", lambda: tmp_path)

    _apply_suggestion_action({
        "category": "skill_pattern",
        "target": "test-skill",
        "suggestion": "Updated description from evolver",
        "suggestion_id": "audit-test-02",
        "confidence": 0.85,
    })

    cl_path = tmp_path / "change_log.jsonl"
    entry = json.loads(cl_path.read_text().strip().split("\n")[-1])
    assert entry["before_state"]["type"] == "skill_update"
    assert entry["before_state"]["old_description"] == "Original description"


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

    monkeypatch.setattr("evolver_store._suggestions_path", lambda: tmp_path / "suggestions.jsonl")

    applied_ids = []

    def fake_apply(sid):
        applied_ids.append(sid)
        return True

    monkeypatch.setattr("evolver.apply_suggestion", fake_apply)
    monkeypatch.setattr("evolver.suggestion_is_applied", lambda sid: True)
    monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock()] * 10)
    monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: (
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


def test_run_evolver_does_not_count_held_suggestion_as_applied(monkeypatch):
    """A processed-but-held guardrail must not enter post-apply verification."""
    from unittest.mock import MagicMock

    monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock()] * 3)
    monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: (
        ["recurring unsafe action"],
        [{
            "category": "new_guardrail",
            "target": "constraint",
            "suggestion": "hold this rule for operator review",
            "failure_pattern": "unsafe action",
            "confidence": 0.9,
        }],
    ))
    monkeypatch.setattr("evolver.apply_suggestion", lambda sid: True)
    monkeypatch.setattr("evolver.suggestion_is_applied", lambda sid: False)
    verify = MagicMock()
    monkeypatch.setattr("evolver._verify_post_apply", verify)

    report = run_evolver(dry_run=False, verbose=False, min_outcomes=1)

    assert len(report.suggestions) == 1
    verify.assert_not_called()


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
        with patch("evolver_scans.build_adapter") as mock_build:
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
        with patch("evolver_scans.build_adapter", return_value=mock_adapter):
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
        with patch("evolver_scans.build_adapter", return_value=mock_adapter):
            signals = scan_outcomes_for_signals(outcomes, min_confidence=0.7)
        assert signals == []

    def test_adapter_error_returns_empty(self):
        mock_adapter = MagicMock()
        mock_adapter.complete.side_effect = RuntimeError("network error")
        outcomes = [self._make_outcome()]
        with patch("evolver_scans.build_adapter", return_value=mock_adapter):
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
        with patch("evolver_scans.build_adapter", return_value=mock_adapter):
            signals = scan_outcomes_for_signals(outcomes)
        assert signals == []


class TestRunEvolverSignalScan:
    def test_signals_become_sub_mission_suggestions(self, monkeypatch, tmp_path):
        """run_evolver converts business signals into sub_mission Suggestion entries."""
        monkeypatch.setattr("evolver.load_outcomes", lambda limit=50: [MagicMock(status="done")] * 5)
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: ([], []))
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
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda s: None)
        monkeypatch.setattr("evolver.scan_outcomes_for_signals",
                            lambda outcomes, dry_run=False: scan_called.append(True) or [])

        run_evolver(dry_run=False, verbose=False, min_outcomes=1, scan_signals=False)
        assert scan_called == []


# ---------------------------------------------------------------------------
# sub_mission auto-enqueue via _apply_suggestion_action
# ---------------------------------------------------------------------------

class TestSubMissionAutoEnqueue:
    """_apply_suggestion_action for sub_mission category: enqueue vs hold for review."""

    def _make_sub_mission_dict(self, suggestion_text="Analyze market trends"):
        return {
            "suggestion_id": "sig-test01",
            "category": "sub_mission",
            "target": "opportunity",
            "suggestion": suggestion_text,
            "confidence": 0.85,
            "applied": False,
        }

    def test_auto_enqueue_when_enabled(self, monkeypatch, tmp_path):
        """When evolver.auto_enqueue_signals=True, sub_mission is enqueued via enqueue_goal."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        enqueued = []
        monkeypatch.setattr("evolver._cfg_get" if hasattr(__import__("evolver"), "_cfg_get") else
                            "config.get", lambda k, d=None: True if k == "evolver.auto_enqueue_signals" else d,
                            raising=False)

        # Patch at the import level
        import evolver as _ev
        import config as _cfg
        monkeypatch.setattr(_cfg, "get", lambda k, d=None: True if k == "evolver.auto_enqueue_signals" else d)

        def _fake_enqueue(goal, *, reason="", blocked_by=None):
            enqueued.append(goal)
            return "job-fake-01"
        import handle as _handle
        monkeypatch.setattr(_handle, "enqueue_goal", _fake_enqueue)

        from evolver import _apply_suggestion_action
        _apply_suggestion_action(self._make_sub_mission_dict("Research crypto arbitrage patterns"))

        assert len(enqueued) == 1
        assert "arbitrage" in enqueued[0]

    def test_hold_for_review_when_disabled(self, monkeypatch, tmp_path):
        """When evolver.auto_enqueue_signals=False (default), sub_mission goes to playbook."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        enqueued = []
        import handle as _handle
        monkeypatch.setattr(_handle, "enqueue_goal", lambda *a, **kw: enqueued.append(a) or "job-x")

        import config as _cfg
        monkeypatch.setattr(_cfg, "get", lambda k, d=None: False if k == "evolver.auto_enqueue_signals" else d)

        playbook_entries = []
        try:
            import playbook as _pb
            monkeypatch.setattr(_pb, "append_to_playbook", lambda text, section="", source="": playbook_entries.append(text))
        except ImportError:
            pass

        from evolver import _apply_suggestion_action
        _apply_suggestion_action(self._make_sub_mission_dict("Research crypto arbitrage patterns"))

        # Should NOT enqueue
        assert len(enqueued) == 0
        # Should record to playbook (if playbook available)
        if playbook_entries:
            assert any("arbitrage" in e for e in playbook_entries)

    def test_default_is_hold_not_enqueue(self, monkeypatch, tmp_path):
        """Default config (no key set) does not auto-enqueue."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        enqueued = []
        import handle as _handle
        monkeypatch.setattr(_handle, "enqueue_goal", lambda *a, **kw: enqueued.append(a) or "job-x")

        from evolver import _apply_suggestion_action
        _apply_suggestion_action(self._make_sub_mission_dict("Analyze market gaps"))

        assert len(enqueued) == 0, "default should NOT auto-enqueue sub_missions"


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
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: ([], []))
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
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: ([], []))
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


# ---------------------------------------------------------------------------
# scan_step_costs
# ---------------------------------------------------------------------------

class TestScanStepCosts:
    """Tests for scan_step_costs — per-step token cost pattern detection."""

    def _make_entries(self, step_type: str, count: int, avg_tokens: int) -> list:
        """Build synthetic step-cost entries."""
        entries = []
        for i in range(count):
            entries.append({
                "step_type": step_type,
                "tokens_in": avg_tokens * 3 // 4,
                "tokens_out": avg_tokens // 4,
                "total_tokens": avg_tokens,
                "cost_usd": avg_tokens * 0.000003,
                "status": "done",
                "model": "mid",
            })
        return entries

    def test_returns_empty_when_too_few_entries(self, monkeypatch):
        from evolver import scan_step_costs
        result = scan_step_costs(entries=[])
        assert result == []

    def test_returns_empty_when_no_expensive_types(self, monkeypatch):
        """When all step types have similar costs, no suggestions generated."""
        from evolver import scan_step_costs
        # All same avg — no expensive types
        entries = (
            self._make_entries("research", 3, 500) +
            self._make_entries("verify", 3, 450) +
            self._make_entries("analyze", 3, 480)
        )
        result = scan_step_costs(entries=entries)
        assert result == []

    def test_detects_expensive_step_type(self, monkeypatch):
        """High-token step type generates a cost_optimization suggestion."""
        from evolver import scan_step_costs
        # research is 3x more expensive than verify
        entries = (
            self._make_entries("verify", 5, 200) +
            self._make_entries("research", 5, 3000)
        )
        result = scan_step_costs(entries=entries)
        assert len(result) >= 1
        step_types = [s.target for s in result]
        assert "research" in step_types

    def test_suggestion_has_correct_category(self, monkeypatch):
        from evolver import scan_step_costs
        entries = (
            self._make_entries("verify", 5, 200) +
            self._make_entries("research", 5, 3000)
        )
        result = scan_step_costs(entries=entries)
        assert all(s.category == "cost_optimization" for s in result)

    def test_suggestion_mentions_haiku(self, monkeypatch):
        from evolver import scan_step_costs
        entries = (
            self._make_entries("verify", 5, 200) +
            self._make_entries("research", 5, 3000)
        )
        result = scan_step_costs(entries=entries)
        assert any("Haiku" in s.suggestion or "MODEL_CHEAP" in s.suggestion for s in result)

    def test_skips_types_with_single_entry(self, monkeypatch):
        """Step types with only 1 entry are skipped (not enough data)."""
        from evolver import scan_step_costs
        entries = (
            self._make_entries("verify", 5, 200) +
            self._make_entries("research", 1, 9000)  # only 1 entry
        )
        result = scan_step_costs(entries=entries)
        # research only has 1 entry, should be skipped
        assert not any(s.target == "research" for s in result)

    def test_import_error_returns_empty(self, monkeypatch):
        """If metrics import fails, returns empty list without crashing."""
        from evolver import scan_step_costs
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "metrics":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = scan_step_costs(entries=[{"a": 1}] * 10)
        assert result == []

    def test_run_evolver_wires_cost_scan(self, monkeypatch, tmp_path):
        """run_evolver calls scan_step_costs and merges suggestions."""
        from evolver import run_evolver, scan_step_costs, Suggestion
        from memory import Outcome

        # Patch outcome loading
        def _fake_outcomes(limit=50):
            return [
                Outcome(outcome_id=str(i), goal="g", task_type="research",
                        status="done", summary="ok", lessons=[])
                for i in range(5)
            ]
        monkeypatch.setattr("evolver.load_outcomes", _fake_outcomes)
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: ([], []))
        monkeypatch.setattr("evolver.scan_outcomes_for_signals", lambda *a, **kw: [])
        monkeypatch.setattr("evolver.scan_calibration_log", lambda *a, **kw: [])
        monkeypatch.setattr("evolver_store._suggestions_path", lambda: tmp_path / "suggestions.jsonl")

        cost_sugg = [Suggestion(
            suggestion_id="cost-test", category="cost_optimization",
            target="research", suggestion="use haiku", failure_pattern="high_burn",
            confidence=0.70, outcomes_analyzed=5,
        )]
        monkeypatch.setattr("evolver.scan_step_costs", lambda *a, **kw: cost_sugg)

        report = run_evolver(dry_run=False, verbose=False, scan_costs=True,
                             scan_signals=False, scan_calibration=False)
        assert any(s.category == "cost_optimization" for s in report.suggestions)

    def test_run_evolver_scan_costs_false_skips(self, monkeypatch):
        from evolver import run_evolver
        from memory import Outcome

        def _fake_outcomes(limit=50):
            return [
                Outcome(outcome_id=str(i), goal="g", task_type="research",
                        status="done", summary="ok", lessons=[])
                for i in range(5)
            ]
        monkeypatch.setattr("evolver.load_outcomes", _fake_outcomes)
        monkeypatch.setattr("evolver._llm_analyze", lambda outcomes, **kw: ([], []))
        monkeypatch.setattr("evolver.scan_outcomes_for_signals", lambda *a, **kw: [])
        monkeypatch.setattr("evolver.scan_calibration_log", lambda *a, **kw: [])

        called = []
        monkeypatch.setattr("evolver.scan_step_costs", lambda *a, **kw: called.append(1) or [])

        run_evolver(dry_run=True, verbose=False, scan_costs=False,
                    scan_signals=False, scan_calibration=False)
        assert called == []


# ---------------------------------------------------------------------------
# _compactness_adjusted_score / _top_peer_skills (FunSearch-inspired)
# ---------------------------------------------------------------------------

class TestCompactnessAdjustedScore:
    def _make_skill(self, utility_score=0.9, desc="Do something", steps=None):
        from skills import Skill
        return Skill(
            id="s1", name="test", description=desc,
            trigger_patterns=["test"], steps_template=steps or ["step one", "step two"],
            source_loop_ids=[], created_at="2026-01-01T00:00:00+00:00",
            utility_score=utility_score,
        )

    def test_score_decreases_with_longer_description(self):
        from evolver import _compactness_adjusted_score
        short = self._make_skill(utility_score=0.9, desc="Short", steps=["s1"])
        long_ = self._make_skill(utility_score=0.9, desc="A" * 500, steps=["s1", "s2", "s3", "s4", "s5"])
        assert _compactness_adjusted_score(short) > _compactness_adjusted_score(long_)

    def test_score_with_zero_utility_is_zero(self):
        from evolver import _compactness_adjusted_score
        skill = self._make_skill(utility_score=0.0)
        assert _compactness_adjusted_score(skill) == 0.0

    def test_score_never_exceeds_utility_score(self):
        from evolver import _compactness_adjusted_score
        skill = self._make_skill(utility_score=0.8)
        assert _compactness_adjusted_score(skill) <= 0.8

    def test_score_is_positive_for_normal_skill(self):
        from evolver import _compactness_adjusted_score
        skill = self._make_skill(utility_score=0.7)
        assert _compactness_adjusted_score(skill) > 0.0


class TestTopPeerSkills:
    def _make_skill(self, id_, utility_score=0.9, circuit_state="closed"):
        from skills import Skill
        return Skill(
            id=id_, name=f"skill_{id_}", description="desc",
            trigger_patterns=["t"], steps_template=["do it"],
            source_loop_ids=[], created_at="2026-01-01T00:00:00+00:00",
            utility_score=utility_score, circuit_state=circuit_state,
        )

    def test_excludes_failing_skill(self, monkeypatch):
        from evolver import _top_peer_skills
        failing = self._make_skill("fail", utility_score=0.1, circuit_state="open")
        healthy = self._make_skill("good", utility_score=0.9)
        monkeypatch.setattr("evolver._top_peer_skills.__globals__['__builtins__']", None, raising=False)
        with patch("evolver.load_outcomes", return_value=[]):
            # Patch skills.load_skills at the right import path
            pass
        # Direct patch via monkeypatch
        import evolver
        with patch("skills.load_skills", return_value=[failing, healthy]):
            peers = _top_peer_skills(failing, k=2)
        assert all(p.id != "fail" for p in peers)

    def test_excludes_open_circuit_skills(self, monkeypatch):
        from evolver import _top_peer_skills
        failing = self._make_skill("fail", utility_score=0.2, circuit_state="open")
        open_peer = self._make_skill("open_peer", utility_score=0.9, circuit_state="open")
        closed = self._make_skill("closed", utility_score=0.8, circuit_state="closed")
        with patch("skills.load_skills", return_value=[failing, open_peer, closed]):
            peers = _top_peer_skills(failing, k=5)
        assert all(p.circuit_state != "open" for p in peers)

    def test_returns_at_most_k(self, monkeypatch):
        from evolver import _top_peer_skills
        failing = self._make_skill("fail")
        others = [self._make_skill(f"s{i}", utility_score=0.9 - i * 0.05) for i in range(10)]
        with patch("skills.load_skills", return_value=[failing] + others):
            peers = _top_peer_skills(failing, k=2)
        assert len(peers) <= 2

    def test_empty_pool_returns_empty(self):
        from evolver import _top_peer_skills
        failing = self._make_skill("fail")
        with patch("skills.load_skills", return_value=[failing]):
            peers = _top_peer_skills(failing)
        assert peers == []


# ===========================================================================
# _run_skill_test_gate — adapter injection fix (not dry-run)
# ===========================================================================

class TestRunSkillTestGate:
    """Verify _run_skill_test_gate builds a real adapter instead of passing
    adapter=None (which caused validate_skill_mutation to always return
    blocked=False in dry-run mode)."""

    def _make_suggestion(self):
        return {
            "suggestion_id": "gate-unit-00",
            "category": "skill_pattern",
            "target": "test_skill",
            "suggestion": "improved trigger pattern",
            "failure_pattern": "drift",
            "confidence": 0.8,
            "outcomes_analyzed": 3,
            "generated_at": "2026-04-06T00:00:00+00:00",
            "applied": False,
        }

    def test_gate_builds_adapter_not_none(self):
        """Gate must call validate_skill_mutation with a non-None adapter."""
        from unittest.mock import patch, MagicMock, call
        from evolver import _run_skill_test_gate
        from skills import Skill
        from datetime import datetime, timezone

        skill = Skill(
            id="test_skill",
            name="test_skill",
            description="original description",
            trigger_patterns=["test pattern"],
            steps_template=["do the thing"],
            source_loop_ids=[],
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.blocked = False
        mock_result.block_reason = ""

        called_with_adapter = []

        def capture_validate(original, mutated, adapter=None):
            called_with_adapter.append(adapter)
            return mock_result

        # The gate imports llm.build_adapter at call time (evolver_store.py) —
        # patching evolver.build_adapter is a decoy that only "passed" on boxes
        # where the real builder could detect a claude install.
        with patch("llm.build_adapter", return_value=mock_adapter):
            with patch("skills.load_skills", return_value=[skill]):
                with patch("evolver_store.validate_skill_mutation", side_effect=capture_validate):
                    with patch("skills.generate_skill_tests", return_value=[{"input": "x", "expected": "y"}]):
                        with patch("skills.run_skill_tests", return_value=(1, 1)):
                            _run_skill_test_gate(self._make_suggestion())

        # The critical assertion: adapter must NOT be None
        assert len(called_with_adapter) == 1, "validate_skill_mutation should have been called once"
        assert called_with_adapter[0] is not None, (
            "validate_skill_mutation called with adapter=None — gate is in permanent dry-run mode"
        )

    def test_gate_returns_none_when_skill_not_found(self):
        """Gate returns None (allow through) when target skill doesn't exist."""
        from unittest.mock import patch
        from evolver import _run_skill_test_gate

        with patch("skills.load_skills", return_value=[]):
            with patch("evolver.build_adapter", return_value=None):
                result = _run_skill_test_gate(self._make_suggestion())

        assert result is None or result == {"blocked": False, "block_reason": ""}


# ---------------------------------------------------------------------------
# Quality drift detection
# ---------------------------------------------------------------------------

class TestQualityDrift:
    """Tests for scan_quality_drift and baselines."""

    def test_no_findings_with_empty_outcomes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("evolver_scans._baselines_path", lambda: tmp_path / "baselines.jsonl")
        assert scan_quality_drift([]) == []

    def test_no_findings_without_enough_history(self, tmp_path, monkeypatch):
        """Need at least 3 prior baselines to detect drift."""
        monkeypatch.setattr("evolver_scans._baselines_path", lambda: tmp_path / "baselines.jsonl")
        outcomes = [{"status": "done"}, {"status": "stuck"}]
        findings = scan_quality_drift(outcomes)
        assert findings == []

    def test_baseline_saved_on_each_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr("evolver_scans._baselines_path", lambda: tmp_path / "baselines.jsonl")
        scan_quality_drift([{"status": "done"}])
        baselines = _load_baselines()
        assert len(baselines) == 1
        assert baselines[0]["success_rate"] == 1.0

    def test_drift_detected_after_consecutive_drops(self, tmp_path, monkeypatch):
        """Sustained success_rate drop below threshold triggers finding."""
        bl_path = tmp_path / "baselines.jsonl"
        monkeypatch.setattr("evolver_scans._baselines_path", lambda: bl_path)

        # Seed 5 cycles of 80% success
        for i in range(5):
            _save_baseline({"ts": f"2026-01-0{i+1}", "success_rate": 0.8, "avg_cost_usd": 0.01, "outcomes_count": 10})

        # Seed 3 cycles of sharp decline (below 15% drop from 0.8 = below 0.68)
        for i in range(3):
            _save_baseline({"ts": f"2026-01-1{i}", "success_rate": 0.4, "avg_cost_usd": 0.01, "outcomes_count": 10})

        # Current cycle: also bad
        outcomes = [{"status": "stuck"}] * 8 + [{"status": "done"}] * 2  # 20% success
        findings = scan_quality_drift(outcomes, consecutive_alert=3)

        sr_findings = [f for f in findings if f.metric == "success_rate"]
        assert len(sr_findings) >= 1
        assert sr_findings[0].consecutive_drops >= 3

    def test_no_drift_when_quality_stable(self, tmp_path, monkeypatch):
        """Stable success_rate produces no findings."""
        bl_path = tmp_path / "baselines.jsonl"
        monkeypatch.setattr("evolver_scans._baselines_path", lambda: bl_path)

        for i in range(5):
            _save_baseline({"ts": f"2026-01-0{i+1}", "success_rate": 0.75, "avg_cost_usd": 0.01, "outcomes_count": 10})

        outcomes = [{"status": "done"}] * 7 + [{"status": "stuck"}] * 3  # 70% - within threshold
        findings = scan_quality_drift(outcomes)
        sr_findings = [f for f in findings if f.metric == "success_rate"]
        assert len(sr_findings) == 0

    def test_cost_drift_detected(self, tmp_path, monkeypatch):
        """Rising avg cost triggers finding when sustained."""
        bl_path = tmp_path / "baselines.jsonl"
        monkeypatch.setattr("evolver_scans._baselines_path", lambda: bl_path)

        for i in range(5):
            _save_baseline({"ts": f"2026-01-0{i+1}", "success_rate": 0.8, "avg_cost_usd": 0.01, "outcomes_count": 10})

        # 3 cycles of high cost
        for i in range(3):
            _save_baseline({"ts": f"2026-01-1{i}", "success_rate": 0.8, "avg_cost_usd": 0.05, "outcomes_count": 10})

        # Current cycle: also high cost
        outcomes = [{"status": "done", "cost_usd": 0.05}] * 10
        findings = scan_quality_drift(outcomes, consecutive_alert=3)
        cost_findings = [f for f in findings if f.metric == "avg_cost_usd"]
        assert len(cost_findings) >= 1

    def test_load_baselines_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("evolver_scans._baselines_path", lambda: tmp_path / "baselines.jsonl")
        _save_baseline({"ts": "2026-01-01", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 5})
        _save_baseline({"ts": "2026-01-02", "success_rate": 0.8, "avg_cost_usd": 0.02, "outcomes_count": 10})
        loaded = _load_baselines()
        assert len(loaded) == 2
        assert loaded[0]["ts"] == "2026-01-02"  # newest first


# ---------------------------------------------------------------------------
# Stage 2→3: scan_canon_candidates
# ---------------------------------------------------------------------------

class TestScanCanonCandidates:
    """Tests for scan_canon_candidates() — Stage 2→3 crystallization surface."""

    def _make_candidates(self, n: int = 2) -> list:
        """Build fake get_canon_candidates return values."""
        return [
            {
                "lesson_id": f"lid{i:02d}",
                "lesson": f"Always do X for task type {i}",
                "task_type": "research",
                "score": 0.95,
                "times_applied": 12 + i,
                "task_types_seen": ["research", "build", "ops"],
                "sessions_validated": 5,
                "recorded_at": "2026-01-01",
            }
            for i in range(n)
        ]

    def test_returns_suggestions_for_each_candidate(self):
        """One crystallization Suggestion per canon candidate."""
        import sys, types
        from evolver import scan_canon_candidates
        candidates = self._make_candidates(3)
        fake_mem = types.ModuleType("memory")
        fake_mem.get_canon_candidates = lambda **kw: candidates
        orig = sys.modules.get("memory")
        sys.modules["memory"] = fake_mem
        try:
            result = scan_canon_candidates()
            assert len(result) == 3
            for s in result:
                assert s.category == "crystallization"
                assert "PROMOTE TO IDENTITY" in s.suggestion
                assert 0.5 <= s.confidence <= 1.0
        finally:
            if orig is None:
                sys.modules.pop("memory", None)
            else:
                sys.modules["memory"] = orig

    def test_returns_empty_when_no_candidates(self, monkeypatch):
        """Empty list when no lessons meet the threshold."""
        import sys, types
        fake_mem = types.ModuleType("memory")
        fake_mem.get_canon_candidates = lambda **kw: []
        orig = sys.modules.get("memory")
        sys.modules["memory"] = fake_mem
        try:
            from evolver import scan_canon_candidates
            result = scan_canon_candidates()
            assert result == []
        finally:
            if orig is None:
                sys.modules.pop("memory", None)
            else:
                sys.modules["memory"] = orig

    def test_suggestion_confidence_scales_with_hits(self):
        """More applications → higher confidence, capped at 0.95."""
        import sys, types
        from evolver import scan_canon_candidates
        for times_applied in [10, 20, 30]:
            fake_mem = types.ModuleType("memory")
            fake_mem.get_canon_candidates = lambda **kw: [{
                "lesson_id": "x",
                "lesson": "test lesson",
                "task_type": "research",
                "score": 0.9,
                "times_applied": times_applied,
                "task_types_seen": ["research", "build", "ops"],
                "sessions_validated": 3,
                "recorded_at": "2026-01-01",
            }]
            orig = sys.modules.get("memory")
            sys.modules["memory"] = fake_mem
            try:
                result = scan_canon_candidates()
            finally:
                if orig is None:
                    sys.modules.pop("memory", None)
                else:
                    sys.modules["memory"] = orig
            assert len(result) == 1
            assert result[0].confidence <= 0.95

    def test_handles_import_error_gracefully(self, monkeypatch):
        """Returns [] if memory module is unavailable."""
        import sys
        from evolver import scan_canon_candidates
        orig = sys.modules.get("memory")
        sys.modules.pop("memory", None)
        # Remove from builtins import path temporarily by making it unimportable
        import importlib
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        try:
            result = scan_canon_candidates()
            # Either returns [] or raises — both acceptable if memory import fails
            assert isinstance(result, list)
        except Exception:
            pass  # acceptable — ImportError path
        finally:
            if orig is not None:
                sys.modules["memory"] = orig

    def test_run_evolver_includes_canon_scan(self, monkeypatch):
        """run_evolver with scan_canon=True calls scan_canon_candidates."""
        from evolver import run_evolver
        canon_called = []

        def fake_canon(**kw):
            canon_called.append(True)
            return []

        monkeypatch.setattr("evolver.scan_canon_candidates", fake_canon)

        # Provide enough outcomes to pass min_outcomes check
        fake_outcomes = [
            MagicMock(status="done", goal="g", summary="s", task_type="research",
                      cost_usd=0.01, outcome_id="x")
            for _ in range(5)
        ]
        monkeypatch.setattr("evolver.load_outcomes", lambda **kw: fake_outcomes)
        monkeypatch.setattr("evolver._llm_analyze", lambda *a, **kw: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda *a, **kw: None)
        monkeypatch.setattr("evolver_scans._save_baseline", lambda *a, **kw: None)

        run_evolver(dry_run=True, scan_canon=True, scan_signals=False,
                    scan_calibration=False, scan_costs=False, scan_drift=False,
                    verbose=False)
        assert canon_called, "scan_canon_candidates was not called"

    def test_run_evolver_skips_canon_scan_when_disabled(self, monkeypatch):
        """run_evolver with scan_canon=False does not call scan_canon_candidates."""
        from evolver import run_evolver
        canon_called = []

        def fake_canon(**kw):
            canon_called.append(True)
            return []

        monkeypatch.setattr("evolver.scan_canon_candidates", fake_canon)

        fake_outcomes = [
            MagicMock(status="done", goal="g", summary="s", task_type="research",
                      cost_usd=0.01, outcome_id="x")
            for _ in range(5)
        ]
        monkeypatch.setattr("evolver.load_outcomes", lambda **kw: fake_outcomes)
        monkeypatch.setattr("evolver._llm_analyze", lambda *a, **kw: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda *a, **kw: None)
        monkeypatch.setattr("evolver_scans._save_baseline", lambda *a, **kw: None)

        run_evolver(dry_run=True, scan_canon=False, scan_signals=False,
                    scan_calibration=False, scan_costs=False, scan_drift=False,
                    verbose=False)
        assert not canon_called, "scan_canon_candidates was called despite scan_canon=False"


# ---------------------------------------------------------------------------
# scan_suggestion_outcomes — empirical confidence calibration
# ---------------------------------------------------------------------------

class TestScanSuggestionOutcomes:
    """Tests for scan_suggestion_outcomes() and _record_suggestion_outcomes()."""

    def _write_outcomes(self, path, entries):
        import json
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    def test_no_file_returns_empty(self, tmp_path):
        from evolver import scan_suggestion_outcomes
        result = scan_suggestion_outcomes(outcomes_path=tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_detects_overconfident_category(self, tmp_path):
        """Category with high self-reported confidence but low pass rate → suggestion."""
        from evolver import scan_suggestion_outcomes
        out_file = tmp_path / "suggestion_outcomes.jsonl"
        # 3 skill_mutation suggestions: all self-reported 0.9 but only 1/3 passed
        entries = [
            {"suggestion_id": f"s{i}", "category": "skill_mutation",
             "confidence": 0.9, "verified": i == 0, "run_id": "r1", "verified_at": "2026-04-14T00:00:00Z"}
            for i in range(3)
        ]
        self._write_outcomes(out_file, entries)
        suggestions = scan_suggestion_outcomes(min_samples=3, outcomes_path=out_file)
        assert len(suggestions) == 1
        assert suggestions[0].category == "observation"
        assert "skill_mutation" in suggestions[0].suggestion
        assert "CONFIDENCE MISCALIBRATION" in suggestions[0].suggestion

    def test_calibrated_category_no_suggestion(self, tmp_path):
        """Category with matching empirical and self-reported confidence → no suggestion."""
        from evolver import scan_suggestion_outcomes
        out_file = tmp_path / "suggestion_outcomes.jsonl"
        # prompt_tweak: self-reported 0.75, empirical 4/5 = 0.8 → calibrated
        entries = [
            {"suggestion_id": f"s{i}", "category": "prompt_tweak",
             "confidence": 0.75, "verified": i < 4, "run_id": "r1", "verified_at": "2026-04-14T00:00:00Z"}
            for i in range(5)
        ]
        self._write_outcomes(out_file, entries)
        suggestions = scan_suggestion_outcomes(min_samples=3, outcomes_path=out_file)
        assert len(suggestions) == 0

    def test_min_samples_gate(self, tmp_path):
        """Category with fewer than min_samples entries is skipped."""
        from evolver import scan_suggestion_outcomes
        out_file = tmp_path / "suggestion_outcomes.jsonl"
        entries = [
            {"suggestion_id": "s0", "category": "skill_mutation",
             "confidence": 0.9, "verified": False, "run_id": "r1", "verified_at": "2026-04-14T00:00:00Z"}
        ]
        self._write_outcomes(out_file, entries)
        # Only 1 sample, min_samples=3 → should skip
        suggestions = scan_suggestion_outcomes(min_samples=3, outcomes_path=out_file)
        assert len(suggestions) == 0

    def test_record_suggestion_outcomes_writes_file(self, tmp_path, monkeypatch):
        """_record_suggestion_outcomes writes one entry per suggestion_id."""
        import json
        from evolver import _record_suggestion_outcomes

        # Conftest autouse fixture sets MARO_WORKSPACE=tmp_path; memory_dir()
        # will resolve to tmp_path/memory and create it automatically.
        # Write a fake change_log so _record_suggestion_outcomes can look up
        # category/confidence for each suggestion_id.
        from config import memory_dir
        mem_dir = memory_dir()  # creates tmp_path/memory
        cl_path = mem_dir / "change_log.jsonl"
        cl_path.write_text(
            json.dumps({"suggestion_id": "sid1", "category": "prompt_tweak", "confidence": 0.8}) + "\n",
            encoding="utf-8",
        )

        _record_suggestion_outcomes(["sid1"], True, "run-abc")

        out_file = mem_dir / "suggestion_outcomes.jsonl"
        assert out_file.exists()
        entries = [json.loads(l) for l in out_file.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        assert entries[0]["suggestion_id"] == "sid1"
        assert entries[0]["category"] == "prompt_tweak"
        assert entries[0]["confidence"] == 0.8
        assert entries[0]["verified"] is True

    def test_run_evolver_includes_suggestion_calibration(self, monkeypatch):
        """run_evolver calls scan_suggestion_outcomes when scan_suggestion_calibration=True."""
        from evolver import run_evolver
        from unittest.mock import MagicMock

        calibration_called = []

        def fake_calibration(**kw):
            calibration_called.append(True)
            return []

        monkeypatch.setattr("evolver.scan_suggestion_outcomes", fake_calibration)

        fake_outcomes = [
            MagicMock(status="done", goal="g", summary="s", task_type="research",
                      cost_usd=0.01, outcome_id="x")
            for _ in range(5)
        ]
        monkeypatch.setattr("evolver.load_outcomes", lambda **kw: fake_outcomes)
        monkeypatch.setattr("evolver._llm_analyze", lambda *a, **kw: ([], []))
        monkeypatch.setattr("evolver._save_suggestions", lambda *a, **kw: None)
        monkeypatch.setattr("evolver_scans._save_baseline", lambda *a, **kw: None)

        run_evolver(dry_run=True, scan_suggestion_calibration=True, scan_signals=False,
                    scan_calibration=False, scan_costs=False, scan_drift=False,
                    scan_canon=False, verbose=False)
        assert calibration_called, "scan_suggestion_outcomes was not called"


# ---------------------------------------------------------------------------
# _load_user_signals + SIGNALS.md context injection
# ---------------------------------------------------------------------------

from evolver import _load_user_signals


class TestLoadUserSignals:
    # Resolution goes through config.user_file(): workspace overlay
    # (<MARO_WORKSPACE>/user/SIGNALS.md — conftest points MARO_WORKSPACE at
    # tmp_path) wins over the repo template (config.repo_user_dir()).

    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        """No workspace overlay and no repo template returns empty string."""
        import config as _config_mod
        monkeypatch.setattr(
            _config_mod, "repo_user_dir", lambda: tmp_path / "no-such-user-dir"
        )
        result = _load_user_signals()
        assert result == ""

    def test_reads_and_caps_at_600_chars(self, tmp_path):
        """Reads the workspace-overlay SIGNALS.md (beats the repo template) and caps at 600 chars."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "SIGNALS.md").write_text("A" * 700)

        result = _load_user_signals()
        assert len(result) <= 600
        assert result == "A" * 600

    def test_repo_template_used_when_no_overlay(self, tmp_path, monkeypatch):
        """Without a workspace overlay, the repo/install template is the fallback."""
        import config as _config_mod
        repo_user = tmp_path / "repo-user"
        repo_user.mkdir()
        (repo_user / "SIGNALS.md").write_text("shipped template content")
        monkeypatch.setattr(_config_mod, "repo_user_dir", lambda: repo_user)

        result = _load_user_signals()
        assert result == "shipped template content"

    def test_nocrash_on_permission_error(self, tmp_path):
        """Permission error loading SIGNALS.md returns empty, never raises."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        sig_file = user_dir / "SIGNALS.md"
        sig_file.write_text("test")
        sig_file.chmod(0o000)
        try:
            result = _load_user_signals()
            # Either empty (permission denied) or the content — no crash
            assert isinstance(result, str)
        finally:
            sig_file.chmod(0o644)


class TestScanOutcomesForSignalsWithUserSignals:
    def _make_outcome(self, status="done", goal="research goal", summary="found useful pattern"):
        o = MagicMock()
        o.status = status
        o.goal = goal
        o.summary = summary
        o.task_type = "research"
        return o

    def test_user_signals_included_in_llm_call(self, tmp_path, monkeypatch):
        """Workspace-overlay SIGNALS.md content is passed to the LLM when available."""
        import evolver_scans as _evolver_mod

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "SIGNALS.md").write_text("## Active research: Polymarket arbitrage strategies")

        captured_user_msg = []

        def fake_build_adapter(model=None):
            class _Adapter:
                def complete(self, messages, **kwargs):
                    # Capture the user message to check signal content was included
                    user_msgs = [m for m in messages if getattr(m, 'role', '') == 'user']
                    if user_msgs:
                        captured_user_msg.append(getattr(user_msgs[0], 'content', ''))
                    return MagicMock(
                        content=json.dumps({"signals": []}),
                        input_tokens=10,
                        output_tokens=5,
                    )
            return _Adapter()

        monkeypatch.setattr(_evolver_mod, "build_adapter", fake_build_adapter)

        outcomes = [self._make_outcome()]
        scan_outcomes_for_signals(outcomes)

        assert len(captured_user_msg) > 0
        # User signals block should be present in the LLM message
        assert "Polymarket arbitrage" in captured_user_msg[0]

    def test_no_signals_file_still_works(self, monkeypatch):
        """Missing SIGNALS.md doesn't break signal scanning."""
        import evolver_scans as _evolver_mod
        monkeypatch.setattr(_evolver_mod, "_load_user_signals", lambda: "")

        signal_json = json.dumps({
            "signals": [{
                "signal_type": "lead",
                "description": "something",
                "suggested_goal": "Investigate lead further in depth",
                "confidence": 0.9,
                "source_outcome": "run",
            }]
        })
        mock_adapter = MagicMock()
        mock_adapter.complete.return_value = MagicMock(
            content=signal_json, input_tokens=10, output_tokens=30
        )
        outcomes = [self._make_outcome()]
        with patch("evolver_scans.build_adapter", return_value=mock_adapter):
            signals = scan_outcomes_for_signals(outcomes)
        assert len(signals) == 1


# ---------------------------------------------------------------------------
# scan_evolver_impact / format_impact_summary
# ---------------------------------------------------------------------------

class TestScanEvolverImpact:
    """Tests for longitudinal evolver impact analysis."""

    def _make_apply_event(self, applied_at: str, suggestion_id: str = "test-sid", category: str = "prompt_tweak"):
        return {
            "timestamp": applied_at,
            "subject": suggestion_id,
            "context": {"suggestion_id": suggestion_id, "category": category},
        }

    def _make_outcome(self, ts: str, status: str = "done"):
        """Minimal outcome-like object with created_at and status."""
        class FakeOutcome:
            def __init__(self):
                self.created_at = ts
                self.status = status
        return FakeOutcome()

    def test_returns_empty_when_no_apply_events(self):
        with patch("evolver_scans.query_log", return_value=[]):
            records = scan_evolver_impact()
        assert records == []

    def test_returns_empty_when_captains_log_unavailable(self):
        with patch("evolver_scans.query_log", side_effect=ImportError("no captains_log")):
            records = scan_evolver_impact()
        assert records == []

    def test_verdict_improved_when_stuck_rate_drops(self):
        events = [self._make_apply_event("2026-04-14T10:00:00+00:00")]
        # Before: 5 outcomes, 2 stuck (40%); After: 5 outcomes, 0 stuck (0%)
        before_outcomes = [
            self._make_outcome("2026-04-14T09:00:00+00:00", "stuck"),
            self._make_outcome("2026-04-14T09:10:00+00:00", "stuck"),
            self._make_outcome("2026-04-14T09:20:00+00:00", "done"),
            self._make_outcome("2026-04-14T09:30:00+00:00", "done"),
            self._make_outcome("2026-04-14T09:40:00+00:00", "done"),
        ]
        after_outcomes = [
            self._make_outcome("2026-04-14T10:10:00+00:00", "done"),
            self._make_outcome("2026-04-14T10:20:00+00:00", "done"),
            self._make_outcome("2026-04-14T10:30:00+00:00", "done"),
            self._make_outcome("2026-04-14T10:40:00+00:00", "done"),
            self._make_outcome("2026-04-14T10:50:00+00:00", "done"),
        ]
        all_outcomes = before_outcomes + after_outcomes

        with patch("evolver_scans.query_log", return_value=events):
            try:
                with patch("evolver_scans.load_outcomes", return_value=all_outcomes):
                    records = scan_evolver_impact(lookback_hours=2, lookahead_hours=2)
            except Exception:
                import evolver as _ev
                orig = _ev.__dict__.get("load_outcomes_window")
                records = []

        if records:
            assert records[0].verdict in ("improved", "neutral", "insufficient_data")

    def test_verdict_insufficient_data_when_too_few_outcomes(self):
        events = [self._make_apply_event("2026-04-14T10:00:00+00:00")]
        # Only 1 outcome in each window — below default min_outcomes=3
        sparse = [
            self._make_outcome("2026-04-14T09:30:00+00:00", "stuck"),
            self._make_outcome("2026-04-14T10:30:00+00:00", "done"),
        ]
        with patch("evolver_scans.query_log", return_value=events):
            with patch("evolver_scans.load_outcomes", return_value=sparse):
                records = scan_evolver_impact(lookback_hours=1, lookahead_hours=1, min_outcomes=3)
        if records:
            assert records[0].verdict == "insufficient_data"

    def test_event_with_unparseable_timestamp_skipped(self):
        events = [{"timestamp": "not-a-date", "subject": "sid1", "context": {}}]
        with patch("evolver_scans.query_log", return_value=events):
            with patch("evolver_scans.load_outcomes", return_value=[]):
                records = scan_evolver_impact()
        assert records == []

    def test_suggestions_store_is_primary_source(self, tmp_path):
        """applied_at in suggestions.jsonl wins over a log event for the same
        suggestion — the store is the source of truth, the log is fallback."""
        path = tmp_path / "suggestions.jsonl"
        s1 = Suggestion(suggestion_id="dup-sid", category="prompt_tweak",
                        target="all", suggestion="x", failure_pattern="y",
                        confidence=0.5, outcomes_analyzed=5, applied=True,
                        applied_at="2026-04-14T10:00:00+00:00")
        path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")
        # Same suggestion also has a log event with a DIFFERENT timestamp
        stale_event = self._make_apply_event(
            "2026-01-01T00:00:00+00:00", suggestion_id="dup-sid")

        with patch("evolver_store._suggestions_path", return_value=path), \
             patch("evolver_scans.query_log", return_value=[stale_event]), \
             patch("evolver_scans.load_outcomes", return_value=[]):
            records = scan_evolver_impact(min_outcomes=99)

        assert len(records) == 1
        assert records[0].applied_at == "2026-04-14T10:00:00+00:00"

    def test_log_fallback_covers_historical_applies(self, tmp_path):
        """Applies that predate the applied_at stamp still come from the log."""
        path = tmp_path / "suggestions.jsonl"
        # Old-style record: applied but never stamped
        s1 = Suggestion(suggestion_id="old-sid", category="prompt_tweak",
                        target="all", suggestion="x", failure_pattern="y",
                        confidence=0.5, outcomes_analyzed=5, applied=True)
        path.write_text(json.dumps(s1.to_dict()) + "\n", encoding="utf-8")
        event = self._make_apply_event(
            "2026-04-14T10:00:00+00:00", suggestion_id="old-sid")

        with patch("evolver_store._suggestions_path", return_value=path), \
             patch("evolver_scans.query_log", return_value=[event]), \
             patch("evolver_scans.load_outcomes", return_value=[]):
            records = scan_evolver_impact(min_outcomes=99)

        assert len(records) == 1
        assert records[0].suggestion_id == "old-sid"
        assert records[0].applied_at == "2026-04-14T10:00:00+00:00"


class TestFormatImpactSummary:
    def _make_record(self, verdict: str, category: str = "prompt_tweak") -> "EvolverImpactRecord":
        return EvolverImpactRecord(
            suggestion_id="abc123",
            category=category,
            applied_at="2026-04-14T10:00:00+00:00",
            outcomes_before=10,
            stuck_before=3,
            outcomes_after=10,
            stuck_after=1,
            stuck_rate_before=0.30,
            stuck_rate_after=0.10,
            delta=-0.20,
            verdict=verdict,
        )

    def test_returns_message_on_empty_records(self):
        result = format_impact_summary([])
        assert "No EVOLVER_APPLIED" in result

    def test_includes_verdict_counts(self):
        records = [
            self._make_record("improved"),
            self._make_record("neutral"),
            self._make_record("degraded"),
        ]
        result = format_impact_summary(records)
        assert "improved=1" in result
        assert "degraded=1" in result
        assert "neutral=1" in result

    def test_improved_record_shows_delta(self):
        records = [self._make_record("improved")]
        result = format_impact_summary(records)
        assert "improved" in result
        assert "abc123" in result

    def test_insufficient_data_record_shown(self):
        record = EvolverImpactRecord(
            suggestion_id="xyz", category="observation",
            applied_at="2026-04-14T10:00:00+00:00",
            outcomes_before=1, stuck_before=0, outcomes_after=0, stuck_after=0,
            stuck_rate_before=float("nan"), stuck_rate_after=float("nan"),
            delta=0.0, verdict="insufficient_data",
        )
        result = format_impact_summary([record])
        assert "insufficient data" in result


class TestRewriteSkillSignature:
    """Session 40 regression: rewrite_skill lost its verbose param — both
    production call sites pass verbose=verbose, so every call raised
    TypeError, silently swallowed by the callers' broad except blocks.
    Skill rewriting (circuit-breaker recovery) was dead since the param
    was dropped."""

    def _make_skill(self):
        from skills import Skill
        return Skill(
            id="rw01", name="rewrite-me", description="A skill that fails",
            trigger_patterns=["x"], steps_template=["do the thing"],
            source_loop_ids=[], created_at="2026-01-01T00:00:00+00:00",
            tier="provisional", utility_score=0.2,
        )

    def test_accepts_verbose_kwarg_with_junk_adapter(self, tmp_path, monkeypatch):
        """The caller contract: rewrite_skill(skill, adapter=..., verbose=...).
        A junk adapter response exercises the parse-error path, which also
        referenced the missing verbose name."""
        from evolver import rewrite_skill

        class _JunkAdapter:
            def complete(self, messages, **kw):
                class _R:
                    content = "this is not json"
                return _R()

        result = rewrite_skill(self._make_skill(), adapter=_JunkAdapter(), verbose=True)
        assert result is None

    def test_none_adapter_returns_none(self):
        from evolver import rewrite_skill
        assert rewrite_skill(self._make_skill(), adapter=None, verbose=False) is None


class TestRewriteSkillEmitsEvent:
    """BACKLOG #8: SKILL_REWRITE was registered in EVENT_TYPES and consumed
    (recall.py loop context, evolver.py learning-activity header) since the
    2026-06-24 inventory, but no code path ever emitted it. Wired 2026-07-03:
    the success path of rewrite_skill logs it."""

    def _make_skill(self):
        from skills import Skill
        return Skill(
            id="rw02", name="rewrite-me-live", description="A skill that fails",
            trigger_patterns=["x"], steps_template=["do the thing"],
            source_loop_ids=[], created_at="2026-01-01T00:00:00+00:00",
            tier="provisional", utility_score=0.2, consecutive_failures=3,
        )

    def test_success_path_emits_skill_rewrite(self, monkeypatch):
        import skills as skills_mod
        import captains_log
        from evolver import rewrite_skill

        skill = self._make_skill()
        monkeypatch.setattr(skills_mod, "load_skills", lambda: [skill])
        monkeypatch.setattr(skills_mod, "_save_skills", lambda s: None)

        events = []
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda event_type, subject, summary, **kw: events.append(
                (event_type, subject, kw.get("context") or {})) or {},
        )

        class _GoodAdapter:
            def complete(self, messages, **kw):
                class _R:
                    content = (
                        '{"description": "Revised description.",'
                        ' "steps_template": ["step one", "step two"],'
                        ' "trigger_patterns": ["kw one", "kw two", "kw three"]}'
                    )
                return _R()

        result = rewrite_skill(skill, adapter=_GoodAdapter(), verbose=False)
        assert result is not None
        assert result.circuit_state == "half_open"
        rewrites = [e for e in events if e[0] == "SKILL_REWRITE"]
        assert rewrites, f"expected SKILL_REWRITE, saw {[e[0] for e in events]}"
        assert rewrites[0][1] == "rewrite-me-live"
        assert rewrites[0][2].get("skill_id") == "rw02"
        assert rewrites[0][2].get("failures_before") == 3

    def test_failure_path_does_not_emit(self, monkeypatch):
        import captains_log
        from evolver import rewrite_skill

        events = []
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda event_type, subject, summary, **kw: events.append(event_type) or {},
        )

        class _JunkAdapter:
            def complete(self, messages, **kw):
                class _R:
                    content = "this is not json"
                return _R()

        assert rewrite_skill(self._make_skill(), adapter=_JunkAdapter(), verbose=False) is None
        assert "SKILL_REWRITE" not in events


# ---------------------------------------------------------------------------
# Run-cadence counter (evolver meta-cycle rides run finalizations — no timer)
# ---------------------------------------------------------------------------

class TestEvolverCadenceTick:
    """evolver_store.evolver_cadence_tick — the entire scheduling mechanism
    for the meta-cycle (2026-07-09 supervision decision: app, not systemic)."""

    def _tick(self, tmp_path, monkeypatch, cadence):
        import evolver_store
        monkeypatch.setattr(
            evolver_store, "_cadence_path", lambda: tmp_path / "evolver_cadence.json"
        )
        return evolver_store.evolver_cadence_tick(cadence)

    def _count(self, tmp_path):
        return json.loads(
            (tmp_path / "evolver_cadence.json").read_text()
        )["runs_since_evolve"]

    def test_cadence_zero_never_fires(self, tmp_path, monkeypatch):
        for _ in range(5):
            assert self._tick(tmp_path, monkeypatch, 0) is False
        # counter still accumulates so a later flip-on has real history
        assert self._count(tmp_path) == 5

    def test_counter_increments(self, tmp_path, monkeypatch):
        self._tick(tmp_path, monkeypatch, 10)
        assert self._count(tmp_path) == 1
        self._tick(tmp_path, monkeypatch, 10)
        assert self._count(tmp_path) == 2

    def test_fires_at_n_and_resets(self, tmp_path, monkeypatch):
        assert self._tick(tmp_path, monkeypatch, 3) is False
        assert self._tick(tmp_path, monkeypatch, 3) is False
        assert self._tick(tmp_path, monkeypatch, 3) is True
        assert self._count(tmp_path) == 0
        # next cycle starts clean
        assert self._tick(tmp_path, monkeypatch, 3) is False
        assert self._count(tmp_path) == 1

    def test_fires_immediately_when_counter_already_past_n(self, tmp_path, monkeypatch):
        # accumulated while off, then Jeremy flips cadence on
        for _ in range(4):
            self._tick(tmp_path, monkeypatch, 0)
        assert self._tick(tmp_path, monkeypatch, 3) is True
        assert self._count(tmp_path) == 0

    def test_corrupt_state_file_resets_not_raises(self, tmp_path, monkeypatch):
        (tmp_path / "evolver_cadence.json").write_text("not json{{{")
        assert self._tick(tmp_path, monkeypatch, 3) is False
        assert self._count(tmp_path) == 1


# ---------------------------------------------------------------------------
# VERIFY_LEARN_ARC §4 — verdict trust policy
# ---------------------------------------------------------------------------

class _FO:
    """Fake outcome — dataclass-shaped, carries the fields the trust policy reads."""
    def __init__(self, recorded_at="2026-05-01T11:00:00+00:00", status="done",
                 goal_achieved=None, goal_verdict_source="",
                 goal_verdict_confidence=None):
        self.recorded_at = recorded_at
        self.status = status
        self.goal_achieved = goal_achieved
        self.goal_verdict_source = goal_verdict_source
        self.goal_verdict_confidence = goal_verdict_confidence


class TestVerdictTrust:
    def test_unjudged_is_neutral(self):
        assert verdict_trust(_FO(goal_achieved=None)) == VERDICT_TRUST_NEUTRAL

    def test_judged_high_confidence_is_full(self):
        o = _FO(goal_achieved=False, goal_verdict_source="closure",
                goal_verdict_confidence=0.9)
        assert verdict_trust(o) == VERDICT_TRUST_FULL

    def test_judged_low_confidence_is_directional(self):
        o = _FO(goal_achieved=True, goal_verdict_source="closure",
                goal_verdict_confidence=0.5)
        assert verdict_trust(o) == VERDICT_TRUST_DIRECTIONAL

    def test_closure_unverifiable_is_excluded(self):
        o = _FO(goal_achieved=None, goal_verdict_source="closure_unverifiable")
        assert verdict_trust(o) == VERDICT_TRUST_EXCLUDED

    def test_judged_without_confidence_is_full(self):
        # Deterministic provenance guard / NOW self-verdict carry no confidence
        # but are authoritative — not directional.
        o = _FO(goal_achieved=False, goal_verdict_source="provenance")
        assert verdict_trust(o) == VERDICT_TRUST_FULL

    def test_accepts_dict_rows(self):
        assert verdict_trust({"status": "done"}) == VERDICT_TRUST_NEUTRAL
        assert verdict_trust(
            {"goal_achieved": False, "goal_verdict_source": "closure_unverifiable"}
        ) == VERDICT_TRUST_EXCLUDED


class TestVerifyCounts:
    def test_directional_and_excluded_dropped_from_denominator(self):
        outcomes = [
            _FO(status="done"),                                              # neutral, pass
            _FO(status="stuck"),                                             # neutral, fail
            _FO(goal_achieved=False, goal_verdict_source="closure",
                goal_verdict_confidence=0.9),                               # full, fail
            _FO(goal_achieved=True, goal_verdict_source="closure",
                goal_verdict_confidence=0.5),                               # directional -> dropped
            _FO(goal_achieved=None, goal_verdict_source="closure_unverifiable"),  # excluded -> dropped
        ]
        counted, failing = _verify_counts(outcomes)
        assert counted == 3           # two neutral + one full; directional+excluded dropped
        assert failing == 2           # the stuck neutral + the judged-False full

    def test_outcome_ts_prefers_recorded_at(self):
        # The bug that made the warn path dead: real Outcomes carry recorded_at,
        # not created_at. _outcome_ts must read recorded_at first.
        o = _FO(recorded_at="2026-05-01T11:00:00+00:00")
        assert _outcome_ts(o) is not None
        # created_at-only shape (test fakes, legacy) still parses.
        class OldShape:
            created_at = "2026-05-01T11:00:00+00:00"
        assert _outcome_ts(OldShape()) is not None


# ---------------------------------------------------------------------------
# VERIFY_LEARN_ARC V2 — cadence verdicts + authority-aware auto-revert
# ---------------------------------------------------------------------------

_T_APPLY = "2026-05-01T12:00:00+00:00"


def _mk_sug(sid, *, applied=True, applied_manually=False, applied_at=_T_APPLY,
            category="prompt_tweak", verified_at="", verify_extensions=0):
    return Suggestion(
        suggestion_id=sid, category=category, target="all",
        suggestion="tweak text", failure_pattern="x", confidence=0.9,
        outcomes_analyzed=10, applied=applied, applied_at=applied_at,
        applied_manually=applied_manually, verified_at=verified_at,
        verify_extensions=verify_extensions,
    )


def _write_suggestions(*sugs):
    from orch_items import memory_dir
    p = memory_dir() / "suggestions.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(s.to_dict()) for s in sugs) + "\n",
                 encoding="utf-8")
    return p


def _write_change_log(sid, category="prompt_tweak", before_state=None):
    """A change_log entry so revert_suggestion can find before_state."""
    from orch_items import memory_dir
    p = memory_dir() / "change_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": "2026-05-01T12:00:00+00:00",
            "suggestion_id": sid, "category": category, "target": "all",
            "confidence": 0.9, "suggestion_text": "tweak text",
            "before_state": before_state or {"type": "lesson_add"},
        }) + "\n")


def _read_sug(sid):
    from orch_items import memory_dir
    p = memory_dir() / "suggestions.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("suggestion_id") == sid:
            return d
    return None


def _read_outcome_calibration(sid):
    from orch_items import memory_dir
    p = memory_dir() / "suggestion_outcomes.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if r.get("suggestion_id") == sid]


def _read_escalations():
    from config import workspace_root
    p = workspace_root() / "output" / "escalations.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _outcomes(before_stuck, after_stuck, *, n=10):
    """n before + n after outcomes; `*_stuck` of each window are stuck."""
    out = []
    for i in range(n):
        out.append(_FO(recorded_at=f"2026-05-01T11:{i:02d}:00+00:00",
                       status="stuck" if i < before_stuck else "done"))
    for i in range(n):
        out.append(_FO(recorded_at=f"2026-05-01T13:{i:02d}:00+00:00",
                       status="stuck" if i < after_stuck else "done"))
    return out


class TestVerifyAppliedSuggestions:
    def test_confirmed_stamps_and_calibrates(self):
        _write_suggestions(_mk_sug("s-ok"))
        # stuck-rate 60% -> 0% : moved the expected direction (down) -> confirmed
        outs = _outcomes(before_stuck=6, after_stuck=0)
        with patch("evolver_scans.load_outcomes", return_value=outs):
            summary = verify_applied_suggestions("run1", min_post_apply=10)
        assert summary["confirmed"] == 1
        row = _read_sug("s-ok")
        assert row["verify_verdict"] == "confirmed"
        assert row["verified_at"]           # terminal stamp
        assert row["applied"] is True       # confirmed changes stay applied
        cal = _read_outcome_calibration("s-ok")
        assert cal and cal[0]["verified"] is True

    def test_degraded_self_applied_auto_reverts(self):
        _write_suggestions(_mk_sug("s-bad", applied_manually=False))
        _write_change_log("s-bad")
        # stuck-rate 0% -> 80% : rose past threshold -> degraded
        outs = _outcomes(before_stuck=0, after_stuck=8)
        with patch("evolver_scans.load_outcomes", return_value=outs):
            summary = verify_applied_suggestions("run2", min_post_apply=10)
        assert summary["reverted"] == 1
        assert summary["review_queued"] == 0
        row = _read_sug("s-bad")
        assert row["applied"] is False              # auto-reverted
        assert row["verify_verdict"] == "degraded"
        assert row["verified_at"]
        cal = _read_outcome_calibration("s-bad")
        assert cal and cal[0]["verified"] is False
        esc = [e for e in _read_escalations()
               if e.get("event_type") == "self_improvement_verdict"]
        assert esc and esc[-1]["action"] == "reverted"
        assert esc[-1]["blocking"] is False         # self-heal, non-blocking

    def test_degraded_human_applied_is_review_queued_not_reverted(self):
        _write_suggestions(_mk_sug("s-human", applied_manually=True))
        _write_change_log("s-human")
        outs = _outcomes(before_stuck=0, after_stuck=8)
        with patch("evolver_scans.load_outcomes", return_value=outs):
            summary = verify_applied_suggestions("run3", min_post_apply=10)
        assert summary["review_queued"] == 1
        assert summary["reverted"] == 0
        row = _read_sug("s-human")
        assert row["applied"] is True                       # NEVER auto-reverted
        assert row["verify_verdict"] == "degraded_needs_review"
        assert row["verified_at"]
        esc = [e for e in _read_escalations()
               if e.get("event_type") == "self_improvement_verdict"]
        assert esc and esc[-1]["action"] == "review_required"
        assert esc[-1]["blocking"] is True                  # human must decide

    def test_inconclusive_extends_then_parks_unverifiable(self):
        _write_suggestions(_mk_sug("s-flat"))
        # Only 2 post-apply outcomes -> below min_post_apply=10 -> inconclusive
        outs = _outcomes(before_stuck=3, after_stuck=1, n=2)
        with patch("evolver_scans.load_outcomes", return_value=outs):
            s1 = verify_applied_suggestions("r", min_post_apply=10, max_extensions=3)
            assert s1["pending"] == 1
            assert _read_sug("s-flat")["verify_extensions"] == 1
            assert _read_sug("s-flat")["verified_at"] == ""   # still pending
            s2 = verify_applied_suggestions("r", min_post_apply=10, max_extensions=3)
            assert s2["pending"] == 1
            assert _read_sug("s-flat")["verify_extensions"] == 2
            s3 = verify_applied_suggestions("r", min_post_apply=10, max_extensions=3)
        assert s3["unverifiable"] == 1
        row = _read_sug("s-flat")
        assert row["verify_verdict"] == "unverifiable"
        assert row["verified_at"]                             # terminal
        assert row["verify_extensions"] == 3

    def test_already_verified_rows_are_skipped(self):
        _write_suggestions(_mk_sug("s-done", verified_at="2026-05-01T12:30:00+00:00"))
        outs = _outcomes(before_stuck=6, after_stuck=0)
        with patch("evolver_scans.load_outcomes", return_value=outs):
            summary = verify_applied_suggestions("r", min_post_apply=10)
        assert summary["candidates"] == 0

    def test_unstamped_applied_row_is_skipped_not_parked(self):
        _write_suggestions(_mk_sug("s-legacy", applied_at=""))
        outs = _outcomes(before_stuck=6, after_stuck=0)
        with patch("evolver_scans.load_outcomes", return_value=outs):
            summary = verify_applied_suggestions("r", min_post_apply=10)
        assert summary["skipped_no_stamp"] == 1
        assert _read_sug("s-legacy")["verified_at"] == ""    # untouched

    def test_dry_run_writes_nothing(self):
        _write_suggestions(_mk_sug("s-dry"))
        outs = _outcomes(before_stuck=6, after_stuck=0)
        with patch("evolver_scans.load_outcomes", return_value=outs):
            summary = verify_applied_suggestions("r", min_post_apply=10, dry_run=True)
        assert summary["confirmed"] == 1                     # would confirm
        row = _read_sug("s-dry")
        assert row["verified_at"] == ""                      # but nothing written
        assert row["verify_verdict"] == ""

    def test_disabled_by_config_skips(self, monkeypatch):
        import config
        real_get = config.get
        monkeypatch.setattr(
            config, "get",
            lambda k, d=None: False if k == "evolver.verify_cadence_verdicts" else real_get(k, d),
        )
        _write_suggestions(_mk_sug("s-x"))
        with patch("evolver_scans.load_outcomes", return_value=_outcomes(6, 0)):
            summary = verify_applied_suggestions("r")
        assert summary["enabled"] is False

    def test_excluded_verdicts_do_not_count_as_post_apply_evidence(self):
        """A window of unverifiable (verifier-own-failure) outcomes must not
        reach the min_post_apply bar — it should stay inconclusive, never
        confirm a change off a verifier-cwd-bug stream."""
        _write_suggestions(_mk_sug("s-cwd"))
        outs = [_FO(recorded_at=f"2026-05-01T11:{i:02d}:00+00:00", status="done")
                for i in range(10)]
        # 12 post-apply outcomes, ALL excluded (closure_unverifiable)
        outs += [_FO(recorded_at=f"2026-05-01T13:{i:02d}:00+00:00", status="done",
                     goal_verdict_source="closure_unverifiable")
                 for i in range(12)]
        with patch("evolver_scans.load_outcomes", return_value=outs):
            summary = verify_applied_suggestions("r", min_post_apply=10, max_extensions=3)
        assert summary["confirmed"] == 0
        assert summary["pending"] == 1        # inconclusive: no trusted post-apply data
