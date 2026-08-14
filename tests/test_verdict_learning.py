"""Tests for SF-2 / data-02: verdict-aware learning (done ≠ achieved).

Write side: outcomes/lessons rows carry the goal-verdict tri-state
(goal_achieved True/False/ABSENT + goal_verdict_source), and the agenda
lane's post-closure annotation stamps the verdict onto the already-written
row via stamp_outcome_verdict(loop_id).

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
    OutcomeVerdictStampResult,
    record_outcome,
    load_outcomes,
    stamp_outcome_verdict,
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
    assert "measurement_class" not in row
    assert "handle_id" not in row


def test_record_outcome_persists_measurement_and_run_dedup_keys(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome(
        "goal", "done", "summary",
        measurement_class="organic", handle_id="handle-1",
    )
    row = _raw_rows()[-1]
    assert row["measurement_class"] == "organic"
    assert row["handle_id"] == "handle-1"


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
    assert stamp_outcome_verdict(
        "lp-2", goal_achieved=False, goal_verdict_source="closure",
        goal_verdict_confidence=0.9,
    ).status == "updated"
    rows = _raw_rows()
    # Newest lp-2 row got the verdict; the older lp-2 row and the other
    # loop's row are untouched.
    assert rows[2]["goal_achieved"] is False
    assert rows[2]["goal_verdict_source"] == "closure"
    assert rows[2]["goal_verdict_confidence"] == pytest.approx(0.9)
    assert "goal_achieved" not in rows[1]
    assert "goal_achieved" not in rows[0]


def test_restamp_preserves_superseded_verdict_on_row(monkeypatch, tmp_path):
    """Re-stamp honesty (Jeremy decree 2026-08-10): flipping a judged
    verdict must keep the superseded one visible on the row itself —
    "note they were failures at run time somewhere"."""
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-flip")
    stamp_outcome_verdict(
        "lp-flip", goal_achieved=False, goal_verdict_source="closure",
        goal_verdict_confidence=0.9,
    )
    stamp_outcome_verdict(
        "lp-flip", goal_achieved=True,
        goal_verdict_source="operator_reverdict",
        goal_verdict_confidence=0.75,
    )
    row = _raw_rows()[-1]
    assert row["goal_achieved"] is True
    assert row["goal_verdict_source"] == "operator_reverdict"
    (hist,) = row["verdict_history"]
    assert hist["goal_achieved"] is False
    assert hist["goal_verdict_source"] == "closure"
    assert hist["goal_verdict_confidence"] == pytest.approx(0.9)
    assert hist["superseded_by"] == "operator_reverdict"
    assert hist["superseded_at"]


def test_first_stamp_writes_no_verdict_history(monkeypatch, tmp_path):
    """The first verdict landing on an unjudged row is not a correction —
    no history entry."""
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-first")
    stamp_outcome_verdict(
        "lp-first", goal_achieved=True, goal_verdict_source="closure",
    )
    row = _raw_rows()[-1]
    assert "verdict_history" not in row


def test_annotate_unverifiable_leaves_goal_achieved_absent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-3")
    assert stamp_outcome_verdict(
        "lp-3", goal_achieved=None, goal_verdict_source="closure_unverifiable",
        goal_verdict_confidence=0.4,
    ).status == "updated"
    row = _raw_rows()[-1]
    # Unjudged stays absent — closure_unverifiable is not a failure verdict.
    assert "goal_achieved" not in row
    assert row["goal_verdict_source"] == "closure_unverifiable"


def test_annotate_stamps_goal_verdict_at(monkeypatch, tmp_path):
    # Chunk B (2026-07-31): without a stamp time the framing→verdict delay
    # is unmeasurable. Unverifiable stamps get it too — "judged, could not
    # verify" is itself a verdict event.
    _setup(monkeypatch, tmp_path)
    from datetime import datetime
    record_outcome("goal a", "done", "s", loop_id="lp-at1")
    record_outcome("goal b", "done", "s", loop_id="lp-at2")
    stamp_outcome_verdict("lp-at1", goal_achieved=True, goal_verdict_source="closure")
    stamp_outcome_verdict("lp-at2", goal_achieved=None,
                          goal_verdict_source="closure_unverifiable")
    rows = {r["loop_id"]: r for r in _raw_rows()}
    for lid in ("lp-at1", "lp-at2"):
        stamped = datetime.fromisoformat(rows[lid]["goal_verdict_at"])
        recorded = datetime.fromisoformat(rows[lid]["recorded_at"])
        assert stamped.tzinfo is not None
        assert stamped >= recorded


def test_annotate_none_preserves_existing_false(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-4")
    # Provenance guard stamps a deterministic False ...
    stamp_outcome_verdict("lp-4", goal_achieved=False, goal_verdict_source="provenance")
    # ... then an unverifiable closure verdict must not erase it.
    stamp_outcome_verdict("lp-4", goal_achieved=None, goal_verdict_source="closure_unverifiable")
    row = _raw_rows()[-1]
    assert row["goal_achieved"] is False
    assert row["goal_verdict_source"] == "closure_unverifiable"


def test_annotate_unknown_loop_returns_false(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-5")
    before = _raw_rows()
    assert stamp_outcome_verdict(
        "lp-nope", goal_achieved=True, goal_verdict_source="closure",
    ).status == "missing"
    assert _raw_rows() == before


def test_annotate_empty_loop_id_is_noop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s")
    assert stamp_outcome_verdict(
        "", goal_achieved=True, goal_verdict_source="closure",
    ).status == "missing"


def test_typed_stamp_distinguishes_updated_and_missing(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-typed")

    updated = stamp_outcome_verdict(
        "lp-typed", goal_achieved=False, goal_verdict_source="closure")
    missing = stamp_outcome_verdict(
        "lp-absent", goal_achieved=False, goal_verdict_source="closure")

    assert updated == OutcomeVerdictStampResult("updated", attempts=1)
    assert missing == OutcomeVerdictStampResult("missing", attempts=1)


def test_typed_stamp_forbids_ambiguous_boolean_coercion():
    with pytest.raises(TypeError, match="inspect .status"):
        bool(OutcomeVerdictStampResult("write_failed", attempts=1))


def test_typed_stamp_missing_file_does_not_create_ledger(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = stamp_outcome_verdict(
        "lp-absent", goal_achieved=False, goal_verdict_source="closure")

    assert result == OutcomeVerdictStampResult("missing", attempts=1)
    assert not _outcomes_path().exists()


def test_typed_stamp_retries_oserror_then_converges(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-retry")
    from file_lock import atomic_write as real_atomic_write

    calls = 0

    def _flaky_atomic_write(path, content, encoding="utf-8"):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient lock failure")
        return real_atomic_write(path, content, encoding=encoding)

    monkeypatch.setattr("file_lock.atomic_write", _flaky_atomic_write)
    result = stamp_outcome_verdict(
        "lp-retry", goal_achieved=False, goal_verdict_source="closure",
        max_attempts=2,
    )

    assert result == OutcomeVerdictStampResult("updated", attempts=2)
    assert calls == 2


def test_typed_stamp_reports_bounded_write_failure(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "s", loop_id="lp-failed")

    def _failed_atomic_write(path, content, encoding="utf-8"):
        raise OSError("ledger unavailable")

    monkeypatch.setattr("file_lock.atomic_write", _failed_atomic_write)
    result = stamp_outcome_verdict(
        "lp-failed", goal_achieved=False, goal_verdict_source="closure",
        max_attempts=2,
    )

    assert result == OutcomeVerdictStampResult(
        "write_failed", attempts=2, error="ledger unavailable")


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
    stamp_outcome_verdict("lp-d1", goal_achieved=False, goal_verdict_source="closure")
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
    stamp_outcome_verdict("lp-d2", goal_achieved=True, goal_verdict_source="closure")
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


def test_deferred_adapter_failure_is_strict_by_default(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import memory
    reflect_and_record("goal", "done", "s", dry_run=True, loop_id="lp-adapter",
                       defer_lessons=True)

    class BrokenAdapter:
        def complete(self, messages, **kwargs):
            raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        memory.extract_deferred_lessons(
            "lp-adapter", adapter=BrokenAdapter(), dry_run=False)

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
    stamp_outcome_verdict("lp-d4", goal_achieved=False, goal_verdict_source="closure")
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
    stamp_outcome_verdict("lp-d5", goal_achieved=True, goal_verdict_source="closure")
    loop_finalize.finalize_deferred_learning(_loop_result("lp-d5"))
    record_outcome("the goal", "done", "s", loop_id="lp-d6")  # unjudged
    loop_finalize.finalize_deferred_learning(_loop_result("lp-d6"))
    # True verdict and unjudged both crystallize (unjudged = pre-fix behavior).
    assert len(calls) == 2


def test_finalize_deferred_learning_skips_skills_on_directional_true(monkeypatch, tmp_path):
    """MH #1 reach (adversarial review 2026-08-10): a pass-audit-capped
    verdict (True at conf 0.6, below VERDICT_CONFIDENCE_FLOOR) is
    directional trust — it must not crystallize "strongest example" skills.
    An explicit high-confidence True still does."""
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    calls = []
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize",
                        lambda **kw: calls.append(kw))
    record_outcome("the goal", "done", "s", loop_id="lp-d9")
    stamp_outcome_verdict("lp-d9", goal_achieved=True,
                          goal_verdict_source="closure",
                          goal_verdict_confidence=0.6)
    loop_finalize.finalize_deferred_learning(_loop_result("lp-d9"))
    assert calls == []
    record_outcome("the goal", "done", "s", loop_id="lp-d10")
    stamp_outcome_verdict("lp-d10", goal_achieved=True,
                          goal_verdict_source="closure",
                          goal_verdict_confidence=0.9)
    loop_finalize.finalize_deferred_learning(_loop_result("lp-d10"))
    assert len(calls) == 1


def test_finalize_deferred_learning_extracts_for_extra_loop_ids(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize", lambda **kw: None)
    # A restarted handle: attempt 1 deferred, superseded; attempt 2 final.
    record_outcome("try 1", "done", "s", lessons=[], loop_id="lp-d7a")
    stamp_outcome_verdict("lp-d7a", goal_achieved=False, goal_verdict_source="closure")
    record_outcome("try 2", "done", "s", lessons=[], loop_id="lp-d7b")
    stamp_outcome_verdict("lp-d7b", goal_achieved=True, goal_verdict_source="closure")
    loop_finalize.finalize_deferred_learning(
        _loop_result("lp-d7b"), dry_run=True, extra_loop_ids=["lp-d7a"],
    )
    rows = {r["loop_id"]: r for r in _raw_rows()}
    assert "failed" in rows["lp-d7a"]["lessons"][0]
    assert "succeeded" in rows["lp-d7b"]["lessons"][0]


def test_finalize_deferred_learning_skips_skills_for_unstamped_loop(monkeypatch, tmp_path):
    """EXT-AUDIT-2 residual: a loop whose verdict stamp failed must be
    quarantined out of skill crystallization even though the on-disk row
    still reads back as unjudged (the pre-fix permissive case)."""
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    calls = []
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize",
                        lambda **kw: calls.append(kw))
    record_outcome("the goal", "done", "s", loop_id="lp-d8")  # unjudged on disk
    loop_finalize.finalize_deferred_learning(
        _loop_result("lp-d8"), unstamped_loop_ids={"lp-d8"},
    )
    assert calls == []


def test_finalize_deferred_learning_skips_lessons_for_unstamped_loop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    import memory
    extract_calls = []
    monkeypatch.setattr(memory, "extract_deferred_lessons",
                        lambda *a, **kw: extract_calls.append(a))
    record_outcome("the goal", "done", "s", loop_id="lp-d9")
    loop_finalize.finalize_deferred_learning(
        _loop_result("lp-d9"), dry_run=True, unstamped_loop_ids=["lp-d9"],
    )
    assert extract_calls == []


def test_finalize_deferred_learning_unstamped_only_quarantines_named_loop(monkeypatch, tmp_path):
    """extra_loop_ids still get extraction when they aren't the unstamped one —
    quarantine is per-loop_id, not all-or-nothing for the handle."""
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize", lambda **kw: None)
    record_outcome("try 1", "done", "s", lessons=[], loop_id="lp-d10a")
    stamp_outcome_verdict("lp-d10a", goal_achieved=False, goal_verdict_source="closure")
    record_outcome("try 2", "done", "s", lessons=[], loop_id="lp-d10b")
    loop_finalize.finalize_deferred_learning(
        _loop_result("lp-d10b"), dry_run=True,
        extra_loop_ids=["lp-d10a"], unstamped_loop_ids={"lp-d10b"},
    )
    rows = {r["loop_id"]: r for r in _raw_rows()}
    assert "failed" in rows["lp-d10a"]["lessons"][0]
    assert "lessons" not in rows["lp-d10b"] or not rows["lp-d10b"]["lessons"]


def test_finalize_deferred_learning_quarantines_only_named_loop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize", lambda **kw: None)
    record_outcome("safe earlier", "done", "s", lessons=[], loop_id="lp-safe",
                   lesson_extraction_status="deferred")
    stamp_outcome_verdict(
        "lp-safe", goal_achieved=False, goal_verdict_source="closure")
    record_outcome("audit failed", "done", "s", lessons=[], loop_id="lp-held",
                   lesson_extraction_status="deferred")

    loop_finalize.finalize_deferred_learning(
        _loop_result("lp-held"), dry_run=True, extra_loop_ids=["lp-safe"],
        skip_loop_ids=["lp-held"],
    )

    rows = {r["loop_id"]: r for r in _raw_rows()}
    assert rows["lp-safe"]["lesson_extraction_status"] == "completed"
    assert "failed" in rows["lp-safe"]["lessons"][0]
    assert rows["lp-held"]["lesson_extraction_status"] == "deferred"
    assert rows["lp-held"]["lessons"] == []


# ---------------------------------------------------------------------------
# Risk minting (Jeremy ruling 2026-08-10): loop-discovered risks/unknowns
# belong in the project RISKS.md record.
# ---------------------------------------------------------------------------

def _seed_run_dir(monkeypatch, tmp_path, loop_id, *, gaps=None, skipped="",
                  scope_failed=False):
    import runs as runs_module
    rd = tmp_path / "runs" / f"run-{loop_id}"
    (rd / "build").mkdir(parents=True, exist_ok=True)
    if gaps is not None or skipped:
        row = {"loop_id": loop_id, "gaps": gaps or []}
        if skipped:
            row["skipped"] = skipped
        (rd / "build" / "closure_verdicts.jsonl").write_text(
            json.dumps(row) + "\n", encoding="utf-8")
    if scope_failed:
        (rd / "build" / "scope-raw-FAILED.txt").write_text("raw", encoding="utf-8")
    monkeypatch.setattr(runs_module, "resolve_run_dir", lambda ref: rd)
    return rd


def test_risk_mint_writes_gaps_and_scope_failure(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    from orch_items import ensure_project, risks_path
    ensure_project("proj-r1", "mission")
    _seed_run_dir(monkeypatch, tmp_path, "lp-r1",
                  gaps=["missing citations", "no honesty section"],
                  scope_failed=True)
    n = loop_finalize._mint_run_risks_to_project("proj-r1", "lp-r1")
    assert n == 3
    text = risks_path("proj-r1").read_text(encoding="utf-8")
    assert "Open gap from run lp-r1 (closure): missing citations" in text
    assert "no honesty section" in text
    assert "without scope injection" in text


def test_risk_mint_caps_total_at_three(monkeypatch, tmp_path):
    """Adversarial review 2026-08-10: three gaps + the scope sentinel made
    four lines. <=3 RISK lines; the sentinel outranks the trailing gap.
    Round-15 update: the selection is computed FIRST and one annotation
    line announces exactly what was dropped (the earlier note-then-cap
    order capped the note WITH the gaps and made it a lie)."""
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    from orch_items import ensure_project, risks_path
    ensure_project("proj-r6", "mission")
    _seed_run_dir(monkeypatch, tmp_path, "lp-r6",
                  gaps=["gap one", "gap two", "gap three"],
                  scope_failed=True)
    n = loop_finalize._mint_run_risks_to_project("proj-r6", "lp-r6")
    assert n == 4  # 3 risk lines + 1 omission annotation
    text = risks_path("proj-r6").read_text(encoding="utf-8")
    assert "gap one" in text and "gap two" in text
    assert "gap three" not in text
    assert "1 more closure gap(s) from run lp-r6 not minted" in text
    assert "without scope injection" in text


def test_risk_mint_dedupe_is_atomic_under_the_lock(monkeypatch, tmp_path):
    """TOCTOU pin (adversarial review 2026-08-10): even when the caller-side
    pre-check races (both callers observe absence), the in-lock dedupe_token
    check makes the second append a no-op."""
    _setup(monkeypatch, tmp_path)
    from orch_items import ensure_project, append_risk, risks_path
    ensure_project("proj-r7", "mission")
    assert append_risk("proj-r7", ["risk from lp-r7"],
                       dedupe_token="lp-r7") is True
    # Round-2 pin: the race loser reports the no-op honestly.
    assert append_risk("proj-r7", ["risk from lp-r7 duplicate"],
                       dedupe_token="lp-r7") is False
    text = risks_path("proj-r7").read_text(encoding="utf-8")
    assert text.count("lp-r7") == 1
    assert "duplicate" not in text


def test_risk_mint_flattens_multiline_gaps(monkeypatch, tmp_path):
    """Round-2 pin (2026-08-10): an LLM gap with embedded newlines must
    render as one bullet, not break the markdown or the <=3 cap."""
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    from orch_items import ensure_project, risks_path
    ensure_project("proj-r8", "mission")
    _seed_run_dir(monkeypatch, tmp_path, "lp-r8",
                  gaps=["missing A\nmissing B\nmissing C"])
    n = loop_finalize._mint_run_risks_to_project("proj-r8", "lp-r8")
    assert n == 1
    text = risks_path("proj-r8").read_text(encoding="utf-8")
    assert "missing A missing B missing C" in text


def test_risk_mint_is_idempotent_per_loop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    from orch_items import ensure_project, risks_path
    ensure_project("proj-r2", "mission")
    _seed_run_dir(monkeypatch, tmp_path, "lp-r2", gaps=["gap one"])
    assert loop_finalize._mint_run_risks_to_project("proj-r2", "lp-r2") == 1
    assert loop_finalize._mint_run_risks_to_project("proj-r2", "lp-r2") == 0
    assert risks_path("proj-r2").read_text(encoding="utf-8").count("gap one") == 1


def test_risk_mint_ignores_skipped_verdict_rows(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    from orch_items import ensure_project, risks_path
    ensure_project("proj-r3", "mission")
    _seed_run_dir(monkeypatch, tmp_path, "lp-r3",
                  gaps=["phantom"], skipped="no_checks_generated")
    assert loop_finalize._mint_run_risks_to_project("proj-r3", "lp-r3") == 0
    assert not risks_path("proj-r3").exists()


def test_risk_mint_killswitch_off(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    import config as _cfg
    from orch_items import ensure_project, risks_path
    ensure_project("proj-r4", "mission")
    _seed_run_dir(monkeypatch, tmp_path, "lp-r4", gaps=["gap"])
    _orig = _cfg.get
    monkeypatch.setattr(
        _cfg, "get",
        lambda key, default=None: False if key == "project.risk_mint"
        else _orig(key, default))
    assert loop_finalize._mint_run_risks_to_project("proj-r4", "lp-r4") == 0
    assert not risks_path("proj-r4").exists()


def test_risk_mint_no_run_dir_degrades(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    import runs as runs_module
    monkeypatch.setattr(runs_module, "resolve_run_dir", lambda ref: None)
    assert loop_finalize._mint_run_risks_to_project("proj-r5", "lp-r5") == 0


def test_finalize_deferred_learning_mints_risks(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize",
                        lambda **kw: None)
    minted = []
    monkeypatch.setattr(loop_finalize, "_mint_run_risks_to_project",
                        lambda project, loop_id: minted.append((project, loop_id)))
    record_outcome("the goal", "done", "s", loop_id="lp-r6")
    stamp_outcome_verdict("lp-r6", goal_achieved=True, goal_verdict_source="closure")
    loop_finalize.finalize_deferred_learning(_loop_result("lp-r6"), dry_run=True)
    assert minted == [("p", "lp-r6")]


def test_finalize_deferred_learning_skips_risk_mint_for_audited_loop(monkeypatch, tmp_path):
    """A loop held back by an unresolved verdict audit must not mint risks
    from its disputed verdict."""
    _setup(monkeypatch, tmp_path)
    import loop_finalize
    monkeypatch.setattr(loop_finalize, "_crystallize_and_synthesize",
                        lambda **kw: None)
    minted = []
    monkeypatch.setattr(loop_finalize, "_mint_run_risks_to_project",
                        lambda project, loop_id: minted.append(loop_id))
    record_outcome("the goal", "done", "s", loop_id="lp-r7")
    loop_finalize.finalize_deferred_learning(
        _loop_result("lp-r7"), dry_run=True, skip_loop_ids=["lp-r7"])
    assert minted == []
