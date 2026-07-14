"""Regression tests for prospective done-vs-achieved measurement."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verdict_gap_stats", ROOT / "scripts" / "verdict_gap_stats.py"
)
assert SPEC and SPEC.loader
verdict_stats = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verdict_stats
SPEC.loader.exec_module(verdict_stats)


def _row(
    outcome_id: str,
    *,
    measurement_class: str | None = "organic",
    achieved: bool | None = True,
    status: str = "done",
    handle_id: str = "",
    recorded_at: str = "2026-07-14T00:00:00Z",
    dry_run: bool = False,
) -> dict:
    row = {
        "outcome_id": outcome_id,
        "status": status,
        "recorded_at": recorded_at,
        "dry_run": dry_run,
    }
    if measurement_class is not None:
        row["measurement_class"] = measurement_class
    if achieved is not None:
        row["goal_achieved"] = achieved
    if handle_id:
        row["handle_id"] = handle_id
    return row


def test_calculate_counts_only_explicit_judged_organic_rows_for_gate():
    rows = [
        _row("a", achieved=True),
        _row("b", achieved=False),
        _row("c", achieved=None),
        _row("d", measurement_class="smoke"),
        _row("e", measurement_class="control", achieved=False),
        _row("f", measurement_class=None),
        _row("g", measurement_class="made-up"),
        _row("h", dry_run=True),
    ]
    report = verdict_stats.calculate(rows, target=3)
    assert report["class_counts"] == {
        "organic": 3, "smoke": 1, "control": 1, "benchmark": 0, "unknown": 2,
    }
    assert report["dry_run_excluded"] == 1
    assert report["organic"]["judged"] == 2
    assert report["organic"]["unjudged"] == 1
    assert report["organic"]["raw_achieved_rate_among_judged"] == 0.5
    assert report["re_audit_due"] is False
    assert report["judged_organic_runs_remaining"] == 1


def test_done_and_achieved_remain_independent_dimensions():
    rows = [
        _row("a", status="done", achieved=False),
        _row("b", status="incomplete", achieved=True),
        _row("c", status="done", achieved=True),
    ]
    organic = verdict_stats.calculate(rows, target=3)["organic"]
    assert organic["done"] == 2
    assert organic["achieved"] == 2
    assert organic["done_not_achieved"] == 1
    assert organic["not_done_achieved"] == 1


def test_restarts_collapse_to_newest_outcome_for_same_handle():
    rows = [
        _row("old", handle_id="run-1", achieved=False,
             recorded_at="2026-07-14T00:00:00Z"),
        _row("new", handle_id="run-1", achieved=True,
             recorded_at="2026-07-14T00:01:00Z"),
    ]
    report = verdict_stats.calculate(rows, target=1)
    assert report["class_counts"]["organic"] == 1
    assert report["organic"]["achieved"] == 1
    assert report["re_audit_due"] is True


def test_newer_unjudged_continuation_keeps_request_out_of_judged_gate():
    rows = [
        _row("first", handle_id="run-1", achieved=False,
             recorded_at="2026-07-14T00:00:00Z"),
        _row("continuation", handle_id="run-1", achieved=None,
             recorded_at="2026-07-14T00:01:00Z"),
    ]
    report = verdict_stats.calculate(rows, target=1)
    assert report["class_counts"]["organic"] == 1
    assert report["organic"]["judged"] == 0
    assert report["organic"]["unjudged"] == 1
    assert report["re_audit_due"] is False


def test_since_excludes_older_and_unparseable_rows():
    rows = [
        _row("old", recorded_at="2026-07-01T00:00:00Z"),
        _row("new", recorded_at="2026-07-15T00:00:00Z"),
        _row("bad", recorded_at="not-a-date"),
    ]
    report = verdict_stats.calculate(
        rows, since=datetime(2026, 7, 10, tzinfo=timezone.utc)
    )
    assert report["class_counts"]["organic"] == 1
    assert report["since"] == "2026-07-10T00:00:00+00:00"
    assert report["source_rows_after_run_dedup"] == 3
    assert report["source_rows_after_filters"] == 1


def test_shell_entrypoint_emits_json(tmp_path):
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text(json.dumps(_row("a")) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [
            str(ROOT / "scripts" / "verdict-gap-stats.sh"),
            "--outcomes", str(outcomes), "--target", "1", "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["re_audit_due"] is True
