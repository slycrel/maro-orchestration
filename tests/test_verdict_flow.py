"""verdict_flow readout pins (chunk B, 2026-07-31).

Synthetic-ledger tests for the flow readout the taste-lens panel ordered
first: verdicts/week by lane + framing→verdict delay, with the honesty
counters (torn lines, dry-run exclusions, not-computable delays) pinned
so silent truncation can't creep back in.
"""

import json
from datetime import datetime, timezone

from verdict_flow import (
    _bucket_label,
    compute_report,
    load_rows,
    main,
    render,
)

# Fixed clock for deterministic week windows: a Friday in ISO week 2026-W31.
NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


def _row(**kw):
    base = {"recorded_at": "2026-07-30T10:00:00+00:00", "task_type": "agenda",
            "status": "done", "goal": "g", "summary": "s"}
    base.update(kw)
    return base


def _write_ledger(tmp_path, rows, torn=0):
    p = tmp_path / "outcomes.jsonl"
    lines = [json.dumps(r) for r in rows] + ['{"torn'] * torn
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Loading honesty
# ---------------------------------------------------------------------------

def test_load_rows_counts_torn_lines(tmp_path):
    p = _write_ledger(tmp_path, [_row(), _row()], torn=3)
    cov = {}
    rows = load_rows(p, cov)
    assert len(rows) == 2
    assert cov["lines_read"] == 5
    assert cov["lines_torn"] == 3


def test_load_rows_missing_file_is_empty(tmp_path):
    cov = {}
    assert load_rows(tmp_path / "nope.jsonl", cov) == []
    assert cov["lines_read"] == 0


def test_dry_run_and_unparsable_ts_excluded_but_counted():
    rows = [
        _row(dry_run=True, goal_achieved=True, goal_verdict_source="closure"),
        _row(recorded_at="not-a-date"),
        _row(),
    ]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["coverage"]["dry_run_excluded"] == 1
    assert report["coverage"]["ts_unparsable"] == 1
    assert report["stock"]["agenda"]["rows"] == 1


# ---------------------------------------------------------------------------
# Stock: judged / stamped-unverifiable / never-stamped split
# ---------------------------------------------------------------------------

def test_stock_three_way_split():
    rows = [
        _row(goal_achieved=True, goal_verdict_source="closure"),
        _row(goal_achieved=False, goal_verdict_source="closure"),
        # "judged, could not verify" — stamp without a goal_achieved value.
        _row(goal_verdict_source="closure_unverifiable"),
        _row(),  # never stamped at all
    ]
    report = compute_report(rows, weeks=6, now=NOW)
    st = report["stock"]["agenda"]
    assert st["rows"] == 4
    assert st["judged"] == 2
    assert st["achieved"] == 1 and st["not_achieved"] == 1
    assert st["stamped_unverifiable"] == 1
    assert st["never_stamped"] == 1
    assert report["sources_judged"] == {"closure": 2}
    assert report["sources_unverifiable"] == {"closure_unverifiable": 1}


def test_stock_separates_lanes():
    rows = [
        _row(task_type="now", goal_achieved=False,
             goal_verdict_source="provenance"),
        _row(task_type="evolver_verify", goal_achieved=True,
             goal_verdict_source="deterministic_tests"),
    ]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["stock"]["now"]["judged"] == 1
    assert report["stock"]["evolver_verify"]["judged"] == 1


# ---------------------------------------------------------------------------
# Flow: verdicts/week bucketed by record week, windowed
# ---------------------------------------------------------------------------

def test_flow_counts_verdict_events_by_arrival_week():
    # Review F2: verdict events bucket by when the verdict ARRIVED
    # (goal_verdict_at; record time for at-record sources), not by the
    # row's record week — a January row judged this week is this week's
    # verdict event, and a record-week bucketing would hide it entirely.
    rows = [
        # Recorded W30, verdict stamped W31 → recorded in W30, verdict in W31.
        _row(recorded_at="2026-07-21T10:00:00+00:00",
             goal_achieved=True, goal_verdict_source="closure",
             goal_verdict_at="2026-07-30T10:00:00+00:00"),
        # Recorded in January (outside window), judged W31 → verdict event
        # in W31, no recorded count anywhere in the window.
        _row(recorded_at="2026-01-05T10:00:00+00:00",
             goal_achieved=False, goal_verdict_source="closure",
             goal_verdict_at="2026-07-31T09:00:00+00:00"),
        # At-record source, no stamp → verdict event in its record week.
        _row(recorded_at="2026-07-30T11:00:00+00:00", task_type="now",
             goal_achieved=False, goal_verdict_source="provenance",
             elapsed_ms=1_000),
        # Unjudged row → recorded only, no verdict event.
        _row(recorded_at="2026-07-21T11:00:00+00:00"),
    ]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["flow"]["2026-W30"]["agenda"] == {"recorded": 2}
    assert report["flow"]["2026-W31"]["agenda"] == {"verdicts": 2, "judged": 2}
    assert report["flow"]["2026-W31"]["now"] == {
        "recorded": 1, "verdicts": 1, "judged": 1}
    # Stock still holds all four — stock is all-time by design.
    assert report["stock"]["agenda"]["rows"] == 3


