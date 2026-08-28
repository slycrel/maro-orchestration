"""Behavior: memory ledgers — outcomes, verdicts, lessons.

Store-level scenarios driven through the registered writer APIs (the
ingress seams a goal cannot cheaply reach without an LLM); assertions read
the on-disk rows only. Contract citations: docs/CONTRACTS.md B6 (outcomes),
B7 (lesson stores), B3 (run-metadata verdict block).
"""

import json

from harness import read_jsonl, read_meta, workspace


def _outcomes_path():
    return workspace() / "memory" / "outcomes.jsonl"


# ---------------------------------------------------------------------------
# (4) Outcome row — B6: required keys, tri-state verdict, omit-when-empty
# ---------------------------------------------------------------------------

def test_outcome_row_tristate_and_omit_when_empty():
    from memory_ledger import record_outcome

    # Unjudged row: verdict keys must be ABSENT (rule A6 — never null/False).
    record_outcome("probe goal one", "done", "it finished", task_type="general")
    row = read_jsonl(_outcomes_path())[-1]
    for key in ("outcome_id", "goal", "summary", "status", "recorded_at",
                "task_type"):
        assert key in row, f"required key {key} missing (B6)"
    assert len(row["outcome_id"]) == 8, "outcome_id is an 8-hex uuid (B6)"
    assert row["status"] == "done"
    assert "goal_achieved" not in row, "unjudged row must omit goal_achieved (A6)"
    # Omit-when-empty: empty join keys stay off the row entirely.
    for key in ("loop_id", "handle_id", "stop_verdict", "stop_evidence",
                "pause_reason", "measurement_class"):
        assert key not in row, f"empty {key} must be omitted, not written (B6)"

    # Judged-at-record row: verdict rides as exact bool + source.
    record_outcome(
        "probe goal two", "done", "verified", task_type="general",
        goal_achieved=True, goal_verdict_source="closure",
        loop_id="loop-b6-1", handle_id="cafe0002",
    )
    row = read_jsonl(_outcomes_path())[-1]
    assert row["goal_achieved"] is True
    assert row["goal_verdict_source"] == "closure"
    assert row["loop_id"] == "loop-b6-1"
    assert row["handle_id"] == "cafe0002"

    # Malformed verdict at ingress: recorded UNJUDGED, never coerced
    # (B6 quirk 6 / R3-3 — bool("false") laundering is closed).
    record_outcome(
        "probe goal three", "done", "sketchy", task_type="general",
        goal_achieved="false",  # type: ignore[arg-type]
    )
    row = read_jsonl(_outcomes_path())[-1]
    assert "goal_achieved" not in row, (
        "a malformed goal_achieved must record as unjudged, never as True (B6 q6)"
    )


def test_outcome_closed_vocabularies_drop_at_ingress():
    """B6: stop_verdict / pause_reason are CLOSED vocabularies; ingress
    drops off-vocabulary values instead of persisting them."""
    from memory_ledger import record_outcome

    record_outcome(
        "vocab probe", "stuck", "stopped", task_type="general",
        stop_verdict="out-of-budget", stop_evidence="budget breaker fired",
    )
    row = read_jsonl(_outcomes_path())[-1]
    assert row["stop_verdict"] == "out-of-budget"
    assert row["stop_evidence"] == "budget breaker fired"

    record_outcome(
        "vocab probe bad", "stuck", "stopped", task_type="general",
        stop_verdict="totally-made-up-verdict", stop_evidence="x",
    )
    row = read_jsonl(_outcomes_path())[-1]
    assert "stop_verdict" not in row, "off-vocabulary stop_verdict must drop (B6)"
    assert "stop_evidence" not in row, "evidence drops with its verdict (B6)"


# ---------------------------------------------------------------------------
# (5) Verdict stamping — B6: post-hoc stamp, unknown-key round-trip,
# verdict_history append, malformed refusal; B3: run-metadata verdict block
# ---------------------------------------------------------------------------

