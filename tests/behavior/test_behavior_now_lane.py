"""Behavior: NOW-lane one-shot + re-run identity, at the workspace boundary.

Scenario (1): trivial goal → intake row (B11), run dir + answer artifact
(B3), outcome row (B6). Scenario (11): the same goal dispatched twice →
two joinable intake rows, and the re-run brief surfaces the first attempt
to the second (B11's registered reader).
"""

import json

from harness import assert_common_contracts, drive, read_jsonl, workspace
from scenarios import RERUN_GOAL, by_id


# ---------------------------------------------------------------------------
# (1) NOW one-shot: the answer artifact
# ---------------------------------------------------------------------------

def test_now_answer_artifact_shape():
    """B3: the NOW lane's deliverable lands in the run dir's artifact/
    subtree as now-<handle_id>.json with the result payload."""
    sc = by_id("now-one-shot")
    result, rd = drive(sc)
    assert_common_contracts(sc, result, rd)

    art = rd / "artifact" / f"now-{result.handle_id}.json"
    assert art.exists(), "NOW answer artifact missing from artifact/ (B3)"
    payload = json.loads(art.read_text(encoding="utf-8"))
    assert payload["handle_id"] == result.handle_id
    assert payload["lane"] == "now"
    assert payload["message"] == sc.goal
    assert "Paris" in payload["result"], "artifact must carry the actual answer"
    assert payload.get("created_at")


# ---------------------------------------------------------------------------
# (11) Re-run identity — B11
# ---------------------------------------------------------------------------

def test_rerun_same_goal_twice_joinable_and_brief_sees_first():
    sc = RERUN_GOAL

    result1, rd1 = drive(sc)
    result2, rd2 = drive(sc)
    assert result1.handle_id != result2.handle_id
    assert rd1 != rd2, "each dispatch owns its own run dir (B3)"

    # B11: one intake row per handle() call, raw_input verbatim, rows
    # joinable on handle_id.
    rows = read_jsonl(workspace() / "memory" / "handle_inputs.jsonl")
    mine = [r for r in rows if r.get("raw_input") == sc.goal]
    assert len(mine) == 2, f"expected 2 intake rows for the goal, got {len(mine)}"
    assert {r["handle_id"] for r in mine} == {result1.handle_id, result2.handle_id}
    for r in mine:
        assert r.get("ts")

    # B11's registered reader: the deterministic re-run brief resolves the
    # first attempt from the intake ledger + run metadata.
    from rerun_identity import brief_for_goal, prior_attempts

    attempts = prior_attempts(sc.goal, exclude_handle_id=result2.handle_id)
    assert [a.handle_id for a in attempts].count(result1.handle_id) == 1, (
        "second dispatch must see the first via handle_inputs (B11)"
    )
    brief = brief_for_goal(sc.goal, exclude_handle_id=result2.handle_id)
    assert result1.handle_id in brief, (
        "the rendered re-run brief must name the prior attempt"
    )