def test_flow_counts_unverifiable_stamps_as_verdict_events():
    # Review F3: "judged, could not verify" is a verdict event — a week of
    # closure_unverifiable stamps is a moving pipe, not a dead one.
    rows = [_row(goal_verdict_source="closure_unverifiable",
                 goal_verdict_at="2026-07-30T12:00:00+00:00")]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["flow"]["2026-W31"]["agenda"] == {
        "recorded": 1, "verdicts": 1, "unverifiable": 1}


def test_stamped_provenance_prefers_verdict_at_over_source():
    # Review F5: agenda-lane provenance verdicts are stamped post-record —
    # a goal_verdict_at always wins over the at-record source assumption.
    rows = [_row(goal_achieved=False, goal_verdict_source="provenance",
                 goal_verdict_at="2026-07-30T11:00:00+00:00",
                 elapsed_ms=60_000)]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["delay_kind"] == {"stamped_later": 1}
    # 1h stamp gap + 1m run → 1-6h bucket, not the <1m elapsed-only bucket.
    assert report["delay_buckets"] == {"1-6h": 1}


def test_judge_error_rows_counted_not_verdict_events():
    # Review F7 consumer side: attempted-but-failed verdicts are a broken
    # pipe signal — visible, but never counted as verdict events or delays.
    rows = [_row(task_type="now",
                 goal_verdict_source="now_self_verdict_error")]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["coverage"]["judge_error_rows"] == 1
    assert report["flow"]["2026-W31"]["now"] == {"recorded": 1}
    assert report["delay_buckets"] == {}
    assert report["stock"]["now"]["stamped_unverifiable"] == 1
    assert report["sources_unverifiable"] == {"now_self_verdict_error": 1}


# ---------------------------------------------------------------------------
# Delay distribution
# ---------------------------------------------------------------------------

def test_bucket_labels():
    assert _bucket_label(59.0) == "<1m"
    assert _bucket_label(60.0) == "1-10m"
    assert _bucket_label(3599.0) == "10-60m"
    assert _bucket_label(3600.0) == "1-6h"  # boundary belongs to the upper bucket
    assert _bucket_label(7 * 3600.0) == "6-24h"
    assert _bucket_label(48 * 3600.0) == ">24h"


def test_at_record_sources_use_run_duration():
    rows = [
        _row(task_type="now", goal_achieved=False,
             goal_verdict_source="provenance", elapsed_ms=30_000),
        _row(task_type="now", goal_achieved=True,
             goal_verdict_source="now_self_verdict_free", elapsed_ms=30_000),
        _row(task_type="evolver_verify", goal_achieved=True,
             goal_verdict_source="deterministic_tests", elapsed_ms=700_000),
    ]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["delay_kind"]["at_record"] == 3
    assert report["delay_buckets"]["<1m"] == 2
    assert report["delay_buckets"]["10-60m"] == 1


def test_stamped_later_delay_adds_run_duration():
    # Recorded 10:00, verdict stamped 10:30, run took 5m → framing→verdict
    # is 35m (10-60m bucket), record→verdict is 30m.
    rows = [_row(goal_achieved=True, goal_verdict_source="closure",
                 goal_verdict_at="2026-07-30T10:30:00+00:00",
                 elapsed_ms=300_000)]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["delay_kind"]["stamped_later"] == 1
    assert report["delay_buckets"]["10-60m"] == 1
    assert report["delays_stamped_later_s"] == [1800.0]


def test_historic_closure_without_stamp_is_not_computable():
    rows = [_row(goal_achieved=True, goal_verdict_source="closure")]
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["coverage"]["verdict_time_unknown"] == 1
    assert report["delay_buckets"] == {}
    # Unknown arrival week → excluded from flow verdict events too.
    assert "verdicts" not in report["flow"].get("2026-W31", {}).get("agenda", {})


def test_negative_delay_counted_not_bucketed():
    rows = [_row(goal_achieved=True, goal_verdict_source="closure",
                 goal_verdict_at="2026-07-30T09:00:00+00:00")]  # before record
    report = compute_report(rows, weeks=6, now=NOW)
    assert report["coverage"]["delay_negative"] == 1
    assert report["delay_buckets"] == {}


# ---------------------------------------------------------------------------
# Render + CLI
# ---------------------------------------------------------------------------

def test_render_carries_honesty_lines(tmp_path):
    p = _write_ledger(tmp_path, [
        _row(goal_achieved=True, goal_verdict_source="closure"),
        _row(task_type="now", goal_achieved=False,
             goal_verdict_source="provenance", elapsed_ms=1_000),
    ], torn=1)
    cov = {}
    rows = load_rows(p, cov)
    text = render(compute_report(rows, weeks=6, now=NOW), weeks=6,
                  coverage=cov, ledger=p)
    assert "torn 1" in text
    assert "WARNING: torn lines skipped" in text
    assert "STOCK" in text and "FLOW" in text
    assert "NOT COMPUTABLE" in text
    assert "1 verdict-bearing rows predate the goal_verdict_at" in text


def test_main_cli_runs_on_explicit_ledger(tmp_path, capsys):
    p = _write_ledger(tmp_path, [
        _row(goal_achieved=True, goal_verdict_source="closure",
             goal_verdict_at="2026-07-30T10:10:00+00:00"),
    ])
    assert main(["--ledger", str(p), "--weeks", "4"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT FLOW" in out
    assert "closure" in out
    assert "stamped-later verdicts 1" in out
