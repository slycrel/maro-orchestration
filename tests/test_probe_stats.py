"""Regression tests for scripts/probe-stats.sh reviewer calibration."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("probe_stats", ROOT / "scripts" / "probe_stats.py")
assert SPEC and SPEC.loader
probe_stats = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe_stats
SPEC.loader.exec_module(probe_stats)


def _event(timestamp: str, status: str, *, event_type: str = "CLAIM_PROBED") -> dict:
    return {
        "timestamp": timestamp,
        "event_type": event_type,
        "context": {"probe_status": status},
    }


def test_calculate_reports_current_previous_rates_and_deltas():
    events = [
        _event("2026-07-05T00:00:00Z", "validated"),
        _event("2026-07-06T00:00:00+00:00", "validated"),
        _event("2026-07-07T00:00:00Z", "dismissed"),
        _event("2026-07-08T00:00:00Z", "unprobed"),
        _event("2026-07-09T00:00:00Z", "unrunnable"),
        _event("2026-06-25T00:00:00Z", "validated"),
        _event("2026-06-26T00:00:00Z", "dismissed"),
        _event("2026-06-27T00:00:00Z", "dismissed"),
    ]

    report = probe_stats.calculate(
        events, days=10, now=datetime(2026, 7, 10, tzinfo=timezone.utc)
    )

    current = report["current"]
    previous = report["previous"]
    assert current["counts"] == {
        "validated": 2, "dismissed": 1, "unprobed": 1,
        "unrunnable": 1, "unknown": 0,
    }
    assert current["reviewer_verdict_retention_rate"] == 0.666667
    assert current["probe_coverage"] == 0.6
    assert previous["reviewer_verdict_retention_rate"] == 0.333333
    assert report["delta_current_minus_previous"]["reviewer_verdict_retention_rate"] == 0.333334


def test_windows_are_start_inclusive_end_exclusive_and_normalize_offsets():
    report = probe_stats.calculate(
        [
            _event("2026-07-09T00:00:00-06:00", "validated"),  # == start
            _event("2026-07-10T06:00:00Z", "dismissed"),       # == end
            _event("not-a-date", "validated"),
        ],
        days=1,
        now=datetime(2026, 7, 10, 6, tzinfo=timezone.utc),
    )
    assert report["current"]["counts"]["validated"] == 1
    assert report["current"]["counts"]["dismissed"] == 0


def test_load_claim_probes_spans_archives_and_skips_bad_rows(tmp_path):
    active = tmp_path / "captains_log.jsonl"
    archive = tmp_path / "captains_log.20260701-000000.jsonl"
    archive.write_text(
        "not json\n"
        + json.dumps(["valid json, wrong shape"]) + "\n"
        + json.dumps(_event("2026-07-01T00:00:00Z", "validated")) + "\n",
        encoding="utf-8",
    )
    active.write_text(
        json.dumps(_event("2026-07-02T00:00:00Z", "dismissed")) + "\n"
        + json.dumps(_event("2026-07-02T00:00:00Z", "validated", event_type="OTHER")) + "\n",
        encoding="utf-8",
    )

    rows = probe_stats.load_claim_probes(active)

    assert [row["context"]["probe_status"] for row in rows] == ["validated", "dismissed"]


def test_log_paths_skip_archives_rotated_before_compared_windows(tmp_path):
    old = tmp_path / "captains_log.20260101-000000.jsonl"
    collision = tmp_path / "captains_log.20260701-000000-2.jsonl"
    unknown = tmp_path / "captains_log.imported.jsonl"
    active = tmp_path / "captains_log.jsonl"
    for path in (old, collision, unknown, active):
        path.write_text("", encoding="utf-8")

    paths = probe_stats._log_paths(
        active, earliest=datetime(2026, 6, 1, tzinfo=timezone.utc)
    )

    assert old not in paths
    assert collision in paths
    assert unknown in paths  # unparseable names stay conservative
    assert active in paths


def test_unknown_status_and_empty_denominators_are_honest():
    report = probe_stats.calculate(
        [_event("2026-07-09T00:00:00Z", "future-status")],
        days=2,
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    assert report["current"]["counts"]["unknown"] == 1
    assert report["current"]["reviewer_verdict_retention_rate"] is None
    assert report["previous"]["probe_coverage"] is None
    assert report["delta_current_minus_previous"]["probe_coverage"] is None


def test_shell_entrypoint_emits_json(tmp_path):
    active = tmp_path / "captains_log.jsonl"
    active.write_text(
        json.dumps(_event("2026-07-09T00:00:00Z", "validated")) + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            str(ROOT / "scripts" / "probe-stats.sh"),
            "--days", "2", "--now", "2026-07-10T00:00:00Z",
            "--log", str(active), "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["current"]["counts"]["validated"] == 1
    assert report["current"]["reviewer_verdict_retention_rate"] == 1.0


def test_missing_log_warns_instead_of_silently_looking_idle(tmp_path):
    proc = subprocess.run(
        [
            str(ROOT / "scripts" / "probe-stats.sh"),
            "--log", str(tmp_path / "missing" / "captains_log.jsonl"),
            "--now", "2026-07-10T00:00:00Z", "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "warning: no captain's-log source found" in proc.stderr
    assert json.loads(proc.stdout)["current"]["total"] == 0


def test_main_without_log_uses_default_captains_log_path(tmp_path, capsys):
    import captains_log

    active = tmp_path / "captains_log.jsonl"
    active.write_text(
        json.dumps(_event("2026-07-09T00:00:00Z", "validated")) + "\n",
        encoding="utf-8",
    )
    captains_log.set_log_path(active)
    try:
        rc = probe_stats.main([
            "--days", "2", "--now", "2026-07-10T00:00:00Z", "--json",
        ])
    finally:
        captains_log.set_log_path(None)

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert json.loads(captured.out)["current"]["counts"]["validated"] == 1
