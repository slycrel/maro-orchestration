"""Durable tail jobs — the record, the drain, and the real process spawn.

The chunk's claim is specific and testable: after the answer is delivered, the
run's process should be able to EXIT while the tail is still running. Phase 1
only reordered the tail past the notify (same process, same thread), so a
caller waiting on process exit paid all of it — 53% of wall clock on the
measured run. `test_spawned_tail_outlives_the_parent_call` is the probe for
that claim: it asserts the dispatch returns before the child has finished, and
that the child then finishes on its own.

The rest guard the properties that make the spawn safe: the handoff is
byte-exact (a closure carried objects; a record has to carry them itself), the
two lanes never both run a job, a spawn that cannot be made falls back to the
behaviour it replaced, and a tail whose process died is discoverable rather
than lost.
"""

import json
import os
import time
from pathlib import Path

import pytest

import tail_jobs
from loop_types import StepOutcome


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_run(handle_id: str = "tj000001") -> Path:
    """A run-dir of the shape `runs.run_dir` resolves, without a real run."""
    from runs import run_dir
    rd = run_dir(handle_id)
    (rd / "build").mkdir(parents=True, exist_ok=True)
    return rd


class _FakeLoop:
    def __init__(self, **kw):
        self.loop_id = kw.get("loop_id", "loop-1")
        self.project = kw.get("project", "proj")
        self.goal = kw.get("goal", "a goal")
        self.status = kw.get("status", "done")
        self.steps = kw.get("steps", [])
        self.had_no_matching_skill = kw.get("had_no_matching_skill", False)


class _FakeAdapter:
    backend = "anthropic"
    model_key = "mid"


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def test_no_run_dir_means_not_recordable():
    """A handle that never opened a run-dir must not mint one here.

    The caller reads False as "you still own this work" and keeps the phase
    in-process. Minting a run-dir instead would put a phantom run in front of
    the run index and the report writers.
    """
    assert tail_jobs.jobs_path("nosuchrun") is None
    assert tail_jobs.record_learning("nosuchrun", _FakeLoop()) is False
    assert tail_jobs.record_maintenance("nosuchrun") is False
    assert tail_jobs.pending_jobs("nosuchrun") == []


def test_record_learning_writes_a_pending_job():
    _make_run()
    assert tail_jobs.record_learning(
        "tj000001", _FakeLoop(), adapter=_FakeAdapter(), project="proj") is True
    pending = tail_jobs.pending_jobs("tj000001")
    assert [j["kind"] for j in pending] == ["learning"]
    spec = pending[0]["spec"]
    assert spec["loop_id"] == "loop-1"
    assert spec["adapter"] == {"backend": "anthropic", "model_key": "mid"}


def test_learning_drains_before_maintenance_whatever_the_record_order():
    """Promotions read this run's freshly crystallized skills, so learning is
    first — the order the in-process drains have always run in."""
    _make_run()
    tail_jobs.record_maintenance("tj000001", loop_id="loop-1")
    tail_jobs.record_learning("tj000001", _FakeLoop())
    assert [j["kind"] for j in tail_jobs.pending_jobs("tj000001")] == [
        "learning", "maintenance"]


def test_step_results_survive_the_handoff_byte_exact():
    """The closure carried StepOutcome objects; the record has to carry them.

    This is the field the run dir does NOT hold: `build/loop-*-log.json`
    persists `result_length`, not `result`, and the per-step `.md` is a
    rendered file with a synthesized header. Step lessons and crystallization
    read `result`, so a lossy handoff would quietly teach from less text.
    """
    _make_run()
    payload = "line one\nline two\ttabbed\nünïcode ✓ \\ \" '\n"
    step = StepOutcome(index=1, text="step text", status="done",
                       result=payload, iteration=1, tokens_in=5,
                       elapsed_ms=12, confidence="strong")
    tail_jobs.record_learning("tj000001", _FakeLoop(steps=[step]))
    spec = tail_jobs.pending_jobs("tj000001")[0]["spec"]
    rebuilt = tail_jobs._steps_from_rows(spec["steps"])
    assert len(rebuilt) == 1
    assert rebuilt[0].result == payload
    assert rebuilt[0].text == "step text"
    assert rebuilt[0].confidence == "strong"
    assert rebuilt[0].tokens_in == 5


def test_schema_drifted_step_row_degrades_instead_of_vanishing():
    """An unknown field must not delete a step.

    A dropped step is invisible in the learning that follows, and invisible is
    the failure mode this project keeps paying for; a weaker step is not.
    """
    rows = [{"index": 2, "text": "t", "status": "done", "result": "r",
             "iteration": 1, "field_from_the_future": 99}]
    rebuilt = tail_jobs._steps_from_rows(rows)
    assert len(rebuilt) == 1
    assert rebuilt[0].result == "r"
    # And a row missing required fields still becomes a step, not a hole.
    assert len(tail_jobs._steps_from_rows([{"text": "only text"}])) == 1


# ---------------------------------------------------------------------------
# Draining
# ---------------------------------------------------------------------------

def test_run_jobs_executes_and_marks_done(monkeypatch):
    _make_run()
    seen = {}

    def _fake_learning(spec, adapter):
        seen["spec"] = spec

    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", _fake_learning)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000001", _FakeLoop(loop_id="L9"))

    assert tail_jobs.run_jobs("tj000001") == 1
    assert seen["spec"]["loop_id"] == "L9"
    # Marked done, so a second drain (a sweep, a retry, the fallback lane
    # after a spawn that already ran) does not repeat the work.
    assert tail_jobs.pending_jobs("tj000001") == []
    assert tail_jobs.run_jobs("tj000001") == 0


def test_a_failing_job_never_raises_and_is_recorded(monkeypatch):
    """The tail must not change the outcome of the run it belongs to — by the
    time it runs, the user already has the answer."""
    _make_run()

    def _boom(spec, adapter):
        raise RuntimeError("tail exploded")

    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", _boom)
    tail_jobs.record_learning("tj000001", _FakeLoop())

    assert tail_jobs.run_jobs("tj000001") == 0        # nothing ran to completion
    rows = tail_jobs.state("tj000001")["rows"]
    done = [r for r in rows if r.get("event") == "done"]
    assert done and done[0]["ok"] is False
    assert "tail exploded" in done[0]["error"]
    # ...and it is not left pending for a sweep to re-run its first half.
    assert tail_jobs.pending_jobs("tj000001") == []


