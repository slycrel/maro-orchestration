"""Behavior: run lifecycle at the workspace boundary.

Goal in → run dir + metadata + call records + run card + ledger rows out.
Every assertion targets on-disk artifacts and cites its docs/CONTRACTS.md
entry; nothing here inspects Python internals. See tests/behavior/README.md.
"""

import json

import pytest

from harness import (
    GoalScenario,
    assert_common_contracts,
    drive,
    read_jsonl,
    read_meta,
    workspace,
)
from scenarios import GOAL_SCENARIOS, by_id

# CONTRACTS.md B6: the closed stop_verdict vocabulary (owned by
# src/stop_verdicts.py; carried here as data so the suite stays
# engine-agnostic).
STOP_VERDICTS = {
    "out-of-budget",
    "thesis-refuted",
    "reachable-but-not-worth-it",
    "lost-the-plot",
    "external-interrupt",
}

# CONTRACTS.md B3: process-status vocabulary a terminal run may wear
# (done/stuck + the paused/interrupt family + error).
TERMINAL_STATUSES = {
    "done", "stuck", "error", "incomplete",
    "interrupted", "stranded", "refused_busy", "clarification_needed",
}


# ---------------------------------------------------------------------------
# The data-first table: every goal scenario satisfies the cross-cutting
# contracts (B3 run-dir/metadata, B5 card, B6 outcome row, B8 captains-log,
# B9 events discipline, B11 intake row) plus its declared expectations.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sc", GOAL_SCENARIOS, ids=lambda s: s.id)
def test_goal_scenario_contracts(sc: GoalScenario):
    result, rd = drive(sc)
    assert_common_contracts(sc, result, rd)


# ---------------------------------------------------------------------------
# (3) Call records under record mode — B4
# ---------------------------------------------------------------------------

def test_call_records_shape():
    """B4: build/calls/call-NNNNN.json — one complete record per LLM call,
    seq monotonic from 1, filename matches seq, required keys present."""
    sc = by_id("now-call-records")
    result, rd = drive(sc)

    calls_dir = rd / "build" / "calls"
    assert calls_dir.is_dir(), "record mode is default-ON; calls/ must exist (B4)"
    files = sorted(calls_dir.glob("call-*.json"))
    assert files, "no call records for a run that made LLM calls (B4)"

    seqs = []
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        # B4 shape, field by field.
        assert isinstance(rec["seq"], int)
        assert f.name == f"call-{rec['seq']:05d}.json", (
            "filename number must match the record's seq (B4)"
        )
        assert isinstance(rec["prompt"], str)
        assert isinstance(rec["response"], str)
        assert isinstance(rec["tool_events"], list), (
            "tool_events is required, may be empty (B4)"
        )
        assert isinstance(rec.get("backend", ""), str)
        assert isinstance(rec.get("model", ""), str)
        for tok_key in ("tokens_in", "tokens_out", "max_tokens_requested"):
            assert rec[tok_key] is None or isinstance(rec[tok_key], int), (
                f"{tok_key} is int|null (B4)"
            )
        assert isinstance(rec.get("purpose", ""), str)
        assert isinstance(rec.get("cost_usd", 0.0), (int, float))
        assert rec.get("error", "") == "", "scripted success calls carry empty error"
        assert rec.get("ts")
        seqs.append(rec["seq"])

    # Monotonic + unique from 1 (write-once is observable as no duplicate
    # seq ever reaching a reader-visible name).
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs), "duplicate seq at reader-visible names (B4)"
    assert seqs[0] == 1

    # B4 quirk: readers must ignore non-matching names — assert no temp
    # debris matches the reader glob (temp names are dot-prefixed).
    for p in calls_dir.iterdir():
        if p.name.startswith("call-") and p.suffix == ".json":
            continue
        assert p.name.startswith("."), f"unexpected reader-visible file {p.name}"


# ---------------------------------------------------------------------------
# (6) Blocked/stuck path — B3/B5/B6: failure recorded, never fabricated
# ---------------------------------------------------------------------------

