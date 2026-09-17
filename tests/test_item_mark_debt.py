"""LoopsBench chunk 8 (2026-09-17): a NEXT.md mark a row still owes is durable
state on the checkpoint row, settled at the next snapshot or by the resume.

Chunk-5 r2 lead: the parallel lane retried a failed `mark_item` at its next
snapshot but nothing durable recorded the debt, so a crash in between left
the checkpoint saying done while NEXT.md said TODO — and NEXT.md-driven
work could execute the item again. The sequential lane had no retry at
all. Doctrine: the checkpoint is the authoritative execution record and
NEXT.md its mirror; the mirror catches up from the record, never the other
way round; a mirror whose identity drifted (ledger edited) is surfaced,
not blindly marked.

Row state `item_mark`: applied (nothing owed) / pending (owed, retried) /
drifted (owed, the item no longer names the row — surfaced, never
retried) / attempt (not a verdict: a retry's record, owes nothing and
supersedes nothing — r3). A terminal row on a real item is born pending unless its
producer records the applied mark (r1: producers that never marked, or
swallowed the failure, reported "applied" by default). Every settlement
is a compare-and-mark under the ledger lock (r1: verify-then-mark raced).
"""
from __future__ import annotations

import json

import pytest

import checkpoint as ckmod
from checkpoint import load_checkpoint, write_checkpoint
from loop_types import MARK_APPLIED, MARK_ATTEMPT, MARK_DRIFTED, MARK_PENDING, step_from_decompose

from test_plan_node_ids import _Adapter, _loop_env, _next_items  # noqa: F401
from test_parallel_checkpoint import _FailFirst, _drive, _path_env  # noqa: F401

PLAN2 = ["Step one: fetch", "Step two: report"]


def _states(project):
    return {it.index: it.state for it in _next_items(project)}


def _spy_writes(monkeypatch, writes):
    real = ckmod.write_checkpoint

    def spy(loop_id, goal, project, steps, step_outcomes, **kw):
        writes.append([(o.index, o.status, o.item_mark) for o in step_outcomes])
        return real(loop_id, goal, project, steps, step_outcomes, **kw)
    monkeypatch.setattr(ckmod, "write_checkpoint", spy)


def _failing_marks(monkeypatch, events, *, fail_states=("x",), fail_times=1):
    """Real mark_item, except the first `fail_times` marks to a state in
    `fail_states` raise (a locked ledger). Every call is logged to events."""
    import orch
    real = orch.mark_item
    left = {"n": fail_times}

    def mark(project, item, state, **kw):
        events.append(("mark", item, state))
        if state in fail_states and left["n"] > 0:
            left["n"] -= 1
            raise OSError("NEXT.md locked")
        return real(project, item, state, **kw)
    monkeypatch.setattr(orch, "mark_item", mark)


class _EventAdapter(_Adapter):
    """Records each executed step into the shared event list too."""

    def complete(self, messages, **kwargs):
        before = len(self.seen)
        resp = super().complete(messages, **kwargs)
        if len(self.seen) > before:
            self.events.append(("step", self.seen[-1].strip()[:40]))
        return resp


# ------------------------------------------------------------- sequential lane

def test_every_mark_succeeds_negative_control(monkeypatch, tmp_path):
    """No failure anywhere: exactly one mark per item, and no snapshot ever
    records an owed state (the settler has nothing to do)."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events, writes = [], []
    _failing_marks(monkeypatch, events, fail_times=0)
    _spy_writes(monkeypatch, writes)
    res = al.run_agent_loop("no debt", adapter=_Adapter([]), preset_steps=PLAN2,
                            max_steps=2, max_iterations=4)
    assert res.status == "done", res.stuck_reason
    items = list(load_checkpoint(res.loop_id).step_items)
    assert [e for e in events if e[0] == "mark"] == [("mark", items[0], "x"), ("mark", items[1], "x")]
    assert writes and all(m == MARK_APPLIED for w in writes for _, _, m in w), writes
    assert [r.item_mark for r in res.steps] == [MARK_APPLIED, MARK_APPLIED]


def test_sequential_failed_done_mark_is_owed_and_the_next_snapshot_settles_it(monkeypatch, tmp_path):
    """Step one's DONE mark fails, and so does the retry the snapshot
    makes before writing: the checkpoint written for step one records the
    row pending (durable); the snapshot after step two retries and
    settles it, and NEXT.md ends with both items done."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events, writes = [], []
    _failing_marks(monkeypatch, events, fail_times=2)
    _spy_writes(monkeypatch, writes)
    res = al.run_agent_loop("mark debt", adapter=_Adapter([]), preset_steps=PLAN2,
                            max_steps=2, max_iterations=4)
    assert res.status == "done", res.stuck_reason
    ck = load_checkpoint(res.loop_id)
    items = list(ck.step_items)
    first = [w for w in writes if len(w) == 1][0]
    assert first == [(items[0], "done", MARK_PENDING)], first
    assert writes[-1] == [(items[0], "done", MARK_APPLIED), (items[1], "done", MARK_APPLIED)]
    assert all(r.item_mark == MARK_APPLIED for r in ck.completed)
    st = _states(res.project)
    assert st[items[0]] == "x" and st[items[1]] == "x", st
    marks = [e for e in events if e[0] == "mark"]
    assert marks == [("mark", items[0], "x"), ("mark", items[0], "x"),
                     ("mark", items[1], "x"), ("mark", items[0], "x")], marks