def test_kinds_filter_leaves_the_rest_pending(monkeypatch):
    """The quality-gate escalation drains learning early — the retry's
    decompose needs the lessons of the loop it is retrying — and nothing
    else about the tail is owed yet."""
    _make_run()
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance", lambda s, a: None)
    tail_jobs.record_learning("tj000001", _FakeLoop())
    tail_jobs.record_maintenance("tj000001", loop_id="L1")

    assert tail_jobs.run_jobs("tj000001", kinds=("learning",),
                              refresh=False, respect_claim=False) == 1
    assert [j["kind"] for j in tail_jobs.pending_jobs("tj000001")] == [
        "maintenance"]


def test_refresh_false_skips_the_surface_pass(monkeypatch):
    _make_run()
    called = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces",
                        lambda hid, path=None: called.append(hid))
    tail_jobs.record_learning("tj000001", _FakeLoop())
    tail_jobs.run_jobs("tj000001", refresh=False)
    assert called == []


def test_live_claim_declines_a_second_drainer(monkeypatch):
    """One tail process per handle_id. Tails for DIFFERENT runs may overlap —
    they already do, because heartbeat runs skill maintenance on its own tick
    — but the same run's jobs must not run twice at once."""
    _make_run()
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    tail_jobs.record_learning("tj000001", _FakeLoop())
    path = tail_jobs.jobs_path("tj000001")
    tail_jobs._append(path, {"event": "claim", "pid": os.getpid(),
                             "host": tail_jobs._hostname(),
                             "claimed_at": "now"})

    assert tail_jobs.run_jobs("tj000001") == 0            # declined
    assert tail_jobs.pending_jobs("tj000001")             # still owed
    # respect_claim=False is the parent draining its own run before any child
    # exists (the escalation lane).
    assert tail_jobs.run_jobs("tj000001", respect_claim=False) == 1


def test_a_dead_claim_does_not_block(monkeypatch):
    _make_run()
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000001", _FakeLoop())
    path = tail_jobs.jobs_path("tj000001")
    monkeypatch.setattr(tail_jobs, "_is_pid_alive", lambda pid: False)
    tail_jobs._append(path, {"event": "claim", "pid": 999999,
                             "host": tail_jobs._hostname(),
                             "claimed_at": "then"})
    assert tail_jobs.run_jobs("tj000001") == 1


def test_a_claim_from_another_host_is_not_live():
    """A pid from another machine says nothing about this one."""
    assert tail_jobs._live_claim(
        {"pid": os.getpid(), "host": "some-other-box"}) is False
    assert tail_jobs._live_claim(
        {"pid": os.getpid(), "host": tail_jobs._hostname()}) is True


def test_unknown_job_kind_is_recorded_not_silently_skipped():
    _make_run()
    path = tail_jobs.jobs_path("tj000001")
    tail_jobs._append(path, {"event": "job", "seq": 1, "kind": "from-the-future",
                             "spec": {}})
    assert tail_jobs.run_jobs("tj000001") == 0
    done = [r for r in tail_jobs.state("tj000001")["rows"]
            if r.get("event") == "done"]
    assert done and "unknown kind" in done[0]["error"]


# ---------------------------------------------------------------------------
# Dispatch: spawn or inline
# ---------------------------------------------------------------------------

def test_dispatch_is_inline_when_spawn_is_off(monkeypatch):
    _make_run()
    monkeypatch.setattr(tail_jobs, "spawn_enabled", lambda: False)
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000001", _FakeLoop())
    out = tail_jobs.drain_or_spawn("tj000001")
    assert out == {"mode": "inline", "pid": None, "ran": 1}


def test_a_spawn_that_cannot_be_made_falls_back_inline(monkeypatch):
    """Never worse than the behaviour it replaced: if the child cannot start,
    the same jobs run here, which is exactly phase 1."""
    _make_run()
    monkeypatch.setattr(tail_jobs, "spawn_enabled", lambda: True)
    monkeypatch.setattr(tail_jobs, "spawn", lambda hid: None)
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000001", _FakeLoop())
    out = tail_jobs.drain_or_spawn("tj000001")
    assert out["mode"] == "inline" and out["ran"] == 1


def test_dispatch_with_nothing_pending_does_nothing(monkeypatch):
    _make_run()
    monkeypatch.setattr(tail_jobs, "spawn_enabled",
                        lambda: pytest.fail("must not consult config"))
    assert tail_jobs.drain_or_spawn("tj000001")["mode"] == "empty"


def test_spawn_enabled_reads_the_config_key(monkeypatch):
    import config
    monkeypatch.delenv("MARO_TAIL_SPAWN", raising=False)
    # A fresh install has no `tail.spawn` key at all and inherits ON — the
    # 2026-08-21 flip decree, made on burn-in evidence (three clean runs +
    # a deliberate crash with full recovery semantics).
    assert tail_jobs.spawn_enabled() is True
    monkeypatch.setattr(config, "get",
                        lambda key, default=None: False if key == "tail.spawn"
                        else default)
    assert tail_jobs.spawn_enabled() is False


def test_the_env_override_beats_config(monkeypatch):
    """MARO_TAIL_SPAWN wins over config — the recording_enabled contract.

    This is also what keeps the unit suite honest: conftest pins it "0" so
    no test forks a real child by accident, and an operator can kill the
    lane the same way without touching config."""
    import config
    monkeypatch.setattr(config, "get",
                        lambda key, default=None: True if key == "tail.spawn"
                        else default)
    monkeypatch.setenv("MARO_TAIL_SPAWN", "0")
    assert tail_jobs.spawn_enabled() is False
    monkeypatch.setattr(config, "get",
                        lambda key, default=None: False if key == "tail.spawn"
                        else default)
    monkeypatch.setenv("MARO_TAIL_SPAWN", "1")
    assert tail_jobs.spawn_enabled() is True


# ---------------------------------------------------------------------------
# The claim the chunk exists for
# ---------------------------------------------------------------------------

