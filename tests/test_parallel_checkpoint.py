"""Chunk 5 (2026-09-16): the DAG / fan-out lane writes a checkpoint after
every committed node, carries a resume's rows, and a crash mid-DAG resumes
at the unfinished nodes instead of "nothing done".

Doctrine: every finished node is durable, in every lane; the checkpoint a
lane writes is the same shape the sequential lane writes (plan, items,
binding, rows) so one loader resumes both.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import checkpoint as ckmod
from checkpoint import load_checkpoint

from test_plan_node_ids import _Adapter, _loop_env, _next_items  # noqa: F401


class _Crash(BaseException):
    """A worker death the coordinator does not catch (it catches Exception)."""


class _CrashingAdapter(_Adapter):
    def __init__(self, seen, crash_on):
        super().__init__(seen)
        self.crash_on = crash_on

    def complete(self, messages, **kwargs):
        user = " ".join(m.content for m in messages if getattr(m, "role", "") == "user")
        if "Current step" in user and self.crash_on in user.split("Current step")[-1][:120]:
            raise _Crash("worker died")
        return super().complete(messages, **kwargs)


DAG = ["Step A: fetch", "Step B: parse [after:1]", "Step C: index [after:1]",
       "Step D: report [after:2,3]"]


def test_dag_lane_checkpoints_each_node_and_a_crash_resumes_at_the_unfinished(monkeypatch, tmp_path):
    """Fresh DAG run (A → {B, C} → D). The worker for D dies with a
    BaseException: the coordinator does not catch it and the loop crashes.
    The checkpoint written after C carries A, B, C done on their items with
    the original binding; the resume runs ONLY D and finishes every item."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    seen = []
    writes = []
    real = ckmod.write_checkpoint

    def spy(loop_id, goal, project, steps, step_outcomes, **kw):
        writes.append((loop_id, list(steps), [(o.index, o.text, o.status) for o in step_outcomes],
                       kw.get("step_indices"), kw.get("plan_items")))
        return real(loop_id, goal, project, steps, step_outcomes, **kw)
    monkeypatch.setattr(ckmod, "write_checkpoint", spy)
    # The crash is simulated IN this process: the kernel would drop the
    # crashed loop's run-lease flock at process exit, here nothing unwinds
    # it — release it by hand, or the resume (rightly) sees the owner alive
    # (chunk 7: the loader probes the source's owner before claiming).
    import run_lease
    leases = []
    real_acq = run_lease.acquire_run_lease
    monkeypatch.setattr(run_lease, "acquire_run_lease",
                        lambda *a, **k: leases.append(real_acq(*a, **k)) or leases[-1])
    with pytest.raises(_Crash):
        al.run_agent_loop("dag ckpt", adapter=_CrashingAdapter(seen, "Step D"), preset_steps=DAG,
                          max_steps=4, max_iterations=8, parallel_fan_out=2)
    for _lease in leases:
        if _lease is not None:
            _lease.release()
    assert writes, "the DAG lane wrote no checkpoint"
    loop_id = writes[0][0]
    assert all(w[0] == loop_id for w in writes)
    # one write per committed node, rows growing, plan + items + binding on every write
    assert [len(w[2]) for w in writes] == [1, 2, 3], writes
    for _, steps, rows, items, binding in writes:
        assert steps == DAG and len(items) == 4 and binding == items
    ck = load_checkpoint(loop_id)
    assert ck is not None and ck.positioned
    assert ck.remaining_steps == [DAG[3]] and ck.remaining_items == [ck.step_items[3]]
    assert {c.text for c in ck.completed} == set(DAG[:3])
    assert [c.index for c in ck.completed] == ck.step_items[:3]
    assert ck.is_complete() is False
    items = list(ck.step_items)
    before = _next_items(ck.project)
    states = {it.index: it.state for it in before}
    assert states[items[3]] == " ", states           # D never ran: still TODO
    assert all(states[i] == "x" for i in items[:3]), states   # marked as each node committed

    seen.clear()
    second = al.run_agent_loop("dag ckpt", adapter=_Adapter(seen), max_steps=4, max_iterations=8,
                               parallel_fan_out=2, resume_from_loop_id=loop_id)
    assert second.status == "done", second.stuck_reason
    assert [c for c in seen if "Current" in c or True] and all("Step D" in c for c in seen), seen
    after = _next_items(ck.project)
    assert [it.index for it in after] == [it.index for it in before]
    assert all({it.index: it.state for it in after}[i] == "x" for i in items)
    # the resumed rows lead the result (carried), D's row carries its item
    assert [s.text for s in second.steps] == DAG
    assert second.steps[-1].index == items[3]
    ck2 = load_checkpoint(second.loop_id)
    assert ck2.steps == [DAG[3]] and ck2.step_items == [items[3]] and ck2.plan_items == items
    assert ck2.is_complete()