def test_owed_mark_survives_a_crash_and_the_resume_settles_it_before_executing(monkeypatch, tmp_path):
    """Hop 1: the mark fails and the loop stops after step one (no later
    snapshot). The file on disk says done / pending and NEXT.md says TODO
    — the exact shape that used to double-execute. Hop 2 resumes: the
    item is marked from the checkpoint BEFORE any step runs, and the
    successor's own checkpoint carries the row applied."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events = []
    # the mark, the snapshot's retry and the loop-exit flush all fail
    _failing_marks(monkeypatch, events, fail_times=3)
    first = al.run_agent_loop("crash debt", adapter=_Adapter([]), preset_steps=PLAN2,
                              max_steps=2, max_iterations=1)
    ck1 = load_checkpoint(first.loop_id)
    items = list(ck1.step_items)
    assert [(r.index, r.status, r.item_mark) for r in ck1.completed] == [(items[0], "done", MARK_PENDING)]
    raw = json.loads(ckmod.find_checkpoint(first.loop_id).path.read_text(encoding="utf-8"))
    assert raw["completed"][0]["item_mark"] == "pending"      # the owed state is in the bytes
    assert _states(first.project)[items[0]] == " "             # mirror behind the record
    events.clear()
    adapter = _EventAdapter([])
    adapter.events = events
    second = al.run_agent_loop("crash debt", adapter=adapter, max_steps=2, max_iterations=4,
                               resume_from_loop_id=first.loop_id)
    assert second.status == "done", second.stuck_reason
    assert events[0] == ("mark", items[0], "x"), events
    assert events[1][0] == "step" and "Step two" in events[1][1], events
    assert ("mark", items[1], "x") in events
    st = _states(first.project)
    assert st[items[0]] == "x" and st[items[1]] == "x", st
    ck2 = load_checkpoint(second.loop_id)
    assert [(r.index, r.item_mark) for r in ck2.completed] == [(items[0], MARK_APPLIED), (items[1], MARK_APPLIED)]


def test_resume_never_marks_an_item_whose_text_drifted(monkeypatch, tmp_path):
    """The ledger was hand-edited between the hops: item one's line no
    longer carries step one's text. A blind mark would land on whatever
    that line is now — the resume surfaces the owed mark (warning +
    decision line), records the row DRIFTED, marks nothing, and no later
    snapshot retries it."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import orch_items
    events = []
    _failing_marks(monkeypatch, events, fail_times=3)      # mark, snapshot retry, loop-exit flush
    first = al.run_agent_loop("drift debt", adapter=_Adapter([]), preset_steps=PLAN2,
                              max_steps=2, max_iterations=1)
    ck1 = load_checkpoint(first.loop_id)
    items = list(ck1.step_items)
    next_md = orch_items.project_dir(first.project) / "NEXT.md"
    text = next_md.read_text(encoding="utf-8")
    assert "Step one: fetch" in text
    next_md.write_text(text.replace("Step one: fetch", "Step one: something else"), encoding="utf-8")
    events.clear()
    second = al.run_agent_loop("drift debt", adapter=_Adapter([]), max_steps=2, max_iterations=4,
                               resume_from_loop_id=first.loop_id)
    assert second.status == "done", second.stuck_reason
    marks = [e for e in events if e[0] == "mark"]
    # the settle attempt (refused under the lock) and step two's own mark — no retry
    assert marks == [("mark", items[0], "x"), ("mark", items[1], "x")], marks
    assert _states(first.project)[items[0]] == " "
    decisions = (orch_items.project_dir(first.project) / "DECISIONS.md").read_text(encoding="utf-8")
    assert f"NEXT.md item {items[0]} no longer names" in decisions
    ck2 = load_checkpoint(second.loop_id)
    assert [(r.index, r.item_mark) for r in ck2.completed] == [(items[0], MARK_DRIFTED), (items[1], MARK_APPLIED)]