def test_spawned_tail_outlives_the_parent_call(monkeypatch):
    """The dispatch returns while the tail is still owed, and the child
    finishes it after.

    This is the difference between phase 1 and phase 3 stated as an
    assertion: `drain_or_spawn` comes back with the job still pending (the
    parent is free to exit right here), and the pending job is gone later
    because a different process did it. A reordered-but-synchronous tail
    cannot pass this test — it would return with `ran=1` and nothing pending.
    """
    _make_run("tj000002")
    # A learning job with no ledger row and no steps: the real runner reaches
    # its early return in milliseconds, so this measures the process
    # mechanics rather than an LLM call. No API keys exist under the test
    # workspace, so the child's adapter build fails closed to None.
    tail_jobs.record_learning("tj000002", _FakeLoop(
        loop_id="tail-e2e-loop", status="stuck", project="", steps=[]))

    import config
    monkeypatch.delenv("MARO_TAIL_SPAWN", raising=False)
    _real_get = config.get
    monkeypatch.setattr(config, "get",
                        lambda key, default=None: (True if key == "tail.spawn"
                                                   else _real_get(key, default)))
    out = tail_jobs.drain_or_spawn("tj000002")

    assert out["mode"] == "spawned", out
    assert out["pid"] and out["pid"] > 0
    # The parent returned with the work still owed — this is the whole point.
    assert tail_jobs.pending_jobs("tj000002"), "tail was drained synchronously"

    deadline = time.time() + 90
    while time.time() < deadline:
        if not tail_jobs.pending_jobs("tj000002"):
            break
        time.sleep(0.25)
    else:
        log = tail_jobs.jobs_path("tj000002").parent / tail_jobs.TAIL_LOG_FILENAME
        pytest.fail("spawned tail never finished; log:\n"
                    + (log.read_text(encoding="utf-8", errors="replace")
                       if log.exists() else "(no log)"))

    done = [r for r in tail_jobs.state("tj000002")["rows"]
            if r.get("event") == "done"]
    assert done and done[0]["ok"] is True, done
    # It really was another process.
    claims = [r for r in tail_jobs.state("tj000002")["rows"]
              if r.get("event") == "claim"]
    assert claims and claims[0]["pid"] != os.getpid()


def test_spawn_does_not_hand_the_child_the_parents_stdout():
    """An inherited pipe keeps `out=$(maro handle ...)` blocked until the LAST
    writer closes it — which would make the caller wait for the whole tail
    while believing it was asynchronous. The child writes to build/tail.log.
    """
    _make_run("tj000003")
    tail_jobs.record_learning("tj000003", _FakeLoop(
        loop_id="tail-log-loop", status="stuck", project="", steps=[]))
    pid = tail_jobs.spawn("tj000003")
    assert pid
    log_path = (tail_jobs.jobs_path("tj000003").parent
                / tail_jobs.TAIL_LOG_FILENAME)
    deadline = time.time() + 90
    while time.time() < deadline and tail_jobs.pending_jobs("tj000003"):
        time.sleep(0.25)
    assert log_path.exists(), "the child's output did not land in build/tail.log"


# ---------------------------------------------------------------------------
# Stranding
# ---------------------------------------------------------------------------

def test_stranded_tail_is_discoverable_and_drainable(monkeypatch):
    """The phase-1 watch-item was a stranded closure with no trace. A record
    is durable, so a tail whose process died is findable — and every job kind
    is idempotent, so draining it later is safe."""
    _make_run("tj000004")
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000004", _FakeLoop())
    path = tail_jobs.jobs_path("tj000004")
    # A claim from a process that no longer exists — the crash signature.
    monkeypatch.setattr(tail_jobs, "_is_pid_alive", lambda pid: False)
    tail_jobs._append(path, {"event": "claim", "pid": 999999,
                             "host": tail_jobs._hostname(),
                             "claimed_at": "then"})
    # Age it past the sweep's grace window.
    old = time.time() - 3600
    os.utime(path, (old, old))

    found = tail_jobs.find_stranded(min_age_s=900)
    assert [f["handle_id"] for f in found] == ["tj000004"]
    assert found[0]["pending"] == ["learning"]

    result = tail_jobs.sweep_stranded(min_age_s=900)
    assert result["drained"] == 1
    assert tail_jobs.pending_jobs("tj000004") == []


def test_a_young_tail_is_not_called_stranded(monkeypatch):
    """A job recorded seconds ago belongs to a run whose child may not have
    started yet — calling that stranded would make the sweep race the spawn."""
    _make_run("tj000005")
    tail_jobs.record_learning("tj000005", _FakeLoop())
    assert tail_jobs.find_stranded(min_age_s=900) == []
    assert tail_jobs.sweep_stranded(min_age_s=900)["drained"] == 0


def test_sweep_dry_run_reports_without_draining():
    _make_run("tj000006")
    tail_jobs.record_learning("tj000006", _FakeLoop())
    path = tail_jobs.jobs_path("tj000006")
    old = time.time() - 3600
    os.utime(path, (old, old))
    result = tail_jobs.sweep_stranded(min_age_s=900, dry_run=True)
    assert len(result["stranded"]) == 1
    assert result["drained"] == 0
    assert tail_jobs.pending_jobs("tj000006")


# ---------------------------------------------------------------------------
# Store hygiene
# ---------------------------------------------------------------------------

def test_a_torn_row_costs_one_record_not_the_store():
    """Append-only plus an announced read: the ten-round destructive-rewrite
    arc is a record of what a read->transform->rewrite does to a store two
    processes share. Nothing here rewrites a line."""
    _make_run("tj000007")
    tail_jobs.record_learning("tj000007", _FakeLoop(loop_id="good-1"))
    path = tail_jobs.jobs_path("tj000007")
    with open(path, "ab") as fh:
        fh.write(b'{"event": "job", "seq": 2, "kind": "learn\xff\xfe"}\n')
    tail_jobs.record_maintenance("tj000007", loop_id="good-2")

    kinds = [j["kind"] for j in tail_jobs.pending_jobs("tj000007")]
    assert kinds == ["learning", "maintenance"]


def test_adapter_identity_falls_back_rather_than_running_no_tail(monkeypatch):
    """A tail on a neighbouring backend is worth more than no tail."""
    import llm
    attempts = []

    def _fake_build(**kwargs):
        attempts.append(kwargs)
        if kwargs:
            raise RuntimeError("no such backend here")
        return "default-adapter"

    monkeypatch.setattr(llm, "build_adapter", _fake_build)
    got = tail_jobs._build_adapter({"adapter": {"backend": "groq",
                                                "model_key": "cheap"}})
    assert got == "default-adapter"
    assert attempts[0] == {"backend": "groq", "model": "cheap"}
    assert attempts[-1] == {}


def test_a_base_backend_identity_is_not_replayed(monkeypatch):
    """`base` and `failover` name no buildable backend — skip straight to the
    model, then to the default, instead of a call that cannot succeed."""
    import llm
    attempts = []
    monkeypatch.setattr(llm, "build_adapter",
                        lambda **kw: attempts.append(kw) or "a")
    tail_jobs._build_adapter({"adapter": {"backend": "failover",
                                          "model_key": "mid"}})
    assert attempts == [{"model": "mid"}]


def test_a_released_claim_does_not_block_a_later_job(monkeypatch):
    """A claim is released when its drain finishes, so the NEXT job recorded
    on the same run is not declined by the ghost of the last one.

    `state()` keeps the most recent claim row, and after a completed drain
    that row is the release. Without the `released_at` check the run would be
    permanently claimed by a process that is often still alive — the parent.
    """
    _make_run("tj000008")
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000008", _FakeLoop())
    assert tail_jobs.run_jobs("tj000008") == 1
    # Same process is still alive; only the release makes the claim inert.
    tail_jobs.record_maintenance("tj000008", loop_id="L2")
    assert tail_jobs.run_jobs("tj000008") == 1


