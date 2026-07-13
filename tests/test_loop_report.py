"""Tests for loop_report.py — per-run HTML report + cross-run static index.

Design: docs/RUN_VISIBILITY_DESIGN.md. Mirrors the workspace-isolation
conventions used by tests/test_agent_loop.py's plan-manifest tests
(MARO_ORCH_ROOT for the no-run-dir fallback path; MARO_WORKSPACE +
runs.set_current_run_dir for the run-dir-active path).
"""

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import loop_report as lr
from loop_types import StepOutcome, step_from_decompose


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _outcome(text, status="done", elapsed_ms=100, ended_ts=None, tokens_in=10,
             tokens_out=10, call_record="", confidence="strong"):
    # ended_ts=None (default) -> step_from_decompose defaults to "now" (real
    # timing, matches most tests' intent). Pass ended_ts="" explicitly to
    # test the approximate-mode fallback (2026-07-08 review, findings #2/#5).
    return step_from_decompose(
        text, 1,
        status=status, result="result text", iteration=1,
        tokens_in=tokens_in, tokens_out=tokens_out, elapsed_ms=elapsed_ms,
        confidence=confidence, call_record=call_record, ended_ts=ended_ts,
    )


@pytest.fixture(autouse=True)
def _clean_run_dir_state(monkeypatch):
    """Every test starts with no active run-dir and a clean debounce table."""
    import runs
    runs.set_current_run_dir(None)
    lr._last_index_write.clear()
    yield
    runs.set_current_run_dir(None)


# ---------------------------------------------------------------------------
# step_from_decompose / ended_ts default
# ---------------------------------------------------------------------------

def test_step_from_decompose_defaults_ended_ts_to_now():
    s = step_from_decompose("do a thing", 1, status="done", elapsed_ms=50)
    assert s.ended_ts  # non-empty, ISO-ish
    assert "T" in s.ended_ts


def test_step_from_decompose_respects_explicit_ended_ts():
    s = step_from_decompose("do a thing", 1, ended_ts="2026-01-01T00:00:00+00:00")
    assert s.ended_ts == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# write_run_report — no run-dir active (falls back to projects/<slug>/artifacts)
# ---------------------------------------------------------------------------

