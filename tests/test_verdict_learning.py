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


def _extraction_events():
    path = _memory_dir() / "captains_log.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event_type") == "LESSON_EXTRACTION"
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


# ---------------------------------------------------------------------------
# data-r2-01: deferred (post-closure) lesson extraction + skill crystallization
# ---------------------------------------------------------------------------

def test_reflect_defer_lessons_records_row_without_lessons(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # dry_run normally produces a stub lesson — deferral must skip even that.
    reflect_and_record("goal", "done", "s", dry_run=True, loop_id="lp-d0",
                       defer_lessons=True)
    row = _raw_rows()[-1]
    assert row["lessons"] == []
    assert row["loop_id"] == "lp-d0"
    assert row["dry_run"] is True
    assert row["lesson_extraction_status"] == "deferred"
    event = _extraction_events()[-1]
    assert event["context"]["status"] == "deferred"
    assert event["context"]["outcome_id"] == row["outcome_id"]


def test_reflect_defer_without_loop_id_extracts_immediately(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # No join key = the deferred pass could never find the row — fall back to
    # extracting now (verdict-blind beats losing the lessons entirely).
    reflect_and_record("goal", "done", "s", dry_run=True, defer_lessons=True)
    row = _raw_rows()[-1]
    assert row["lessons"], "fallback should have extracted the stub lesson"


def test_extract_deferred_lessons_failure_flavored_after_false_verdict(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from memory import extract_deferred_lessons
    reflect_and_record("ship the report", "done", "s", dry_run=True,
                       loop_id="lp-d1", defer_lessons=True)
    # Closure judges AFTER finalize — stamp a False verdict, then extract.
    annotate_outcome_verdict("lp-d1", goal_achieved=False, goal_verdict_source="closure")
    n = extract_deferred_lessons("lp-d1", dry_run=True)
    assert n == 1
    row = _raw_rows()[-1]
    # The dry-run stub is verdict-aware: done + goal_achieved False = failed.
    assert "failed" in row["lessons"][0]
    # Legacy lesson row carries the verdict it was extracted under.
    lesson_rows = [
        json.loads(l)
        for l in _lessons_path().read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert lesson_rows[-1]["goal_achieved"] is False
    assert lesson_rows[-1]["goal_verdict_source"] == "closure"


def test_extract_deferred_lessons_success_flavored_after_true_verdict(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from memory import extract_deferred_lessons
    reflect_and_record("ship the report", "done", "s", dry_run=True,
                       loop_id="lp-d2", defer_lessons=True)
    annotate_outcome_verdict("lp-d2", goal_achieved=True, goal_verdict_source="closure")
    assert extract_deferred_lessons("lp-d2", dry_run=True) == 1
    row = _raw_rows()[-1]
    assert "succeeded" in row["lessons"][0]
    assert row["lesson_extraction_status"] == "completed"
    assert row["lesson_extraction_count"] == 1
    assert [event["context"]["status"] for event in _extraction_events()] == [
        "deferred", "completed"
    ]
    assert _extraction_events()[-1]["context"]["extracted_count"] == 1


def test_extract_deferred_lessons_failure_is_observable(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import memory
    reflect_and_record("goal", "done", "s", dry_run=True, loop_id="lp-fail",
                       defer_lessons=True)
    monkeypatch.setattr(memory, "extract_lessons_via_llm", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("adapter down")))

    with pytest.raises(RuntimeError, match="adapter down"):
        memory.extract_deferred_lessons("lp-fail", dry_run=True)

    event = _extraction_events()[-1]
    assert event["context"]["status"] == "failed"
    assert event["context"]["error"] == "adapter down"
    assert _raw_rows()[-1]["lesson_extraction_status"] == "failed"


def test_completed_zero_deferred_extraction_is_durably_idempotent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import memory
    reflect_and_record("goal", "done", "s", dry_run=True, loop_id="lp-zero",
                       defer_lessons=True)
    calls = {"count": 0}

    def _zero(*args, **kwargs):
        calls["count"] += 1
        return []

    monkeypatch.setattr(memory, "extract_lessons_via_llm", _zero)
    assert memory.extract_deferred_lessons("lp-zero", dry_run=False) == 0
    assert memory.extract_deferred_lessons("lp-zero", dry_run=False) == 0
    assert calls["count"] == 1
    row = _raw_rows()[-1]
    assert row["lessons"] == []
    assert row["lesson_extraction_status"] == "completed"
    assert row["lesson_extraction_count"] == 0


def test_deferred_stamp_failure_never_emits_completed(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import memory
    import memory_ledger
    reflect_and_record("goal", "done", "s", dry_run=True, loop_id="lp-stamp",
                       defer_lessons=True)
    monkeypatch.setattr(memory, "extract_lessons_via_llm", lambda *a, **kw: [("real lesson", "execution")])
    monkeypatch.setattr(memory_ledger, "annotate_outcome_lessons", lambda *a, **kw: False)

    with pytest.raises(RuntimeError, match="could not persist extracted lessons"):
        memory.extract_deferred_lessons("lp-stamp", dry_run=False)

    assert _extraction_events()[-1]["context"]["status"] == "failed"
    assert all(
        event["context"]["status"] != "completed"
        for event in _extraction_events()
    )


def test_extract_deferred_lessons_idempotent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from memory import extract_deferred_lessons
    reflect_and_record("goal", "done", "s", dry_run=True, loop_id="lp-d3",
                       defer_lessons=True)
    assert extract_deferred_lessons("lp-d3", dry_run=True) == 1
    before = _raw_rows()
    # A row that already has lessons (this one, or any non-deferred row) is
    # left alone — no double extraction, no double recording.
    assert extract_deferred_lessons("lp-d3", dry_run=True) == 0
    assert _raw_rows() == before


def test_extract_deferred_lessons_unknown_loop_is_noop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from memory import extract_deferred_lessons
    assert extract_deferred_lessons("lp-nope", dry_run=True) == 0


def _loop_result(loop_id, status="done"):
    from loop_types import LoopResult, StepOutcome
    return LoopResult(
        loop_id=loop_id, project="p", goal="the goal", status=status,
        steps=[StepOutcome(index=1, text="step", status="done",
                           result="did it", iteration=1)],
    )


def test_finalize_deferred_learning_skips_skills_on_false_verdict(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    calls = []
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize",
                        lambda **kw: calls.append(kw))
    record_outcome("the goal", "done", "s", loop_id="lp-d4")
    annotate_outcome_verdict("lp-d4", goal_achieved=False, goal_verdict_source="closure")
    loop_finalize.finalize_deferred_learning(_loop_result("lp-d4"))
    # Judged not-achieved: the run's pattern must NOT enter the skill library.
    assert calls == []


def test_finalize_deferred_learning_crystallizes_on_true_or_unjudged(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    calls = []
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize",
                        lambda **kw: calls.append(kw))
    record_outcome("the goal", "done", "s", loop_id="lp-d5")
    annotate_outcome_verdict("lp-d5", goal_achieved=True, goal_verdict_source="closure")
    loop_finalize.finalize_deferred_learning(_loop_result("lp-d5"))
    record_outcome("the goal", "done", "s", loop_id="lp-d6")  # unjudged
    loop_finalize.finalize_deferred_learning(_loop_result("lp-d6"))
    # True verdict and unjudged both crystallize (unjudged = pre-fix behavior).
    assert len(calls) == 2


def test_finalize_deferred_learning_extracts_for_extra_loop_ids(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize", lambda **kw: None)
    # A restarted handle: attempt 1 deferred, superseded; attempt 2 final.
    record_outcome("try 1", "done", "s", lessons=[], loop_id="lp-d7a")
    annotate_outcome_verdict("lp-d7a", goal_achieved=False, goal_verdict_source="closure")
    record_outcome("try 2", "done", "s", lessons=[], loop_id="lp-d7b")
    annotate_outcome_verdict("lp-d7b", goal_achieved=True, goal_verdict_source="closure")
    loop_finalize.finalize_deferred_learning(
        _loop_result("lp-d7b"), dry_run=True, extra_loop_ids=["lp-d7a"],
    )
    rows = {r["loop_id"]: r for r in _raw_rows()}
    assert "failed" in rows["lp-d7a"]["lessons"][0]
    assert "succeeded" in rows["lp-d7b"]["lessons"][0]