def test_verdict_stamp_preserves_unknown_keys_and_appends_history():
    from file_lock import locked_append
    from memory_ledger import stamp_outcome_verdict

    # A row written by a hypothetical OTHER engine, carrying a field this
    # engine has never seen — the two-engine coexistence case (rule A4).
    foreign = {
        "outcome_id": "f0f0f0f0",
        "goal": "foreign-engine goal",
        "summary": "recorded elsewhere",
        "status": "done",
        "task_type": "general",
        "recorded_at": "2026-08-28T00:00:00+00:00",
        "loop_id": "loop-foreign-1",
        "future_field_from_go": {"keep": "me"},
    }
    locked_append(_outcomes_path(), json.dumps(foreign))

    # Post-hoc stamp lands on the row and round-trips the unknown key.
    res = stamp_outcome_verdict(
        "loop-foreign-1", goal_achieved=True,
        goal_verdict_source="closure", goal_verdict_confidence=0.8,
    )
    assert res.status == "updated"
    row = [r for r in read_jsonl(_outcomes_path())
           if r.get("loop_id") == "loop-foreign-1"][-1]
    assert row["goal_achieved"] is True
    assert row["goal_verdict_source"] == "closure"
    assert row["goal_verdict_confidence"] == 0.8
    assert row.get("goal_verdict_at"), "post-hoc stamp records its own time (B6)"
    assert row["future_field_from_go"] == {"keep": "me"}, (
        "RMW must round-trip unknown keys verbatim (rule A4 / B6 quirk 2)"
    )

    # Re-stamp supersedes: prior verdict moves to append-only verdict_history.
    stamp_outcome_verdict(
        "loop-foreign-1", goal_achieved=False,
        goal_verdict_source="closure", goal_verdict_confidence=0.9,
    )
    row = [r for r in read_jsonl(_outcomes_path())
           if r.get("loop_id") == "loop-foreign-1"][-1]
    assert row["goal_achieved"] is False
    hist = row.get("verdict_history")
    assert isinstance(hist, list) and hist, "superseded verdict must be kept (B6)"
    assert any(h.get("goal_achieved") is True for h in hist)

    # Malformed stamp refuses without touching the row (B6 quirk 6).
    before = [r for r in read_jsonl(_outcomes_path())
              if r.get("loop_id") == "loop-foreign-1"][-1]
    res = stamp_outcome_verdict(
        "loop-foreign-1", goal_achieved="false",  # type: ignore[arg-type]
        goal_verdict_source="closure",
    )
    assert res.status == "invalid"
    after = [r for r in read_jsonl(_outcomes_path())
             if r.get("loop_id") == "loop-foreign-1"][-1]
    assert after == before, "a refused stamp must leave the row untouched"


def test_run_metadata_verdict_block_absent_until_judged():
    """B3: the metadata verdict block follows absent-until-judged, and a
    judged stamp writes the full tuple."""
    from runs import create_run_dir, scoped_run_dir, stamp_run_verdict

    hid = "beadfeed"
    rd = create_run_dir(hid, prompt="verdict block probe", lane="agenda")
    meta = read_meta(rd)
    for key in ("goal_achieved", "goal_verdict_source", "goal_verdict_summary"):
        assert key not in meta, f"{key} must be absent before judgment (A6/B3)"

    with scoped_run_dir(rd):
        stamp_run_verdict(
            goal_achieved=False, source="closure", confidence=0.7,
            summary="did not land", gaps=["missing artifact"],
        )
    meta = read_meta(rd)
    assert meta["goal_achieved"] is False
    assert meta["goal_verdict_source"] == "closure"
    assert meta["goal_verdict_summary"] == "did not land"
    assert meta["goal_verdict_gaps"] == ["missing artifact"]


# ---------------------------------------------------------------------------
# (7) Lesson stores — B7: tiered + flat ingress, shared lesson_id,
# dedup-reinforce, unknown-key round-trip through a store rewrite
# ---------------------------------------------------------------------------

