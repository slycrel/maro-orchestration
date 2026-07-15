"""Time-blindness first slice: `memory.age_stamps` (BACKLOG vehicle, hooks d+a).

Contract under test (mirrors the memory.worker_slice off-path pattern):
- flag OFF ⇒ byte-identical prompts at every stamped seam;
- flag ON but timestamps absent ⇒ byte-identical prompts;
- stamping that actually occurred marks WORKER_SLICE_INJECTED /
  RECALL_PERFORMED with `age_stamped: true`;
- a material (≥10 min) wall-clock gap between steps rides the contribution
  ledger into the next step's prompt — and only then.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from age_stamp import (
    age_stamps_enabled,
    age_suffix,
    format_elapsed,
    parse_stored_ts,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _force_flag(monkeypatch, value):
    """Force memory.age_stamps through config.get — the seam every stamped
    call site resolves lazily (`from config import get` at call time)."""
    import config as _cfg

    _orig = _cfg.get

    def _patched(key, default=None):
        if key == "memory.age_stamps":
            return value
        return _orig(key, default)

    monkeypatch.setattr(_cfg, "get", _patched)


_NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# age_stamp helpers
# ---------------------------------------------------------------------------

class TestAgeHelpers:
    def test_format_elapsed_buckets(self):
        assert format_elapsed(30) == "under a minute"
        assert format_elapsed(60) == "1 minute"
        assert format_elapsed(42 * 60) == "42 minutes"
        assert format_elapsed(3600) == "1 hour"
        assert format_elapsed(5 * 3600) == "5 hours"
        assert format_elapsed(86400) == "1 day"
        assert format_elapsed(29 * 86400) == "29 days"
        assert format_elapsed(30 * 86400) == "1 month"
        assert format_elapsed(151 * 86400) == "5 months"

    def test_format_elapsed_clamps_negative(self):
        # format_elapsed keeps the clamp: its only negative-capable caller is
        # age_suffix, which now short-circuits future stamps before formatting.
        assert format_elapsed(-5) == "under a minute"

    def test_age_suffix_future_timestamp_renders_date_without_age(self):
        # F5: a stored timestamp in the future (clock skew, imported row) must
        # not claim an age — "(learned 2026-12-25 — under a minute ago)" would
        # be a fabricated claim. Date only.
        s = age_suffix("2026-12-25T00:00:00Z", now=_NOW)
        assert s == " (learned 2026-12-25)"
        assert "ago" not in s

    def test_age_suffix_renders_date_and_coarse_age(self):
        s = age_suffix("2026-02-14T00:00:00Z", now=_NOW)
        assert s == " (learned 2026-02-14 — 5 months ago)"

    def test_age_suffix_singular_unit(self):
        s = age_suffix("2026-07-14T12:00:00+00:00", now=_NOW)
        assert s == " (learned 2026-07-14 — 1 day ago)"

    def test_age_suffix_empty_for_absent_or_garbage(self):
        assert age_suffix("") == ""
        assert age_suffix("not-a-date") == ""
        assert age_suffix(None) == ""

    def test_parse_stored_ts_date_only_and_naive_as_utc(self):
        parsed = parse_stored_ts("2026-02-14")
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.date().isoformat() == "2026-02-14"

    def test_age_stamps_enabled_default_off(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        assert age_stamps_enabled() is False


# ---------------------------------------------------------------------------
# memory_bridge.stamp_items_with_age (worker slice, hook a)
# ---------------------------------------------------------------------------

def _make_item(content, recorded_at=""):
    from memory_port import MemoryItem
    meta = {"recorded_at": recorded_at} if recorded_at else {}
    return MemoryItem(kind="lesson", content=content, meta=meta)


class TestStampItemsWithAge:
    def test_flag_off_returns_input_unchanged(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from memory_bridge import stamp_items_with_age

        items = [_make_item("aged", recorded_at="2026-02-01T00:00:00Z")]
        out, stamped = stamp_items_with_age(items)
        assert out is items
        assert stamped is False
        assert out[0].content == "aged"

    def test_flag_on_without_timestamps_returns_input_unchanged(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        from memory_bridge import stamp_items_with_age

        items = [_make_item("no ts here")]
        out, stamped = stamp_items_with_age(items)
        assert out is items
        assert stamped is False

    def test_flag_on_stamps_only_items_carrying_timestamps(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        from memory_bridge import stamp_items_with_age

        items = [
            _make_item("aged", recorded_at="2026-02-01T00:00:00Z"),
            _make_item("undated"),
        ]
        out, stamped = stamp_items_with_age(items)
        assert stamped is True
        assert re.search(r"^aged \(learned 2026-02-01 — .+ ago\)$",
                         out[0].content)
        assert out[1].content == "undated"
        # Source items are never mutated — stamped copies only.
        assert items[0].content == "aged"


# ---------------------------------------------------------------------------
# Director wiring: worker slice injection + WORKER_SLICE_INJECTED event
# ---------------------------------------------------------------------------

_AGED_LESSON_ROW = {
    "lesson_id": "l1",
    "task_type": "research",
    "outcome": "done",
    "lesson": "Always widget the recall gadget before shipping.",
    "source_goal": "g1",
    "confidence": 0.9,
    "times_applied": 1,
    "times_reinforced": 0,
    "recorded_at": "2026-02-01T00:00:00Z",
}


def _write_lesson_row(row):
    import config as _cfg
    mem_dir = _cfg.memory_dir()
    (mem_dir / "lessons.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8")


def _run_director_capturing(monkeypatch):
    """run_director with dispatch-context + captains-log spies (the
    TestWorkerSliceExperiment pattern)."""
    import director as _director_mod
    from director import run_director

    captured = {}
    orig_dispatch = _director_mod.dispatch_worker

    def _spy_dispatch(worker_type, task, *, context="", **kw):
        captured["context"] = context
        return orig_dispatch(worker_type, task, context=context, **kw)

    monkeypatch.setattr(_director_mod, "dispatch_worker", _spy_dispatch)

    events = []
    orig_log_event = _director_mod.log_event

    def _spy_log_event(event_type, *a, **kw):
        events.append((event_type, kw.get("context")))
        return orig_log_event(event_type, *a, **kw)

    monkeypatch.setattr(_director_mod, "log_event", _spy_log_event)

    result = run_director("widget the recall gadget", dry_run=True)
    return result, captured, events


class TestWorkerSliceAgeStamps:
    def test_age_off_context_unstamped_and_event_unmarked(
            self, monkeypatch, tmp_path):
        """Default flag (off): a timestamped lesson injects without any age
        suffix, and the event carries no age_stamped field."""
        _setup(monkeypatch, tmp_path)
        _write_lesson_row(_AGED_LESSON_ROW)

        result, captured, events = _run_director_capturing(monkeypatch)

        assert any(r.memory_slice_injected for r in result.worker_results)
        assert "(learned" not in captured["context"]
        slice_events = [ctx for et, ctx in events
                        if et == "WORKER_SLICE_INJECTED"]
        assert slice_events
        assert "age_stamped" not in slice_events[0]

    def test_age_on_stamps_context_and_marks_event(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        _write_lesson_row(_AGED_LESSON_ROW)

        result, captured, events = _run_director_capturing(monkeypatch)

        assert any(r.memory_slice_injected for r in result.worker_results)
        assert re.search(r"\(learned 2026-02-01 — .+ ago\)",
                         captured["context"])
        slice_events = [ctx for et, ctx in events
                        if et == "WORKER_SLICE_INJECTED"]
        assert slice_events
        assert slice_events[0].get("age_stamped") is True

    def test_age_on_without_timestamps_byte_identical(
            self, monkeypatch, tmp_path):
        """Flag ON but the stored lesson carries no recorded_at: the injected
        context is byte-identical to the flag-OFF run, and no event marks."""
        _setup(monkeypatch, tmp_path)
        row = dict(_AGED_LESSON_ROW)
        del row["recorded_at"]
        _write_lesson_row(row)

        import config as _cfg
        _orig = _cfg.get
        state = {"on": True}

        def _patched(key, default=None):
            if key == "memory.age_stamps":
                return state["on"]
            return _orig(key, default)

        monkeypatch.setattr(_cfg, "get", _patched)

        _, captured_on, events_on = _run_director_capturing(monkeypatch)
        state["on"] = False
        _, captured_off, _ = _run_director_capturing(monkeypatch)

        assert captured_on["context"] == captured_off["context"]
        assert "(learned" not in captured_on["context"]
        slice_events = [ctx for et, ctx in events_on
                        if et == "WORKER_SLICE_INJECTED"]
        assert slice_events
        assert "age_stamped" not in slice_events[0]


# ---------------------------------------------------------------------------
# recall() loop slice (feeds decompose) + RECALL_PERFORMED event
# ---------------------------------------------------------------------------

class _AgedLesson:
    outcome = "done"
    lesson = "the aged lesson"
    recorded_at = "2026-02-01T00:00:00+00:00"


_LEGACY_LESSON_BLOCK = "## Lessons from Prior Runs (apply these)\n- ✓ the aged lesson"


def _toggleable_flag(monkeypatch):
    """Patch config.get so memory.age_stamps follows state["on"] — lets one
    test flip the flag between otherwise-identical runs."""
    import config as _cfg

    _orig = _cfg.get
    state = {"on": True}

    def _patched(key, default=None):
        if key == "memory.age_stamps":
            return state["on"]
        return _orig(key, default)

    monkeypatch.setattr(_cfg, "get", _patched)
    return state


def _recall_capturing(monkeypatch, lesson_obj):
    from unittest.mock import patch
    import memory
    from recall import recall

    monkeypatch.setattr(memory, "load_lessons", lambda **kw: [lesson_obj])
    events = []
    with patch("captains_log.log_event",
               side_effect=lambda et, **kw: events.append((et, kw))):
        r = recall("any goal", slice="loop")
    performed = [kw for et, kw in events if et == "RECALL_PERFORMED"]
    assert len(performed) == 1
    return r, performed[0]["context"]


class TestRecallLoopSliceAgeStamps:
    def test_age_on_stamps_lessons_and_marks_event(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        r, event_ctx = _recall_capturing(monkeypatch, _AgedLesson())
        assert re.search(
            r"- ✓ the aged lesson \(learned 2026-02-01 — .+ ago\)$",
            r.lessons)
        assert event_ctx.get("age_stamped") is True

    def test_age_off_renders_legacy_bytes(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        r, event_ctx = _recall_capturing(monkeypatch, _AgedLesson())
        assert r.lessons == _LEGACY_LESSON_BLOCK
        assert "age_stamped" not in event_ctx

    def test_age_on_legacy_row_without_ts_byte_identical(
            self, monkeypatch, tmp_path):
        """F1, recall seam, REAL loader: a stored lessons row with NO
        recorded_at key must render byte-identically flag-ON vs flag-OFF.
        Pre-fix, Lesson.recorded_at's default_factory fabricated a load-time
        date → "(learned today — under a minute ago)"."""
        from unittest.mock import patch as _patch
        from recall import recall

        _setup(monkeypatch, tmp_path)
        # Lesson text shares words with the goal so the query-ranked
        # load_lessons path returns it.
        row = dict(_AGED_LESSON_ROW)
        row["task_type"] = "agenda"
        del row["recorded_at"]
        _write_lesson_row(row)
        state = _toggleable_flag(monkeypatch)

        def _run():
            events = []
            with _patch("captains_log.log_event",
                        side_effect=lambda et, **kw: events.append((et, kw))):
                r = recall("widget the recall gadget", slice="loop")
            performed = [kw for et, kw in events if et == "RECALL_PERFORMED"]
            assert len(performed) == 1
            return r, performed[0]["context"]

        r_on, ctx_on = _run()
        state["on"] = False
        r_off, _ = _run()

        assert r_on.lessons  # the lesson itself still injects
        assert r_on.lessons == r_off.lessons
        assert "(learned" not in r_on.lessons
        assert "age_stamped" not in ctx_on


# ---------------------------------------------------------------------------
# memory.inject_lessons_for_task (recall()'s fallback + direct callers)
# ---------------------------------------------------------------------------

class TestInjectLessonsForTaskAgeStamps:
    def test_age_off_renders_legacy_bytes(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _write_lesson_row(_AGED_LESSON_ROW)
        from memory import inject_lessons_for_task

        result = inject_lessons_for_task("research", "any goal")
        assert result == ("## Lessons from Prior Runs (apply these)\n"
                          "- ✓ Always widget the recall gadget before shipping.")

    def test_age_on_appends_suffix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        _write_lesson_row(_AGED_LESSON_ROW)
        from memory import inject_lessons_for_task

        result = inject_lessons_for_task("research", "any goal")
        assert re.search(
            r"- ✓ Always widget the recall gadget before shipping\. "
            r"\(learned 2026-02-01 — .+ ago\)$",
            result)

    def test_age_on_legacy_row_without_ts_byte_identical(
            self, monkeypatch, tmp_path):
        """F1, inject seam, REAL loader (probe_legacy_row_no_ts): a stored
        row with NO recorded_at key loads unstamped ("") instead of the
        default_factory fabricating a load-time date, so flag-ON output is
        byte-identical to flag-OFF."""
        _setup(monkeypatch, tmp_path)
        row = dict(_AGED_LESSON_ROW)
        del row["recorded_at"]
        _write_lesson_row(row)
        state = _toggleable_flag(monkeypatch)
        from memory import inject_lessons_for_task

        with_flag = inject_lessons_for_task("research", "any goal")
        state["on"] = False
        without_flag = inject_lessons_for_task("research", "any goal")

        assert with_flag == without_flag
        assert "(learned" not in with_flag
        assert "Always widget the recall gadget" in with_flag

    def test_load_lessons_preserves_absent_recorded_at_as_empty(
            self, monkeypatch, tmp_path):
        """F1 at the loader itself: absence stays "" — never a load-time
        now() (the rewrite paths persist whatever load produced)."""
        _setup(monkeypatch, tmp_path)
        row = dict(_AGED_LESSON_ROW)
        del row["recorded_at"]
        _write_lesson_row(row)
        from memory import load_lessons

        lessons = load_lessons(task_type="research")
        assert len(lessons) == 1
        assert lessons[0].recorded_at == ""
        assert parse_stored_ts(lessons[0].recorded_at) is None

    def test_newly_stored_lesson_still_gets_real_recorded_at(
            self, monkeypatch, tmp_path):
        """Write path unchanged: a genuinely new lesson is stamped at
        creation with a parseable UTC timestamp."""
        _setup(monkeypatch, tmp_path)
        from memory_ledger import _store_lesson
        from memory import load_lessons

        _store_lesson("research", "done",
                      "Fresh lesson from this very run.", "goal-x")
        lessons = load_lessons(task_type="research")
        assert len(lessons) == 1
        assert parse_stored_ts(lessons[0].recorded_at) is not None


# ---------------------------------------------------------------------------
# Elapsed-time step contribution (hook d) — rides the contribution ledger
# ---------------------------------------------------------------------------

class _FakeClock:
    """Stands in for loop_execute's `time` module (monotonic only — the
    module's sole use)."""

    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now