def test_resumed_dag_lane_checkpoints_the_suffix_with_carried_rows(monkeypatch, tmp_path):
    """Hop 1 sequential finishes A; hop 2 takes the DAG lane for {B, C} → D.
    Every hop-2 write carries A's row in front, the suffix as the plan, the
    suffix's items and the ORIGINAL binding; the last one is complete."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    seen = []
    first = al.run_agent_loop("dag resume ckpt", adapter=_Adapter(seen), preset_steps=DAG,
                              max_steps=4, max_iterations=1)
    ck1 = load_checkpoint(first.loop_id)
    items = list(ck1.step_items)
    writes = []
    real = ckmod.write_checkpoint

    def spy(loop_id, goal, project, steps, step_outcomes, **kw):
        writes.append((list(steps), [(o.index, o.status) for o in step_outcomes],
                       kw.get("step_indices"), kw.get("plan_items")))
        return real(loop_id, goal, project, steps, step_outcomes, **kw)
    monkeypatch.setattr(ckmod, "write_checkpoint", spy)
    second = al.run_agent_loop("dag resume ckpt", adapter=_Adapter(seen), max_steps=4,
                               max_iterations=8, parallel_fan_out=2,
                               resume_from_loop_id=first.loop_id)
    assert second.status == "done", second.stuck_reason
    assert len(writes) >= 3
    for steps, rows, idxs, binding in writes:
        assert steps == DAG[1:] and idxs == items[1:] and binding == items
        assert rows[0] == (items[0], "done")                 # carried row leads
    assert [r[0] for r in writes[-1][1]] == items            # final write: every item, in order
    ck2 = load_checkpoint(second.loop_id)
    assert ck2.is_complete() and ck2.remaining_steps == []
    assert [s.index for s in second.steps] == items          # LoopResult carries A too


def test_dag_commit_helper_reports_every_row_under_the_lock():
    """Unit: every commit path (done, execution error, pre-gated, gated
    dependent) reaches `on_progress` with the snapshot so far; the caller's
    write failing never fails the lane."""
    from loop_parallel import _run_steps_dag
    snapshots = []

    def _fake_exec(**kwargs):
        if kwargs["step_num"] == 3:
            raise RuntimeError("boom")
        return {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0,
                "summary": "ok"}

    def _on_progress(snap):
        snapshots.append({k: v["status"] for k, v in snap.items()})
        if len(snapshots) == 1:
            raise OSError("disk full")            # never fatal to the lane

    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        outcomes = _run_steps_dag(
            goal="g", steps=["A", "B", "C", "D", "E"],
            deps={1: set(), 2: {1}, 3: set(), 4: {3}, 5: set()},
            adapter=SimpleNamespace(model_key="t"), ancestry_context="", tools=[],
            verbose=False, max_workers=1, declared={4: {3}},
            pre_gated={5: "not executed — declared prerequisite not met: step 9 ended skipped (before this resume)"},
            on_progress=_on_progress)
    assert [o["status"] for o in outcomes] == ["done", "done", "blocked", "blocked", "blocked"]
    assert snapshots[0] == {5: "blocked"}                       # pre-gated, first
    assert len(snapshots) == 5 and snapshots[-1] == {1: "done", 2: "done", 3: "blocked",
                                                      4: "blocked", 5: "blocked"}
    for a, b in zip(snapshots, snapshots[1:]):
        assert set(a) < set(b), (a, b)                          # strictly growing


def test_fanout_lane_reports_progress_per_outcome():
    from loop_parallel import _run_steps_parallel
    snapshots = []

    def _fake_exec(**kwargs):
        return {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0,
                "summary": "ok"}
    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        outcomes = _run_steps_parallel(
            goal="g", steps=["A", "B", "C"], adapter=SimpleNamespace(model_key="t"),
            ancestry_context="", tools=[], verbose=False, max_workers=2,
            on_progress=lambda snap: snapshots.append(sorted(snap)))
    assert [o["status"] for o in outcomes] == ["done"] * 3
    assert [len(s) for s in snapshots] == [1, 2, 3]


def test_parallel_path_without_items_writes_no_checkpoint(monkeypatch, tmp_path):
    """Direct callers that pass no `step_indices` get the old behaviour:
    positional rows, nothing checkpointed (a positional file would resume
    by the legacy row-count rule)."""
    _loop_env(monkeypatch, tmp_path)
    import loop_parallel
    from loop_types import LoopContext
    writes = []
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append(a))

    def _fake_dag(**kw):
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}
                for _ in kw["steps"]]
    monkeypatch.setattr(loop_parallel, "_run_steps_dag", _fake_dag)
    ctx = LoopContext(goal="g", project="", loop_id="lp-direct", verbose=False)
    ctx.adapter = SimpleNamespace(model_key="t")
    res = loop_parallel._run_parallel_path(
        ctx, ["A", "B"], clean_steps=["A", "B"], deps={1: set(), 2: set()},
        levels=[[1, 2]], parallel_levels=[[1, 2]], parallel_fan_out=2, proj_fanout_dir="",
        loop_shared_ctx={}, use_dag=True, resolve_tools_fn=lambda: [])
    assert res.status == "done" and [s.index for s in res.steps] == [1, 2]
    assert not writes


# --- round-1 fixes (chunk-5 review, 2026-09-16) ------------------------------

def _path_env(monkeypatch, tmp_path, dag_impl, *, items=None, project="proj", marks=None):
    """Drive `_run_parallel_path` with a fake DAG that replays `dag_impl`
    (called with the on_progress hook; returns the outcome list)."""
    _loop_env(monkeypatch, tmp_path)
    import loop_parallel
    from loop_types import LoopContext
    monkeypatch.setattr(loop_parallel, "_run_steps_dag",
                        lambda **kw: dag_impl(kw.get("on_progress"), kw["steps"]))
    if marks is not None:
        import orch

        def _mark(project_, item, state, **kw):     # chunk 8: compare-and-mark passes expected_text
            marks.append((item, state))
            if isinstance(marks, _FailFirst) and marks.fail_once:
                marks.fail_once = False
                raise OSError("NEXT locked")
        monkeypatch.setattr(orch, "mark_item", _mark)
    ctx = LoopContext(goal="g", project=project, loop_id="lp-path", verbose=False)
    ctx.adapter = SimpleNamespace(model_key="t")
    ctx.plan_items = list(items) if items else None
    return ctx, loop_parallel


class _FailFirst(list):
    fail_once = True


def _drive(ctx, loop_parallel, steps, items):
    return loop_parallel._run_parallel_path(
        ctx, steps, clean_steps=steps, deps={i: set() for i in range(1, len(steps) + 1)},
        levels=[list(range(1, len(steps) + 1))], parallel_levels=[list(range(1, len(steps) + 1))],
        parallel_fan_out=2, proj_fanout_dir="", loop_shared_ctx={}, use_dag=True,
        resolve_tools_fn=lambda: [], step_indices=items)


def test_timeout_then_late_success_moves_the_item_blocked_to_done(monkeypatch, tmp_path):
    """A DAG timeout row is provisional: the worker's real result later
    replaces it. The item follows the LATEST applied state (blocked → done)
    and the final file says done (r1: a once-only set froze the first)."""
    marks = []
    writes = []

    def dag(on_progress, steps):
        on_progress({1: {"status": "blocked", "stuck_reason": "dag timeout (0s)", "result": ""}})
        on_progress({1: {"status": "done", "result": "actual"}})
        return [{"status": "done", "result": "actual", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[7], marks=marks)
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.index, o.status) for o in a[4]]))
    res = _drive(ctx, lp, ["A"], [7])
    assert res.status == "done"
    assert marks == [(7, "!"), (7, "x")], marks
    assert writes[-1] == [(7, "done")]


def test_failed_mark_is_retried_at_the_next_snapshot(monkeypatch, tmp_path):
    marks = _FailFirst()
    writes = []

    def dag(on_progress, steps):
        on_progress({1: {"status": "done", "result": "ok"}})
        on_progress({1: {"status": "done", "result": "ok"}, 2: {"status": "done", "result": "ok"}})
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}] * 2
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[7, 8], marks=marks)
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.index, o.status) for o in a[4]]))
    res = _drive(ctx, lp, ["A", "B"], [7, 8])
    assert res.status == "done"
    # first attempt on 7 raised; the next snapshot retried it — exactly once each after that
    assert list(marks) == [(7, "x"), (7, "x"), (8, "x")], list(marks)


def test_status_domain_is_closed_at_the_lane_boundary(monkeypatch, tmp_path):
    """`skipped` finishes its position AND marks its item done; `pending` /
    `interrupted` / `DONE` are not terminal outcomes — they become blocked
    rows (resumable) and the run is stuck, never a silent success."""
    marks = []
    writes = []

    def dag(on_progress, steps):
        return [{"status": "skipped", "result": ""}, {"status": "pending", "result": ""},
                {"status": "DONE", "result": "x"}, {"status": "interrupted", "result": ""}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[1, 2, 3, 4], marks=marks)
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append([(o.index, o.status) for o in a[4]]))
    res = _drive(ctx, lp, ["A", "B", "C", "D"], [1, 2, 3, 4])
    assert [s.status for s in res.steps] == ["skipped", "blocked", "blocked", "blocked"]
    assert res.status == "stuck" and "unrecognized outcome status" in res.stuck_reason
    assert marks == [(1, "x"), (2, "!"), (3, "!"), (4, "!")], marks
    assert writes[-1] == [(1, "skipped"), (2, "blocked"), (3, "blocked"), (4, "blocked")]


def test_node_effects_land_before_the_done_row(monkeypatch, tmp_path):
    """A committed node's world facts are in the checkpoint written for
    that very commit (r1 HIGH: they were recorded only after the lane
    returned, so a crash skipped the node forever and lost them)."""
    writes = []

    def dag(on_progress, steps):
        on_progress({1: {"status": "done", "result": "ok",
                         "world_facts": [{"kind": "anecdotal", "fact": "python is 3.14",
                                          "evidence": "python --version"}]}})
        raise _Crash("worker died after A committed")
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[5, 6])
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append((list(a[3]), k.get("world_facts"))))
    with pytest.raises(_Crash):
        _drive(ctx, lp, ["A", "B"], [5, 6])
    assert writes and writes[0][1], "no world fact in the commit's own write"
    assert any("python is 3.14" in str(f) for f in writes[0][1])
    assert ctx.world_facts.to_list()


def test_final_progress_write_never_fails_the_lane(monkeypatch, tmp_path):
    import orch

    def dag(on_progress, steps):
        return [{"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0}]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[9])

    def _boom(*a, **k):
        raise RuntimeError("unexpected marker bug")
    monkeypatch.setattr(orch, "mark_item", _boom)
    res = _drive(ctx, lp, ["A"], [9])
    assert res.status == "done" and res.steps[0].index == 9


def test_fanout_commit_covers_timeout_and_late_error_and_excludes_persistence(monkeypatch):
    """One commit for every fan-out row: the timeout row reaches the hook;
    a late worker that RAISED is reconciled as an execution error, not left
    as a timeout; and a slow hook does not eat a finished peer's budget."""
    import time as _t
    from loop_parallel import _run_steps_parallel
    monkeypatch.setenv("MARO_STEP_TIMEOUT", "1")
    snaps = []

    def _fake_exec(**kwargs):
        n = kwargs["step_num"]
        if n == 2:
            _t.sleep(0.6)
        if n == 3:
            _t.sleep(1.6)
            raise RuntimeError("late-real-error")
        return {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0, "summary": "ok"}

    def _slow_hook(snap):
        snaps.append({k: v["status"] for k, v in snap.items()})
        if len(snaps) == 1:
            _t.sleep(0.8)          # persistence latency after step 1 landed
    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        outcomes = _run_steps_parallel(
            goal="g", steps=["A", "B", "C"], adapter=SimpleNamespace(model_key="t"),
            ancestry_context="", tools=[], verbose=False, max_workers=3,
            on_progress=_slow_hook)
    assert outcomes[0]["status"] == "done"
    assert outcomes[1]["status"] == "done", outcomes[1]          # NOT a synthetic timeout
    assert outcomes[2]["status"] == "blocked"
    assert "parallel execution error: late-real-error" in outcomes[2]["stuck_reason"], outcomes[2]
    assert any(s.get(3) == "blocked" for s in snaps)             # timeout/replacement rows reached the hook
    assert snaps[-1] == {1: "done", 2: "done", 3: "blocked"}