def test_lesson_dual_store_shapes_and_shared_id():
    """B7 ingress pinned via the store APIs: the LLM extraction pipeline is
    the production caller, but mint requires a model — the stores' on-disk
    shapes are the contract either way (noted in README FINDINGS)."""
    from knowledge_web import record_tiered_lesson
    from memory_ledger import record_outcome

    tl = record_tiered_lesson(
        "Always pin the workspace before writing",
        "general", "done", "behavior probe goal",
        lesson_type="execution", minted_from="outcome",
    )
    tiered_path = workspace() / "memory" / "medium" / "lessons.jsonl"
    rows = read_jsonl(tiered_path)
    mine = [r for r in rows if r.get("lesson_id") == tl.lesson_id]
    assert len(mine) == 1
    row = mine[0]
    # B7 core + tier mechanics.
    for key in ("lesson_id", "task_type", "outcome", "lesson", "source_goal",
                "confidence", "recorded_at", "tier", "score"):
        assert key in row, f"tiered row missing {key} (B7)"
    assert row["tier"] == "medium"
    assert row["minted_from"] == "outcome"
    assert row["lesson_type"] == "execution"

    # Flat store dual-write with the SAME lesson_id (the join key across
    # stores), via the record_outcome ingress.
    record_outcome(
        "behavior probe goal", "done", "done well", task_type="general",
        lessons=["Always pin the workspace before writing"],
        lesson_ids=[tl.lesson_id],
    )
    flat_rows = read_jsonl(workspace() / "memory" / "lessons.jsonl")
    flat_mine = [r for r in flat_rows if r.get("lesson_id") == tl.lesson_id]
    assert flat_mine, "flat store must carry the shared lesson_id (B7)"
    for key in ("lesson_id", "task_type", "outcome", "lesson", "source_goal",
                "confidence", "recorded_at"):
        assert key in flat_mine[0], f"flat row missing {key} (B7)"


def test_lesson_rewrite_round_trips_unknown_keys():
    """B7 / C0.1: a tier rewrite (triggered by dedup-reinforce) must
    round-trip a foreign row's unknown keys instead of stripping them."""
    from file_lock import locked_append
    from knowledge_web import record_tiered_lesson

    tiered_path = workspace() / "memory" / "medium" / "lessons.jsonl"
    tiered_path.parent.mkdir(parents=True, exist_ok=True)
    foreign = {
        "lesson_id": "go-000001",
        "task_type": "general",
        "outcome": "done",
        "lesson": "A lesson minted by the other engine",
        "source_goal": "foreign goal",
        "confidence": 0.6,
        "recorded_at": "2026-08-28T00:00:00+00:00",
        "tier": "medium",
        "score": 1.0,
        "last_reinforced": "2026-08-28",
        "field_only_go_knows": [1, 2, 3],
    }
    locked_append(tiered_path, json.dumps(foreign))

    # First record appends a new row; the exact-duplicate second record
    # takes the reinforce path, which REWRITES the tier file.
    record_tiered_lesson("Cache the config stat result", "general", "done",
                         "probe goal A")
    record_tiered_lesson("Cache the config stat result", "general", "done",
                         "probe goal B")

    rows = read_jsonl(tiered_path)
    ours = [r for r in rows if r.get("lesson") == "Cache the config stat result"]
    assert len(ours) == 1, "exact duplicate must reinforce, not duplicate (B7)"
    assert ours[0].get("times_reinforced", 0) >= 1

    foreign_after = [r for r in rows if r.get("lesson_id") == "go-000001"]
    assert len(foreign_after) == 1, "foreign row must survive the rewrite"
    assert foreign_after[0].get("field_only_go_knows") == [1, 2, 3], (
        "unknown keys must ride through the rewrite verbatim (B7/C0.1)"
    )


# ---------------------------------------------------------------------------
# (14) Interrupt/pause — the ledger + card half (the drivable half; the live
# mid-run interrupt path is a README finding)
# ---------------------------------------------------------------------------

def test_paused_family_vocabulary_on_ledger_and_card():
    """B6: pause_reason is a closed vocabulary (off-vocab drops at ingress);
    B5: an interrupt-family status classifies as success_class
    'interrupted', never 'unknown'."""
    from memory_ledger import record_outcome
    from runs import close_run, create_run_dir

    record_outcome(
        "interrupted probe", "interrupted", "operator stop",
        task_type="general",
        stop_verdict="external-interrupt",
        pause_reason="manual-intervention",
    )
    row = read_jsonl(_outcomes_path())[-1]
    assert row["stop_verdict"] == "external-interrupt"
    assert row["pause_reason"] == "manual-intervention"

    record_outcome(
        "interrupted probe 2", "interrupted", "operator stop",
        task_type="general", pause_reason="not-a-real-reason",
    )
    row = read_jsonl(_outcomes_path())[-1]
    assert "pause_reason" not in row, "off-vocabulary pause_reason must drop (B6)"

    hid = "feedf00d"
    rd = create_run_dir(hid, prompt="interrupted run probe", lane="agenda")
    close_run(hid, status="interrupted")
    card = json.loads((rd / "run_card.json").read_text(encoding="utf-8"))
    assert card["success_class"] == "interrupted", (
        "interrupt-family status must classify as 'interrupted' (B5)"
    )
