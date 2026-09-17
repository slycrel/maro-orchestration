"""Checkpoint rows resolve to PLAN POSITIONS, not NEXT.md item numbers (2026-09-16).

Pre-existing bug found by the LoopsBench chunk's round-2 review: every row
stored `StepOutcome.index` (the NEXT.md item the loop assigned — live files
held [13, 49, 11, 12] for 2–7-step plans) while `Checkpoint.remaining_steps`
compared it to 1..n, so a resume got the WHOLE plan back and re-executed
finished steps.

Must-detect fixtures (review round 1 of the fix):
- item numbers that COLLIDE with other steps' positions resume correctly;
- a legacy file (no `positioned` marker) resumes exactly as before;
- a positioned file whose rows ALL sit at position 0 (a resume's in-flight
  write before its first suffix step completes) never reads item numbers
  as positions;
- a blocked row never finishes a position (retry / sub-step supersession);
- a second resume is not refused as "complete" while suffix work remains;
- every production writer passes the loop's real item→position mapping;
- a priority interrupt keeps text ↔ item index paired.
"""
import json

import pytest

import checkpoint as ckmod
from checkpoint import Checkpoint, CompletedStep, load_checkpoint, resume_from, write_checkpoint


class _Row:
    def __init__(self, index, text, status="done"):
        self.index, self.text, self.status, self.result = index, text, status, "r"


LIVE_SHAPE = [13, 49, 11, 12]  # completed idx of a real 4-step checkpoint on this box


# ---------------------------------------------------------------- writer

