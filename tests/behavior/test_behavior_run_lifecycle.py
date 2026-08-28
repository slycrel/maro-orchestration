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
        # B4 shape, field by field. `type(...) is int` throughout: in
        # CPython bool IS an int, so isinstance would accept `"seq": true`
        # — a wire value no other engine should be told to accept.
        assert type(rec["seq"]) is int
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
            assert rec[tok_key] is None or type(rec[tok_key]) is int, (
                f"{tok_key} is int|null, never bool (B4)"
            )
        assert isinstance(rec.get("purpose", ""), str)
        assert type(rec.get("cost_usd", 0.0)) in (int, float), (
            "cost_usd is a JSON number, never bool (B4)"
        )
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
    """Exact posture, not a conditional one: for this scenario no judge ever
    runs, so a stuck run is UNJUDGED — `goal_achieved` must be ABSENT from
    metadata and the outcomes row (rule A6; a fabricated `true` with a
    plausible source must FAIL here, not slip through an if-guard), the
    stop_verdict rides the closed vocabulary, and the card classifies
    exactly `failed`."""
    sc = by_id("agenda-stuck")
    result, rd = drive(sc)
    meta = read_meta(rd)

    assert meta["status"] == "stuck"
    assert meta["status"] in TERMINAL_STATUSES
    assert "goal_achieved" not in meta, (
        "no judge ran — a stuck run must stay unjudged; a goal_achieved "
        "key here is fabricated (A6/B3)"
    )
    assert meta.get("stop_verdict") in STOP_VERDICTS, (
        f"stuck termination must carry a closed-vocabulary stop_verdict, "
        f"got {meta.get('stop_verdict')!r} (B6)"
    )

    # Same discipline on the outcomes row (B6).
    rows = [
        r for r in read_jsonl(workspace() / "memory" / "outcomes.jsonl")
        if r.get("handle_id") == result.handle_id
    ]
    assert rows
    row = rows[-1]
    assert row["status"] == "stuck"
    assert "goal_achieved" not in row, (
        "the outcomes row for an unjudged stuck run must omit goal_achieved"
    )
    assert row.get("stop_verdict") in STOP_VERDICTS

    # And the curated view classifies stuck-unjudged as exactly `failed`
    # (B5 grid) — `!= "success"` alone would accept `achieved-not-done`,
    # the class a fabricated verdict produces.
    card = json.loads((rd / "run_card.json").read_text(encoding="utf-8"))
    assert card["success_class"] == "failed"


# ---------------------------------------------------------------------------
# (2b) The flow, not just the object — B3/B9: scripted step results must
# reach durable evidence, and events join the run via its loops lineage
# ---------------------------------------------------------------------------

def test_agenda_flow_reaches_durable_evidence():
    """A `done` agenda run whose declared work leaves no durable trace of
    the actual step results is lying. The scripted outputs must appear in
    the run dir's build/ evidence, and the events feed must carry rows
    joined to the run's loop_id (B9 rows join by loop_id, not handle_id)."""
    sc = by_id("agenda-happy-path")
    result, rd = drive(sc)
    meta = read_meta(rd)

    # Loops lineage is real: at least one loop with a non-empty loop_id (B3).
    loops = meta.get("loops")
    assert isinstance(loops, list) and loops, "loops lineage must be non-empty"
    loop_ids = {l.get("loop_id") for l in loops}
    assert all(loop_ids), "every lineage entry carries a loop_id (B3)"

    # Every scripted step result reaches its OWN step artifact — the
    # contractual evidence writer's files, NOT the captains-log slice
    # (build/captains_log_slice.jsonl is a byte-copy of the global bus;
    # counting it as evidence let a logging-only engine pass — round-2
    # review catch). Step N's artifact carries step N's result, and the
    # final result also lands in the loop RESULT.md.
    scripted_results = [
        "Collected 12 rows of numbers", "Summary written: revenue flat",
    ]
    for n, scripted in enumerate(scripted_results, 1):
        step_files = list((rd / "build").glob(f"loop-*-step-{n:02d}.md"))
        assert step_files, f"no step-{n:02d} artifact under build/ (B3)"
        step_text = "".join(
            p.read_text(encoding="utf-8") for p in step_files
        )
        assert scripted in step_text, (
            f"step {n} artifact does not carry its scripted result "
            f"{scripted!r} — the evidence dropped the work (B3)"
        )
    result_files = list((rd / "build").glob("loop-*-RESULT.md"))
    assert result_files, "no loop RESULT.md under build/ (B3)"
    assert any(scripted_results[-1] in p.read_text(encoding="utf-8")
               for p in result_files), (
        "the final step result must reach the loop RESULT.md (B3)"
    )

    # Events join the run through its loop lineage (B9): ONE loop must own
    # a complete lifecycle pair — aggregating types across loops would
    # accept a loop_start from loop A spliced with a loop_done from loop B.
    ev_rows = read_jsonl(workspace() / "memory" / "events.jsonl")
    mine = [r for r in ev_rows if r.get("loop_id") in loop_ids]
    assert mine, "no events.jsonl row joins the run's loop_id (B9)"
    by_loop = {}
    for r in mine:
        by_loop.setdefault(r["loop_id"], set()).add(r["event_type"])
    assert any({"loop_start", "loop_done"} <= types
               for types in by_loop.values()), (
        f"no single loop carries a complete loop_start+loop_done "
        f"lifecycle: {by_loop}"
    )


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
