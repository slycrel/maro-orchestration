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
    assert "Global context" in content
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