def test_a_permission_error_means_the_pid_exists(monkeypatch):
    """EPERM from `os.kill(pid, 0)` is proof the process is there and owned by
    someone else — reading it as "dead" would let a second drainer in."""
    import os as _os

    def _kill(pid, sig):
        raise PermissionError("not yours")

    monkeypatch.setattr(_os, "kill", _kill)
    assert tail_jobs._is_pid_alive(4242) is True

    def _gone(pid, sig):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(_os, "kill", _gone)
    assert tail_jobs._is_pid_alive(4242) is False


def test_refresh_follows_attempts_not_successes(monkeypatch):
    """A failed job is not "nothing happened".

    A learning job can make paid LLM calls, write lessons, and THEN raise —
    the round-1 version of this test pinned the opposite premise (skip the
    refresh when every job failed) and round 2 called it out: the run-dir pin
    exists precisely because those calls are captured, so the surfaces must
    be re-derived after any ATTEMPT. Only a drain where no runner was ever
    invoked leaves the close_run totals standing.
    """
    _make_run("tj000009")
    called = []

    def _boom(spec, adapter):
        raise RuntimeError("no")

    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", _boom)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces",
                        lambda hid, path=None: called.append(hid))
    tail_jobs.record_learning("tj000009", _FakeLoop())
    assert tail_jobs.run_jobs("tj000009") == 0     # nothing SUCCEEDED
    assert called == ["tj000009"]                  # but something ran

    # And the true nothing-happened case: a job whose runner is never
    # invoked (unknown kind) must not trigger a refresh.
    _make_run("tj000027")
    path = tail_jobs.jobs_path("tj000027")
    tail_jobs._append(path, {"event": "job", "seq": 1,
                             "kind": "from-the-future", "spec": {}})
    called.clear()
    tail_jobs.run_jobs("tj000027")
    assert called == []


def test_spawn_detaches_the_child_and_redirects_its_streams(monkeypatch):
    """The three properties that make this a real detachment.

    Asserted on the arguments of a REAL spawn (the wrapper calls through), not
    on a stand-in: a new session, /dev/null on stdin, and the child's output
    pointed at a file handle rather than the parent's stdout. The last one is
    the subtle one — an inherited pipe keeps `out=$(maro handle ...)` blocked
    until the last writer closes it, so the caller would wait for the whole
    tail while believing it had been handed an answer.
    """
    import subprocess as _sp
    seen = {}
    _real_popen = _sp.Popen

    def _spy(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return _real_popen(cmd, **kwargs)

    monkeypatch.setattr(tail_jobs.subprocess, "Popen", _spy)
    _make_run("tj000010")
    tail_jobs.record_learning("tj000010", _FakeLoop(
        loop_id="detach-loop", status="stuck", project="", steps=[]))
    assert tail_jobs.spawn("tj000010")

    assert seen["kwargs"]["start_new_session"] is True
    assert seen["kwargs"]["stdin"] == _sp.DEVNULL
    assert seen["kwargs"]["stdout"] is not None
    assert seen["kwargs"]["stdout"] not in (None, _sp.PIPE)
    assert seen["kwargs"]["stderr"] == _sp.STDOUT
    assert "finalize-tail" in seen["cmd"] and "tj000010" in seen["cmd"]
    # The child must be able to import src/ regardless of how the parent ran.
    assert str(Path(tail_jobs.__file__).resolve().parent) in \
        seen["kwargs"]["env"]["PYTHONPATH"]


def test_a_tail_with_a_live_claim_is_not_reported_stranded():
    """Old is not the same as abandoned.

    A long tail — lesson extraction, ~10 promotion validations, the evolver —
    outlives the sweep's grace window as a matter of course. Age alone would
    report a working child as stranded; the claim is what distinguishes them.
    `run_jobs` would still decline the drain, so the damage is a false report
    rather than a double run, and a false report is what makes an operator
    stop believing the sweep.
    """
    _make_run("tj000011")
    tail_jobs.record_learning("tj000011", _FakeLoop())
    path = tail_jobs.jobs_path("tj000011")
    tail_jobs._append(path, {"event": "claim", "pid": os.getpid(),
                             "host": tail_jobs._hostname(),
                             "claimed_at": "now"})
    old = time.time() - 3600
    os.utime(path, (old, old))

    assert tail_jobs.find_stranded(min_age_s=900) == []
    assert tail_jobs.sweep_stranded(min_age_s=900)["stranded"] == []


# ---------------------------------------------------------------------------
# Adversarial round 1 — the fix layer
# ---------------------------------------------------------------------------

def _lock_held_for(path):
    """Is this thread inside the store's lock right now?"""
    import file_lock
    key = str((path.parent / (path.name + ".lock")).resolve())
    return key in file_lock._get_held()


def test_sequence_is_allocated_inside_the_store_lock():
    """Append-only is not atomic.

    Two registrars that read the same rows both allocated `seq: 1` — both
    lines physically on disk, one job invisible, because the executor is keyed
    by seq. Byte-level safety says nothing about a decision made from a read
    that a later write depends on, so the read, the decision and the append
    are one transaction. This asserts the property that makes the race
    impossible rather than trying to lose a race on purpose.
    """
    _make_run("tj000012")
    path = tail_jobs.jobs_path("tj000012")
    seen = []
    real_next = tail_jobs._next_seq

    def _watched(rows):
        seen.append(_lock_held_for(path))
        return real_next(rows)

    tail_jobs._next_seq = _watched
    try:
        tail_jobs.record_learning("tj000012", _FakeLoop())
        tail_jobs.record_maintenance("tj000012", loop_id="L1")
    finally:
        tail_jobs._next_seq = real_next
    assert seen == [True, True], "sequence allocated outside the store lock"


def test_claim_is_decided_inside_the_store_lock():
    """The other half of the same defect: check-then-append let two drainers
    both read "unclaimed" and both run the same jobs."""
    _make_run("tj000013")
    path = tail_jobs.jobs_path("tj000013")
    tail_jobs.record_learning("tj000013", _FakeLoop())
    seen = []
    real_live = tail_jobs._live_claim

    def _watched(claim):
        seen.append(_lock_held_for(path))
        return real_live(claim)

    tail_jobs._live_claim = _watched
    try:
        tail_jobs.run_jobs("tj000013")
    finally:
        tail_jobs._live_claim = real_live
    assert seen and all(seen), "claim decided outside the store lock"


def test_two_jobs_recorded_from_two_threads_both_survive():
    """The end the transaction exists for, exercised through real contention."""
    import threading
    _make_run("tj000014")
    barrier = threading.Barrier(2)

    def _rec(fn):
        barrier.wait(timeout=10)
        fn()

    threads = [
        threading.Thread(target=_rec, args=(
            lambda: tail_jobs.record_learning("tj000014", _FakeLoop()),)),
        threading.Thread(target=_rec, args=(
            lambda: tail_jobs.record_maintenance("tj000014", loop_id="L1"),)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    kinds = sorted(j["kind"] for j in tail_jobs.pending_jobs("tj000014"))
    assert kinds == ["learning", "maintenance"], kinds


def test_the_inline_lane_uses_the_runs_live_adapter(monkeypatch):
    """Phase 1's closures captured the adapter OBJECT, and `_handle_impl`
    builds its own when the caller passes none — so it is not recoverable from
    `handle()`'s scope. Rebuilding from the recorded identity drops a
    failover adapter's live state and any injected adapter, in the lane that
    ships ON by default. The object stays reachable where it still exists.
    """
    _make_run("tj000015")
    live = _FakeAdapter()
    got = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning",
                        lambda spec, adapter: got.append(adapter))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    monkeypatch.setattr(tail_jobs, "_build_adapter",
                        lambda spec: pytest.fail("rebuilt instead of reusing"))
    tail_jobs.record_learning("tj000015", _FakeLoop(), adapter=live)
    assert tail_jobs.run_jobs("tj000015") == 1
    assert got == [live], "the inline lane did not use the live adapter"


def test_the_live_adapter_is_released_after_the_drain(monkeypatch):
    """It is a fidelity cache, not a registry — holding the adapter of every
    run this process ever finalized would be a leak."""
    _make_run("tj000016")
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000016", _FakeLoop(), adapter=_FakeAdapter())
    assert ("tj000016", 1) in tail_jobs._LIVE_ADAPTERS
    tail_jobs.run_jobs("tj000016")
    assert ("tj000016", 1) not in tail_jobs._LIVE_ADAPTERS


def test_a_claim_that_cannot_be_published_declines_the_drain(monkeypatch):
    """A claim we could not write is a claim we do not hold. Running anyway is
    the unsafe direction for a store whose whole job is telling a second
    drainer to stand down."""
    _make_run("tj000017")
    tail_jobs.record_learning("tj000017", _FakeLoop())
    ran = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning",
                        lambda s, a: ran.append(1))
    monkeypatch.setattr(tail_jobs, "_append", lambda path, row: False)
    assert tail_jobs.run_jobs("tj000017") == 0
    assert ran == [], "drained without holding a claim"


def test_a_completion_that_cannot_be_recorded_is_loud(monkeypatch, caplog):
    """The side effect happened and the store does not know — the next sweep
    will see the job pending. That is the one case where per-kind idempotence
    is doing real work, and the operator should know it was leaned on."""
    import logging
    _make_run("tj000018")
    tail_jobs.record_learning("tj000018", _FakeLoop())
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    real_append = tail_jobs._append

    def _fail_done(path, row):
        if row.get("event") == "done":
            return False
        return real_append(path, row)

    monkeypatch.setattr(tail_jobs, "_append", _fail_done)
    with caplog.at_level(logging.ERROR, logger="maro.tail_jobs"):
        tail_jobs.run_jobs("tj000018")
    assert any("could not be recorded" in r.message or
               "could not be recorded" in r.getMessage()
               for r in caplog.records), caplog.text


def test_the_job_runs_with_its_run_pinned(monkeypatch):
    """`runs.current_run_dir()` is a ContextVar — process-local — so a spawned
    child inherits nothing. `runs.record_llm_call` NO-OPS with no run-dir
    active and record-mode is ON by default, so the spawned lane would have
    stopped capturing the tail's LLM calls into `build/calls/` and the run
    card's `n_calls` would under-report calls the run paid for.
    """
    import runs
    rd = _make_run("tj000019")
    seen = {}
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning",
                        lambda s, a: seen.setdefault("pinned",
                                                     runs.current_run_dir()))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000019", _FakeLoop())
    before = runs.current_run_dir()
    tail_jobs.run_jobs("tj000019")
    assert seen["pinned"] == rd
    # ...and restored, because the heartbeat sweep drains ANOTHER run's tail
    # inside its own process.
    assert runs.current_run_dir() == before