def test_cli_resume_of_a_dag_written_checkpoint_reenters_the_dag_lane(monkeypatch, tmp_path):
    """`maro resume <loop>` restores the run's own execution policy
    (`Checkpoint.parallel_fan_out`): a file the DAG lane wrote resumes in
    the DAG lane and runs only the unfinished nodes (r1: the CLI passed no
    width, so it resumed sequentially)."""
    import argparse
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    import llm
    import loop_parallel
    seen = []
    writes = []
    real = ckmod.write_checkpoint

    def spy(loop_id, goal, project, steps, step_outcomes, **kw):
        writes.append((loop_id, kw.get("parallel_fan_out")))
        return real(loop_id, goal, project, steps, step_outcomes, **kw)
    monkeypatch.setattr(ckmod, "write_checkpoint", spy)
    import run_lease
    leases = []
    real_lease = run_lease.acquire_run_lease
    monkeypatch.setattr(run_lease, "acquire_run_lease",
                        lambda *a, **k: leases.append(real_lease(*a, **k)) or leases[-1])
    # D is alone in its level (deterministic crash point once B and C are
    # done); the unfinished suffix D..G still has a parallel level (E, F).
    plan = ["Step A: fetch", "Step B: parse [after:1]", "Step C: index [after:1]",
            "Step D: report [after:2,3]", "Step E: ship [after:4]",
            "Step F: notify [after:4]", "Step G: close [after:5,6]"]
    with pytest.raises(_Crash):
        al.run_agent_loop("cli dag", adapter=_CrashingAdapter(seen, "Step D"), preset_steps=plan,
                          max_steps=7, max_iterations=10, parallel_fan_out=2)
    for _l in leases:      # the dead process's flock is kernel-released in prod
        _l.release()
    loop_id = writes[0][0]
    assert all(w[1] == 2 for w in writes)
    ck = load_checkpoint(loop_id)
    assert ck.parallel_fan_out == 2 and ck.remaining_steps == plan[3:]
    monkeypatch.setattr(ckmod, "write_checkpoint", real)
    took_dag = {}
    real_dag = loop_parallel._run_steps_dag
    monkeypatch.setattr(loop_parallel, "_run_steps_dag",
                        lambda **kw: took_dag.setdefault("steps", list(kw["steps"])) and real_dag(**kw))
    monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _Adapter(seen))
    monkeypatch.setattr(cli, "_closure_verdict_pass", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning", lambda *a, **k: None)
    seen.clear()
    rc = cli._cmd_resume(argparse.Namespace(run_id=loop_id, verbose=False, format="text"))
    assert rc == 0, rc
    assert [t.split(" [after")[0] for t in took_dag.get("steps", [])] == [
        "Step D: report", "Step E: ship", "Step F: notify", "Step G: close"], took_dag
    assert seen and not any("Step A" in c or "Step B" in c or "Step C" in c for c in seen), seen
    after = _next_items(ck.project)
    assert all({it.index: it.state for it in after}[i] == "x" for i in ck.step_items)


# --- round-2 fixes (chunk-5 review, 2026-09-16) ------------------------------

def test_a_worker_returning_none_is_a_contract_row_in_both_lanes(monkeypatch):
    """The closed status domain covers executor OUTPUTS, not just status
    strings: a worker that returns None becomes a blocked contract row in
    the scheduler (r2: AttributeError in gating / at the lane boundary)."""
    from loop_parallel import _run_steps_dag, _run_steps_parallel
    monkeypatch.setenv("MARO_STEP_TIMEOUT", "5")

    def _fake_exec(**kwargs):
        if kwargs["step_num"] == 1:
            return None
        return {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0, "summary": "ok"}
    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        dag = _run_steps_dag(goal="g", steps=["A", "B [after:1]"], deps={1: set(), 2: {1}},
                             declared={2: {1}},
                             adapter=SimpleNamespace(model_key="t"), ancestry_context="",
                             tools=[], verbose=False, max_workers=2)
        fan = _run_steps_parallel(goal="g", steps=["A", "B"], adapter=SimpleNamespace(model_key="t"),
                                  ancestry_context="", tools=[], verbose=False, max_workers=2)
    assert dag[0]["status"] == "blocked" and "execution contract" in dag[0]["stuck_reason"]
    assert dag[1]["status"] == "blocked"              # gated on its blocked prerequisite
    assert fan[0]["status"] == "blocked" and "returned NoneType" in fan[0]["stuck_reason"]
    assert fan[1]["status"] == "done"


def test_dag_timeout_row_never_overwrites_a_done_that_landed_in_between(monkeypatch):
    """Legal interleaving (r2 HIGH): the coordinator hits its deadline, the
    worker commits `done`, THEN the coordinator's synthetic timeout row is
    applied. Check-and-insert is one critical section now, so the real
    result stands (before: `done` → `blocked`, the node re-ran on resume
    and its effects ran twice)."""
    import threading
    from loop_parallel import _run_steps_dag
    monkeypatch.setenv("MARO_STEP_TIMEOUT", "0")
    orig_lock = threading.Lock
    ready, allow, committed = threading.Event(), threading.Event(), threading.Event()
    main = threading.get_ident()
    fired = {"n": 0}

    class GateLock:
        """After the worker is running, the FIRST time the coordinator
        leaves the results lock, let the worker commit before it goes on."""
        def __init__(self):
            self.inner = orig_lock()
            self.acquire, self.release, self.locked = (
                self.inner.acquire, self.inner.release, self.inner.locked)

        def __enter__(self):
            self.inner.acquire()
            return self

        def __exit__(self, *exc):
            self.inner.release()
            if threading.get_ident() == main and ready.is_set() and fired["n"] == 0:
                fired["n"] = 1
                allow.set()
                committed.wait(4)

    import inspect

    def factory():
        # only the scheduler's OWN lock (created directly in _run_steps_dag),
        # not the Futures' conditions created beneath it
        caller = inspect.currentframe().f_back
        if threading.get_ident() == main and caller.f_code.co_name == "_run_steps_dag":
            return GateLock()
        return orig_lock()
    monkeypatch.setattr(threading, "Lock", factory)
    snaps = []

    def _fake_exec(**kwargs):
        ready.set()
        allow.wait(4)
        return {"status": "done", "result": "real", "tokens_in": 0, "tokens_out": 0}

    def _progress(s):
        snaps.append({k: v.get("status") for k, v in s.items()})
        if s.get(1, {}).get("status") == "done":
            committed.set()
    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        out = _run_steps_dag(goal="g", steps=["A"], deps={1: set()},
                             adapter=SimpleNamespace(model_key="t"), ancestry_context="",
                             tools=[], verbose=False, max_workers=1, on_progress=_progress)
    assert fired["n"] == 1, "the interleaving under test did not happen"
    assert out[0]["status"] == "done" and out[0]["result"] == "real", out
    assert snaps[-1] == {1: "done"}, snaps
    assert all(s.get(1) != "blocked" or i < len(snaps) - 1 for i, s in enumerate(snaps))


def test_node_effects_are_retried_until_they_succeed(monkeypatch, tmp_path):
    """A transient failure inside a node's effects is NOT acknowledged
    (r2 HIGH): the next snapshot / the final pass runs them again, so the
    fact lands before the run ends."""
    import loop_post_step
    calls = {"n": 0}
    real = loop_post_step.record_step_world_facts

    def _flaky(ctx, key, oc):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient ledger failure")
        return real(ctx, key, oc)
    monkeypatch.setattr(loop_post_step, "record_step_world_facts", _flaky)
    writes = []

    def dag(on_progress, steps):
        oc = {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0,
              "world_facts": [{"kind": "anecdotal", "fact": "the api rate-limits at 60/min",
                               "evidence": "429 after 60 calls"}]}
        on_progress({1: oc})
        return [oc]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[3])
    monkeypatch.setattr(ckmod, "write_checkpoint",
                        lambda *a, **k: writes.append(k.get("world_facts")))
    res = _drive(ctx, lp, ["A"], [3])
    assert res.status == "done"
    assert calls["n"] == 2, calls
    assert writes[0] == [] and writes[-1] and "rate-limits" in str(writes[-1])
    assert ctx.world_facts.to_list()