def test_stuck_run_not_fabricated():
    sc = by_id("agenda-stuck")
    result, rd = drive(sc)
    meta = read_meta(rd)

    assert meta["status"] == "stuck"
    assert meta["status"] in TERMINAL_STATUSES

    # A stuck run must not conjure success: goal_achieved may be absent
    # (unjudged) or a judged bool carrying its source — never a bare True.
    if "goal_achieved" in meta:
        assert type(meta["goal_achieved"]) is bool
        assert meta.get("goal_verdict_source"), (
            "a judged verdict must name its source (B3 verdict block)"
        )
    if meta.get("stop_verdict"):
        assert meta["stop_verdict"] in STOP_VERDICTS, (
            f"stop_verdict {meta['stop_verdict']!r} outside the closed vocabulary (B6)"
        )

    # Same discipline on the outcomes row (B6).
    rows = [
        r for r in read_jsonl(workspace() / "memory" / "outcomes.jsonl")
        if r.get("handle_id") == result.handle_id
    ]
    assert rows
    row = rows[-1]
    assert row["status"] == "stuck"
    if "goal_achieved" in row:
        assert type(row["goal_achieved"]) is bool
        assert row.get("goal_verdict_source")
    if row.get("stop_verdict"):
        assert row["stop_verdict"] in STOP_VERDICTS

    # And the curated view must not classify it as clean success (B5).
    card = json.loads((rd / "run_card.json").read_text(encoding="utf-8"))
    assert card["success_class"] != "success"


# ---------------------------------------------------------------------------
# (15) metadata.json merge-on-write — B3: caller extras survive stamp
# traffic and finalize; corrupt bytes are parked, never destroyed
# ---------------------------------------------------------------------------

def test_metadata_extras_survive_stamp_traffic_and_finalize():
    from runs import (
        create_run_dir,
        finalize_run,
        scoped_run_dir,
        stamp_run_metadata_for,
        stamp_run_verdict,
        write_metadata,
    )

    hid = "beefcafe"
    rd = create_run_dir(hid, prompt="merge-on-write probe", lane="agenda")
    write_metadata(
        rd, handle_id=hid, prompt="merge-on-write probe", lane="agenda",
        extra={"experiment_arm": "A", "unknown_future_key": {"nested": [1, 2]}},
    )
    started_at = read_meta(rd)["started_at"]

    # Stamp traffic from three different writer families.
    stamp_run_metadata_for(hid, {"origin_note": "behavior-suite"})
    with scoped_run_dir(rd):
        stamp_run_verdict(
            goal_achieved=True, source="closure", confidence=0.9,
            summary="probe verdict", gaps=None,
        )
    finalize_run(hid, status="done")

    meta = read_meta(rd)
    # Caller extras and unknown keys survive every rewrite (B3 quirk:
    # merge-on-write, not blind overwrite — the just-fixed C0.2 guarantee).
    assert meta["experiment_arm"] == "A"
    assert meta["unknown_future_key"] == {"nested": [1, 2]}
    assert meta["origin_note"] == "behavior-suite"
    # Lifecycle: started_at preserved across rewrites; finalize stamped the
    # terminal pair.
    assert meta["started_at"] == started_at
    assert meta["status"] == "done"
    assert meta["ended_at"]
    # Verdict block landed beside them (B3): judged bool + source.
    assert meta["goal_achieved"] is True
    assert meta["goal_verdict_source"] == "closure"
    # Extras never override the core set.
    assert meta["handle_id"] == hid
    assert meta["prompt"] == "merge-on-write probe"


def test_corrupt_metadata_is_parked_not_destroyed():
    """B3: unparseable metadata bytes are parked verbatim to a unique
    metadata.json.corrupt.* sidecar before any mutator proceeds."""
    from runs import create_run_dir, finalize_run

    hid = "deadbee1"
    rd = create_run_dir(hid, prompt="corrupt-park probe")
    garbage = "{not json at all"
    (rd / "metadata.json").write_text(garbage, encoding="utf-8")

    finalize_run(hid, status="done")

    parks = list(rd.glob("metadata.json.corrupt.*"))
    assert parks, "corrupt bytes must be parked to a sidecar (B3)"
    assert parks[0].read_text(encoding="utf-8") == garbage, (
        "the park must hold the original bytes verbatim"
    )
    # The live file proceeded on a fresh object with the stamp applied.
    meta = read_meta(rd)
    assert meta["status"] == "done"