def test_maintenance_whose_runner_started_is_surfaced_not_repeated(monkeypatch):
    """`run_post_run_maintenance` advances DURABLE cadence counters, so
    threshold-based is not idempotent: a child that died after a tick and
    before its done row would have that tick counted twice. The evidence is
    the job's OWN `started` row — round 2 showed the store-global claim
    heuristic was laundered by the first recovery sweep's own release."""
    _make_run("tj000020")
    ran = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: ran.append(1))
    tail_jobs.record_maintenance("tj000020", loop_id="L1")
    path = tail_jobs.jobs_path("tj000020")
    # The crash signature: the runner was invoked (started row) and never
    # finished (no done row); the claim's pid is dead.
    monkeypatch.setattr(tail_jobs, "_is_pid_alive", lambda pid: False)
    tail_jobs._append(path, {"event": "claim", "pid": 999999,
                             "host": tail_jobs._hostname(),
                             "claimed_at": "then"})
    tail_jobs._append(path, {"event": "started", "seq": 1, "pid": 999999,
                             "ts": "then"})
    old = time.time() - 3600
    os.utime(path, (old, old))

    found = tail_jobs.find_stranded(min_age_s=900)
    assert found[0]["needs_operator"] == ["maintenance"]
    assert found[0]["drainable"] == []
    result = tail_jobs.sweep_stranded(min_age_s=900)
    assert ran == [], "a partially-run maintenance job was repeated"
    assert result["needs_operator"] == ["tj000020"]


def test_untouched_maintenance_behind_a_crashed_claim_is_drainable(monkeypatch):
    """The other direction round 2 caught: a child that claimed the store,
    finished learning, and died BEFORE invoking maintenance leaves an
    unreleased claim — but maintenance's own runner was never reached, and
    the store proves it (no started row for seq 2). The store-global
    heuristic stranded that job forever; per-job evidence drains it."""
    _make_run("tj000028")
    ran = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: ran.append("maintenance"))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    path = tail_jobs.jobs_path("tj000028")
    monkeypatch.setattr(tail_jobs, "_is_pid_alive", lambda pid: False)
    for row in [
        {"event": "job", "seq": 1, "kind": "learning", "spec": {}},
        {"event": "job", "seq": 2, "kind": "maintenance", "spec": {}},
        {"event": "claim", "pid": 999999, "host": tail_jobs._hostname(),
         "claimed_at": "then"},
        {"event": "started", "seq": 1, "pid": 999999, "ts": "then"},
        {"event": "done", "seq": 1, "ok": True},
        # ...and the child died here: seq 2 never started.
    ]:
        tail_jobs._append(path, row)
    old = time.time() - 3600
    os.utime(path, (old, old))

    found = tail_jobs.find_stranded(min_age_s=900)
    assert found[0]["drainable"] == ["maintenance"]
    assert found[0]["needs_operator"] == []
    assert tail_jobs.sweep_stranded(min_age_s=900)["drained"] == 1
    assert ran == ["maintenance"]