def _run_gap_loop(monkeypatch, tmp_path, *, gap_s):
    """Three-step loop with the clock jumped by gap_s after step 1;
    returns the captured per-step executor prompts."""
    _setup(monkeypatch, tmp_path)
    import loop_execute
    from agent_loop import run_agent_loop, _DryRunAdapter
    from llm import LLMResponse

    clock = _FakeClock()
    monkeypatch.setattr(loop_execute, "time", clock)

    class _CaptureAdapter(_DryRunAdapter):
        def __init__(self):
            self.exec_user_msgs = []

        def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"), "")
            if ("decompose" in user_content.lower()
                    or "concrete steps" in user_content.lower()):
                steps = [f"Perform part {i} of the work" for i in (1, 2, 3)]
                return LLMResponse(
                    content=json.dumps(steps), stop_reason="end_turn",
                    input_tokens=10, output_tokens=10,
                )
            if tools and tool_choice == "required":
                self.exec_user_msgs.append(user_content)
            return super().complete(
                messages, tools=tools, tool_choice=tool_choice, **kwargs)

    def _advance_after_step_1(step_num, step_text, summary, status):
        if step_num == 1:
            clock.now += gap_s

    adapter = _CaptureAdapter()
    result = run_agent_loop(
        "three part goal exercising the time gap contributor",
        adapter=adapter,
        dry_run=False,
        step_callback=_advance_after_step_1,
    )
    assert result.status == "done"
    return adapter.exec_user_msgs