def test_resume_settlement_failure_keeps_the_owed_mark_on_the_successor(monkeypatch, tmp_path):
    """The ledger is still locked when the resume tries: the row stays
    pending on the carried row (the successor's first snapshot retries it)."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events = []
    _failing_marks(monkeypatch, events, fail_times=4)      # hop-1 mark, snapshot retry, loop-exit flush + the resume's settle
    first = al.run_agent_loop("sticky debt", adapter=_Adapter([]), preset_steps=PLAN2,
                              max_steps=2, max_iterations=1)
    items = list(load_checkpoint(first.loop_id).step_items)
    writes = []
    _spy_writes(monkeypatch, writes)
    second = al.run_agent_loop("sticky debt", adapter=_Adapter([]), max_steps=2, max_iterations=4,
                               resume_from_loop_id=first.loop_id)
    assert second.status == "done", second.stuck_reason
    assert writes[-1] == [(items[0], "done", MARK_APPLIED), (items[1], "done", MARK_APPLIED)], writes[-1]
    st = _states(first.project)
    assert st[items[0]] == "x" and st[items[1]] == "x", st


# --------------------------------------------------------------- parallel lane

def test_parallel_lane_rows_record_the_applied_state(monkeypatch, tmp_path):
    marks = _FailFirst()
    writes = []

    def dag(on_progress, steps):
        on_progress({1: {"status": "done", "result": "ok"}})
        on_progress({1: {"status": "done", "result": "ok"}, 2: {"status": "done", "result": "ok"}})
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}] * 2
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[7, 8], marks=marks)
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.index, o.item_mark) for o in a[4]]))
    res = _drive(ctx, lp, ["A", "B"], [7, 8])
    assert res.status == "done"
    assert writes[0] == [(7, MARK_PENDING)], writes                  # the owed state is in the first snapshot
    assert writes[1] == [(7, MARK_APPLIED), (8, MARK_APPLIED)], writes  # settled by the retry
    assert [r.item_mark for r in res.steps] == [MARK_APPLIED, MARK_APPLIED]


def test_parallel_final_rows_agree_with_the_checkpoint_when_the_mark_keeps_failing(monkeypatch, tmp_path):
    """r1 Skeptic 1 / Architect 5: the returned rows were built with the
    default and said applied while the file said owed."""
    import orch
    marks = []

    def _boom(project_, item, state, **kw):
        marks.append((item, state))
        raise OSError("locked through the final write")

    def dag(on_progress, steps):
        on_progress({1: {"status": "done", "result": "ok"}})
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[7])
    monkeypatch.setattr(orch, "mark_item", _boom)
    writes = []
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.index, o.item_mark) for o in a[4]]))
    res = _drive(ctx, lp, ["A"], [7])
    assert res.status == "done"
    assert writes == [[(7, MARK_PENDING)], [(7, MARK_PENDING)]], writes
    assert [(r.index, r.item_mark) for r in res.steps] == [(7, MARK_PENDING)]
    # the commit's mark, the final pass's fill, the final snapshot's retry
    assert marks == [(7, "x"), (7, "x"), (7, "x")], marks


def test_parallel_provisional_row_owes_done_until_the_move_is_applied(monkeypatch, tmp_path):
    """blocked (applied) → done whose mark fails: the done row owes `x`
    even though the item WAS marked once (blocked). The final pass applies
    the move."""
    marks = []
    writes = []
    import orch
    calls = {"n": 0}

    def _mark(project_, item, state, **kw):
        calls["n"] += 1
        marks.append((item, state))
        if calls["n"] == 2:
            raise OSError("NEXT locked")

    def dag(on_progress, steps):
        on_progress({1: {"status": "blocked", "stuck_reason": "dag timeout (0s)", "result": ""}})
        on_progress({1: {"status": "done", "result": "actual"}})
        return [{"status": "done", "result": "actual", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[7])
    monkeypatch.setattr(orch, "mark_item", _mark)
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.index, o.status, o.item_mark) for o in a[4]]))
    res = _drive(ctx, lp, ["A"], [7])
    assert res.status == "done"
    assert writes == [[(7, "blocked", MARK_APPLIED)], [(7, "done", MARK_PENDING)],
                      [(7, "done", MARK_APPLIED)]], writes
    assert marks == [(7, "!"), (7, "x"), (7, "x")], marks
    assert res.steps[0].item_mark == MARK_APPLIED


def test_parallel_lane_settles_carried_owed_marks_at_its_snapshot(monkeypatch, tmp_path):
    """A resume carried a row still owing its mark into the DAG lane: the
    lane's first snapshot settles it alongside its own nodes."""
    marks = []
    writes = []

    def dag(on_progress, steps):
        on_progress({1: {"status": "done", "result": "ok"}})
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[3], marks=marks)
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.index, o.item_mark) for o in a[4]]))
    carried = [step_from_decompose("Z", 2, status="done", result="r", ended_ts="")]
    assert carried[0].item_mark == MARK_PENDING                 # born owed: no producer recorded a mark
    res = lp._run_parallel_path(
        ctx, ["A"], clean_steps=["A"], deps={1: set()}, levels=[[1]], parallel_levels=[[1]],
        parallel_fan_out=2, proj_fanout_dir="", loop_shared_ctx={}, use_dag=True,
        resolve_tools_fn=lambda: [], step_indices=[3], carried_outcomes=carried)
    assert res.status == "done"
    assert marks == [(3, "x"), (2, "x")], marks
    assert writes[0] == [(2, MARK_APPLIED), (3, MARK_APPLIED)], writes