# --- round-3 fixes (chunk-5 review, 2026-09-16) ------------------------------

def test_a_non_terminal_prerequisite_gates_its_declared_dependent_in_the_real_scheduler(monkeypatch):
    """The status domain is closed at the EXECUTOR boundary (r3 HIGH): a
    prerequisite returning `pending` is blocked before the scheduler gates
    its dependents, so a declared dependent is never run — not normalized
    only after the work already happened."""
    from loop_parallel import _run_steps_dag
    monkeypatch.setenv("MARO_STEP_TIMEOUT", "5")
    called = []

    def _fake_exec(**kwargs):
        n = kwargs["step_num"]
        called.append(n)
        if n == 1:
            return {"status": "pending", "result": "", "tokens_in": 0, "tokens_out": 0}
        return {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0, "summary": "ok"}
    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        out = _run_steps_dag(goal="g", steps=["A", "B [after:1]"], deps={1: set(), 2: {1}},
                             declared={2: {1}}, adapter=SimpleNamespace(model_key="t"),
                             ancestry_context="", tools=[], verbose=False, max_workers=2)
    assert called == [1], called
    assert out[0]["status"] == "blocked" and "unrecognized outcome status 'pending'" in out[0]["stuck_reason"]
    assert out[1]["status"] == "blocked" and "prerequisite" in out[1]["stuck_reason"]