def test_a_partial_recovery_sweep_does_not_launder_the_crash_evidence(monkeypatch):
    """The round-2 top finding, pinned end to end.

    A child starts BOTH jobs and dies. The first sweep drains learning (safe)
    and leaves maintenance surfaced. That sweep appends its own claim and
    release — which, under the old store-global heuristic, became the newest
    claim and made the SECOND sweep read "no drain ever started" and re-run
    the maintenance job that had already ticked durable counters. Per-job
    started rows cannot be laundered by someone else's release.
    """
    _make_run("tj000029")
    ran = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning",
                        lambda s, a: ran.append("learning"))
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: ran.append("maintenance"))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    path = tail_jobs.jobs_path("tj000029")
    real_alive = tail_jobs._is_pid_alive
    monkeypatch.setattr(tail_jobs, "_is_pid_alive",
                        lambda pid: False if pid == 999999 else real_alive(pid))
    for row in [
        {"event": "job", "seq": 1, "kind": "learning", "spec": {}},
        {"event": "job", "seq": 2, "kind": "maintenance", "spec": {}},
        {"event": "claim", "pid": 999999, "host": tail_jobs._hostname(),
         "claimed_at": "then"},
        {"event": "started", "seq": 1, "pid": 999999, "ts": "then"},
        {"event": "started", "seq": 2, "pid": 999999, "ts": "then"},
        # child died: no done rows, no release.
    ]:
        tail_jobs._append(path, row)
    old = time.time() - 3600
    os.utime(path, (old, old))

    # Sweep 1: learning re-drains (idempotent), maintenance surfaced.
    r1 = tail_jobs.sweep_stranded(min_age_s=900)
    assert ran == ["learning"]
    assert r1["needs_operator"] == ["tj000029"]

    # Sweep 2 — after sweep 1's own claim+release. Maintenance must STILL be
    # surfaced, not re-classified as safe.
    os.utime(path, (old, old))
    r2 = tail_jobs.sweep_stranded(min_age_s=900)
    assert ran == ["learning"], "the second sweep re-ran touched maintenance"
    assert r2["needs_operator"] == ["tj000029"]


def test_maintenance_that_never_started_is_drained(monkeypatch):
    """The other direction: nothing has happened yet, so nothing repeats."""
    _make_run("tj000021")
    ran = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: ran.append(1))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_maintenance("tj000021", loop_id="L1")
    path = tail_jobs.jobs_path("tj000021")
    old = time.time() - 3600
    os.utime(path, (old, old))
    assert tail_jobs.find_stranded(min_age_s=900)[0]["drainable"] == ["maintenance"]
    assert tail_jobs.sweep_stranded(min_age_s=900)["drained"] == 1
    assert ran == [1]


def test_an_old_stranded_tail_is_found_behind_many_newer_ones(monkeypatch):
    """The scan walks until it has `limit` stranded runs, rather than
    truncating the candidate list first.

    Heartbeat calls this with limit=3, and the first cut looked at only
    `limit * 4` newest-first stores — twelve healthy recent runs were enough
    to hide an old abandoned tail from every tick, forever.
    """
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    old_run = _make_run("tjold001")
    tail_jobs.record_learning("tjold001", _FakeLoop())
    op = tail_jobs.jobs_path("tjold001")
    ancient = time.time() - 7200
    os.utime(op, (ancient, ancient))
    # 20 newer decoys, all completed (nothing pending) — the shape that used
    # to fill the whole window.
    for i in range(20):
        hid = f"tjnew{i:03d}"
        _make_run(hid)
        p = tail_jobs.jobs_path(hid)
        tail_jobs._append(p, {"event": "job", "seq": 1, "kind": "learning",
                              "spec": {}})
        tail_jobs._append(p, {"event": "done", "seq": 1, "ok": True})
        recent = time.time() - 3000
        os.utime(p, (recent, recent))

    found = tail_jobs.find_stranded(limit=3, min_age_s=900)
    assert [f["handle_id"] for f in found] == ["tjold001"], found


def test_a_quoted_false_still_means_off(monkeypatch):
    """`bool("false")` is True, and YAML hands back a string whenever the
    value was quoted. Post-flip the stakes invert but the contract holds:
    an explicit quoted "false" must turn the spawn OFF — an operator's
    opt-out must not be eaten by truthiness."""
    import config
    monkeypatch.delenv("MARO_TAIL_SPAWN", raising=False)
    for value in ("false", "False", "no", "off", "0", ""):
        monkeypatch.setattr(config, "get",
                            lambda key, default=None, _v=value: _v)
        assert tail_jobs.spawn_enabled() is False, value
    for value in ("true", "yes", "on", "1", True):
        monkeypatch.setattr(config, "get",
                            lambda key, default=None, _v=value: _v)
        assert tail_jobs.spawn_enabled() is True, value
    # A value nobody can read is a value nobody decided: take the default —
    # which since the flip is ON.
    monkeypatch.setattr(config, "get", lambda key, default=None: "banana")
    assert tail_jobs.spawn_enabled() is True


def test_an_unreadable_store_is_not_an_empty_one(monkeypatch):
    """Treating unreadable as empty would allocate seq 1 over whatever the
    store already holds, and would declare a run un-stranded from a read that
    failed."""
    _make_run("tj000022")
    monkeypatch.setattr(tail_jobs, "_read_rows", lambda path: None)
    # The caller keeps the work rather than believing it was handed over.
    assert tail_jobs.record_learning("tj000022", _FakeLoop()) is False
    assert tail_jobs.record_maintenance("tj000022") is False
    assert tail_jobs.state("tj000022")["unreadable"] is True
    assert tail_jobs.run_jobs("tj000022") == 0


