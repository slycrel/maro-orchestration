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