def test_write_run_report_creates_file_fallback_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    steps = ["Research the topic", "Write the report"]
    result = lr.write_run_report(
        project="test-proj",
        loop_id="abc12345",
        goal="Do research",
        planned_steps=steps,
        start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    assert result is not None
    path = tmp_path / "projects" / "test-proj" / "artifacts" / "loop-abc12345-report.html"
    assert path.exists()
    content = path.read_text()
    assert "abc12345" in content
    assert "Do research" in content
    assert "running" in content  # default status
    # Not yet executed — pending chips (with the plan text in their title
    # attribute) render, but the Steps table body (executed outcomes only)
    # stays empty.
    assert "<tbody></tbody>" in content
    assert "st-pending" in content


def test_write_run_report_renders_step_timeline_and_table(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    steps = ["Step one", "Step two"]
    outcomes = [
        _outcome("Step one", status="done", elapsed_ms=500),
        _outcome("Step two", status="blocked", elapsed_ms=200),
    ]
    lr.write_run_report(
        project="test-proj", loop_id="xyz99999", goal="multi-step goal",
        planned_steps=steps, start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=outcomes,
    )
    content = (tmp_path / "projects" / "test-proj" / "artifacts" / "loop-xyz99999-report.html").read_text()
    assert "st-done" in content
    assert "st-blocked" in content
    assert "500ms" in content
    assert "Step one" in content
    assert "Step two" in content


def test_write_run_report_html_escapes_step_text(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    outcomes = [_outcome("<script>alert(1)</script>", status="done")]
    lr.write_run_report(
        project="p", loop_id="esc1", goal="<b>goal</b>",
        planned_steps=["<script>alert(1)</script>"],
        start_ts="2026-04-04T00:00:00+00:00", step_outcomes=outcomes,
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-esc1-report.html").read_text()
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content


def test_write_run_report_no_call_record_shows_placeholder(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    outcomes = [_outcome("Step one", status="done", call_record="")]
    lr.write_run_report(
        project="p", loop_id="nocall", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=outcomes,
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-nocall-report.html").read_text()
    assert "no call record" in content
    # .detail-toggle is defined in the shared <style> block regardless; what
    # matters is no *element* uses it when there's no call record to show.
    assert '<button class="detail-toggle"' not in content


def test_write_run_report_call_record_gets_detail_and_raw_link(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    outcomes = [_outcome("Step one", status="done",
                         call_record=str(tmp_path / "build" / "calls" / "call-00001.json"))]
    lr.write_run_report(
        project="p", loop_id="withcall", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=outcomes,
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-withcall-report.html").read_text()
    assert "detail-toggle" in content
    assert "raw-link" in content
    assert "data-call-record=" in content


# ---------------------------------------------------------------------------
# Freeze semantics
# ---------------------------------------------------------------------------

def test_terminal_write_freezes_report(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    outcomes = [_outcome("Only step", status="done", elapsed_ms=999)]
    lr.write_run_report(
        project="p", loop_id="finaltest", goal="goal",
        planned_steps=["Only step"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=outcomes, status="done", elapsed_ms=1500,
    )
    path = tmp_path / "projects" / "p" / "artifacts" / "loop-finaltest-report.html"
    assert path.exists()
    frozen_content = path.read_text()
    assert "maro-report: final status=done" in frozen_content

    # A later call (e.g. a stray post-step callback, or curation re-entry)
    # must not mutate the frozen file.
    lr.write_run_report(
        project="p", loop_id="finaltest", goal="a different goal!!",
        planned_steps=["Only step", "A new step"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=outcomes + [_outcome("A new step")], status="running",
    )
    assert path.read_text() == frozen_content


def test_running_status_not_frozen_and_keeps_updating(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    path = tmp_path / "projects" / "p" / "artifacts" / "loop-running1-report.html"
    lr.write_run_report(
        project="p", loop_id="running1", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[], status="running",
    )
    first = path.read_text()
    assert "maro-report: final status=" not in first

    lr.write_run_report(
        project="p", loop_id="running1", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[_outcome("Step one", status="done")], status="running",
    )
    second = path.read_text()
    assert second != first
    assert "st-done" in second


# ---------------------------------------------------------------------------
# report.enabled config gate
# ---------------------------------------------------------------------------

def test_reports_disabled_via_config_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setattr(lr, "_reports_enabled", lambda: False)
    result = lr.write_run_report(
        project="p", loop_id="off1", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    assert result is None
    assert not (tmp_path / "projects" / "p" / "artifacts" / "loop-off1-report.html").exists()


# ---------------------------------------------------------------------------
# Debug snapshot mode
# ---------------------------------------------------------------------------

def test_debug_snapshots_off_by_default_creates_no_debug_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    lr.write_run_report(
        project="p", loop_id="dbg1", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    debug_dir = tmp_path / "projects" / "p" / "artifacts" / "loop-dbg1-report-debug"
    assert not debug_dir.exists()


def test_debug_snapshots_when_enabled_accumulate(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setattr(lr, "_debug_snapshots_enabled", lambda: True)
    for i in range(3):
        lr.write_run_report(
            project="p", loop_id="dbg2", goal="goal",
            planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
            step_outcomes=[_outcome("Step one")] * (i + 1) if i else [],
        )
    debug_dir = tmp_path / "projects" / "p" / "artifacts" / "loop-dbg2-report-debug"
    assert debug_dir.exists()
    snapshots = list(debug_dir.glob("report-*.html"))
    assert len(snapshots) == 3


def test_turning_debug_off_cleans_up_prior_snapshots(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setattr(lr, "_debug_snapshots_enabled", lambda: True)
    lr.write_run_report(
        project="p", loop_id="dbg3", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    debug_dir = tmp_path / "projects" / "p" / "artifacts" / "loop-dbg3-report-debug"
    assert debug_dir.exists()

    monkeypatch.setattr(lr, "_debug_snapshots_enabled", lambda: False)
    lr.write_run_report(
        project="p", loop_id="dbg3", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[_outcome("Step one")],
    )
    assert not debug_dir.exists()


# ---------------------------------------------------------------------------
# Captain's log markers — attributed vs. global context
# ---------------------------------------------------------------------------

def test_gather_log_markers_splits_attributed_and_global(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import captains_log
    captains_log.log_event("STEP_TOO_BROAD", "step 1", "attributed entry", loop_id="loopA")
    captains_log.log_event("METACOGNITIVE_DECISION", "reflection", "global entry", loop_id=None)
    captains_log.log_event("STEP_TOO_BROAD", "other loop", "different loop entry", loop_id="loopB")

    attributed, global_entries = lr._gather_log_markers("loopA", "2020-01-01T00:00:00+00:00")
    assert len(attributed) == 1
    assert attributed[0]["subject"] == "step 1"
    subjects = [e["subject"] for e in global_entries]
    assert "reflection" in subjects
    assert "other loop" not in subjects  # attributed to a different loop, not global


def test_gather_log_markers_global_filter_handles_mixed_utc_offsets(monkeypatch, tmp_path):
    """2026-07-08 review round 2 (Plan Critic): the global-context filter
    used raw string comparison on ISO timestamps, which misorders entries
    that use a different (but valid) UTC offset than start_ts even when
    they're chronologically at-or-after it. "2019-12-31T23:00:00-01:00" is
    the exact same instant as "2020-01-01T00:00:00+00:00" — lexicographic
    string comparison wrongly excludes it (starts with "2019"); datetime
    comparison correctly includes it."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import captains_log

    same_instant_different_offset = {
        "timestamp": "2019-12-31T23:00:00-01:00",
        "event_type": "METACOGNITIVE_DECISION",
        "subject": "cross-offset entry",
        "summary": "",
    }
    monkeypatch.setattr(captains_log, "load_log", lambda **kw: [same_instant_different_offset])

    attributed, global_entries = lr._gather_log_markers("someloop", "2020-01-01T00:00:00+00:00")
    subjects = [e["subject"] for e in global_entries]
    assert "cross-offset entry" in subjects


def test_write_run_report_includes_decision_points_and_global_context(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import captains_log
    captains_log.log_event("QUALITY_GATE_VERDICT", "escalate", "gate flagged this run", loop_id="markloop")
    captains_log.log_event("METACOGNITIVE_DECISION", "reflection", "cross-run note", loop_id=None)

    lr.write_run_report(
        project="p", loop_id="markloop", goal="goal",
        planned_steps=["Step one"], start_ts="2020-01-01T00:00:00+00:00",
        step_outcomes=[_outcome("Step one", status="done")],
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-markloop-report.html").read_text()
    assert "Decision points" in content
    assert "gate flagged this run" in content
    assert "Run activity" in content
    assert "cross-run note" in content


# ---------------------------------------------------------------------------
# Run-dir-active path (artifact_dir -> <run-dir>/build/) + index cross-link
# ---------------------------------------------------------------------------

def test_write_run_report_uses_run_dir_when_active(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = tmp_path / "runs" / "h1-nick"
    (rd / "build").mkdir(parents=True)
    (rd / "source").mkdir(parents=True)
    runs.set_current_run_dir(rd)

    lr.write_run_report(
        project="p", loop_id="rundir1", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    report_path = rd / "build" / "loop-rundir1-report.html"
    assert report_path.exists()


def test_report_links_back_to_index_when_run_dir_active(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = tmp_path / "runs" / "h2-nick"
    (rd / "build").mkdir(parents=True)
    runs.set_current_run_dir(rd)

    lr.write_run_report(
        project="p", loop_id="rundir2", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    content = (rd / "build" / "loop-rundir2-report.html").read_text()
    assert "all runs" in content
    assert "../../index.html" in content


def test_report_omits_index_backlink_on_fallback_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    lr.write_run_report(
        project="p", loop_id="nobacklink", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-nobacklink-report.html").read_text()
    assert "all runs" not in content


# ---------------------------------------------------------------------------
# write_runs_index
# ---------------------------------------------------------------------------

def test_write_runs_index_lists_runs_with_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = runs.create_run_dir("h3", prompt="Do the thing", lane="agenda")
    runs.write_metadata(rd, handle_id="h3", prompt="Do the thing", status="done",
                        ended_at="2026-04-04T00:05:00+00:00")

    out = lr.write_runs_index(force=True)
    assert out is not None
    index_path = Path(runs.runs_root()) / "index.html"
    assert index_path.exists()
    content = index_path.read_text()
    assert "Do the thing" in content
    assert "done" in content


def test_write_runs_index_lists_report_links(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = runs.create_run_dir("h4", prompt="Report-linked goal")
    runs.write_metadata(rd, handle_id="h4", prompt="Report-linked goal", status="running")
    runs.set_current_run_dir(rd)
    lr.write_run_report(
        project="p", loop_id="idxloop", goal="Report-linked goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    out = lr.write_runs_index(force=True)
    content = Path(out).read_text()
    assert "loop-idxloop-report.html" in content


def test_write_runs_index_debounces_without_force(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    runs.create_run_dir("h5", prompt="First goal")

    lr.write_runs_index(force=True)
    index_path = Path(runs.runs_root()) / "index.html"
    first = index_path.read_text()

    runs.create_run_dir("h6", prompt="Second goal — should not appear yet")
    lr.write_runs_index()  # not forced — within debounce window, should be a no-op
    assert index_path.read_text() == first

    lr.write_runs_index(force=True)
    assert "Second goal" in index_path.read_text()


def test_write_runs_index_handles_no_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    out = lr.write_runs_index(force=True)
    assert out is not None
    assert "No runs yet" in Path(out).read_text()


# ---------------------------------------------------------------------------
# 2026-07-08 adversarial review fixes
# ---------------------------------------------------------------------------

def test_ended_ts_empty_string_forces_approximate_mode():
    """Finding #2/#5: ended_ts="" (not omitted) must NOT default to "now" —
    it's the explicit signal parallel/checkpoint-resume call sites use to
    opt out of fabricated precision."""
    s = step_from_decompose("do a thing", 1, elapsed_ms=50, ended_ts="")
    assert s.ended_ts == ""


def test_approximate_mode_renders_badge_and_no_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    outcomes = [
        _outcome("Step one", status="done", elapsed_ms=100, ended_ts=""),
        _outcome("Step two", status="done", elapsed_ms=200, ended_ts=""),
    ]
    lr.write_run_report(
        project="p", loop_id="approxtest", goal="goal",
        planned_steps=["Step one", "Step two"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=outcomes,
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-approxtest-report.html").read_text()
    assert "approximate timing" in content
    assert "st-done" in content


def test_approximate_mode_uses_equal_width_chips_not_fabricated_elapsed(monkeypatch, tmp_path):
    """2026-07-08 review round 2 (Minimalist): even flagged 'approximate', the
    timeline used to size chips by elapsed_ms directly — which can itself be
    the fabricated value (a parallel batch assigns ~the same elapsed_ms to
    every step in it, measured from batch start). Two wildly different
    elapsed_ms values should render the SAME chip width in approximate mode."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    outcomes = [
        _outcome("Step one", status="done", elapsed_ms=60000, ended_ts=""),
        _outcome("Step two", status="done", elapsed_ms=100, ended_ts=""),
    ]
    lr.write_run_report(
        project="p", loop_id="approxwidth", goal="goal",
        planned_steps=["Step one", "Step two"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=outcomes, status="done",
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-approxwidth-report.html").read_text()
    # Both st-done chips (60000ms and 100ms elapsed) must render with the
    # SAME equal flex-grow weight in approximate mode — not proportional to
    # their (unreliable) elapsed_ms.
    assert content.count('class="tl-chip st-done" style="flex-grow:1"') == 2
    assert "flex-grow:60000" not in content


def test_naive_ended_ts_does_not_crash_report_generation(monkeypatch, tmp_path):
    """Finding #10: a naive (non-tz-aware) ended_ts used to raise TypeError
    when compared against captain's-log's tz-aware timestamps, silently
    failing the whole report write (caught by the outer except)."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import captains_log
    captains_log.log_event("STEP_TOO_BROAD", "step 1", "a marker", loop_id="naiveloop")

    naive_outcome = _outcome("Step one", status="done", elapsed_ms=100,
                              ended_ts="2020-01-01T00:00:01")  # no offset — naive
    result = lr.write_run_report(
        project="p", loop_id="naiveloop", goal="goal",
        planned_steps=["Step one"], start_ts="2020-01-01T00:00:00+00:00",
        step_outcomes=[naive_outcome],
    )
    assert result is not None
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-naiveloop-report.html").read_text()
    assert "st-done" in content


def test_write_run_report_atomic_write_leaves_no_tmp_file(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    lr.write_run_report(
        project="p", loop_id="atomictest", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    artifacts = tmp_path / "projects" / "p" / "artifacts"
    tmp_leftovers = list(artifacts.glob(".*.tmp-*"))
    assert tmp_leftovers == [], f"leftover temp file(s): {tmp_leftovers}"


def test_write_run_report_uses_file_lock(monkeypatch, tmp_path):
    """Finding #4: the frozen-check-then-write sequence must be serialized
    via file_lock, not a bare unlocked write — spy on locked_write to confirm
    it's actually invoked with the report path, not silently bypassed."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import file_lock
    calls = []
    _real_locked_write = file_lock.locked_write

    @contextmanager
    def _spy(path):
        calls.append(path)
        with _real_locked_write(path):
            yield

    monkeypatch.setattr(file_lock, "locked_write", _spy)
    lr.write_run_report(
        project="p", loop_id="locktest", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    assert len(calls) == 1
    assert calls[0].name == "loop-locktest-report.html"


def test_write_runs_index_atomic_and_locked(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import file_lock
    calls = []
    _real_locked_write = file_lock.locked_write

    @contextmanager
    def _spy(path):
        calls.append(path)
        with _real_locked_write(path):
            yield

    monkeypatch.setattr(file_lock, "locked_write", _spy)
    lr.write_runs_index(force=True)
    assert len(calls) == 1
    assert calls[0].name == "index.html"

    tmp_leftovers = list((tmp_path / "runs").glob(".*.tmp-*"))
    assert tmp_leftovers == [], f"leftover temp file(s): {tmp_leftovers}"


def test_write_runs_index_respects_report_enabled(monkeypatch, tmp_path):
    """Finding #6: report.enabled=false previously stopped per-run reports
    but NOT the index — the index kept scanning/rewriting regardless."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    runs.create_run_dir("h7", prompt="Should not be indexed")
    monkeypatch.setattr(lr, "_reports_enabled", lambda: False)

    result = lr.write_runs_index(force=True)
    assert result is None
    assert not (Path(runs.runs_root()) / "index.html").exists()


def test_captains_log_marker_cap_matches_documented_1000(monkeypatch, tmp_path):
    """Finding #8: the design doc's own accepted-risk note says ~1000-entry
    rotation tail; the implementation was stricter (500) than what it
    documented as acceptable loss."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import captains_log
    calls = {}
    _real_load_log = captains_log.load_log

    def _spy(**kwargs):
        calls.update(kwargs)
        return _real_load_log(**kwargs)

    monkeypatch.setattr(captains_log, "load_log", _spy)
    lr._gather_log_markers("someloop", "2020-01-01T00:00:00+00:00")
    assert calls.get("limit") == 1000


def test_debug_snapshot_cleanup_runs_even_when_reports_disabled(monkeypatch, tmp_path):
    """Finding #9 (partial fix): previously, if report.enabled was false,
    write_run_report returned before ever checking whether a leftover debug
    snapshot dir needed cleaning up."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setattr(lr, "_debug_snapshots_enabled", lambda: True)
    lr.write_run_report(
        project="p", loop_id="dbgoff", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    debug_dir = tmp_path / "projects" / "p" / "artifacts" / "loop-dbgoff-report-debug"
    assert debug_dir.exists()

    monkeypatch.setattr(lr, "_debug_snapshots_enabled", lambda: False)
    monkeypatch.setattr(lr, "_reports_enabled", lambda: False)
    result = lr.write_run_report(
        project="p", loop_id="dbgoff", goal="goal",
        planned_steps=["Step one"], start_ts="2026-04-04T00:00:00+00:00",
        step_outcomes=[],
    )
    assert result is None  # reports disabled — no report written
    assert not debug_dir.exists()  # but the leftover snapshot dir is still cleaned up


def test_js_fallback_uses_dom_apis_not_string_concat(monkeypatch, tmp_path):
    """Finding #7: the fetch-failure fallback used to build innerHTML via
    string concatenation with the raw call_record attribute value — cheap to
    fix with DOM APIs even though not exploitable with today's internally-
    generated call_record paths."""
    assert "createElement" in lr._DETAIL_JS
    assert "createTextNode" in lr._DETAIL_JS
    # The old vulnerable pattern built the error div via string concat
    # ending in "+ rec + '</a></div>'" — assert that's gone.
    assert "+ rec + '</a>" not in lr._DETAIL_JS
    assert "panel.innerHTML = '<div class=\"detail-error\"" not in lr._DETAIL_JS


# ---------------------------------------------------------------------------
# Captain's-log slice preference (2026-07-09, Jeremy: "the captain's log is
# somehow not surfaced" — 85% of real entries carry no loop_id, and the
# global load_log tail rotates away; the run's own slice is the durable,
# already-scoped source)
# ---------------------------------------------------------------------------

def _write_slice(build_dir, entries):
    build_dir.mkdir(parents=True, exist_ok=True)
    with (build_dir / "captains_log_slice.jsonl").open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_gather_log_markers_prefers_run_slice(monkeypatch, tmp_path):
    build = tmp_path / "build"
    _write_slice(build, [
        {"timestamp": "2026-07-01T00:00:10+00:00", "event_type": "QUALITY_GATE_VERDICT",
         "subject": "gate", "summary": "attributed", "loop_id": "sliceloop"},
        {"timestamp": "2026-07-01T00:00:20+00:00", "event_type": "SKILL_PROMOTED",
         "subject": "skillX", "summary": "no loop id"},
        {"timestamp": "2026-07-01T00:00:30+00:00", "event_type": "DIAGNOSIS",
         "subject": "sibling", "summary": "other loop", "loop_id": "otherloop"},
    ])
    # Poison the global-log path: if the slice is preferred, load_log is never called.
    import captains_log
    monkeypatch.setattr(captains_log, "load_log",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("load_log used despite slice")))

    attributed, activity = lr._gather_log_markers("sliceloop", "2026-07-01T00:00:00+00:00", build)
    assert [e["subject"] for e in attributed] == ["gate"]
    subjects = [e["subject"] for e in activity]
    assert "skillX" in subjects        # unattributed meta stays visible
    assert "sibling" in subjects       # sibling-loop entries are run context, not dropped


def test_gather_log_markers_falls_back_to_load_log_without_slice(monkeypatch, tmp_path):
    import captains_log
    monkeypatch.setattr(captains_log, "load_log", lambda **kw: [
        {"timestamp": "2026-07-01T00:00:10+00:00", "event_type": "LOOP_CREATED",
         "subject": "from-global", "summary": "", "loop_id": "noslice"},
    ])
    attributed, _ = lr._gather_log_markers("noslice", "2026-07-01T00:00:00+00:00", tmp_path / "build")
    assert [e["subject"] for e in attributed] == ["from-global"]


def test_run_activity_section_shows_family_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = tmp_path / "runs" / "hact-nick"
    (rd / "build").mkdir(parents=True)
    _write_slice(rd / "build", [
        {"timestamp": "2026-07-01T00:00:10+00:00", "event_type": "SKILL_PROMOTED",
         "subject": "skillA", "summary": "learned a thing"},
        {"timestamp": "2026-07-01T00:00:11+00:00", "event_type": "SKILL_VARIANT_CREATED",
         "subject": "skillB", "summary": ""},
        {"timestamp": "2026-07-01T00:00:12+00:00", "event_type": "EVOLVER_APPLIED",
         "subject": "evo", "summary": ""},
    ])
    runs.set_current_run_dir(rd)
    lr.write_run_report(
        project="p", loop_id="actloop", goal="goal",
        planned_steps=["Step one"], start_ts="2026-07-01T00:00:00+00:00",
        step_outcomes=[_outcome("Step one")],
    )
    content = (rd / "build" / "loop-actloop-report.html").read_text()
    assert "Run activity" in content
    assert "SKILL 2" in content
    assert "EVOLVER 1" in content
    assert "skillA" in content


# ---------------------------------------------------------------------------
# LLM-calls section + per-step model column (call-record meta)
# ---------------------------------------------------------------------------

def _write_call(calls_dir, seq, *, prompt, model="claude-sonnet-5", backend="anthropic",
                tokens_in=100, tokens_out=10, purpose=None):
    calls_dir.mkdir(parents=True, exist_ok=True)
    p = calls_dir / f"call-{seq:05d}.json"
    rec = {
        "seq": seq, "backend": backend, "model": model, "prompt": prompt,
        "response": "ok", "tool_events": [], "tokens_in": tokens_in,
        "tokens_out": tokens_out, "ts": "2026-07-01T00:00:05+00:00",
    }
    if purpose is not None:
        rec["purpose"] = purpose
    p.write_text(json.dumps(rec))
    return p


def test_report_lists_all_llm_calls_not_just_step_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = tmp_path / "runs" / "hcalls-nick"
    build = rd / "build"
    _write_call(build / "calls", 1, prompt="[system]\nYou are a routing agent. Classify...",
                model="claude-haiku-4-5-20251001")
    step_rec = _write_call(build / "calls", 2,
                           prompt="[system]\nYou are an autonomous execution agent.\nComplete the given step")
    runs.set_current_run_dir(rd)
    lr.write_run_report(
        project="p", loop_id="callloop", goal="goal",
        planned_steps=["Step one"], start_ts="2026-07-01T00:00:00+00:00",
        step_outcomes=[_outcome("Step one", call_record=str(step_rec))],
    )
    content = (build / "loop-callloop-report.html").read_text()
    assert "LLM calls (2)" in content
    assert "routing" in content            # purpose sniffed from prompt head
    assert "step execution" in content
    assert "(step 1)" in content           # step attribution marked
    assert "haiku-4-5" in content          # model surfaced (shortened)
    # per-step model column in the step table too
    assert content.index("<th>Model</th>") < content.index("LLM calls (2)")


def test_call_purpose_sniffer_extracts_persona():
    purpose, persona = lr._sniff_call_head("# Persona: Director Proxy\n\nYou are operating as...")
    assert persona == "Director Proxy"


def test_call_meta_prefers_stamped_purpose_over_sniffer(tmp_path):
    """BACKLOG #17 sub-item 2: a caller-stamped purpose= wins over the
    prompt-opener sniffer, even when the prompt text would sniff to a
    different label."""
    p = _write_call(
        tmp_path / "calls", 1,
        prompt="[system]\nYou are a routing agent. Classify...",
        purpose="custom label",
    )
    meta = lr._call_meta(str(p))
    assert meta["purpose"] == "custom label"


def test_call_meta_falls_back_to_sniffer_when_no_purpose_stamped(tmp_path):
    """A record written before purpose= existed (or by a caller that hasn't
    been stamped yet) still gets a label via the sniffer fallback."""
    p = _write_call(
        tmp_path / "calls", 1,
        prompt="[system]\nYou are a routing agent. Classify...",
    )
    meta = lr._call_meta(str(p))
    assert meta["purpose"] == "routing"


# ---------------------------------------------------------------------------
# Outcome verdict panel (run_card.json)
# ---------------------------------------------------------------------------

def test_report_shows_run_card_verdict_when_present(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = tmp_path / "runs" / "hcard-nick"
    (rd / "build").mkdir(parents=True)
    (rd / "run_card.json").write_text(json.dumps({
        "status": "incomplete", "success_class": "partial",
        "goal_achieved": False, "total_cost_usd": 0.21,
        "goal_verdict_summary": "Two of four delivery criteria fail.",
    }))
    runs.set_current_run_dir(rd)
    lr.write_run_report(
        project="p", loop_id="cardloop", goal="goal",
        planned_steps=["Step one"], start_ts="2026-07-01T00:00:00+00:00",
        step_outcomes=[_outcome("Step one")],
    )
    content = (rd / "build" / "loop-cardloop-report.html").read_text()
    assert "Outcome" in content
    assert "Goal achieved:</b> no" in content
    assert "$0.2100" in content
    assert "Two of four delivery criteria fail." in content


# ---------------------------------------------------------------------------
# Backfill (2026-07-09: 665 pre-feature runs on this box had loop logs but
# no reports; the index listed them link-less forever)
# ---------------------------------------------------------------------------

def _make_historical_run(ws, handle, loop_id, *, status="done", with_report=False):
    rd = ws / "runs" / handle
    build = rd / "build"
    build.mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps({
        "handle_id": handle.split("-")[0], "prompt": f"goal for {handle}",
        "lane": "agenda", "status": status,
        "started_at": "2026-06-01T00:00:00+00:00", "ended_at": "2026-06-01T00:10:00+00:00",
    }))
    (build / f"loop-{loop_id}-log.json").write_text(json.dumps({
        "loop_id": loop_id, "project": "p", "goal": f"goal for {handle}",
        "status": status, "started_at": "2026-06-01T00:00:00+00:00",
        "elapsed_ms": 600000, "stuck_reason": None,
        "steps": [
            {"index": 1, "text": "First step", "status": "done", "result_length": 10,
             "iteration": 1, "tokens_in": 1000, "tokens_out": 50, "elapsed_ms": 30000,
             "call_record": ""},
            {"index": 2, "text": "Second step", "status": "blocked", "result_length": 0,
             "iteration": 1, "tokens_in": 500, "tokens_out": 20, "elapsed_ms": 15000,
             "call_record": ""},
        ],
        "totals": {"steps_done": 1, "steps_blocked": 1, "tokens_in": 1500, "tokens_out": 70},
    }))
    if with_report:
        (build / f"loop-{loop_id}-report.html").write_text(
            "<!-- maro-report: final status=done -->\n<html>old</html>")
    return rd


def test_backfill_writes_frozen_reports_and_index(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    _make_historical_run(tmp_path, "aaaa1111-old-run", "oldloop1")
    _make_historical_run(tmp_path, "bbbb2222-older-run", "oldloop2", status="stuck")

    counts = lr.backfill_run_reports()
    assert counts["written"] == 2
    assert counts["failed"] == 0

    report = tmp_path / "runs" / "aaaa1111-old-run" / "build" / "loop-oldloop1-report.html"
    content = report.read_text()
    assert content.startswith("<!-- maro-report: final status=done -->")  # frozen
    assert "First step" in content
    assert "approximate timing" in content  # no ended_ts on old logs -> approx mode
    index = (tmp_path / "runs" / "index.html").read_text()
    assert "loop-oldloop1-report.html" in index
    assert "loop-oldloop2-report.html" in index


def test_backfill_skips_existing_reports_unless_forced(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_historical_run(tmp_path, "cccc3333-has-report", "hasrep", with_report=True)

    counts = lr.backfill_run_reports()
    assert counts["written"] == 0
    assert counts["skipped"] == 1
    report = tmp_path / "runs" / "cccc3333-has-report" / "build" / "loop-hasrep-report.html"
    assert report.read_text().endswith("<html>old</html>")  # untouched

    counts = lr.backfill_run_reports(force=True)
    assert counts["written"] == 1
    assert "First step" in report.read_text()  # regenerated over the frozen file


def test_backfill_coerces_running_status_to_interrupted(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_historical_run(tmp_path, "dddd4444-crashed", "crashloop", status="running")

    counts = lr.backfill_run_reports()
    assert counts["written"] == 1
    content = (tmp_path / "runs" / "dddd4444-crashed" / "build"
               / "loop-crashloop-report.html").read_text()
    # A crashed run must not be rendered as live: frozen sentinel, no auto-refresh.
    assert content.startswith("<!-- maro-report: final status=interrupted -->")
    assert 'http-equiv="refresh"' not in content


# ---------------------------------------------------------------------------
# Environment panel (persona / skills manifest / config era)
# ---------------------------------------------------------------------------

def test_report_renders_environment_panel(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = tmp_path / "runs" / "henv-nick"
    (rd / "build").mkdir(parents=True)
    (rd / "source").mkdir()
    (rd / "metadata.json").write_text(json.dumps({
        "handle_id": "henv", "prompt": "goal", "persona": "poe",
        "persona_confidence": 0.91, "persona_fallback": False,
    }))
    (rd / "source" / "environment.json").write_text(json.dumps({
        "captured_at": "2026-07-01T00:00:00+00:00",
        "maro_git_sha": "abc1234",
        "host": {"hostname": "macmini", "python": "3.14.0"},
        "spend_today_usd_at_start": 1.25,
        "env_overrides": {"MARO_WORKSPACE": "/x"},
        "config": {"inspector": {"breach_threshold": 0.3}},
    }))
    (rd / "source" / "skills_manifest.jsonl").write_text(json.dumps({
        "ts": "2026-07-01T00:00:01+00:00", "stage": "decompose",
        "skills": [{"id": "s1", "name": "deploy-check", "content_hash": "cafebabe99",
                    "variant_of": "deploy-check-parent", "tier": 2}],
    }) + "\n")
    runs.set_current_run_dir(rd)
    lr.write_run_report(
        project="p", loop_id="envloop", goal="goal",
        planned_steps=["Step one"], start_ts="2026-07-01T00:00:00+00:00",
        step_outcomes=[_outcome("Step one")],
    )
    content = (rd / "build" / "loop-envloop-report.html").read_text()
    assert "Environment" in content
    assert "Persona:</b> poe" in content
    assert "(conf 0.91)" in content
    assert "deploy-check" in content
    assert "variant of deploy-check-parent" in content
    assert "abc1234" in content
    assert "macmini" in content
    assert "$1.2500" in content
    assert "breach_threshold" in content


def test_environment_panel_absent_without_capture(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import runs
    rd = tmp_path / "runs" / "hnoenv-nick"
    (rd / "build").mkdir(parents=True)
    runs.set_current_run_dir(rd)
    lr.write_run_report(
        project="p", loop_id="noenvloop", goal="goal",
        planned_steps=["Step one"], start_ts="2026-07-01T00:00:00+00:00",
        step_outcomes=[_outcome("Step one")],
    )
    content = (rd / "build" / "loop-noenvloop-report.html").read_text()
    assert "<h2>Environment</h2>" not in content


# ---------------------------------------------------------------------------
# NOW-lane mini-report (2026-07-09: 258 NOW dirs on this box had full capture
# but no report — a rendering gap, not a capture gap)
# ---------------------------------------------------------------------------

def _make_now_run(ws, handle_id, *, status="done", with_card=False):
    rd = ws / "runs" / f"{handle_id}-now-run"
    (rd / "artifact").mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps({
        "handle_id": handle_id, "nickname": "quiet-otter", "prompt": "what is 2+2?",
        "lane": "now", "status": status, "goal_achieved": True,
        "started_at": "2026-06-15T00:00:00+00:00", "ended_at": "2026-06-15T00:00:03+00:00",
    }))
    (rd / "artifact" / f"now-{handle_id}.json").write_text(json.dumps({
        "handle_id": handle_id, "lane": "now", "message": "what is 2+2?",
        "result": "4 — arithmetic, no tools needed.", "elapsed_ms": 3200,
        "created_at": "2026-06-15T00:00:00+00:00",
    }))
    if with_card:
        (rd / "run_card.json").write_text(json.dumps({
            "status": status, "success_class": "achieved",
            "goal_achieved": True, "total_cost_usd": 0.003,
        }))
    return rd


def test_now_report_renders_request_result_and_freezes(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    rd = _make_now_run(tmp_path, "eeee5555", with_card=True)
    counts = lr.write_reports_for_run_dir(rd)
    assert counts["written"] == 1 and counts["failed"] == 0
    report = rd / "build" / "now-eeee5555-report.html"
    content = report.read_text()
    assert content.startswith("<!-- maro-report: final status=done -->")
    assert "what is 2+2?" in content
    assert "4 &#x2014; arithmetic" in content or "4 — arithmetic" in content
    assert "Goal achieved:</b> yes" in content   # run_card verdict panel
    assert 'http-equiv="refresh"' not in content
    # index links it with a lane label, not the raw UUID stem
    index = (tmp_path / "runs" / "index.html").read_text()
    assert "now-eeee5555-report.html" in index
    assert ">now</a>" in index


def test_write_reports_for_run_dir_rerenders_frozen_loop_report(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    rd = _make_historical_run(tmp_path, "ffff6666-postcure", "cureloop", with_report=True)
    # run_card lands AFTER the report froze — the post-curation hook's reason to exist
    (rd / "run_card.json").write_text(json.dumps({
        "status": "done", "success_class": "achieved",
        "goal_achieved": True, "total_cost_usd": 0.42,
    }))
    counts = lr.write_reports_for_run_dir(rd)
    assert counts["written"] == 1
    content = (rd / "build" / "loop-cureloop-report.html").read_text()
    assert "Goal achieved:</b> yes" in content   # verdict now present
    assert "$0.4200" in content
    assert "old" not in content.split("</html>")[0].split("<html>")[-1] or "First step" in content


def test_backfill_covers_now_runs_and_skips_unless_forced(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_now_run(tmp_path, "aaaa7777")
    _make_historical_run(tmp_path, "bbbb8888-loop-run", "mixloop")

    counts = lr.backfill_run_reports()
    assert counts["written"] == 2          # one NOW report + one loop report
    assert counts["runs_scanned"] == 2

    counts = lr.backfill_run_reports()
    assert counts["written"] == 0
    assert counts["skipped"] == 2

    counts = lr.backfill_run_reports(force=True)
    assert counts["written"] == 2


def test_now_report_metadata_only_fallback(monkeypatch, tmp_path):
    # Pre-artifact-writer NOW runs: metadata is the only marker; the report
    # must exist, be honest about the missing result, and compute elapsed.
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    rd = tmp_path / "runs" / "gggg9999-old-now"
    rd.mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps({
        "handle_id": "gggg9999", "nickname": "old-otter", "prompt": "quick question",
        "lane": "now", "status": "error",
        "started_at": "2026-05-12T16:14:46+00:00", "ended_at": "2026-05-12T16:14:54+00:00",
    }))
    counts = lr.backfill_run_reports()
    assert counts["written"] == 1
    content = (rd / "build" / "now-gggg9999-report.html").read_text()
    assert content.startswith("<!-- maro-report: final status=error -->")
    assert "quick question" in content
    assert "result not captured" in content
    assert "8000ms" in content


# ---------------------------------------------------------------------------
# search_runs (BACKLOG #17 — goal search in the run visualization)
# ---------------------------------------------------------------------------

def _make_run(ws, handle_id, prompt, *, lane="agenda", started_at="2026-07-01T00:00:00+00:00",
              success_class=None, cost=None):
    """Direct metadata.json / run_card.json construction — same shape
    _gather_run_summaries() reads, without relying on write_metadata's
    started_at-preservation (which would keep the real wall-clock time
    a plain create_run_dir()+write_metadata() call ran at, defeating any
    date-range test).
    """
    import runs
    rd = runs.create_run_dir(handle_id, prompt=prompt, lane=lane)
    meta_path = rd / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["started_at"] = started_at
    meta["status"] = "done"
    meta_path.write_text(json.dumps(meta))
    if success_class is not None or cost is not None:
        card = {"status": "done"}
        if success_class is not None:
            card["success_class"] = success_class
        if cost is not None:
            card["total_cost_usd"] = cost
        (rd / "run_card.json").write_text(json.dumps(card))
    return rd


def test_search_runs_goal_text_case_insensitive_substring(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000001", "Fix the flaky LOGIN test")
    _make_run(tmp_path, "s0000002", "Research polymarket edges")

    results = lr.search_runs(goal="login")
    assert [r["handle_id"] for r in results] == ["s0000001"]

    # case-insensitive on both sides
    results = lr.search_runs(goal="LoGiN")
    assert [r["handle_id"] for r in results] == ["s0000001"]

    assert lr.search_runs(goal="nonexistent-goal-text") == []


def test_search_runs_status_filter_uses_effective_status(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000003", "Curated success run", success_class="success")
    _make_run(tmp_path, "s0000004", "Curated failure run", success_class="failed")
    _make_run(tmp_path, "s0000005", "Not yet curated run")  # no run_card.json

    assert [r["handle_id"] for r in lr.search_runs(status="success")] == ["s0000003"]
    assert [r["handle_id"] for r in lr.search_runs(status="failed")] == ["s0000004"]
    # falls back to raw process status ("done") when no success_class exists
    assert [r["handle_id"] for r in lr.search_runs(status="done")] == ["s0000005"]
    # case-insensitive
    assert [r["handle_id"] for r in lr.search_runs(status="SUCCESS")] == ["s0000003"]


def test_search_runs_lane_filter(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000006", "An agenda run", lane="agenda")
    _make_run(tmp_path, "s0000007", "A now run", lane="now")

    assert [r["handle_id"] for r in lr.search_runs(lane="now")] == ["s0000007"]
    assert [r["handle_id"] for r in lr.search_runs(lane="AGENDA")] == ["s0000006"]


def test_search_runs_date_range_inclusive_bare_dates(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000008", "Early run", started_at="2026-07-01T00:05:00+00:00")
    _make_run(tmp_path, "s0000009", "Mid run", started_at="2026-07-05T12:00:00+00:00")
    _make_run(tmp_path, "s0000010", "Late run", started_at="2026-07-10T00:05:00+00:00")

    results = lr.search_runs(since="2026-07-04", until="2026-07-06")
    assert [r["handle_id"] for r in results] == ["s0000009"]

    # a bare `until` date must include the whole day, not exclude everything
    # after midnight (the naive lexicographic-string-compare bug).
    results = lr.search_runs(since="2026-07-10", until="2026-07-10")
    assert [r["handle_id"] for r in results] == ["s0000010"]

    results = lr.search_runs(since="2026-07-01")
    assert {r["handle_id"] for r in results} == {"s0000008", "s0000009", "s0000010"}


def test_search_runs_combined_filters_and(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000011", "Fix login bug", lane="agenda", success_class="success")
    _make_run(tmp_path, "s0000012", "Fix login bug", lane="now", success_class="success")

    results = lr.search_runs(goal="login", lane="agenda", status="success")
    assert [r["handle_id"] for r in results] == ["s0000011"]


def test_search_runs_no_filters_returns_all_newest_first(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000013", "First", started_at="2026-07-01T00:00:00+00:00")
    _make_run(tmp_path, "s0000014", "Second", started_at="2026-07-05T00:00:00+00:00")

    results = lr.search_runs()
    assert [r["handle_id"] for r in results] == ["s0000014", "s0000013"]


def test_search_runs_empty_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    assert lr.search_runs(goal="anything") == []


# ---------------------------------------------------------------------------
# Index filter UI (client-side search bar baked into the static index.html)
# ---------------------------------------------------------------------------

def test_index_html_includes_filter_bar_and_row_data_attrs(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000015", "Fix the flaky login test", lane="agenda",
              started_at="2026-07-01T00:05:00+00:00", success_class="success")

    out = lr.write_runs_index(force=True)
    content = Path(out).read_text()

    # filter controls present
    for control_id in ("f-goal", "f-status", "f-lane", "f-since", "f-until", "f-clear", "f-count"):
        assert f'id="{control_id}"' in content

    # row carries lowercased goal text + status/lane/date for JS filtering
    assert 'data-goal="fix the flaky login test"' in content
    assert 'data-status="success"' in content
    assert 'data-lane="agenda"' in content
    assert 'data-date="2026-07-01"' in content

    # the filter JS itself is inlined (no external script / no new endpoint)
    assert "f-goal" in content and "addEventListener" in content


def test_index_html_omits_filter_bar_when_no_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    out = lr.write_runs_index(force=True)
    content = Path(out).read_text()
    assert "No runs yet" in content
    assert 'id="f-goal"' not in content


def test_index_html_status_dropdown_options_reflect_actual_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _make_run(tmp_path, "s0000016", "Run A", success_class="success")
    _make_run(tmp_path, "s0000017", "Run B", success_class="failed")

    out = lr.write_runs_index(force=True)
    content = Path(out).read_text()
    assert '<option value="success">' in content
    assert '<option value="failed">' in content
    # no bogus options for statuses that don't occur
    assert '<option value="partial">' not in content