def test_a_failed_job_is_visible_in_state_and_the_sweep(monkeypatch):
    """A job that RAISED is done, so it is not pending — and pending is all
    find_stranded reports. Without its own lane the failure is visible
    nowhere, while the comment claimed the sweep reported it."""
    _make_run("tj000023")

    def _boom(spec, adapter):
        raise RuntimeError("the tail broke here")

    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", _boom)
    tail_jobs.record_learning("tj000023", _FakeLoop())
    tail_jobs.run_jobs("tj000023")

    st = tail_jobs.state("tj000023")
    assert st["pending"] == []
    assert len(st["failed"]) == 1
    assert "the tail broke here" in st["failed"][0]["error"]

    path = tail_jobs.jobs_path("tj000023")
    old = time.time() - 3600
    os.utime(path, (old, old))
    result = tail_jobs.sweep_stranded(min_age_s=900)
    assert result["failed_jobs"] == ["tj000023"]
    assert result["drained"] == 0


def test_a_failed_surface_refresh_is_recorded(monkeypatch):
    """Every job is already marked done by the time refresh runs, so a silent
    failure leaves a store confidently saying the tail finished beside a card
    and a report that are stale — a record that lies."""
    _make_run("tj000024")

    def _boom(handle_id):
        raise RuntimeError("render failed")

    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    import runs
    monkeypatch.setattr(runs, "slice_log_for_run", _boom)
    tail_jobs.record_learning("tj000024", _FakeLoop())
    tail_jobs.run_jobs("tj000024")
    rows = tail_jobs.state("tj000024")["rows"]
    refresh_rows = [r for r in rows if r.get("event") == "refresh"]
    assert refresh_rows and refresh_rows[0]["ok"] is False
    assert "render failed" in refresh_rows[0]["error"]


def test_the_sweep_drains_only_the_safe_kinds_of_a_mixed_run(monkeypatch):
    """A stranded run usually holds BOTH kinds, and only one is safe to
    repeat. Draining the run wholesale would re-tick the maintenance cadence
    counters the per-kind rule exists to protect — the single-kind fixtures
    could not tell a whole-run drain from a filtered one.
    """
    _make_run("tj000025")
    ran = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning",
                        lambda s, a: ran.append("learning"))
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: ran.append("maintenance"))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000025", _FakeLoop())
    tail_jobs.record_maintenance("tj000025", loop_id="L1")
    path = tail_jobs.jobs_path("tj000025")
    monkeypatch.setattr(tail_jobs, "_is_pid_alive", lambda pid: False)
    tail_jobs._append(path, {"event": "claim", "pid": 999999,
                             "host": tail_jobs._hostname(),
                             "claimed_at": "then"})
    # Both runners were invoked before the crash — per-job evidence.
    tail_jobs._append(path, {"event": "started", "seq": 1, "pid": 999999,
                             "ts": "then"})
    tail_jobs._append(path, {"event": "started", "seq": 2, "pid": 999999,
                             "ts": "then"})
    old = time.time() - 3600
    os.utime(path, (old, old))

    found = tail_jobs.find_stranded(min_age_s=900)
    assert found[0]["drainable"] == ["learning"]
    assert found[0]["needs_operator"] == ["maintenance"]

    tail_jobs.sweep_stranded(min_age_s=900)
    assert ran == ["learning"], ran
    # The maintenance job is still owed, and still visible as owed.
    assert [j["kind"] for j in tail_jobs.pending_jobs("tj000025")] == ["maintenance"]


def test_an_unclassifiable_job_store_is_announced(monkeypatch, caplog):
    """A store the sweep cannot read is a run it cannot vouch for either way.

    Skipping it is right — guessing "nothing pending" from a failed read would
    drop a tail — but skipping it in SILENCE means the run is never recovered
    and nobody ever learns why. The skip and the announcement are one
    behaviour, and only the announcement is observable.
    """
    import logging
    _make_run("tj000026")
    tail_jobs.record_learning("tj000026", _FakeLoop())
    path = tail_jobs.jobs_path("tj000026")
    old = time.time() - 3600
    os.utime(path, (old, old))
    monkeypatch.setattr(tail_jobs, "_read_rows",
                        lambda p: None if p == path else [])
    with caplog.at_level(logging.WARNING, logger="maro.tail_jobs"):
        found = tail_jobs.find_stranded(min_age_s=900)
    assert found == []
    assert any("unreadable" in r.getMessage() and "tj000026" in r.getMessage()
               for r in caplog.records), caplog.text


# ---------------------------------------------------------------------------
# Adversarial round 2 — the fix layer's fix layer
# ---------------------------------------------------------------------------

def test_a_transaction_that_cannot_lock_declines(monkeypatch):
    """`locked_write`'s environment fallback yields UNLOCKED by contract —
    fine for its other callers, fatal for a read-decide-append transaction,
    which unlocked is just the round-1 race again. `require=True` turns that
    fallback into a refusal, and the transaction declines."""
    import file_lock
    from contextlib import contextmanager

    @contextmanager
    def _cannot_lock(path, timeout_s=None, require=False):
        if require:
            raise OSError("lock file uncreatable")
        yield   # the legacy fallback: proceed unlocked

    monkeypatch.setattr(file_lock, "locked_write", _cannot_lock)
    _make_run("tj000030")
    # The caller keeps the work — never a silent unlocked write.
    assert tail_jobs.record_learning("tj000030", _FakeLoop()) is False


def test_a_malformed_spec_is_retired_not_crash_looped():
    """Round 2's Expert QA HIGH: a valid JSONL row whose `spec` is a string
    raised out of `run_jobs` BEFORE the old try block — claim never released,
    spawned child dying on the same row forever. Contained now: the job is
    recorded as failed and retired, the claim is released, and the store
    stays drainable."""
    _make_run("tj000031")
    path = tail_jobs.jobs_path("tj000031")
    tail_jobs._append(path, {"event": "job", "seq": 1, "kind": "learning",
                             "spec": "not-a-dict"})
    assert tail_jobs.run_jobs("tj000031") == 0        # nothing ran...
    st = tail_jobs.state("tj000031")
    assert st["pending"] == []                        # ...and nothing loops
    assert st["failed"] and "malformed spec" in st["failed"][0]["error"]
    claims = [r for r in st["rows"] if r.get("event") == "claim"]
    assert claims[-1].get("released_at"), "claim left standing"


def test_the_claim_is_released_even_when_job_handling_raises(monkeypatch):
    """The release moved into `finally` — a drain that dies mid-loop must not
    leave the store claimed by a live parent forever."""
    _make_run("tj000032")
    tail_jobs.record_learning("tj000032", _FakeLoop())

    def _explode(job, path, handle_id, adapter, scope):
        raise RuntimeError("handling itself broke")

    monkeypatch.setattr(tail_jobs, "_run_one", _explode)
    try:
        tail_jobs.run_jobs("tj000032")
    except RuntimeError:
        pass   # run_jobs may propagate; the claim must still be released
    claims = [r for r in tail_jobs.state("tj000032")["rows"]
              if r.get("event") == "claim"]
    assert claims and claims[-1].get("released_at"), "claim not released"