class TestStepGapContribution:
    def test_material_gap_flag_on_injects_time_line(
            self, monkeypatch, tmp_path):
        _force_flag(monkeypatch, True)
        msgs = _run_gap_loop(monkeypatch, tmp_path, gap_s=660)
        step2_msgs = [m for m in msgs if "Perform part 2 of the work" in m]
        assert step2_msgs
        assert any("[time] Previous step finished 11 minutes ago." in m
                   for m in step2_msgs), step2_msgs
        # One delivery only — the gap line must not echo into step 3.
        step3_msgs = [m for m in msgs if "Perform part 3 of the work" in m]
        assert not any("[time]" in m for m in step3_msgs), step3_msgs

    def test_material_gap_flag_off_appends_nothing(
            self, monkeypatch, tmp_path):
        """Default flag (off): the ledger gets no time contribution, so
        prompts stay byte-free of the [time] source."""
        msgs = _run_gap_loop(monkeypatch, tmp_path, gap_s=660)
        assert msgs
        assert not any("[time]" in m for m in msgs), msgs

    def test_small_gap_flag_on_appends_nothing(self, monkeypatch, tmp_path):
        _force_flag(monkeypatch, True)
        msgs = _run_gap_loop(monkeypatch, tmp_path, gap_s=300)
        assert msgs
        assert not any("[time]" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# Time contributions are recomputed, never replayed (F2/F4) — and the batch
# boundary resets the gap origin (F3)
# ---------------------------------------------------------------------------

def _make_capture_adapter(steps):
    """DryRun-backed adapter: answers decompose with `steps`, captures every
    executor user prompt (tool_choice=required) in .exec_user_msgs."""
    from agent_loop import _DryRunAdapter
    from llm import LLMResponse

    class _CaptureAdapter(_DryRunAdapter):
        def __init__(self):
            self.exec_user_msgs = []

        def complete(self, messages, *, tools=None, tool_choice="auto",
                     **kwargs):
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"),
                "")
            if ("decompose" in user_content.lower()
                    or "concrete steps" in user_content.lower()):
                return LLMResponse(content=json.dumps(steps),
                                   stop_reason="end_turn",
                                   input_tokens=10, output_tokens=10)
            if tools and tool_choice == "required":
                self.exec_user_msgs.append(user_content)
            return super().complete(
                messages, tools=tools, tool_choice=tool_choice, **kwargs)

    return _CaptureAdapter()


