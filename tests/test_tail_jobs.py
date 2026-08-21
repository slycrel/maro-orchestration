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
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid: None)
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
                        lambda hid: called.append(hid))
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
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid: None)
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
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid: None)
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
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid: None)
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
    # A fresh install has no `tail.spawn` key at all, and inherits OFF: a
    # detached child moves where the tail's LLM spend and store writes happen.
    assert tail_jobs.spawn_enabled() is False
    monkeypatch.setattr(config, "get",
                        lambda key, default=None: True if key == "tail.spawn"
                        else default)
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
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid: None)
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
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces", lambda hid: None)
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


def test_refresh_is_skipped_when_nothing_ran(monkeypatch):
    """Every job failed, so close_run's totals still stand — re-deriving the
    surfaces would rewrite the card off work that did not happen."""
    _make_run("tj000009")
    called = []

    def _boom(spec, adapter):
        raise RuntimeError("no")

    monkeypatch.setitem(tail_jobs._RUNNERS, "learning", _boom)
    monkeypatch.setattr(tail_jobs, "_refresh_surfaces",
                        lambda hid: called.append(hid))
    tail_jobs.record_learning("tj000009", _FakeLoop())
    assert tail_jobs.run_jobs("tj000009") == 0
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