def test_an_unprovable_start_declines_maintenance_but_not_learning(monkeypatch):
    """If the started marker cannot be recorded, running a non-idempotent job
    means a future sweep can never know it was touched — so maintenance
    declines and stays pending (safe: it did NOT run), while learning, safe
    to repeat by design, proceeds."""
    _make_run("tj000033")
    ran = []
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning",
                        lambda s, a: ran.append("learning"))
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: ran.append("maintenance"))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000033", _FakeLoop())
    tail_jobs.record_maintenance("tj000033", loop_id="L1")
    real_append = tail_jobs._append

    def _no_started(path, row):
        if row.get("event") == "started":
            return False
        return real_append(path, row)

    monkeypatch.setattr(tail_jobs, "_append", _no_started)
    tail_jobs.run_jobs("tj000033")
    assert ran == ["learning"]
    assert [j["kind"] for j in tail_jobs.pending_jobs("tj000033")] == [
        "maintenance"]


def test_each_job_gets_its_own_recorded_adapter(monkeypatch):
    """The cache is keyed by (handle_id, seq): one key per handle meant every
    registration overwrote the last, so maintenance recorded with the run's
    adapter and learning recorded post-escalation with the ESCALATED one
    handed maintenance the wrong adapter."""
    _make_run("tj000034")
    a_run, a_esc = _FakeAdapter(), _FakeAdapter()
    got = {}
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: got.__setitem__("maintenance", a))
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning",
                        lambda s, a: got.__setitem__("learning", a))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    monkeypatch.setattr(tail_jobs, "_build_adapter",
                        lambda spec: pytest.fail("rebuilt instead of reusing"))
    tail_jobs.record_maintenance("tj000034", loop_id="L1", adapter=a_run)
    tail_jobs.record_learning("tj000034", _FakeLoop(), adapter=a_esc)
    tail_jobs.run_jobs("tj000034")
    assert got["learning"] is a_esc
    assert got["maintenance"] is a_run


def test_the_escalation_early_drain_keeps_maintenances_adapter(monkeypatch):
    """The literal escalation shape: learning drains early (kinds filter),
    maintenance later. Round 2 (3 seats): the whole-handle forget on the
    early drain threw away maintenance's adapter before it ran."""
    _make_run("tj000035")
    live = _FakeAdapter()
    got = {}
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    monkeypatch.setitem(tail_jobs._RUNNERS, "maintenance",
                        lambda s, a: got.__setitem__("maintenance", a))
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid, path=None: True)
    tail_jobs.record_learning("tj000035", _FakeLoop(), adapter=live)
    tail_jobs.record_maintenance("tj000035", loop_id="L1", adapter=live)
    # The early drain (escalation lane): learning only.
    tail_jobs.run_jobs("tj000035", kinds=(tail_jobs.KIND_LEARNING,),
                       refresh=False, respect_claim=False)
    # The final drain: maintenance must still see the live object.
    monkeypatch.setattr(tail_jobs, "_build_adapter",
                        lambda spec: pytest.fail("maintenance lost its adapter"))
    tail_jobs.run_jobs("tj000035")
    assert got["maintenance"] is live


def test_a_successful_spawn_releases_the_parents_cached_adapters(monkeypatch):
    """The child has its own module dict and can never consume the parent's
    objects — keeping them was one adapter leaked per handled run in every
    long-lived caller (drain loops, daemons)."""
    _make_run("tj000036")
    tail_jobs.record_learning("tj000036", _FakeLoop(), adapter=_FakeAdapter())
    assert any(k[0] == "tj000036" for k in tail_jobs._LIVE_ADAPTERS)
    monkeypatch.setattr(tail_jobs, "spawn_enabled", lambda: True)
    monkeypatch.setattr(tail_jobs, "spawn", lambda hid: 4242)
    out = tail_jobs.drain_or_spawn("tj000036")
    assert out["mode"] == "spawned"
    assert not any(k[0] == "tj000036" for k in tail_jobs._LIVE_ADAPTERS)


def test_a_string_typed_ok_is_surfaced_as_failure():
    """`"ok": "false"` is a STRING — `is False` read it as success, so a
    forged or schema-drifted done row hid a failure while still retiring the
    job. A done row this cannot read as success is surfaced, not trusted."""
    st = tail_jobs._state_from_rows(
        [{"event": "job", "seq": 1, "kind": "learning", "spec": {}},
         {"event": "done", "seq": 1, "ok": "false", "error": "boom"}], None)
    assert st["pending"] == []          # done retires it either way
    assert len(st["failed"]) == 1       # ...but the doubt is visible


def test_an_orphan_done_row_fabricates_nothing():
    """A done row with no matching job completes nothing that exists — it
    must not invent a failed job for the operator to chase."""
    st = tail_jobs._state_from_rows(
        [{"event": "done", "seq": 99, "ok": False, "error": "forged"}], None)
    assert st["failed"] == []
    assert st["pending"] == []


def test_nonfinite_and_arbitrary_numerics_take_the_default():
    """`bool(nan)` is True and YAML's `.nan`/`.inf` parse as floats — plain
    numeric truthiness turned malformed config into spawn-ON. Only the two
    numbers that MEAN a boolean are accepted."""
    for v in (float("nan"), float("inf"), float("-inf"), 2, -1, 0.5):
        assert tail_jobs._strict_bool(v, False) is False, v
        assert tail_jobs._strict_bool(v, True) is True, v
    assert tail_jobs._strict_bool(0, True) is False
    assert tail_jobs._strict_bool(1, False) is True
    assert tail_jobs._strict_bool(0.0, True) is False
    assert tail_jobs._strict_bool(1.0, False) is True


def test_a_failed_refresh_is_surfaced_by_state_and_the_sweep(monkeypatch):
    """Round 1 recorded the refresh failure; round 2 found no reader — a
    durable event nobody reads is not surfaced. It rides state, the sweep
    result, and (via the sweep) heartbeat now."""
    _make_run("tj000037")
    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", lambda s, a: None)
    import runs
    monkeypatch.setattr(runs, "slice_log_for_run",
                        lambda hid: (_ for _ in ()).throw(RuntimeError("render died")))
    tail_jobs.record_learning("tj000037", _FakeLoop())
    tail_jobs.run_jobs("tj000037")

    st = tail_jobs.state("tj000037")
    assert st["refresh_failed"] and "render died" in st["refresh_failed"][0]["error"]

    path = tail_jobs.jobs_path("tj000037")
    old = time.time() - 3600
    os.utime(path, (old, old))
    result = tail_jobs.sweep_stranded(min_age_s=900)
    assert result["refresh_failed"] == ["tj000037"]