class TestTimeReplayGuards:
    """F2/F4: the re-arm paths (compound-step split, blocked retry) replay
    delivered contributions verbatim — correct for every other source, but a
    wall-clock claim must be recomputed at the next merge point
    (ContributionLedger.drop_source), never replayed."""

    def test_compound_split_rearm_single_fresh_time_line(
            self, monkeypatch, tmp_path):
        """probe_compound_split_time_dup: guard trip after a material gap
        used to deliver TWO contradictory [time] lines in one prompt (the
        re-armed stale one + the fresh one). Exactly one, correct N."""
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        import loop_execute
        from agent_loop import run_agent_loop

        clock = _FakeClock()
        monkeypatch.setattr(loop_execute, "time", clock)
        # Deterministic guard trip: only the marker step is "compound";
        # replacements never re-trip.
        monkeypatch.setattr(loop_execute, "_is_combined_exec_analyze",
                            lambda s: s.startswith("MARKER-COMPOUND"))
        monkeypatch.setattr(loop_execute, "_split_exec_analyze",
                            lambda s: ["replacement run part",
                                       "replacement analyze part"])

        steps = ["Perform part 1 of the work",
                 "MARKER-COMPOUND do a thing and analyze it",
                 "Perform part 3 of the work"]
        adapter = _make_capture_adapter(steps)

        def _advance_after_step_1(step_num, step_text, summary, status):
            if step_num == 1:
                clock.now += 660  # 11 minutes

        result = run_agent_loop(
            "goal exercising compound-split re-arm with a time gap",
            adapter=adapter, dry_run=False,
            step_callback=_advance_after_step_1)
        assert result.status == "done"

        msgs = adapter.exec_user_msgs
        assert sum(m.count("[time]") for m in msgs) == 1, msgs
        with_time = [m for m in msgs if "[time]" in m]
        assert "[time] Previous step finished 11 minutes ago." in with_time[0]
        # It lands on the replacement step's delivery — the boundary the
        # re-arm was protecting.
        assert "replacement run part" in with_time[0]

    def test_blocked_retry_gets_no_stale_time_line(
            self, monkeypatch, tmp_path):
        """probe_blocked_retry_time_stale: the retry follows the blocked
        attempt by seconds — re-delivering "finished 11 minutes ago" would
        be a stale wall-clock claim. The retry prompt carries NO [time]."""
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        import loop_execute
        from agent_loop import run_agent_loop

        clock = _FakeClock()
        monkeypatch.setattr(loop_execute, "time", clock)

        steps = ["Perform part 1 of the work",
                 "Perform part 2 of the work",
                 "Perform part 3 of the work"]
        adapter = _make_capture_adapter(steps)

        real_execute_step = loop_execute._execute_step
        blocked_once = {"fired": False}
        attempts = []  # (step_text, incremental_context)

        def _spy_execute_step(*, goal, step_text, incremental_context="",
                              **kw):
            attempts.append((step_text, incremental_context))
            if step_text == steps[1] and not blocked_once["fired"]:
                blocked_once["fired"] = True
                clock.now += 5  # the blocked attempt itself takes 5s
                return {"status": "blocked",
                        "stuck_reason": "forced by test",
                        "result": "", "tokens_in": 0, "tokens_out": 0}
            return real_execute_step(
                goal=goal, step_text=step_text,
                incremental_context=incremental_context, **kw)

        monkeypatch.setattr(loop_execute, "_execute_step", _spy_execute_step)

        def _advance_after_step_1(step_num, step_text, summary, status):
            if step_num == 1 and status == "done":
                clock.now += 660  # 11 minutes

        run_agent_loop(
            "goal exercising blocked-retry re-arm with a time gap",
            adapter=adapter, dry_run=False,
            step_callback=_advance_after_step_1)

        step2_attempts = [inc for st, inc in attempts if st == steps[1]]
        assert len(step2_attempts) >= 2, attempts  # blocked + retry
        # The first attempt legitimately saw the gap line…
        assert ("[time] Previous step finished 11 minutes ago."
                in step2_attempts[0]), step2_attempts
        # …the retry must not: its previous execution just ran.
        assert all("[time]" not in inc for inc in step2_attempts[1:]), \
            step2_attempts