def test_direct_callers_without_items_owe_nothing(monkeypatch, tmp_path):
    marks = []

    def dag(on_progress, steps):
        on_progress({1: {"status": "done", "result": "ok"}})
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, marks=marks)
    res = lp._run_parallel_path(
        ctx, ["A"], clean_steps=["A"], deps={1: set()}, levels=[[1]], parallel_levels=[[1]],
        parallel_fan_out=2, proj_fanout_dir="", loop_shared_ctx={}, use_dag=True,
        resolve_tools_fn=lambda: [])
    assert res.status == "done" and marks == []
    assert [r.item_mark for r in res.steps] == [MARK_APPLIED]


# --------------------------------------------------- the row state at birth

@pytest.mark.parametrize("status, index, expect", [
    ("done", 3, MARK_PENDING), ("blocked", 3, MARK_PENDING), ("skipped", 3, MARK_PENDING),
    ("pending", 3, MARK_APPLIED), ("done", -1, MARK_APPLIED), ("done", True, MARK_APPLIED),
])
def test_a_terminal_row_on_a_real_item_is_born_owed(status, index, expect):
    """r1 Skeptic 4 / Architect 1: the milestone-advisor `skipped` row, the
    parallel-batch row and the blocked "advance" row never recorded a
    mark and read as applied. Born pending, the settler applies it."""
    assert step_from_decompose("t", index, status=status).item_mark == expect
    assert step_from_decompose("t", index, status=status, item_mark=MARK_APPLIED).item_mark == MARK_APPLIED
    assert step_from_decompose("t", index, status=status, item_mark="garbage").item_mark == MARK_APPLIED


def test_born_owed_rows_are_settled_by_the_sequential_snapshot(monkeypatch, tmp_path):
    """A producer that records no mark (a skipped row) still reaches the
    mirror: `_write_iteration_artifacts` settles it before writing."""
    _loop_env(monkeypatch, tmp_path)
    import loop_post_step as lps
    import orch
    import orch_items
    from loop_types import LoopContext
    items = orch_items.append_next_items("proj", ["Step one: fetch", "Step two: report"])
    marks = []
    real = orch.mark_item
    monkeypatch.setattr(orch, "mark_item", lambda p, i, s, **kw: marks.append((i, s, kw.get("expected_text"))) or real(p, i, s, **kw))
    ctx = LoopContext(goal="g", project="proj", loop_id="lp-born", verbose=False)
    ctx.step_indices = list(items)
    rows = [step_from_decompose("Step one: fetch", items[0], status="skipped",
                                result="skipped on milestone-advisor advice (b)", iteration=1),
            step_from_decompose("Step two: report", items[1], status="done", result="ok",
                                iteration=2, item_mark=MARK_APPLIED)]
    monkeypatch.setattr(ckmod, "write_checkpoint", lambda *a, **k: None)
    lps._write_iteration_artifacts(ctx, "Step two: report", "done", {}, rows,
                                   ["Step one: fetch", "Step two: report"], [], 0, "", False)
    assert marks == [(items[0], "x", "Step one: fetch")], marks
    assert [r.item_mark for r in rows] == [MARK_APPLIED, MARK_APPLIED]
    assert _states("proj")[items[0]] == "x"


# --------------------------------------------------- compare-and-mark + settler

def test_compare_and_mark_refuses_under_the_lock(monkeypatch, tmp_path):
    """r1 Skeptic 3 / Architect 3: the line must still be the step when
    the rewrite happens — text mismatch, a deleted item and a duplicated
    text all refuse with ItemIdentityError and write nothing."""
    _loop_env(monkeypatch, tmp_path)
    import orch_items as o
    items = o.append_next_items("proj", ["Step A", "Step B", "Step B"])
    with pytest.raises(o.ItemIdentityError, match="names 'Step A', not 'Step Z'"):
        o.mark_item("proj", items[0], o.STATE_DONE, expected_text="Step Z")
    with pytest.raises(o.ItemIdentityError, match="ambiguous"):
        o.mark_item("proj", items[1], o.STATE_DONE, expected_text="Step B")
    with pytest.raises(o.ItemIdentityError, match="not found"):
        o.mark_item("proj", 999, o.STATE_DONE, expected_text="Step A")
    assert all(st == " " for st in _states("proj").values())
    o.mark_item("proj", items[0], o.STATE_DONE, expected_text="  Step A ")
    assert _states("proj")[items[0]] == "x"
    o.mark_item("proj", items[1], o.STATE_BLOCKED)              # no expectation: legacy behaviour
    assert _states("proj")[items[1]] == "!"


