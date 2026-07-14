"""Regression tests for the rolling lesson-intake funnel report."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lesson_funnel_stats", ROOT / "scripts" / "lesson_funnel_stats.py"
)
assert SPEC and SPEC.loader
lesson_funnel = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lesson_funnel
SPEC.loader.exec_module(lesson_funnel)


def _outcome(outcome_id: str, timestamp: str, lessons=None) -> dict:
    return {
        "outcome_id": outcome_id,
        "recorded_at": timestamp,
        "lessons": [] if lessons is None else lessons,
    }


def _event(
    outcome_id: str,
    timestamp: str,
    status: str,
    *,
    extracted: int = 0,
    tiered_ok: int = 0,
    tiered_failed: int = 0,
) -> dict:
    return {
        "timestamp": timestamp,
        "event_type": "LESSON_EXTRACTION",
        "context": {
            "outcome_id": outcome_id,
            "status": status,
            "extracted_count": extracted,
            "tiered_succeeded": tiered_ok,
            "tiered_failed": tiered_failed,
            "dry_run": False,
        },
    }


def test_calculate_distinguishes_completed_zero_from_historical_unknown():
    outcomes = [
        _outcome("productive", "2026-07-09T00:00:00Z"),
        _outcome("zero", "2026-07-09T01:00:00Z"),
        _outcome("unknown", "2026-07-09T02:00:00Z"),
        _outcome("historical", "2026-07-09T03:00:00Z", ["one", "two"]),
        _outcome("failed", "2026-07-09T04:00:00Z"),
        _outcome("deferred", "2026-07-09T05:00:00Z"),
    ]
    events = [
        _event("productive", "2026-07-09T06:00:00Z", "completed", extracted=2, tiered_ok=1, tiered_failed=1),
        _event("zero", "2026-07-09T06:00:01Z", "completed"),
        _event("failed", "2026-07-09T06:00:02Z", "failed"),
        _event("deferred", "2026-07-09T06:00:03Z", "deferred"),
    ]

    report = lesson_funnel.calculate(
        outcomes, events, days=2, now=datetime(2026, 7, 10, tzinfo=timezone.utc)
    )
    current = report["current"]

    assert current["counts"] == {
        "completed": 3,
        "deferred": 1,
        "failed": 1,
        "unknown_not_instrumented": 1,
        "dry_run_excluded": 0,
    }
    assert current["productive_outcomes"] == 2
    assert current["extracted_lessons"] == 4
    assert current["instrumentation_coverage"] == 0.833333
    assert current["productive_rate_among_completed"] == 0.666667
    assert current["tiered_persistence_rate"] == 0.5


def test_latest_event_supersedes_initial_deferred_state():
    outcomes = [_outcome("o1", "2026-07-09T00:00:00Z")]
    events = [
        _event("o1", "2026-07-09T00:01:00Z", "deferred"),
        _event("o1", "2026-07-09T00:02:00Z", "completed", extracted=1, tiered_ok=1),
    ]
    report = lesson_funnel.calculate(
        outcomes, events, days=2, now=datetime(2026, 7, 10, tzinfo=timezone.utc)
    )
    assert report["current"]["counts"]["completed"] == 1
    assert report["current"]["counts"]["deferred"] == 0


def test_instrumented_dry_runs_are_excluded_from_rates():
    outcomes = [
        _outcome("dry", "2026-07-09T00:00:00Z"),
        _outcome("real", "2026-07-09T01:00:00Z"),
    ]
    dry = _event("dry", "2026-07-09T02:00:00Z", "completed", extracted=1)
    dry["context"]["dry_run"] = True
    real = _event("real", "2026-07-09T02:00:01Z", "completed", extracted=0)
    report = lesson_funnel.calculate(
        outcomes, [dry, real], days=2,
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    current = report["current"]
    assert current["total_outcomes"] == 2
    assert current["eligible_outcomes"] == 1
    assert current["counts"]["dry_run_excluded"] == 1
    assert current["productive_rate_among_completed"] == 0.0


def test_legacy_dry_run_placeholder_without_event_is_excluded():
    outcomes = [_outcome(
        "legacy-dry", "2026-07-09T00:00:00Z",
        ["[dry-run lesson] agenda task succeeded: test"],
    )]
    report = lesson_funnel.calculate(
        outcomes, [], days=2,
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    current = report["current"]
    assert current["counts"]["dry_run_excluded"] == 1
    assert current["counts"]["completed"] == 0
    assert current["eligible_outcomes"] == 0


def test_windows_are_outcome_cohorts_not_event_time_windows():
    outcomes = [_outcome("o1", "2026-07-08T12:00:00Z")]
    events = [_event("o1", "2026-07-09T12:00:00Z", "completed", extracted=1)]
    report = lesson_funnel.calculate(
        outcomes, events, days=1, now=datetime(2026, 7, 10, tzinfo=timezone.utc)
    )
    assert report["current"]["total_outcomes"] == 0
    assert report["previous"]["total_outcomes"] == 1
    assert report["previous"]["productive_outcomes"] == 1


def test_load_events_spans_archives_and_skips_malformed_rows(tmp_path):
    active = tmp_path / "captains_log.jsonl"
    archive = tmp_path / "captains_log.20260708-000000.jsonl"
    archive.write_text("bad\n" + json.dumps(_event("a", "2026-07-08T00:00:00Z", "deferred")) + "\n")
    active.write_text(
        json.dumps(_event("a", "2026-07-09T00:00:00Z", "completed")) + "\n"
        + json.dumps({"event_type": "OTHER"}) + "\n"
    )
    rows = lesson_funnel.load_extraction_events(active)
    assert [row["context"]["status"] for row in rows] == ["deferred", "completed"]


def test_shell_entrypoint_emits_json(tmp_path):
    outcomes = tmp_path / "outcomes.jsonl"
    log = tmp_path / "captains_log.jsonl"
    outcomes.write_text(json.dumps(_outcome("o1", "2026-07-09T00:00:00Z")) + "\n")
    log.write_text(json.dumps(_event("o1", "2026-07-09T00:01:00Z", "completed")) + "\n")
    proc = subprocess.run(
        [
            str(ROOT / "scripts" / "lesson-funnel-stats.sh"),
            "--days", "2", "--now", "2026-07-10T00:00:00Z",
            "--outcomes", str(outcomes), "--log", str(log), "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["current"]["counts"]["completed"] == 1


def test_missing_sources_warn_and_report_empty(tmp_path, capsys):
    rc = lesson_funnel.main([
        "--outcomes", str(tmp_path / "missing-outcomes.jsonl"),
        "--log", str(tmp_path / "missing-log.jsonl"),
        "--now", "2026-07-10T00:00:00Z", "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "warning: no outcomes source found" in captured.err
    assert "warning: no captain's-log source found" in captured.err
    assert json.loads(captured.out)["current"]["total_outcomes"] == 0