def test_write_maps_item_indices_to_plan_positions(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    steps = ["one", "two", "three"]
    write_checkpoint("lp1", "g", "p", steps,
                     [_Row(13, "one"), _Row(-1, "sub-step"), _Row(14, "two", "blocked")],
                     step_indices=[13, 14, 15])
    ck = load_checkpoint("lp1")
    assert ck.positioned is True
    assert [(c.index, c.position) for c in ck.completed] == [(13, 1), (-1, 0), (14, 2)]
    # the blocked row does NOT finish position 2 — the loop requeues it
    assert ck.remaining_steps == ["two", "three"]
    assert ck.next_step_index == 1
    assert ck.done_count == 1
    assert ck.is_complete() is False
    remaining, done = resume_from(ck)
    assert remaining == ["two", "three"] and len(done) == 3


def test_write_live_shape_item_numbers_collide_with_positions(monkeypatch, tmp_path):
    """The live shape: 4 steps whose item numbers are 13, 49, 11, 12 — every
    one of them is a plausible position of some OTHER step or none."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    steps = ["s1", "s2", "s3", "s4"]
    rows = [_Row(13, "s1"), _Row(49, "s2"), _Row(11, "s3")]
    write_checkpoint("lp-live", "g", "p", steps, rows, step_indices=LIVE_SHAPE)
    ck = load_checkpoint("lp-live")
    assert [c.position for c in ck.completed] == [1, 2, 3]
    assert ck.remaining_steps == ["s4"]
    assert ck.done_count == 3 and ck.is_complete() is False


def test_write_without_step_indices_keeps_legacy_semantics(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    write_checkpoint("lp2", "g", "p", ["one", "two"], [_Row(1, "one")])
    ck = load_checkpoint("lp2")
    assert ck.positioned is False and ck.completed[0].position == 0
    assert ck.remaining_steps == ["two"]           # index read as position, as before
    assert ck.done_count == 1
    ck.completed.append(CompletedStep(index=2, text="two", status="blocked"))
    assert ck.done_count == 1 and ck.is_complete() is True   # legacy: rows count, done-only display


def test_write_malformed_mapping_warns_and_errs_toward_rerun(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    steps = ["one", "two", "three"]
    with caplog.at_level("WARNING", logger="maro.checkpoint"):
        write_checkpoint("lp-short", "g", "p", steps, [_Row(13, "one"), _Row(14, "two")],
                         step_indices=[13])                     # shorter than the plan
        write_checkpoint("lp-dup", "g", "p", steps, [_Row(5, "one"), _Row(5, "two")],
                         step_indices=[5, 5, 6])                # duplicate item
        write_checkpoint("lp-str", "g", "p", steps, [_Row("13", "one")],
                         step_indices=[13, 14, 15])             # str outcome index
    assert sum("does not map the plan cleanly" in r.message for r in caplog.records) == 2
    short = load_checkpoint("lp-short")
    assert [c.position for c in short.completed] == [1, 0]
    assert short.remaining_steps == ["two", "three"]            # unmapped row re-runs
    dup = load_checkpoint("lp-dup")
    assert [c.position for c in dup.completed] == [0, 0]   # ambiguous identity → re-run
    assert dup.remaining_steps == ["one", "two", "three"]
    # r2 finding 3 must-detect: only the SECOND duplicate's outcome exists —
    # the first slot must not be claimed by it.
    write_checkpoint("lp-dup2", "g", "p", ["A", "B"], [_Row(5, "B")], step_indices=[5, 5])
    assert load_checkpoint("lp-dup2").remaining_steps == ["A", "B"]
    assert load_checkpoint("lp-str").completed[0].position == 1  # coerced, matched


# ---------------------------------------------------------------- selector

def test_old_file_without_position_field_still_loads():
    ck = Checkpoint.from_dict({"loop_id": "l", "goal": "g", "project": "p",
                               "steps": ["a", "b"],
                               "completed": [{"index": 1, "text": "a", "status": "done"}]})
    assert ck.positioned is False
    assert ck.completed[0].position == 0 and ck.remaining_steps == ["b"]
    assert ck.is_complete() is False


def test_legacy_collision_negative_control():
    """A genuine legacy file keeps its (buggy) pre-fix reading: index 2 = position 2."""
    ck = Checkpoint(loop_id="l", goal="g", project="p", steps=["A", "B", "C"],
                    completed=[CompletedStep(index=2, text="A", status="done")])
    assert ck.remaining_steps == ["A", "C"]


def test_positioned_rows_win_over_item_indices():
    ck = Checkpoint(loop_id="l", goal="g", project="p", steps=["a", "b", "c"], positioned=True,
                    completed=[CompletedStep(index=2, text="a", status="done", position=1),
                               CompletedStep(index=3, text="sub", status="done", position=0)])
    assert ck.remaining_steps == ["b", "c"]


def test_positioned_file_with_only_history_rows_skips_nothing():
    """Resume's in-flight write before its first suffix step: every row is
    carried history at position 0, and their item numbers (2) collide with a
    suffix position. Nothing may be skipped."""
    ck = Checkpoint(loop_id="l", goal="g", project="p", steps=["B", "C"], positioned=True,
                    completed=[CompletedStep(index=2, text="A", status="done", position=0)])
    assert ck.remaining_steps == ["B", "C"]
    assert ck.done_count == 0 and ck.is_complete() is False


def test_blocked_row_never_finishes_a_position():
    """Crash during a retry: the only row for A is the blocked attempt the
    loop had already requeued. Resume must start AT A. Latest row wins:
    blocked→done finishes, done→blocked (a later refusal) un-finishes."""
    ck = Checkpoint(loop_id="l", goal="g", project="p", steps=["A", "B"], positioned=True,
                    completed=[CompletedStep(index=7, text="A", status="blocked", position=1)])
    assert ck.remaining_steps == ["A", "B"] and ck.next_step_index == 0
    ck.completed.append(CompletedStep(index=7, text="A", status="done", position=1))
    assert ck.remaining_steps == ["B"] and ck.next_step_index == 1
    ck.completed.append(CompletedStep(index=7, text="A", status="blocked", position=1))
    assert ck.remaining_steps == ["A", "B"] and ck.done_count == 0


def test_superseded_and_gated_blocked_rows_resume_at_the_step():
    """r2 finding 1, decided direction: a step superseded by sub-steps (rows
    at position 0) or refused by the prerequisite gate re-runs on resume."""
    ck = Checkpoint(loop_id="l", goal="g", project="p", steps=["A", "B"], positioned=True,
                    completed=[CompletedStep(index=7, text="A", status="blocked", position=1),
                               CompletedStep(index=-1, text="A.1", status="done", position=0),
                               CompletedStep(index=-1, text="A.2", status="done", position=0),
                               CompletedStep(index=8, text="B", status="blocked", position=2,
                                             result="not executed — declared prerequisite not met")])
    assert ck.remaining_steps == ["A", "B"]
    assert ck.done_count == 0 and ck.is_complete() is False and ck.next_step_index == 0
    ck.completed.append(CompletedStep(index=7, text="A", status="done", position=1))
    assert ck.remaining_steps == ["B"] and ck.next_step_index == 1
    ck.completed.append(CompletedStep(index=8, text="B", status="done", position=2))
    assert ck.is_complete() is True and ck.next_step_index == 2


def test_is_complete_after_one_resume_counts_positions_not_rows():
    """After a resume `steps` is the suffix and `completed` holds the carried
    rows too — a row count said "complete" while C remained (r1 finding 1)."""
    ck = Checkpoint(loop_id="l", goal="g", project="p", steps=["B", "C"], positioned=True,
                    completed=[CompletedStep(index=1, text="A", status="done", position=0),
                               CompletedStep(index=9, text="B", status="done", position=1)])
    assert ck.is_complete() is False and ck.remaining_steps == ["C"]
    assert ck.done_count == 1
    legacy = Checkpoint(loop_id="l", goal="g", project="p", steps=["B", "C"],
                        completed=[CompletedStep(index=1, text="A", status="done"),
                                   CompletedStep(index=2, text="B", status="done")])
    assert legacy.is_complete() is True     # unchanged pre-fix rule for old files


@pytest.mark.parametrize("row, expect_pos", [
    ({"index": 1, "text": "a", "status": "done", "position": "1"}, 1),
    ({"index": 1, "text": "a", "status": "done", "position": None}, 0),
    ({"index": 1, "text": "a", "status": "done", "position": -1}, 0),
    ({"index": 1, "text": "a", "status": "done", "position": 7}, 0),     # beyond the plan
    ({"index": True, "text": "a", "status": "done", "position": 1.0}, 1),
    ({"index": 1, "text": "a", "status": "done", "position": 1.9}, 0),   # NOT truncated to 1
    ({"index": 1, "text": "a", "status": "done", "position": float("inf")}, 0),
    ({"index": 1, "text": "a", "status": "done", "position": "1.5"}, 0),
    ({"index": 1, "text": "a", "status": "DONE", "position": 1}, 1),     # case: kept, not finished
    ({"text": "a", "status": "done", "position": 2, "unknown_field": 1}, 2),
])
def test_from_dict_coerces_position_and_index(row, expect_pos):
    ck = Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["a", "b"],
                               "positioned": True, "completed": [row]})
    assert len(ck.completed) == 1
    assert ck.completed[0].position == expect_pos
    assert isinstance(ck.completed[0].index, int)
    ck.remaining_steps; ck.is_complete(); ck.next_step_index   # must not raise
    if row["status"] != "done":
        assert ck.remaining_steps == ["a", "b"]                 # "DONE" is not finished


def test_from_dict_negative_controls_drop_or_demote_garbage():
    """Non-dict rows are not rows; a string marker is not the marker; a
    null `completed` loads as empty; all err toward re-run."""
    ck = Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["A"], "positioned": True,
                               "completed": ["garbage", None, 3,
                                             {"index": 1, "text": "A", "status": "done", "position": 1}]})
    assert len(ck.completed) == 1 and ck.is_complete() is True
    legacy_str = Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["A", "B"],
                                       "positioned": "false",
                                       "completed": [{"index": 2, "text": "A", "status": "done", "position": 1}]})
    assert legacy_str.positioned is False
    assert legacy_str.remaining_steps == ["A"]                 # legacy reading of index 2
    null = Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["A"], "positioned": True,
                                 "completed": None})
    assert null.completed == [] and null.remaining_steps == ["A"]
    nolist = Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["A"],
                                   "completed": ["x"]})
    assert nolist.completed == [] and nolist.is_complete() is False


def test_to_dict_round_trips_marker(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    write_checkpoint("lp-rt", "g", "p", ["one"], [_Row(3, "one")], step_indices=[3])
    raw = json.loads(ckmod._checkpoint_path("lp-rt").read_text())
    assert raw["positioned"] is True and raw["completed"][0]["position"] == 1
    assert Checkpoint.from_dict(raw).positioned is True
    write_checkpoint("lp-rt2", "g", "p", ["one"], [_Row(1, "one")])
    assert "positioned" not in json.loads(ckmod._checkpoint_path("lp-rt2").read_text())


def test_branch_carries_position_semantics(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    write_checkpoint("lp-src", "g", "p", ["one", "two"], [_Row(2, "one")], step_indices=[2, 3])
    new_id = ckmod.branch_checkpoint("lp-src")
    br = load_checkpoint(new_id)
    assert br.positioned is True and br.remaining_steps == ["two"]


def test_export_human_keys_by_position(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    write_checkpoint("lp-h", "g", "p", ["one", "two"],
                     [_Row(2, "one"), _Row(-1, "sub", "done")], step_indices=[2, 3])
    md = ckmod.export_human("lp-h")
    assert "1/2 steps done" in md


# ---------------------------------------------------------------- loop flows

def _loop_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import introspect
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)
    # A budget-exhausted run reads as "stuck"; the auto-recovery lane would
    # re-run it under a NEW loop id (a fresh decomposition), which is
    # exactly what a crash→resume must not do.
    monkeypatch.setattr(introspect, "plan_recovery", lambda diag: None)


class _CompletingAdapter:
    model_key = "test"

    def __init__(self, seen):
        self.seen = seen

    def complete(self, messages, **kwargs):
        from llm import LLMResponse, ToolCall
        user = " ".join(m.content for m in messages if getattr(m, "role", "") == "user")
        if "Current step" in user:
            self.seen.append(user.split("Current step")[-1][:80])
        return LLMResponse(content="", tool_calls=[ToolCall(
            name="complete_step", arguments={"result": "ok", "summary": "ok"})],
            input_tokens=1, output_tokens=1)


PLAN = ["Step one: fetch", "Step two: transform", "Step three: report"]


def test_loop_resume_runs_only_unfinished_steps(monkeypatch, tmp_path):
    """Run 1 finishes step one and stops (iteration budget, the same
    post-step writer a crash between steps leaves behind); run 2 resumes
    and must not send step one to the adapter again."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    seen = []
    first = al.run_agent_loop("resume positions", adapter=_CompletingAdapter(seen),
                              preset_steps=PLAN, max_steps=3, max_iterations=1)
    assert first.status != "done"
    assert seen and "Step one" in seen[0]
    ck = load_checkpoint(first.loop_id)
    assert ck is not None and ck.steps == PLAN and ck.positioned is True
    done_rows = [c for c in ck.completed if c.status == "done"]
    assert [c.position for c in done_rows] == [1], [(c.index, c.position, c.status) for c in ck.completed]
    assert ck.remaining_steps == PLAN[1:]

    seen.clear()
    second = al.run_agent_loop("resume positions", adapter=_CompletingAdapter(seen),
                               preset_steps=PLAN, max_steps=3, max_iterations=6,
                               resume_from_loop_id=first.loop_id)
    assert seen, "resumed run executed nothing"
    assert not any("Step one" in c for c in seen), f"resume re-executed a finished step: {seen}"
    assert any("Step two" in c for c in seen) and any("Step three" in c for c in seen)
    assert second.status == "done"


def test_loop_two_hop_resume(monkeypatch, tmp_path):
    """crash → resume → crash → resume. The second checkpoint's plan is the
    suffix and its rows include carried history; the CLI's `is_complete`
    guard must not refuse the second resume, and the third run must execute
    exactly the last step."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    seen = []
    first = al.run_agent_loop("two hop", adapter=_CompletingAdapter(seen),
                              preset_steps=PLAN, max_steps=3, max_iterations=1)
    seen.clear()
    second = al.run_agent_loop("two hop", adapter=_CompletingAdapter(seen),
                               preset_steps=PLAN, max_steps=3, max_iterations=1,
                               resume_from_loop_id=first.loop_id)
    assert [c for c in seen if "Step two" in c] and not [c for c in seen if "Step one" in c]
    ck2 = load_checkpoint(second.loop_id)
    assert ck2 is not None and ck2.positioned is True
    assert ck2.steps == PLAN[1:], ck2.steps
    assert ck2.is_complete() is False            # r1 finding 1: row count said True
    assert ck2.remaining_steps == PLAN[2:]
    assert ck2.done_count == 1
    seen.clear()
    third = al.run_agent_loop("two hop", adapter=_CompletingAdapter(seen),
                              preset_steps=PLAN, max_steps=3, max_iterations=6,
                              resume_from_loop_id=second.loop_id)
    assert [c[:40] for c in seen if "Step" in c] and all("Step three" in c for c in seen if "Step" in c), seen
    assert third.status == "done"


def test_every_loop_writer_passes_the_real_mapping(monkeypatch, tmp_path):
    """Boundary capture at the ONE writer all four sites alias: every
    checkpoint the loop writes carries `step_indices` of the plan's length
    (removing `step_indices=` at any site fails this)."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    calls = []
    real = ckmod.write_checkpoint

    def spy(loop_id, goal, project, steps, step_outcomes, **kw):
        calls.append((list(steps), kw.get("step_indices"), kw.get("in_flight_index")))
        return real(loop_id, goal, project, steps, step_outcomes, **kw)

    monkeypatch.setattr(ckmod, "write_checkpoint", spy)
    result = al.run_agent_loop("writer capture", adapter=_CompletingAdapter([]),
                               preset_steps=PLAN, max_steps=3, max_iterations=6)
    assert calls, "loop wrote no checkpoint"
    assert any(inflight is not None for _, _, inflight in calls), "in-flight site not exercised"
    assert any(inflight is None for _, _, inflight in calls), "post-step site not exercised"
    for steps, idxs, _ in calls:
        assert isinstance(idxs, list) and len(idxs) == len(steps), (steps, idxs)
        assert all(isinstance(i, int) and i >= 0 for i in idxs), idxs
        assert len(set(idxs)) == len(idxs), idxs
    # The mapping is the RIGHT one, not merely the right length: every done
    # row's item resolves to the position whose plan text is the row's text
    # (r2 finding 6 — a permuted list passes a length check).
    ck = load_checkpoint(result.loop_id)
    assert ck is not None and ck.positioned
    done_rows = [c for c in ck.completed if c.status == "done"]
    assert len(done_rows) == len(PLAN)
    for c in done_rows:
        assert c.position > 0 and ck.steps[c.position - 1] == c.text, (c.index, c.position, c.text)
    assert [c.index for c in done_rows] == calls[-1][1]        # rows carry the loop's items in order
    assert ck.is_complete() and ck.remaining_steps == []


def test_gate_writer_passes_the_real_mapping(monkeypatch, tmp_path):
    """The gate write site (a hard-gated step leaves a blocked row and a
    checkpoint) carries the mapping too."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    calls = []
    real = ckmod.write_checkpoint

    def spy(loop_id, goal, project, steps, step_outcomes, **kw):
        calls.append((list(steps), kw.get("step_indices"),
                      [(getattr(s, "status", ""), getattr(s, "index", None)) for s in step_outcomes]))
        return real(loop_id, goal, project, steps, step_outcomes, **kw)

    monkeypatch.setattr(ckmod, "write_checkpoint", spy)
    from llm import LLMResponse, ToolCall

    class _Blocking:
        model_key = "test"

        def complete(self, messages, **kwargs):
            user = " ".join(m.content for m in messages if getattr(m, "role", "") == "user")
            if "Current step" in user and "Step one" in user:
                return LLMResponse(content="", tool_calls=[ToolCall(
                    name="flag_stuck", arguments={"reason": "cannot"})],
                    input_tokens=1, output_tokens=1)
            return LLMResponse(content="", tool_calls=[ToolCall(
                name="complete_step", arguments={"result": "ok", "summary": "ok"})],
                input_tokens=1, output_tokens=1)

    import loop_execute
    monkeypatch.setattr(loop_execute, "_process_blocked_step",
                        lambda ctx, blk: ("stuck", blk.step_idx, "halt", "halt", 0, 0, blk.replan_count))
    plan = ["Step one: fetch", "Step two: transform [after: 1]", "Step three: report"]
    al.run_agent_loop("gate capture", adapter=_Blocking(), preset_steps=plan,
                      max_steps=3, max_iterations=6)
    gate_writes = [c for c in calls if any(st == "blocked" for st, _ in c[2])]
    assert gate_writes, "no checkpoint written after the blocked/gated rows"
    for steps, idxs, _ in calls:
        assert isinstance(idxs, list) and len(idxs) == len(steps), (steps, idxs)


def test_priority_interrupt_keeps_text_and_item_index_paired(monkeypatch):
    """r1 finding 4: a priority interrupt PREPENDS text but the indices were
    concatenated old+new, so the urgent step wore the next planned step's
    item number and the checkpoint mapped its row onto that step."""
    import loop_post_step as lps
    from loop_types import LoopContext

    class _O:
        def append_next_items(self, project, added):
            return [90 + i for i in range(len(added))]

        def append_decision(self, *a, **k):
            pass

    monkeypatch.setattr(lps, "_orch", lambda: _O())
    monkeypatch.setattr(lps, "_shape_steps", lambda steps, label="": list(steps))

    class _Intr:
        id = "i1"; source = "operator"; intent = "priority"; message = "do X now"

    class _Q:
        def poll(self):
            return [_Intr()]

    ctx = LoopContext(loop_id="l", goal="g", project="p")
    results = {}
    for kind in ("priority", "additive", "corrective"):
        def apply(intr, remaining, goal, _k=kind):
            if _k == "priority":
                return ["X"] + list(remaining), goal, False
            if _k == "additive":
                return list(remaining) + ["X"], goal, False
            return ["X"], goal, False
        out = lps._check_loop_interrupts(
            ctx, remaining_steps=["B", "C"], remaining_indices=[14, 15],
            interrupt_queue=_Q(), apply_interrupt_fn=apply, goal="g", interrupts_applied=0)
        results[kind] = (out[4], out[5])
    assert results["priority"] == (["X", "B", "C"], [90, 14, 15]), results
    assert results["additive"] == (["B", "C", "X"], [14, 15, 90]), results
    assert results["corrective"] == (["X"], [90]), results


def test_cli_resume_guard_accepts_a_resumed_checkpoint(monkeypatch, tmp_path):
    """The production entry: `maro resume <loop_id>` → `_cmd_resume` →
    `is_complete()` guard → `run_agent_loop(resume_from_loop_id=)`. The
    checkpoint is the shape a resumed run leaves (suffix plan + carried
    rows); the row-count rule refused it as complete (r1 finding 1)."""
    import argparse
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import cli
    import agent_loop as al
    import llm
    # The checkpoint a resumed run leaves after finishing ONE suffix step.
    write_checkpoint("lp-cli", "cli goal", "", ["B", "C"],
                     [_Row(1, "A"), _Row(9, "B")], step_indices=[9, 10])
    ck = load_checkpoint("lp-cli")
    assert len(ck.completed) >= len(ck.steps)          # the old rule's trap
    assert ck.is_complete() is False
    captured = {}

    class _Result:
        status = "done"; loop_id = "lp-cli-2"; steps = []; stuck_reason = ""

    def fake_loop(goal, **kw):
        captured.update(kw); captured["goal"] = goal
        return _Result()

    monkeypatch.setattr(al, "run_agent_loop", fake_loop)
    monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: object())
    monkeypatch.setattr(cli, "_closure_verdict_pass", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning", lambda *a, **k: None, raising=False)
    rc = cli._cmd_resume(argparse.Namespace(run_id="lp-cli", verbose=False, format="text"))
    assert captured.get("resume_from_loop_id") == "lp-cli", (rc, captured)
    # A checkpoint with nothing left IS refused by the same guard.
    write_checkpoint("lp-cli-done", "cli goal", "", ["B"], [_Row(9, "B")], step_indices=[9])
    captured.clear()
    rc2 = cli._cmd_resume(argparse.Namespace(run_id="lp-cli-done", verbose=False, format="text"))
    assert rc2 != 0 and not captured