class TestBatchBoundaryCapture:
    """F3: a parallel batch counts as a step end — a gap after the batch is
    measured from BATCH END. The mutant that drops the batch-boundary
    capture measures from the last single step before the batch instead."""

    def test_gap_after_parallel_batch_measured_from_batch_end(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _force_flag(monkeypatch, True)
        import agent_loop
        import loop_execute
        from agent_loop import run_agent_loop

        clock = _FakeClock()
        monkeypatch.setattr(loop_execute, "time", clock)
        # Force the in-loop peer batching, not the Phase D early-return DAG
        # path: _run_parallel_path declines, the main loop still sees the
        # dependency levels.
        monkeypatch.setattr(agent_loop, "_run_parallel_path",
                            lambda *a, **kw: None)

        steps = [
            "Perform part 1 of the work",
            "Perform part 2 of the work [after:1]",
            "Perform part 3 of the work [after:1]",
            "Perform part 4 of the work [after:2,3]",
        ]
        adapter = _make_capture_adapter(steps)

        # The batch itself takes 30 minutes (advance once, on the first
        # executor call of a batch member).
        _inner_complete = adapter.complete
        _batch_advanced = {"done": False}

        def _complete_with_batch_advance(messages, **kwargs):
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"),
                "")
            if (not _batch_advanced["done"]
                    and "Perform part 2 of the work" in user_content
                    and kwargs.get("tool_choice") == "required"):
                _batch_advanced["done"] = True
                clock.now += 1800  # 30 minutes inside the batch
            return _inner_complete(messages, **kwargs)

        adapter.complete = _complete_with_batch_advance

        # 11 minutes pass before each single step reaches its merge point
        # (adapter selection sits between the batch-boundary capture and the
        # gap computation).
        _orig_select = loop_execute._select_step_adapter

        def _select_and_tick(*a, **kw):
            clock.now += 660
            return _orig_select(*a, **kw)

        monkeypatch.setattr(loop_execute, "_select_step_adapter",
                            _select_and_tick)

        result = run_agent_loop(
            "goal exercising the batch boundary capture",
            adapter=adapter, dry_run=False, parallel_fan_out=2,
            preset_steps=steps)
        assert result.status == "done"

        # Classify prompts by their "Current step (i/n) [...]: <text>"
        # header — later prompts repeat earlier step texts in their
        # completed-steps section, so bare substring matches misclassify.
        def _header(m):
            return next((ln for ln in m.splitlines()
                         if ln.startswith("Current step")), "")

        msgs = adapter.exec_user_msgs
        part4 = [m for m in msgs if "Perform part 4" in _header(m)]
        assert part4, msgs
        # Exact N: 11 minutes since BATCH END. The mutant (capture deleted)
        # leaves the origin at step 1's end → "41 minutes ago" (11 + 30).
        assert "[time] Previous step finished 11 minutes ago." in part4[0], \
            part4
        # Batch members carry no time line: gap-before-batch is unsurfaced
        # by design and nothing stale rides in.
        batch_msgs = [m for m in msgs
                      if "Perform part 2" in _header(m)
                      or "Perform part 3" in _header(m)]
        assert len(batch_msgs) == 2, msgs
        assert all("[time]" not in m for m in batch_msgs), batch_msgs