def test_settler_classifies_identity_failures_as_drifted_and_transient_ones_as_pending(monkeypatch, tmp_path, caplog):
    _loop_env(monkeypatch, tmp_path)
    import loop_planning as lp
    import orch_items as o
    items = o.append_next_items("proj", ["Step A", "Step B"])
    text = (o.project_dir("proj") / "NEXT.md").read_text(encoding="utf-8")
    (o.project_dir("proj") / "NEXT.md").write_text(text.replace("Step B", "Step Q"), encoding="utf-8")
    rows = [step_from_decompose("Step A", items[0], status="done"),
            step_from_decompose("Step B", items[1], status="done"),
            step_from_decompose("Step C", 999, status="done")]           # deleted item
    with caplog.at_level("WARNING"):
        assert lp.settle_item_marks("proj", rows, loop_id="l", source="s") == 1
    assert [r.item_mark for r in rows] == [MARK_APPLIED, MARK_DRIFTED, MARK_DRIFTED]
    assert _states("proj") == {items[0]: "x", items[1]: " "}
    decisions = (o.project_dir("proj") / "DECISIONS.md").read_text(encoding="utf-8")
    assert f"NEXT.md item {items[1]} no longer names 'Step B'" in decisions
    assert "NEXT.md item 999 no longer names 'Step C'" in decisions
    assert "owed by s" in decisions
    # drifted rows are never retried
    assert lp.settle_item_marks("proj", rows) == 0
    # a transient failure keeps the row pending
    monkeypatch.setattr(o, "mark_item", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    import orch
    monkeypatch.setattr(orch, "mark_item", o.mark_item)
    rows2 = [step_from_decompose("Step A", items[0], status="done")]
    with caplog.at_level("WARNING"):
        assert lp.settle_item_marks("proj", rows2) == 0
    assert rows2[0].item_mark == MARK_PENDING
    assert any("still failing" in r.message for r in caplog.records)


def test_unreadable_ledger_keeps_the_owed_mark(monkeypatch, tmp_path):
    """r1 Skeptic 2 / Architect 2: a project whose NEXT.md cannot be read
    (here: no project dir at all) is transient, not drift."""
    _loop_env(monkeypatch, tmp_path)
    import loop_planning as lp
    rows = [step_from_decompose("Step A", 3, status="done")]
    assert lp.settle_item_marks("no-such-project", rows) == 0
    assert rows[0].item_mark == MARK_PENDING


def test_settler_marks_only_owed_terminal_rows(monkeypatch, tmp_path):
    _loop_env(monkeypatch, tmp_path)
    import loop_planning as lp
    import orch
    import orch_items as o
    items = o.append_next_items("proj", ["a", "b", "c", "d", "e", "f", "g"])
    marks = []
    real = orch.mark_item
    monkeypatch.setattr(orch, "mark_item", lambda p, i, s, **kw: marks.append((i, s)) or real(p, i, s, **kw))
    rows = [
        step_from_decompose("a", items[0], status="done"),
        step_from_decompose("b", items[1], status="blocked"),
        step_from_decompose("c", items[2], status="skipped"),
        step_from_decompose("d", items[3], status="done", item_mark=MARK_APPLIED),   # nothing owed
        step_from_decompose("e", -1, status="done"),                                 # no item
        step_from_decompose("f", items[5], status="pending"),                        # not terminal
        step_from_decompose("g", items[6], status="done", item_mark=MARK_DRIFTED),   # surfaced already
    ]
    assert lp.settle_item_marks("proj", rows) == 3
    assert marks == [(items[0], "x"), (items[1], "!"), (items[2], "x")], marks
    assert [r.item_mark for r in rows] == [MARK_APPLIED] * 5 + [MARK_APPLIED, MARK_DRIFTED]
    assert lp.settle_item_marks("", rows) == 0           # no project: nothing to mirror


def test_settler_ignores_resume_note_prefixes_and_rejects_loose_indices(monkeypatch, tmp_path):
    _loop_env(monkeypatch, tmp_path)
    import loop_planning as lp
    import orch_items
    items = orch_items.append_next_items("proj", ["Step one: fetch"])
    row = step_from_decompose("[resume note: crashed at T] [resume note: again] Step one: fetch",
                              items[0], status="done")
    assert lp.settle_item_marks("proj", [row], loop_id="l", source="s") == 1
    assert _states("proj")[items[0]] == "x"
    # r1: a non-integral index (a hand-built row) must not truncate onto another item
    loose = step_from_decompose("Step one: fetch", items[0] + 0.9, status="done", item_mark=MARK_PENDING)
    assert lp.settle_item_marks("proj", [loose]) == 0 and loose.item_mark == MARK_PENDING
    # and a float index is not a real item at birth: nothing to mirror
    assert step_from_decompose("t", 2.9, status="done").item_mark == MARK_APPLIED


# ----------------------------------------------------------- persisted state

@pytest.mark.parametrize("persisted, expect", [
    ("pending", MARK_PENDING), ("drifted", MARK_DRIFTED), ("applied", MARK_APPLIED),
    ("attempt", MARK_ATTEMPT), ("ATTEMPT", MARK_APPLIED),
    ("PENDING", MARK_APPLIED), (False, MARK_APPLIED), (True, MARK_APPLIED), (0, MARK_APPLIED),
    (None, MARK_APPLIED), ("", MARK_APPLIED), ([], MARK_APPLIED), ({}, MARK_APPLIED),
])
def test_only_the_literal_owed_states_survive_a_load(persisted, expect):
    row = ckmod._coerce_row({"index": 1, "text": "t", "status": "done", "item_mark": persisted}, 1)
    assert row.item_mark == expect


def test_missing_field_reads_as_applied_and_owed_states_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    legacy = ckmod._coerce_row({"index": 1, "text": "t", "status": "done"}, 1)
    assert legacy.item_mark == MARK_APPLIED
    write_checkpoint("lp-md", "g", "p", ["one", "two", "three"],
                     [step_from_decompose("one", 3, status="done", result="r"),
                      step_from_decompose("two", 4, status="blocked", result="r", item_mark=MARK_DRIFTED),
                      step_from_decompose("three", 5, status="skipped", result="r", item_mark=MARK_APPLIED)],
                     step_indices=[3, 4, 5])
    raw = json.loads(ckmod._checkpoint_path("lp-md").read_text(encoding="utf-8"))
    assert [r["item_mark"] for r in raw["completed"]] == ["pending", "drifted", "applied"]
    ck = load_checkpoint("lp-md")
    assert [r.item_mark for r in ck.completed] == [MARK_PENDING, MARK_DRIFTED, MARK_APPLIED]
    assert ck.remaining_steps == ["two"]                # an owed mark never changes what is done
    md = ckmod.export_human("lp-md")
    assert "(NEXT.md mark pending)" in md and "no longer names this step" in md
    branched = load_checkpoint(ckmod.branch_checkpoint("lp-md"))
    assert [r.item_mark for r in branched.completed] == [MARK_PENDING, MARK_DRIFTED, MARK_APPLIED]


# ------------------------------------------------------- round-2 fixes (r2)

class _StuckOnceAdapter(_Adapter):
    """flag_stuck the FIRST time a step matching `stuck_once` runs, then
    complete it — a retried step whose retry succeeds."""

    def __init__(self, seen, stuck_once):
        super().__init__(seen)
        self.stuck_once = stuck_once
        self.flagged = False

    def complete(self, messages, **kwargs):
        from llm import LLMResponse, ToolCall
        user = " ".join(m.content for m in messages if getattr(m, "role", "") == "user")
        if "Current step" in user:
            cur = user.split("Current step")[-1][:120]
            self.seen.append(cur)
            if self.stuck_once in cur and not self.flagged:
                self.flagged = True
                return LLMResponse(content="", tool_calls=[ToolCall(
                    name="flag_stuck", arguments={"reason": "cannot yet"})],
                    input_tokens=1, output_tokens=1)
        return LLMResponse(content="", tool_calls=[ToolCall(
            name="complete_step", arguments={"result": "ok", "summary": "ok"})],
            input_tokens=1, output_tokens=1)


def test_settler_honours_only_the_latest_row_per_item(monkeypatch, tmp_path):
    """r2 finding 1: a retried attempt's blocked row must not re-mark the
    item blocked after a later row finished it."""
    _loop_env(monkeypatch, tmp_path)
    import loop_planning as lp
    import orch
    import orch_items as o
    items = o.append_next_items("proj", ["Retry me", "Twice"])
    marks = []
    real = orch.mark_item
    monkeypatch.setattr(orch, "mark_item", lambda p, i, s, **kw: marks.append((i, s)) or real(p, i, s, **kw))
    rows = [step_from_decompose("Retry me", items[0], status="blocked", item_mark=MARK_PENDING),
            step_from_decompose("Retry me", items[0], status="done", item_mark=MARK_APPLIED),
            step_from_decompose("Twice", items[1], status="blocked", item_mark=MARK_PENDING),
            step_from_decompose("Twice", items[1], status="blocked", item_mark=MARK_PENDING)]
    assert lp.settle_item_marks("proj", rows) == 1
    assert marks == [(items[1], "!")], marks               # never "!" on the finished item
    assert [r.item_mark for r in rows] == [MARK_APPLIED] * 4


def test_a_retried_step_that_then_succeeds_ends_done_in_the_mirror(monkeypatch, tmp_path):
    """Flow twin of the above: step one is flagged stuck once (a retry row
    is appended, born an attempt — no obligation) and then completes. The mirror
    ends `x` and no `!` is ever written for it after its `x`."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events = []
    _failing_marks(monkeypatch, events, fail_times=0)
    res = al.run_agent_loop("retry then done", adapter=_StuckOnceAdapter([], "Step one"),
                            preset_steps=PLAN2, max_steps=2, max_iterations=6)
    assert res.status == "done", res.stuck_reason
    ck = load_checkpoint(res.loop_id)
    items = list(ck.step_items)
    rows = [(r.index, r.status, r.item_mark) for r in ck.completed]
    assert (items[0], "blocked", MARK_ATTEMPT) in rows and (items[0], "done", MARK_APPLIED) in rows, rows
    marks = [e for e in events if e[0] == "mark" and e[1] == items[0]]
    assert marks and marks[-1] == ("mark", items[0], "x"), marks
    assert _states(res.project)[items[0]] == "x"


def test_a_terminally_blocked_step_marks_once_and_records_it(monkeypatch, tmp_path):
    """r2 finding 5: the terminal blocked mark is recorded on the row, so
    the snapshot does not mark the item a second time."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events, writes = [], []
    _failing_marks(monkeypatch, events, fail_times=0)
    _spy_writes(monkeypatch, writes)
    res = al.run_agent_loop("terminal block", adapter=_Adapter([], stuck_on=("Step one",)),
                            preset_steps=PLAN2, max_steps=2, max_iterations=8)
    ck = load_checkpoint(res.loop_id)
    items = list(ck.step_items)
    blocked_marks = [e for e in events if e[0] == "mark" and e[1] == items[0] and e[2] == "!"]
    assert len(blocked_marks) == 1, [e for e in events if e[0] == "mark"]
    assert not any(m == MARK_PENDING for w in writes for _, _, m in w), writes
    assert _states(res.project)[items[0]] == "!"


def test_the_loop_exit_settles_and_flushes_what_is_still_owed(monkeypatch, tmp_path):
    """r2 finding 2: a row owed after the last snapshot (here: the last
    step's mark and its snapshot retry both failed) is settled and written
    once more at loop exit, so the run never returns with an owed mark it
    could have paid."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events, writes = [], []
    _failing_marks(monkeypatch, events, fail_times=2)
    _spy_writes(monkeypatch, writes)
    res = al.run_agent_loop("flush", adapter=_Adapter([]), preset_steps=["Step one: fetch"],
                            max_steps=1, max_iterations=3)
    assert res.status == "done", res.stuck_reason
    items = list(load_checkpoint(res.loop_id).step_items)
    assert [e for e in events if e[0] == "mark"] == [("mark", items[0], "x")] * 3
    assert writes[-2] == [(items[0], "done", MARK_PENDING)] and writes[-1] == [(items[0], "done", MARK_APPLIED)], writes
    assert _states(res.project)[items[0]] == "x"
    assert res.steps[0].item_mark == MARK_APPLIED


def test_compare_and_mark_uses_the_items_identity_form(monkeypatch, tmp_path):
    """r2 finding 3: the loop executes a `[boundary]` step with the tag
    stripped and NEXT.md reads one line per item — neither is drift."""
    _loop_env(monkeypatch, tmp_path)
    import orch_items as o
    assert o.normalize_item_text("  Ship   remainder [boundary] ") == "Ship remainder"
    assert o.normalize_item_text("first line\nsecond line") == "first line"
    assert o.normalize_item_text("\n\n  x [after:2]  ") == "x [after:2]"
    assert o.normalize_item_text(None) == ""
    # (the multi-line text goes last: append_next_items numbers items by
    # its input, but a second physical line shifts every later item's line)
    items = o.append_next_items("proj", ["Ship remainder [boundary]", "Tagged [after:1]", "Line one\nline two"])
    o.mark_item("proj", items[0], o.STATE_DONE, expected_text="Ship remainder")
    o.mark_item("proj", items[1], o.STATE_DONE, expected_text="Tagged [after:1]")
    o.mark_item("proj", items[2], o.STATE_DONE, expected_text="Line one\nline two")
    with pytest.raises(o.ItemIdentityError):
        o.mark_item("proj", items[1], o.STATE_DONE, expected_text="Tagged")
    st = _states("proj")
    assert all(st[i] == "x" for i in items), st


def test_parallel_returned_rows_follow_a_final_pass_retry_that_succeeds(monkeypatch, tmp_path):
    """r2 finding 4: the mark that succeeds only on the final pass reaches
    the returned rows too."""
    import orch
    calls = {"n": 0}

    def _mark(project_, item, state, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("locked for a while")

    def dag(on_progress, steps):
        on_progress({1: {"status": "blocked", "stuck_reason": "x", "result": ""}})
        return [{"status": "blocked", "stuck_reason": "x", "result": "", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[7])
    monkeypatch.setattr(orch, "mark_item", _mark)
    writes = []
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.status, o.item_mark) for o in a[4]]))
    res = lp._run_parallel_path(
        ctx, ["A"], clean_steps=["A"], deps={1: set()}, levels=[[1]], parallel_levels=[[1]],
        parallel_fan_out=2, proj_fanout_dir="", loop_shared_ctx={}, use_dag=True,
        resolve_tools_fn=lambda: [], step_indices=[7])
    assert writes == [[("blocked", MARK_PENDING)], [("blocked", MARK_APPLIED)]], writes
    assert [(r.status, r.item_mark) for r in res.steps] == [("blocked", MARK_APPLIED)]
    assert calls["n"] == 3


def test_foreign_carried_rows_surface_as_drifted_not_marked(monkeypatch, tmp_path):
    """r2 finding 6: a RestoredCheckpoint naming another project keeps its
    owed marks out of every settler — recorded drifted."""
    _loop_env(monkeypatch, tmp_path)
    from types import SimpleNamespace
    import loop_planning as lp
    import orch
    import orch_items as o
    from checkpoint import Checkpoint, CompletedStep
    from loop_types import LoopContext
    marks = []
    monkeypatch.setattr(orch, "mark_item", lambda p, i, s, **kw: marks.append((p, i, s)))
    items = o.append_next_items("here", ["Coincidentally identical task", "Next"])
    ck = Checkpoint(loop_id="lp-foreign", goal="g", project="elsewhere", steps=["Next"], completed=[
        CompletedStep(index=items[0], text="Coincidentally identical task", status="done",
                      position=0, item_mark=MARK_PENDING)])
    restored = lp.RestoredCheckpoint(loop_id="lp-foreign", ckpt=ck, steps=["Next"], items=None,
                                     plan_items=None, completed=list(ck.completed), project="elsewhere")
    ctx = LoopContext(goal="g", project="here", loop_id="lp-cur", verbose=False)
    ctx.adapter = SimpleNamespace(model_key="t")
    _steps, pf, early = lp._preflight_checks(ctx, ["Next"], parallel_fan_out=0, resume=restored)
    assert early is None
    carried = pf["resume_completed"]
    assert [(r.index, r.item_mark) for r in carried] == [(items[0], MARK_DRIFTED)]
    assert marks == [] and _states("here")[items[0]] == " "
    # and a later in-run settle leaves it alone
    assert lp.settle_item_marks("here", carried) == 0 and marks == []


# ------------------------------------------------------- round-3 fixes (r3)

def test_an_attempt_row_never_supersedes_an_owed_verdict(monkeypatch, tmp_path):
    """r3 finding 1: a carried blocked row still owing `!` followed by a
    fresh retry attempt for the same item — the attempt is not a verdict,
    so the carried debt is still the item's latest word and gets paid."""
    _loop_env(monkeypatch, tmp_path)
    import loop_planning as lp
    import orch
    items = _next_items_for(monkeypatch, tmp_path, ["Carried", "Other"])
    marks = []
    real = orch.mark_item
    monkeypatch.setattr(orch, "mark_item", lambda p, i, s, **kw: marks.append((i, s)) or real(p, i, s, **kw))
    rows = [step_from_decompose("Carried", items[0], status="blocked", item_mark=MARK_PENDING),
            step_from_decompose("Carried", items[0], status="blocked", item_mark=MARK_ATTEMPT),
            step_from_decompose("Other", items[1], status="blocked", item_mark=MARK_PENDING),
            step_from_decompose("Other", items[1], status="done", item_mark=MARK_APPLIED)]
    assert lp.settle_item_marks("proj", rows) == 1
    assert marks == [(items[0], "!")], marks
    assert [r.item_mark for r in rows] == [MARK_APPLIED, MARK_ATTEMPT, MARK_APPLIED, MARK_APPLIED]
    assert _states("proj")[items[0]] == "!"


def _next_items_for(monkeypatch, tmp_path, texts):
    import orch_items as o
    return o.append_next_items("proj", texts)


def test_a_retry_cut_short_by_max_iterations_still_reaches_the_checkpoint(monkeypatch, tmp_path):
    """r3 finding 1 (a): the retry attempt row is appended after the last
    snapshot and owes nothing, so the debt predicate alone left it out of
    the checkpoint; the exit flush now writes any row appended since the
    last snapshot. No verdict was reached, so no `!` is written either."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    events, writes = [], []
    _failing_marks(monkeypatch, events, fail_times=0)
    _spy_writes(monkeypatch, writes)
    res = al.run_agent_loop("cut short", adapter=_Adapter([], stuck_on=("Step one",)),
                            preset_steps=PLAN2, max_steps=2, max_iterations=1)
    assert res.status != "done"
    ck = load_checkpoint(res.loop_id)
    items = list(ck.step_items)
    rows = [(r.index, r.status, r.item_mark) for r in ck.completed]
    assert rows == [(items[0], "blocked", MARK_ATTEMPT)], rows
    assert not [e for e in events if e[0] == "mark" and e[2] == "!"], events
    assert writes[-1] == [(items[0], "blocked", MARK_ATTEMPT)], writes
    assert _states(res.project)[items[0]] != "!"


def test_parallel_final_refresh_leaves_carried_rows_alone(monkeypatch, tmp_path):
    """r3 finding 2: the returned rows are carried + this hop; the refresh
    after the final write must index only this hop's rows by node, or a
    carried drifted row reads the current node's mark."""
    marks = []

    def dag(on_progress, steps):
        on_progress({1: {"status": "done", "result": "ok"}})
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[3], marks=marks)
    carried = [step_from_decompose("Z", 2, status="blocked", result="r", ended_ts="", item_mark=MARK_DRIFTED)]
    res = lp._run_parallel_path(
        ctx, ["A"], clean_steps=["A"], deps={1: set()}, levels=[[1]], parallel_levels=[[1]],
        parallel_fan_out=2, proj_fanout_dir="", loop_shared_ctx={}, use_dag=True,
        resolve_tools_fn=lambda: [], step_indices=[3], carried_outcomes=carried)
    assert res.status == "done"
    assert marks == [(3, "x")], marks                      # drifted is never retried
    assert [(r.text, r.status, r.item_mark) for r in res.steps] == [
        ("Z", "blocked", MARK_DRIFTED), ("A", "done", MARK_APPLIED)]