def test_an_effect_family_that_already_ran_is_not_rerun_when_a_later_one_fails(monkeypatch, tmp_path):
    """Per-family acknowledgement (r3): decisions recorded once; the failed
    world-fact recorder is the only thing retried (the decision journal is
    append-only, so a bundle retry duplicated its rows)."""
    import loop_post_step
    dec_calls = {"n": 0}
    wf_calls = {"n": 0}
    real_wf = loop_post_step.record_step_world_facts

    def _dec(ctx, key, oc, shared):
        dec_calls["n"] += 1
        shared[f"decision:{key}:0"] = "use sqlite — it is already installed"

    def _flaky_wf(ctx, key, oc):
        wf_calls["n"] += 1
        if wf_calls["n"] == 1:
            raise RuntimeError("transient ledger failure")
        return real_wf(ctx, key, oc)
    monkeypatch.setattr(loop_post_step, "record_step_decisions", _dec)
    monkeypatch.setattr(loop_post_step, "record_step_world_facts", _flaky_wf)

    def dag(on_progress, steps):
        oc = {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0,
              "world_facts": [{"kind": "anecdotal", "fact": "sqlite ships with the image",
                               "evidence": "python -c 'import sqlite3'"}]}
        on_progress({1: oc})
        return [oc]
    ctx, lp = _path_env(monkeypatch, tmp_path, dag, items=[3])
    monkeypatch.setattr(ckmod, "write_checkpoint", lambda *a, **k: None)
    res = _drive(ctx, lp, ["A"], [3])
    assert res.status == "done"
    assert (dec_calls["n"], wf_calls["n"]) == (1, 2), (dec_calls, wf_calls)
    assert ctx.world_facts.to_list()
