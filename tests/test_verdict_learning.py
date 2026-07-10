"""Tests for SF-2 / data-02: verdict-aware learning (done ≠ achieved).

Write side: outcomes/lessons rows carry the goal-verdict tri-state
(goal_achieved True/False/ABSENT + goal_verdict_source), and the agenda
lane's post-closure annotation stamps the verdict onto the already-written
row via annotate_outcome_verdict(loop_id).

Read side: learning consumers prefer the verdict when present and treat
absence as unjudged (weaker signal — never counted as success, never as
failure). Covered here for the two most load-bearing consumers: recall's
dispatch repeat-guard and the evolver's outcomes summary.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory import (
    Outcome,
    record_outcome,
    load_outcomes,
    annotate_outcome_verdict,
    reflect_and_record,
    _memory_dir,
    _outcomes_path,
    _lessons_path,
)
from recall import RecallResult, PriorAttempt


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _raw_rows():
    path = _outcomes_path()
    if not path.exists():
        return []
    return [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


# ---------------------------------------------------------------------------
# Write side: record_outcome tri-state serialization
# ---------------------------------------------------------------------------

def test_record_outcome_unjudged_omits_verdict_keys(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "summary")
    row = _raw_rows()[-1]
    # Absent key = unjudged — never null, never False.
    assert "goal_achieved" not in row
    assert "goal_verdict_source" not in row
    assert "loop_id" not in row


def test_record_outcome_judged_false_writes_verdict(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome(
        "goal", "done", "summary",
        goal_achieved=False, goal_verdict_source="provenance", loop_id="lp-1",
    )
    row = _raw_rows()[-1]
    assert row["goal_achieved"] is False
    assert row["goal_verdict_source"] == "provenance"
    assert row["loop_id"] == "lp-1"


def test_record_outcome_judged_true_writes_verdict(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome(
        "goal", "done", "summary",
        goal_achieved=True, goal_verdict_source="now_self_verdict",
    )
    row = _raw_rows()[-1]
    assert row["goal_achieved"] is True
    assert row["goal_verdict_source"] == "now_self_verdict"


def test_record_outcome_stamps_lesson_rows_when_judged(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome(
        "goal", "done", "summary", lessons=["a hard-won lesson from this run"],
        goal_achieved=False, goal_verdict_source="closure",
    )
    lesson_rows = [
        json.loads(l)
        for l in _lessons_path().read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert lesson_rows[-1]["goal_achieved"] is False
    assert lesson_rows[-1]["goal_verdict_source"] == "closure"


def test_record_outcome_unjudged_lesson_rows_omit_verdict(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "summary", lessons=["an unjudged lesson text here"])
    lesson_rows = [
        json.loads(l)
        for l in _lessons_path().read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert "goal_achieved" not in lesson_rows[-1]
    assert "goal_verdict_source" not in lesson_rows[-1]


def test_load_outcomes_roundtrips_tristate(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("unjudged", "done", "s")
    record_outcome("failed", "done", "s", goal_achieved=False, goal_verdict_source="closure")
    record_outcome("achieved", "done", "s", goal_achieved=True, goal_verdict_source="closure")
    by_goal = {o.goal: o for o in load_outcomes(limit=10)}
    assert by_goal["unjudged"].goal_achieved is None
    assert by_goal["failed"].goal_achieved is False
    assert by_goal["achieved"].goal_achieved is True


# ---------------------------------------------------------------------------
# Write side: post-closure annotation (agenda lane)
# ---------------------------------------------------------------------------

def test_annotate_stamps_newest_matching_row(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("other goal", "done", "s", loop_id="lp-other")
    record_outcome("goal try 1", "done", "s", loop_id="lp-2")
    record_outcome("goal try 2 (restart)", "done", "s", loop_id="lp-2")
    assert annotate_outcome_verdict(
        "lp-2", goal_achieved=False, goal_verdict_source="closure",
        goal_verdict_confidence=0.9,
    ) is True
    rows = _raw_rows()
    # Newest lp-2 row got the verdict; the older lp-2 row and the other
    # loop's row are untouched.
    assert rows[2]["goal_achieved"] is False
    assert rows[2]["goal_verdict_source"] == "closure"
    assert rows[2]["goal_verdict_confidence"] == pytest.approx(0.9)
    assert "goal_achieved" not in rows[1]
    assert "goal_achieved" not in rows[0]


def test_annotate_unverifiable_leaves_goal_achieved_absent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-3")
    assert annotate_outcome_verdict(
        "lp-3", goal_achieved=None, goal_verdict_source="closure_unverifiable",
        goal_verdict_confidence=0.4,
    ) is True
    row = _raw_rows()[-1]
    # Unjudged stays absent — closure_unverifiable is not a failure verdict.
    assert "goal_achieved" not in row
    assert row["goal_verdict_source"] == "closure_unverifiable"


def test_annotate_none_preserves_existing_false(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-4")
    # Provenance guard stamps a deterministic False ...
    annotate_outcome_verdict("lp-4", goal_achieved=False, goal_verdict_source="provenance")
    # ... then an unverifiable closure verdict must not erase it.
    annotate_outcome_verdict("lp-4", goal_achieved=None, goal_verdict_source="closure_unverifiable")
    row = _raw_rows()[-1]
    assert row["goal_achieved"] is False
    assert row["goal_verdict_source"] == "closure_unverifiable"


def test_annotate_unknown_loop_returns_false(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-5")
    before = _raw_rows()
    assert annotate_outcome_verdict(
        "lp-nope", goal_achieved=True, goal_verdict_source="closure",
    ) is False
    assert _raw_rows() == before


def test_annotate_empty_loop_id_is_noop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s")
    assert annotate_outcome_verdict(
        "", goal_achieved=True, goal_verdict_source="closure",
    ) is False


# ---------------------------------------------------------------------------
# Write side: reflect_and_record threads the tri-state through
# ---------------------------------------------------------------------------

def test_reflect_and_record_threads_verdict_and_loop_id(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    reflect_and_record(
        "goal", "done", "did the thing",
        dry_run=True,
        goal_achieved=False,
        goal_verdict_source="provenance",
        loop_id="lp-6",
    )
    row = _raw_rows()[-1]
    assert row["goal_achieved"] is False
    assert row["goal_verdict_source"] == "provenance"
    assert row["loop_id"] == "lp-6"


def test_reflect_and_record_unjudged_stays_absent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    reflect_and_record("goal", "done", "did the thing", dry_run=True, loop_id="lp-7")
    row = _raw_rows()[-1]
    assert "goal_achieved" not in row
    assert row["loop_id"] == "lp-7"


# ---------------------------------------------------------------------------
# Read side: recall dispatch repeat-guard prefers the verdict
# ---------------------------------------------------------------------------

def _attempt(status, goal_achieved=None):
    return PriorAttempt(
        goal="g", handle_id="h", status=status,
        when=datetime.now(timezone.utc).isoformat(), match="exact",
        goal_achieved=goal_achieved,
    )


def _signals(attempts):
    r = RecallResult(thread=None, prior_attempts=attempts)
    return r.dispatch_signals(window_minutes=60)


def test_dispatch_guard_arms_on_done_but_goal_failed():
    # Before SF-2: status=="done" disarmed the guard even when every attempt
    # was judged goal-NOT-achieved. Now done ≠ achieved.
    sig = _signals([_attempt("done", goal_achieved=False),
                    _attempt("done", goal_achieved=False)])
    assert sig["all_failing"] is True


def test_dispatch_guard_unjudged_done_still_disarms():
    # Absence means "not judged", not "failed" — an unjudged done attempt is
    # not failure evidence.
    sig = _signals([_attempt("done"), _attempt("stuck")])
    assert sig["all_failing"] is False


def test_dispatch_guard_judged_true_disarms_even_when_not_done():
    sig = _signals([_attempt("stuck"), _attempt("incomplete", goal_achieved=True)])
    assert sig["all_failing"] is False


def test_dispatch_guard_all_stuck_still_arms():
    sig = _signals([_attempt("stuck"), _attempt("error")])
    assert sig["all_failing"] is True


def test_context_block_surfaces_verdict_breakdown():
    r = RecallResult(
        thread=None,
        prior_attempts=[_attempt("done", goal_achieved=False), _attempt("done")],
    )
    block = r.as_context_block()
    assert "goal verdicts: 0 achieved, 1 NOT achieved" in block


# ---------------------------------------------------------------------------
# Read side: evolver outcomes summary prefers the verdict
# ---------------------------------------------------------------------------

def _outcome(goal, status, goal_achieved=None, summary="the summary"):
    return Outcome(
        outcome_id="o-" + goal[:6],
        goal=goal,
        task_type="agenda",
        status=status,
        summary=summary,
        lessons=[],
        goal_achieved=goal_achieved,
    )


def test_evolver_summary_splits_done_by_verdict():
    from evolver import _build_outcomes_summary
    outcomes = [
        _outcome("achieved goal", "done", goal_achieved=True),
        _outcome("failed goal", "done", goal_achieved=False, summary="looked done, was not"),
        _outcome("unjudged goal", "done"),
        _outcome("stuck goal", "stuck"),
    ]
    text = _build_outcomes_summary(outcomes)
    assert "1 verified achieved" in text
    assert "1 goal-NOT-achieved" in text
    assert "1 unjudged" in text
    # Goal-failed runs are surfaced as failure signal for the proposer.
    assert "Completed-but-goal-NOT-achieved summaries" in text
    assert "looked done, was not" in text
    assert "[goal NOT achieved]" in text


def test_evolver_summary_unjudged_only_has_no_failure_section():
    from evolver import _build_outcomes_summary
    text = _build_outcomes_summary([_outcome("unjudged goal", "done")])
    assert "Completed-but-goal-NOT-achieved summaries" not in text
    assert "1 unjudged" in text


# ---------------------------------------------------------------------------
# Read side: strategy evaluator weight prefers the verdict
# ---------------------------------------------------------------------------

def test_strategy_weight_prefers_verdict():
    from strategy_evaluator import _outcome_weight
    assert _outcome_weight(_outcome("g", "done", goal_achieved=False)) == 0.0
    assert _outcome_weight(_outcome("g", "stuck", goal_achieved=True)) == 1.0
    assert _outcome_weight(_outcome("g", "done")) == 1.0       # unjudged → status fallback
    assert _outcome_weight(_outcome("g", "stuck")) == 0.0
