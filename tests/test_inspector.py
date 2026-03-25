"""Tests for inspector.py — Phase 12: quality oversight + friction detection."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from inspector import (
    ALL_SIGNALS,
    SIGNAL_ERROR_EVENTS,
    SIGNAL_BACKTRACKING,
    SIGNAL_CONTEXT_CHURN,
    SIGNAL_ESCALATION_TONE,
    SIGNAL_PLATFORM_CONFUSION,
    SIGNAL_ABANDONED_TOOL_FLOW,
    FrictionSignal,
    SessionQuality,
    InspectionReport,
    detect_friction_signals,
    assess_goal_alignment,
    inspect_session,
    run_inspector,
    get_latest_inspection,
    get_friction_summary,
    _save_inspection_report,
    _save_inspection_suggestions,
    _inspection_log_path,
    _suggestions_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_outcome(
    status="done",
    summary="",
    tokens_in=0,
    goal="test goal",
    project="test-proj",
    outcome_id="abc12345",
) -> dict:
    return {
        "outcome_id": outcome_id,
        "goal": goal,
        "task_type": "general",
        "status": status,
        "summary": summary,
        "lessons": [],
        "project": project,
        "tokens_in": tokens_in,
        "tokens_out": 0,
        "elapsed_ms": 100,
        "recorded_at": "2026-03-25T00:00:00+00:00",
    }


def _mock_adapter(response_text: str):
    adapter = MagicMock()
    resp = MagicMock()
    resp.content = response_text
    adapter.complete.return_value = resp
    return adapter


# ---------------------------------------------------------------------------
# Friction signal detection
# ---------------------------------------------------------------------------

def test_detect_friction_no_signals():
    outcome = _make_outcome(status="done", summary="Completed successfully.")
    signals = detect_friction_signals(outcome)
    assert signals == []


def test_detect_friction_error_events():
    outcome = _make_outcome(
        status="stuck",
        summary="Task failed: LLM call failed after 3 retries.",
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_ERROR_EVENTS in types


def test_detect_friction_error_events_api():
    outcome = _make_outcome(
        status="stuck",
        summary="API timeout encountered during execution.",
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_ERROR_EVENTS in types


def test_detect_friction_backtracking():
    outcome = _make_outcome(
        status="stuck",
        summary="Agent produced same outcome as previous step — repeated approach.",
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_BACKTRACKING in types


def test_detect_friction_backtracking_loop_detected():
    outcome = _make_outcome(
        status="stuck",
        summary="Loop detected: agent already tried this path.",
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_BACKTRACKING in types


def test_detect_friction_escalation_tone():
    outcome = _make_outcome(
        status="stuck",
        summary="Critical failure: process failed. Failed again. Another critical failed attempt.",
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_ESCALATION_TONE in types


def test_detect_friction_escalation_needs_3_hits():
    # Only 2 "failed" — should not trigger
    outcome = _make_outcome(
        status="stuck",
        summary="Task failed. Another failure here.",
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_ESCALATION_TONE not in types


def test_detect_friction_context_churn():
    outcome = _make_outcome(
        status="stuck",
        summary="Got stuck with large context.",
        tokens_in=15000,
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_CONTEXT_CHURN in types


def test_detect_friction_context_churn_not_triggered_low_tokens():
    outcome = _make_outcome(
        status="stuck",
        summary="Got stuck.",
        tokens_in=5000,
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_CONTEXT_CHURN not in types


def test_detect_friction_context_churn_not_triggered_done():
    # High tokens but done = no churn signal
    outcome = _make_outcome(
        status="done",
        summary="Completed with lots of context.",
        tokens_in=20000,
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_CONTEXT_CHURN not in types


def test_detect_friction_platform_confusion():
    outcome = _make_outcome(
        status="stuck",
        summary="Platform confusion: wrong platform for this task.",
    )
    signals = detect_friction_signals(outcome)
    types = [s.signal_type for s in signals]
    assert SIGNAL_PLATFORM_CONFUSION in types


def test_detect_friction_evidence_truncated():
    long_summary = "LLM call failed " + "x" * 200
    outcome = _make_outcome(status="stuck", summary=long_summary)
    signals = detect_friction_signals(outcome)
    assert all(len(s.evidence) <= 120 for s in signals)


def test_detect_friction_session_id_set():
    outcome = _make_outcome(status="stuck", summary="LLM call failed.", outcome_id="sess001")
    signals = detect_friction_signals(outcome)
    assert all(s.session_id == "sess001" for s in signals)


# ---------------------------------------------------------------------------
# Goal alignment assessment
# ---------------------------------------------------------------------------

def test_assess_alignment_no_adapter():
    score = assess_goal_alignment("research X", "Found information about X.", adapter=None)
    assert score == 0.7


def test_assess_alignment_mock_adapter():
    adapter = _mock_adapter("0.85")
    score = assess_goal_alignment("research X", "Found X information.", adapter=adapter)
    assert score == pytest.approx(0.85, abs=0.01)


def test_assess_alignment_bad_response_returns_default():
    adapter = _mock_adapter("not a number at all")
    score = assess_goal_alignment("research X", "Found X.", adapter=adapter)
    assert score == 0.5


def test_assess_alignment_exception_returns_default():
    adapter = MagicMock()
    adapter.complete.side_effect = RuntimeError("LLM down")
    score = assess_goal_alignment("research X", "Found X.", adapter=adapter)
    assert score == 0.5


# ---------------------------------------------------------------------------
# Session inspection
# ---------------------------------------------------------------------------

def test_inspect_session_good():
    outcome = _make_outcome(status="done", summary="Research completed successfully.")
    sq = inspect_session(outcome, adapter=None)
    # alignment=0.7 (default), no friction → good
    assert sq.overall_quality == "good"


def test_inspect_session_poor_high_friction():
    outcome = _make_outcome(
        status="stuck",
        summary="LLM call failed repeatedly. Critical failure. Failed again. Failed more.",
        tokens_in=20000,
    )
    sq = inspect_session(outcome, adapter=None)
    assert sq.overall_quality == "poor"


def test_inspect_session_poor_low_alignment():
    adapter = _mock_adapter("0.2")
    outcome = _make_outcome(status="done", summary="Did something unrelated.")
    sq = inspect_session(outcome, adapter=adapter)
    assert sq.overall_quality == "poor"


def test_inspect_session_fair():
    # Moderate alignment (default 0.7) + medium friction → fair → good (0.7 >= 0.7 and no high)
    # To get "fair": need alignment between 0.4-0.7 with medium friction
    adapter = _mock_adapter("0.55")
    outcome = _make_outcome(
        status="stuck",
        summary="Same outcome repeated, some partial progress.",
    )
    sq = inspect_session(outcome, adapter=adapter)
    assert sq.overall_quality == "fair"


def test_inspect_session_delight_signals():
    outcome = _make_outcome(status="done", summary="Task completed successfully.")
    sq = inspect_session(outcome, adapter=None)
    assert "task_completed_successfully" in sq.delight_signals


def test_inspect_session_no_delight_if_stuck():
    outcome = _make_outcome(status="stuck", summary="Failed to complete.")
    sq = inspect_session(outcome, adapter=None)
    assert sq.delight_signals == []


def test_inspect_session_fields_set():
    outcome = _make_outcome(
        status="done",
        summary="Finished.",
        goal="do the thing",
        project="proj-x",
        outcome_id="oid001",
    )
    sq = inspect_session(outcome, adapter=None)
    assert sq.session_id == "oid001"
    assert sq.project == "proj-x"
    assert sq.status == "done"
    assert 0.0 <= sq.goal_alignment_score <= 1.0
    assert sq.inspected_at != ""


def test_inspect_session_goal_truncated_to_80():
    outcome = _make_outcome(goal="x" * 200)
    sq = inspect_session(outcome, adapter=None)
    assert len(sq.goal) <= 80


# ---------------------------------------------------------------------------
# run_inspector
# ---------------------------------------------------------------------------

def test_run_inspector_empty_outcomes(tmp_path):
    with patch("inspector.load_outcomes", return_value=[]):
        with patch("inspector._inspection_log_path", return_value=tmp_path / "inspection-log.jsonl"):
            report = run_inspector(adapter=None, verbose=False)
    assert report.inspected_sessions == 0
    assert report.run_id != ""


def test_run_inspector_dry_run(tmp_path):
    from memory import Outcome

    fake_outcomes = [
        Outcome(
            outcome_id=f"o{i}",
            goal="test goal",
            task_type="general",
            status="done",
            summary="completed",
            lessons=[],
        )
        for i in range(3)
    ]
    with patch("inspector.load_outcomes", return_value=fake_outcomes):
        with patch("inspector._inspection_log_path", return_value=tmp_path / "log.jsonl"):
            with patch("inspector._suggestions_path", return_value=tmp_path / "suggestions.jsonl"):
                report = run_inspector(adapter=None, dry_run=True, verbose=False)
    assert report.inspected_sessions == 3
    # dry_run — log file may or may not be written; no crash
    assert isinstance(report.quality_distribution, dict)


def test_run_inspector_saves_log(tmp_path):
    from memory import Outcome

    fake_outcomes = [
        Outcome(
            outcome_id="o1",
            goal="goal",
            task_type="general",
            status="done",
            summary="done",
            lessons=[],
        )
    ]
    log_path = tmp_path / "inspection-log.jsonl"
    with patch("inspector.load_outcomes", return_value=fake_outcomes):
        with patch("inspector._inspection_log_path", return_value=log_path):
            with patch("inspector._suggestions_path", return_value=tmp_path / "suggestions.jsonl"):
                run_inspector(adapter=None, verbose=False)
    assert log_path.exists()
    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["inspected_sessions"] == 1


def test_run_inspector_feeds_evolver(tmp_path):
    """Inspector suggestions should be written to suggestions.jsonl."""
    from memory import Outcome

    fake_outcomes = [
        Outcome(
            outcome_id=f"o{i}",
            goal="goal",
            task_type="general",
            status="done",
            summary="done",
            lessons=[],
        )
        for i in range(3)
    ]
    suggestions_path = tmp_path / "suggestions.jsonl"
    adapter = _mock_adapter(json.dumps({
        "patterns": ["pattern A"],
        "suggestions": ["suggestion 1", "suggestion 2"],
        "threshold_breaches": [],
    }))

    with patch("inspector.load_outcomes", return_value=fake_outcomes):
        with patch("inspector._inspection_log_path", return_value=tmp_path / "log.jsonl"):
            with patch("inspector._suggestions_path", return_value=suggestions_path):
                run_inspector(adapter=adapter, verbose=False)

    assert suggestions_path.exists()
    lines = [l for l in suggestions_path.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1


def test_run_inspector_quality_distribution(tmp_path):
    from memory import Outcome

    # 2 done (good), 1 stuck (poor)
    fake_outcomes = [
        Outcome(outcome_id="o1", goal="g", task_type="general", status="done", summary="completed", lessons=[]),
        Outcome(outcome_id="o2", goal="g", task_type="general", status="done", summary="completed", lessons=[]),
        Outcome(
            outcome_id="o3",
            goal="g",
            task_type="general",
            status="stuck",
            summary="LLM call failed. Critical critical critical.",
            lessons=[],
            tokens_in=15000,
        ),
    ]
    with patch("inspector.load_outcomes", return_value=fake_outcomes):
        with patch("inspector._inspection_log_path", return_value=tmp_path / "log.jsonl"):
            with patch("inspector._suggestions_path", return_value=tmp_path / "sug.jsonl"):
                report = run_inspector(adapter=None, verbose=False)
    assert report.inspected_sessions == 3
    total = sum(report.quality_distribution.values())
    assert total == 3


def test_run_inspector_threshold_breach(tmp_path):
    from memory import Outcome

    # All stuck with API errors → error_events in all sessions → breach
    fake_outcomes = [
        Outcome(
            outcome_id=f"o{i}",
            goal="g",
            task_type="general",
            status="stuck",
            summary="LLM call failed again.",
            lessons=[],
        )
        for i in range(5)
    ]
    with patch("inspector.load_outcomes", return_value=fake_outcomes):
        with patch("inspector._inspection_log_path", return_value=tmp_path / "log.jsonl"):
            with patch("inspector._suggestions_path", return_value=tmp_path / "sug.jsonl"):
                report = run_inspector(adapter=None, verbose=False)
    # error_events should have crossed the 30% threshold (100% of sessions)
    assert SIGNAL_ERROR_EVENTS in report.threshold_breaches


# ---------------------------------------------------------------------------
# get_latest_inspection
# ---------------------------------------------------------------------------

def test_get_latest_inspection_empty(tmp_path):
    with patch("inspector._inspection_log_path", return_value=tmp_path / "nonexistent.jsonl"):
        result = get_latest_inspection()
    assert result is None


def test_get_latest_inspection_returns_last(tmp_path):
    log_path = tmp_path / "inspection-log.jsonl"
    r1 = InspectionReport(run_id="aaa", inspected_sessions=1)
    r2 = InspectionReport(run_id="bbb", inspected_sessions=2)
    with log_path.open("w") as f:
        f.write(json.dumps(r1.to_dict()) + "\n")
        f.write(json.dumps(r2.to_dict()) + "\n")
    with patch("inspector._inspection_log_path", return_value=log_path):
        result = get_latest_inspection()
    assert result is not None
    assert result.run_id == "bbb"


# ---------------------------------------------------------------------------
# InspectionReport.summary
# ---------------------------------------------------------------------------

def test_inspection_report_summary():
    report = InspectionReport(
        run_id="test123",
        inspected_sessions=10,
        quality_distribution={"good": 7, "fair": 2, "poor": 1},
        alignment_score_avg=0.75,
        patterns=["pattern A", "pattern B"],
        suggestions=["suggestion 1"],
        threshold_breaches=["error_events"],
        elapsed_ms=42,
    )
    s = report.summary()
    assert s  # non-empty
    assert "test123" in s
    assert "10" in s


# ---------------------------------------------------------------------------
# evolver — friction-aware extensions
# ---------------------------------------------------------------------------

def test_evolver_friction_aware(tmp_path):
    """run_evolver_with_friction should run without crash and return EvolverReport."""
    from evolver import run_evolver_with_friction
    from memory import Outcome

    fake_outcomes = [
        Outcome(
            outcome_id=f"o{i}",
            goal="do something",
            task_type="general",
            status="done",
            summary="done",
            lessons=[],
        )
        for i in range(5)
    ]
    with patch("evolver.load_outcomes", return_value=fake_outcomes):
        with patch("evolver._suggestions_path", return_value=tmp_path / "sug.jsonl"):
            with patch("inspector._inspection_log_path", return_value=tmp_path / "log.jsonl"):
                report = run_evolver_with_friction(dry_run=True, verbose=False)
    assert report.outcomes_reviewed == 5
    assert not report.skipped


def test_get_friction_summary_evolver(tmp_path):
    """get_friction_summary from evolver should return a string when inspection exists."""
    from evolver import get_friction_summary

    log_path = tmp_path / "inspection-log.jsonl"
    r = InspectionReport(
        run_id="xyz",
        inspected_sessions=5,
        quality_distribution={"good": 3, "fair": 1, "poor": 1},
        alignment_score_avg=0.72,
    )
    with log_path.open("w") as f:
        f.write(json.dumps(r.to_dict()) + "\n")

    with patch("inspector._inspection_log_path", return_value=log_path):
        summary = get_friction_summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_get_friction_summary_empty(tmp_path):
    from evolver import get_friction_summary

    with patch("inspector._inspection_log_path", return_value=tmp_path / "nonexistent.jsonl"):
        summary = get_friction_summary()
    assert summary == ""


# ---------------------------------------------------------------------------
# inspector_loop — runs once with mock
# ---------------------------------------------------------------------------

def test_inspector_loop_runs_once(tmp_path):
    """inspector_loop should call run_inspector at least once."""
    import threading
    from inspector import inspector_loop

    call_count = {"n": 0}
    original_run = __import__("inspector").run_inspector

    def fake_run(*a, **kw):
        call_count["n"] += 1
        raise KeyboardInterrupt  # stop after first run

    with patch("inspector.run_inspector", side_effect=fake_run):
        with patch("inspector.build_adapter", return_value=None):
            try:
                inspector_loop(interval_seconds=0.01, adapter=None, verbose=False)
            except KeyboardInterrupt:
                pass

    assert call_count["n"] >= 1


# ---------------------------------------------------------------------------
# heartbeat — quality_summary field
# ---------------------------------------------------------------------------

def test_heartbeat_includes_quality():
    """HeartbeatReport should have quality_summary field."""
    from heartbeat import HeartbeatReport

    report = HeartbeatReport(
        run_id="r1",
        checked_at="2026-01-01T00:00:00+00:00",
        health_status="healthy",
        checks={},
    )
    assert hasattr(report, "quality_summary")
    assert isinstance(report.quality_summary, str)


def test_heartbeat_quality_summary_in_to_dict():
    """quality_summary should be serialized in to_dict()."""
    from heartbeat import HeartbeatReport

    report = HeartbeatReport(
        run_id="r1",
        checked_at="2026-01-01T00:00:00+00:00",
        health_status="healthy",
        checks={},
        quality_summary="Inspector: all good",
    )
    d = report.to_dict()
    assert "quality_summary" in d
    assert d["quality_summary"] == "Inspector: all good"


def test_run_heartbeat_populates_quality_summary(tmp_path):
    """run_heartbeat should try to populate quality_summary from inspector."""
    import heartbeat as _hb

    mock_health = MagicMock()
    mock_health.status = "healthy"
    mock_health.checks = {}
    mock_health.checked_at = "2026-01-01T00:00:00+00:00"

    with patch("heartbeat.check_system_health", return_value=mock_health):
        with patch("heartbeat.check_all_projects", return_value=[]):
            with patch("heartbeat.write_heartbeat_state", return_value=None):
                with patch("heartbeat._log_heartbeat", return_value=None):
                    with patch("inspector.get_friction_summary", return_value="Inspector: 3 sessions, all good"):
                        report = _hb.run_heartbeat(dry_run=True, verbose=False, escalate=False)

    # quality_summary may be set if inspector import worked; just verify it's a string
    assert isinstance(report.quality_summary, str)


# ---------------------------------------------------------------------------
# Phase 12 spec API — detect_friction (SpecFrictionSignal, 7-signal model)
# ---------------------------------------------------------------------------

from inspector import (
    SpecFrictionSignal,
    AlignmentResult,
    InspectorReport,
    detect_friction,
    check_alignment,
    cluster_patterns,
    generate_tickets,
    run_full_inspector,
    format_inspector_report,
    FRICTION_TYPES,
    SIGNAL_REPEATED_REPHRASE,
    SIGNAL_CONTEXT_CHURN,
    _inspector_report_log_path,
    _friction_signals_log_path,
)


def _make_spec_outcome(
    status="done",
    goal="complete the task",
    summary="Task completed successfully.",
    project="proj-a",
    outcome_id="out-001",
    stuck_reason="",
    lessons=None,
    tokens_in=100,
) -> dict:
    return {
        "outcome_id": outcome_id,
        "goal": goal,
        "status": status,
        "summary": summary,
        "stuck_reason": stuck_reason,
        "result_summary": summary,
        "project": project,
        "lessons": lessons or [],
        "tokens_in": tokens_in,
        "tokens_out": 50,
        "elapsed_ms": 200,
        "recorded_at": "2026-03-25T00:00:00+00:00",
    }


def test_detect_friction_error_events():
    """Stuck outcomes produce error_events SpecFrictionSignal."""
    outcomes = [
        _make_spec_outcome(status="stuck", goal="write a report", outcome_id="s1"),
        _make_spec_outcome(status="done", goal="check email", outcome_id="s2"),
    ]
    signals = detect_friction(outcomes)
    types = [s.signal_type for s in signals]
    assert SIGNAL_ERROR_EVENTS in types


def test_detect_friction_escalation_tone():
    """Outcomes with escalation keywords produce escalation_tone signals."""
    outcomes = [
        _make_spec_outcome(
            status="done",
            summary="Task is broken and failed after multiple errors, system cannot continue.",
            outcome_id="e1",
        ),
    ]
    signals = detect_friction(outcomes)
    types = [s.signal_type for s in signals]
    assert SIGNAL_ESCALATION_TONE in types


def test_detect_friction_abandoned_tool_flow():
    """Blocked outcome with short result triggers abandoned_tool_flow."""
    outcomes = [
        _make_spec_outcome(status="stuck", summary="", outcome_id="at1"),
    ]
    signals = detect_friction(outcomes)
    types = [s.signal_type for s in signals]
    assert SIGNAL_ABANDONED_TOOL_FLOW in types


def test_detect_friction_repeated_rephrasing():
    """Same goal stuck 3+ times triggers repeated_rephrasing."""
    outcomes = [
        _make_spec_outcome(status="stuck", goal="write the report", outcome_id=f"r{i}")
        for i in range(4)
    ]
    signals = detect_friction(outcomes)
    types = [s.signal_type for s in signals]
    assert SIGNAL_REPEATED_REPHRASE in types


def test_detect_friction_no_signals_clean_outcomes():
    """All-done outcomes with no escalation language produce no signals."""
    outcomes = [
        _make_spec_outcome(status="done", summary="Task completed without issues.", outcome_id=f"c{i}")
        for i in range(3)
    ]
    signals = detect_friction(outcomes)
    # error_events only fires on stuck/error; these are all done
    error_signals = [s for s in signals if s.signal_type == SIGNAL_ERROR_EVENTS]
    assert len(error_signals) == 0


def test_detect_friction_context_churn():
    """Stuck outcome with lessons loaded → context_churn signal."""
    outcomes = [
        _make_spec_outcome(
            status="stuck",
            lessons=["lesson A", "lesson B"],
            outcome_id="cc1",
        ),
    ]
    signals = detect_friction(outcomes)
    types = [s.signal_type for s in signals]
    assert SIGNAL_CONTEXT_CHURN in types


def test_detect_friction_returns_spec_friction_signals():
    """detect_friction returns SpecFrictionSignal instances, not old FrictionSignal."""
    outcomes = [_make_spec_outcome(status="stuck", outcome_id="x1")]
    signals = detect_friction(outcomes)
    for s in signals:
        assert isinstance(s, SpecFrictionSignal)


def test_detect_friction_severity_float():
    """SpecFrictionSignal.severity is a float in [0, 1]."""
    outcomes = [_make_spec_outcome(status="stuck", outcome_id="sv1")]
    signals = detect_friction(outcomes)
    assert signals
    for s in signals:
        assert isinstance(s.severity, float)
        assert 0.0 <= s.severity <= 1.0


def test_detect_friction_evidence_populated():
    """Evidence field should be a non-empty string for error_events signals."""
    outcomes = [_make_spec_outcome(status="stuck", goal="do something", outcome_id="ev1")]
    signals = detect_friction(outcomes)
    error_sigs = [s for s in signals if s.signal_type == SIGNAL_ERROR_EVENTS]
    assert error_sigs
    assert error_sigs[0].evidence != ""


def test_detect_friction_session_id_populated():
    """SpecFrictionSignal.session_id should be set to outcome_id."""
    outcomes = [_make_spec_outcome(status="stuck", outcome_id="session-abc")]
    signals = detect_friction(outcomes)
    error_sigs = [s for s in signals if s.signal_type == SIGNAL_ERROR_EVENTS]
    assert error_sigs
    assert error_sigs[0].session_id == "session-abc"


# ---------------------------------------------------------------------------
# Phase 12 spec API — check_alignment
# ---------------------------------------------------------------------------

def test_check_alignment_heuristic_done():
    """Heuristic: done status → aligned=True, score=0.8."""
    session = _make_spec_outcome(status="done", goal="do task", summary="did the task")
    result = check_alignment(session, adapter=None)
    assert isinstance(result, AlignmentResult)
    assert result.aligned is True
    assert result.alignment_score == pytest.approx(0.8)
    assert result.gaps == []


def test_check_alignment_heuristic_stuck():
    """Heuristic: stuck status → aligned=False, score=0.3."""
    session = _make_spec_outcome(status="stuck", goal="do task", summary="couldn't do it")
    result = check_alignment(session, adapter=None)
    assert result.aligned is False
    assert result.alignment_score == pytest.approx(0.3)
    assert len(result.gaps) > 0


def test_check_alignment_with_mock_adapter():
    """Mock adapter returning valid JSON is parsed correctly."""
    session = _make_spec_outcome(status="done", goal="write report", summary="wrote the report")
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = '{"aligned": true, "score": 0.92, "gaps": []}'
    mock_adapter.complete.return_value = mock_resp

    result = check_alignment(session, adapter=mock_adapter)
    assert result.aligned is True
    assert result.alignment_score == pytest.approx(0.92)
    assert result.gaps == []


def test_check_alignment_fields_populated():
    """AlignmentResult fields all set correctly from heuristic path."""
    session = _make_spec_outcome(status="done", goal="my goal", summary="my summary", outcome_id="chk1")
    result = check_alignment(session, adapter=None)
    assert result.session_id == "chk1"
    assert result.mission_goal == "my goal"
    assert result.work_summary == "my summary"
    assert result.timestamp != ""


def test_check_alignment_bad_json_falls_back():
    """Adapter returning non-JSON falls back to heuristic."""
    session = _make_spec_outcome(status="done", goal="goal", summary="done it")
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = "not json at all"
    mock_adapter.complete.return_value = mock_resp

    result = check_alignment(session, adapter=mock_adapter)
    # Fallback heuristic: status=done → aligned
    assert result.aligned is True


# ---------------------------------------------------------------------------
# Phase 12 spec API — cluster_patterns
# ---------------------------------------------------------------------------

def test_cluster_patterns_empty_signals():
    """No signals → empty pattern list."""
    patterns = cluster_patterns([], adapter=None)
    assert patterns == []


def test_cluster_patterns_fallback_no_adapter():
    """Fallback groups by signal_type when no adapter provided."""
    signals = [
        SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.5, evidence="err"),
        SpecFrictionSignal(session_id="s2", signal_type=SIGNAL_ERROR_EVENTS, severity=0.7, evidence="err2"),
        SpecFrictionSignal(session_id="s3", signal_type=SIGNAL_ESCALATION_TONE, severity=0.4, evidence="tone"),
    ]
    patterns = cluster_patterns(signals, adapter=None)
    assert len(patterns) >= 1
    assert any("error_events" in p for p in patterns)


def test_cluster_patterns_with_mock_adapter():
    """Mock adapter returning JSON array of strings is used."""
    signals = [
        SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.5, evidence="err"),
    ]
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = '["Pattern A: recurring errors", "Pattern B: tool failures"]'
    mock_adapter.complete.return_value = mock_resp

    patterns = cluster_patterns(signals, adapter=mock_adapter)
    assert "Pattern A: recurring errors" in patterns
    assert "Pattern B: tool failures" in patterns


def test_cluster_patterns_at_most_three():
    """Result capped at 3 patterns even with many signals."""
    signals = [
        SpecFrictionSignal(session_id=f"s{i}", signal_type=SIGNAL_ERROR_EVENTS, severity=0.5, evidence="e")
        for i in range(20)
    ]
    patterns = cluster_patterns(signals, adapter=None)
    assert len(patterns) <= 3


# ---------------------------------------------------------------------------
# Phase 12 spec API — generate_tickets
# ---------------------------------------------------------------------------

def test_generate_tickets_no_patterns():
    """No patterns → empty ticket list."""
    tickets = generate_tickets([], [], adapter=None)
    assert tickets == []


def test_generate_tickets_creates_tickets():
    """One pattern → one ticket with required fields."""
    signals = [
        SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.8, evidence="err"),
    ]
    patterns = ["error_events: recurring failures"]
    tickets = generate_tickets(patterns, signals, adapter=None)
    assert len(tickets) == 1
    t = tickets[0]
    assert "title" in t
    assert "pattern" in t
    assert "suggested_fix" in t
    assert "priority" in t
    assert "auto_evolver" in t
    assert t["priority"] in ("high", "medium", "low")


def test_generate_tickets_auto_evolver_flag():
    """High or medium priority tickets should have auto_evolver=True."""
    signals = [
        SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.9, evidence="err"),
    ]
    patterns = ["error_events: severe recurring failures"]
    tickets = generate_tickets(patterns, signals, adapter=None)
    assert tickets
    # With severity 0.9, priority should be high → auto_evolver=True
    assert tickets[0]["auto_evolver"] is True


def test_generate_tickets_low_severity_no_auto_evolver():
    """Low priority tickets should have auto_evolver=False."""
    signals = [
        SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_CONTEXT_CHURN, severity=0.1, evidence="minor"),
    ]
    # Force low priority by making patterns non-first with low severity
    patterns = ["context_churn: minor issue", "extra pattern 2", "extra pattern 3"]
    tickets = generate_tickets(patterns, signals, adapter=None)
    # At least the low-priority ones should have auto_evolver=False
    low_tickets = [t for t in tickets if t["priority"] == "low"]
    for t in low_tickets:
        assert t["auto_evolver"] is False


def test_generate_tickets_with_mock_adapter():
    """Mock adapter returning JSON ticket array is parsed."""
    signals = [
        SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.7, evidence="err"),
    ]
    patterns = ["error_events: repeated failures"]
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = json.dumps([{
        "title": "Fix error recovery",
        "pattern": "error_events",
        "suggested_fix": "Add retry logic",
        "priority": "high",
    }])
    mock_adapter.complete.return_value = mock_resp

    tickets = generate_tickets(patterns, signals, adapter=mock_adapter)
    assert tickets
    assert tickets[0]["title"] == "Fix error recovery"
    assert tickets[0]["auto_evolver"] is True


# ---------------------------------------------------------------------------
# Phase 12 spec API — run_full_inspector
# ---------------------------------------------------------------------------

def test_run_full_inspector_min_sessions_skip():
    """Returns early with 0 sessions if too few outcomes."""
    with patch("inspector.load_outcomes", return_value=[]):
        report = run_full_inspector(dry_run=True, min_sessions=5)
    assert report.sessions_analyzed == 0
    assert "skipped" in report.executive_summary.lower() or "minimum" in report.executive_summary.lower()


def test_run_full_inspector_dry_run():
    """dry_run=True completes without LLM calls and returns InspectorReport."""
    outcomes = [_make_spec_outcome(status="done", outcome_id=f"o{i}") for i in range(6)]
    with patch("inspector.load_outcomes", return_value=outcomes):
        report = run_full_inspector(dry_run=True, min_sessions=3)
    assert isinstance(report, InspectorReport)
    assert report.sessions_analyzed == 6
    assert report.report_id != ""
    assert report.elapsed_ms >= 0


def test_run_full_inspector_writes_log(tmp_path):
    """dry_run=False writes report to inspector-log.jsonl."""
    outcomes = [_make_spec_outcome(status="done", outcome_id=f"wl{i}") for i in range(6)]
    log_path = tmp_path / "inspector-log.jsonl"
    with patch("inspector.load_outcomes", return_value=outcomes), \
         patch("inspector._inspector_report_log_path", return_value=log_path), \
         patch("inspector._friction_signals_log_path", return_value=tmp_path / "fs.jsonl"):
        report = run_full_inspector(dry_run=False, min_sessions=3)

    assert log_path.exists()
    lines = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["report_id"] == report.report_id


def test_run_full_inspector_writes_friction_signals(tmp_path):
    """Friction signals are persisted to friction-signals.jsonl."""
    outcomes = [
        _make_spec_outcome(status="stuck", outcome_id=f"fs{i}") for i in range(6)
    ]
    fs_path = tmp_path / "friction-signals.jsonl"
    with patch("inspector.load_outcomes", return_value=outcomes), \
         patch("inspector._inspector_report_log_path", return_value=tmp_path / "insp.jsonl"), \
         patch("inspector._friction_signals_log_path", return_value=fs_path):
        report = run_full_inspector(dry_run=False, min_sessions=3)

    assert fs_path.exists() or len(report.friction_signals) == 0  # only written if signals exist
    if report.friction_signals:
        assert fs_path.exists()


def test_run_full_inspector_forwards_to_evolver(tmp_path):
    """Auto-evolver tickets forwarded to evolver.receive_inspector_tickets."""
    outcomes = [_make_spec_outcome(status="stuck", outcome_id=f"fe{i}") for i in range(6)]
    with patch("inspector.load_outcomes", return_value=outcomes), \
         patch("inspector._inspector_report_log_path", return_value=tmp_path / "insp.jsonl"), \
         patch("inspector._friction_signals_log_path", return_value=tmp_path / "fs.jsonl"), \
         patch("inspector.receive_inspector_tickets", return_value=1) as mock_receive:
        from inspector import run_full_inspector as _rfi
        report = _rfi(dry_run=False, min_sessions=3)

    # If auto tickets were generated, receive should have been called
    auto_tickets = [t for t in report.evolver_tickets if t.get("auto_evolver")]
    if auto_tickets:
        mock_receive.assert_called_once()


def test_run_full_inspector_returns_inspector_report():
    """Return type is always InspectorReport."""
    with patch("inspector.load_outcomes", return_value=[]):
        report = run_full_inspector(dry_run=True, min_sessions=1)
    assert isinstance(report, InspectorReport)


# ---------------------------------------------------------------------------
# Phase 12 spec API — format_inspector_report
# ---------------------------------------------------------------------------

def test_format_inspector_report_nonempty():
    """format_inspector_report returns a non-empty string."""
    report = InspectorReport(
        report_id="test123",
        sessions_analyzed=5,
        friction_signals=[
            SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.7, evidence="err"),
        ],
        patterns=["error_events: repeated failures"],
        evolver_tickets=[{
            "id": "t1", "title": "Fix errors", "pattern": "error_events",
            "suggested_fix": "add retry", "priority": "high", "auto_evolver": True,
        }],
        executive_summary="System has recurring errors.",
    )
    text = format_inspector_report(report)
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_inspector_report_contains_report_id():
    """Output includes report_id."""
    report = InspectorReport(report_id="myreport99", sessions_analyzed=3)
    text = format_inspector_report(report)
    assert "myreport99" in text


def test_format_inspector_report_shows_sessions_analyzed():
    """Output shows sessions_analyzed count."""
    report = InspectorReport(report_id="r1", sessions_analyzed=12)
    text = format_inspector_report(report)
    assert "12" in text


def test_format_inspector_report_shows_patterns():
    """Patterns are listed in formatted output."""
    report = InspectorReport(
        report_id="r2", sessions_analyzed=5,
        patterns=["Pattern A: something wrong"],
    )
    text = format_inspector_report(report)
    assert "Pattern A" in text


def test_format_inspector_report_shows_evolver_tickets():
    """Evolver tickets appear in output with auto marker."""
    report = InspectorReport(
        report_id="r3", sessions_analyzed=5,
        evolver_tickets=[{
            "id": "t1", "title": "Improve prompts", "pattern": "ep",
            "suggested_fix": "rewrite", "priority": "high", "auto_evolver": True,
        }],
    )
    text = format_inspector_report(report)
    assert "Improve prompts" in text
    assert "[auto]" in text


def test_format_inspector_report_alignment_ok_miss():
    """OK/MISS flags appear for aligned/misaligned sessions."""
    report = InspectorReport(
        report_id="r4", sessions_analyzed=2,
        alignment_results=[
            AlignmentResult(
                session_id="s1", mission_goal="goal A", work_summary="did it",
                aligned=True, alignment_score=0.9, gaps=[],
            ),
            AlignmentResult(
                session_id="s2", mission_goal="goal B", work_summary="failed",
                aligned=False, alignment_score=0.2, gaps=["incomplete"],
            ),
        ],
    )
    text = format_inspector_report(report)
    assert "OK" in text
    assert "MISS" in text


# ---------------------------------------------------------------------------
# Phase 12 — InspectorReport round-trip
# ---------------------------------------------------------------------------

def test_inspector_report_round_trip():
    """InspectorReport serializes and deserializes without data loss."""
    report = InspectorReport(
        report_id="rt1",
        sessions_analyzed=7,
        friction_signals=[
            SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.6, evidence="ev"),
        ],
        alignment_results=[
            AlignmentResult(
                session_id="s2", mission_goal="goal", work_summary="done",
                aligned=True, alignment_score=0.85, gaps=[],
            ),
        ],
        patterns=["pat1"],
        evolver_tickets=[{"id": "t1", "title": "fix", "priority": "high", "auto_evolver": True}],
        executive_summary="All good.",
        elapsed_ms=42,
    )
    d = report.to_dict()
    r2 = InspectorReport.from_dict(d)
    assert r2.report_id == "rt1"
    assert r2.sessions_analyzed == 7
    assert len(r2.friction_signals) == 1
    assert r2.friction_signals[0].signal_type == SIGNAL_ERROR_EVENTS
    assert len(r2.alignment_results) == 1
    assert r2.alignment_results[0].aligned is True
    assert r2.patterns == ["pat1"]
    assert r2.executive_summary == "All good."
    assert r2.elapsed_ms == 42


def test_inspector_report_executive_summary():
    """InspectorReport.summary() includes key counts."""
    report = InspectorReport(
        report_id="sum1",
        sessions_analyzed=8,
        friction_signals=[
            SpecFrictionSignal(session_id="s1", signal_type=SIGNAL_ERROR_EVENTS, severity=0.5, evidence="e"),
        ],
        patterns=["p1", "p2"],
        evolver_tickets=[{"id": "t1"}],
        executive_summary="Two key patterns found.",
    )
    text = report.summary()
    assert "sum1" in text
    assert "8" in text
    assert "Two key patterns" in text


# ---------------------------------------------------------------------------
# Phase 12 — FRICTION_TYPES dict coverage
# ---------------------------------------------------------------------------

def test_friction_types_covers_all_signals():
    """FRICTION_TYPES has an entry for every signal in ALL_SIGNALS."""
    from inspector import ALL_SIGNALS, FRICTION_TYPES
    for sig in ALL_SIGNALS:
        assert sig in FRICTION_TYPES, f"FRICTION_TYPES missing entry for {sig}"


def test_spec_friction_signal_to_dict_round_trip():
    """SpecFrictionSignal serializes and deserializes correctly."""
    s = SpecFrictionSignal(
        session_id="sess-1",
        signal_type=SIGNAL_ERROR_EVENTS,
        severity=0.75,
        evidence="some evidence here",
        timestamp="2026-03-25T00:00:00+00:00",
    )
    d = s.to_dict()
    s2 = SpecFrictionSignal.from_dict(d)
    assert s2.session_id == "sess-1"
    assert s2.signal_type == SIGNAL_ERROR_EVENTS
    assert s2.severity == pytest.approx(0.75)
    assert s2.evidence == "some evidence here"
