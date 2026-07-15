"""Run-lifetime lease (run_lease.py) — the between-steps liveness fix.

Checkpoints only carry an in_flight pid mid-step, so a healthy loop BETWEEN
steps is invisible to pid heuristics; the lease (a flock held for the loop's
whole lifetime) is the evidence `maro resume` and the heartbeat sweep trust.

Cross-process tests use real subprocesses ON PURPOSE: flock locks attach to
open file descriptions, and same-process semantics are permissive enough
("may be denied", flock(2)) that an in-process "held" probe could falsely
pass against a broken implementation. Only another process proves the
contract.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    return tmp_path


def _child_env(workspace) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["MARO_WORKSPACE"] = str(workspace)
    return env


LEASE_HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[3])
from run_lease import acquire_run_lease
lease = acquire_run_lease(sys.argv[1], handle_id="xproc-holder", goal="held")
print("HELD" if lease is not None else "FAILED", flush=True)
time.sleep(float(sys.argv[2]))
lease.release()
"""


def _spawn_lease_holder(loop_id: str, seconds: float, workspace) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", LEASE_HOLDER, loop_id, str(seconds), SRC],
        env=_child_env(workspace),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == "HELD", proc.stderr.read()
    return proc


# ---------------------------------------------------------------------------
# Path convention + sanitization
# ---------------------------------------------------------------------------

def test_lease_path_lives_under_memory_leases(workspace):
    from run_lease import lease_path

    p = lease_path("abc12345")
    assert p == workspace / "memory" / "leases" / "run-abc12345.lease"


def test_lease_path_sanitizes_hostile_loop_id(workspace):
    from run_lease import lease_path

    leases_dir = (workspace / "memory" / "leases").resolve()
    p = lease_path("../../etc/passwd")
    # path separators are scrubbed, so the name is one component that
    # cannot escape the leases dir even when the id tries to traverse
    assert os.sep not in p.name
    assert p.parent == leases_dir
    assert p.resolve().parent == leases_dir
    # distinct hostile ids that scrub identically must not collide
    assert lease_path("a/b") != lease_path("a?b")


# ---------------------------------------------------------------------------
# Acquire / release
# ---------------------------------------------------------------------------

def test_acquire_writes_holder_info_and_release_clears(workspace):
    from run_lease import acquire_run_lease

    lease = acquire_run_lease("loop-a1", handle_id="h1", goal="the goal")
    assert lease is not None
    payload = json.loads(lease.path.read_text(encoding="utf-8"))
    assert payload["loop_id"] == "loop-a1"
    assert payload["handle_id"] == "h1"
    assert payload["pid"] == os.getpid()
    assert payload["started_at"]

    lease.release()
    # payload cleared but file NOT unlinked (probe semantics: present-unheld
    # = owner dead; unlink would race a concurrent open)
    assert lease.path.exists()
    assert lease.path.read_text(encoding="utf-8") == ""
    lease.release()  # idempotent

    again = acquire_run_lease("loop-a1")
    assert again is not None  # acquire-after-release succeeds
    again.release()


def test_acquire_empty_loop_id_returns_none(workspace):
    from run_lease import acquire_run_lease, probe_owner_alive

    assert acquire_run_lease("") is None
    assert probe_owner_alive("") is None


def test_del_backstop_releases(workspace):
    from run_lease import acquire_run_lease, probe_owner_alive

    lease = acquire_run_lease("loop-del")
    assert lease is not None
    del lease
    # last reference dropped → flock released → present-unheld
    assert probe_owner_alive("loop-del") is False


def test_acquire_unwritable_dir_degrades_ungated(workspace, monkeypatch):
    import run_lease as rl

    monkeypatch.setattr(
        rl, "lease_path",
        lambda loop_id: Path("/proc/no-such-dir/leases") / f"run-{loop_id}.lease",
    )
    assert rl.acquire_run_lease("loop-fs") is None  # warn + None, no raise


# ---------------------------------------------------------------------------
# Probe contract (None / False / True) — cross-process where it matters
# ---------------------------------------------------------------------------

def test_probe_missing_file_is_none(workspace):
    from run_lease import probe_owner_alive

    assert probe_owner_alive("never-acquired") is None


def test_probe_present_unheld_is_false(workspace):
    from run_lease import acquire_run_lease, probe_owner_alive

    lease = acquire_run_lease("loop-b2")
    assert lease is not None
    lease.release()
    assert probe_owner_alive("loop-b2") is False


def test_probe_true_while_live_subprocess_holds(workspace):
    from run_lease import probe_owner_alive

    holder = _spawn_lease_holder("loop-c3", 30, workspace)
    try:
        assert probe_owner_alive("loop-c3") is True
    finally:
        holder.kill()
        holder.communicate(timeout=10)
    # SIGKILL → kernel releases the flock, no cleanup ritual: owner dead
    assert probe_owner_alive("loop-c3") is False


def test_probe_from_subprocess_while_this_process_holds(workspace):
    """The critical direction: another process must see OUR lease as held.

    An implementation whose flock doesn't actually bite across processes
    (e.g. lockf on the wrong fd, or open-per-probe releasing the owner's
    lock) would fail exactly here.
    """
    from run_lease import acquire_run_lease

    lease = acquire_run_lease("loop-d4")
    assert lease is not None
    probe_code = (
        "import sys; sys.path.insert(0, sys.argv[2]);"
        "from run_lease import probe_owner_alive;"
        "print(probe_owner_alive(sys.argv[1]), flush=True)"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", probe_code, "loop-d4", SRC],
            env=_child_env(workspace), capture_output=True, text=True,
            timeout=15,
        )
        assert out.stdout.strip() == "True", out.stderr
    finally:
        lease.release()
    out = subprocess.run(
        [sys.executable, "-c", probe_code, "loop-d4", SRC],
        env=_child_env(workspace), capture_output=True, text=True, timeout=15,
    )
    assert out.stdout.strip() == "False", out.stderr


def test_acquire_while_subprocess_holds_returns_none(workspace):
    from run_lease import acquire_run_lease

    holder = _spawn_lease_holder("loop-e5", 30, workspace)
    try:
        assert acquire_run_lease("loop-e5") is None
    finally:
        holder.kill()
        holder.communicate(timeout=10)


# ---------------------------------------------------------------------------
# Heartbeat: _find_resumable_runs
# ---------------------------------------------------------------------------

def _make_checkpoint_run(tmp_path, monkeypatch, *, loop_id, handle_id,
                         in_flight_pid=None):
    """A run dir + checkpoint wired the way test_stranded_sweep does it."""
    import checkpoint as ckpt_module
    rd = tmp_path / "runs" / f"{handle_id}-red-fern"
    (rd / "build").mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps(
        {"handle_id": handle_id, "status": None}))
    ckpt = {
        "loop_id": loop_id, "goal": "g", "project": "",
        "handle_id": handle_id, "steps": ["s1", "s2", "s3"],
        "completed": [{"index": 1, "text": "s1", "status": "done",
                       "result": "", "tokens_in": 0, "tokens_out": 0,
                       "elapsed_ms": 0}],
        "timestamp": "2026-07-14T00:00:00",
    }
    if in_flight_pid is not None:
        ckpt["in_flight"] = {"index": 2,
                             "started_at": "2026-07-14T00:01:00",
                             "pid": in_flight_pid}
    (rd / "build" / "checkpoint.json").write_text(json.dumps(ckpt))
    monkeypatch.setattr(ckpt_module, "_runs_root", lambda: rd.parent)
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir",
                        lambda: tmp_path / "empty-legacy")
    import runs as runs_module
    monkeypatch.setattr(runs_module, "run_dir",
                        lambda h: rd.parent / f"{h}-red-fern")
    return rd


def test_find_resumable_skips_lease_held_between_steps(workspace, monkeypatch):
    """Healthy loop between steps: no in_flight pid at all, lease held →
    NOT resumable. Without the lease this checkpoint would be listed —
    exactly the hijack the backlog describes."""
    from heartbeat import _find_resumable_runs

    _make_checkpoint_run(workspace, monkeypatch,
                         loop_id="loopheld", handle_id="hheld")
    holder = _spawn_lease_holder("loopheld", 30, workspace)
    try:
        assert not any(r["loop_id"] == "loopheld"
                       for r in _find_resumable_runs())
    finally:
        holder.kill()
        holder.communicate(timeout=10)
    # owner killed → kernel released the lease → now genuinely resumable
    assert any(r["loop_id"] == "loopheld" for r in _find_resumable_runs())


def test_find_resumable_lease_dead_overrides_live_pid(workspace, monkeypatch):
    """Lease present-unheld beats a live-but-recycled in_flight pid: the
    run MUST still be listed resumable."""
    from run_lease import acquire_run_lease
    from heartbeat import _find_resumable_runs

    _make_checkpoint_run(workspace, monkeypatch,
                         loop_id="looprecycled", handle_id="hrec",
                         in_flight_pid=os.getpid())  # alive — but recycled
    lease = acquire_run_lease("looprecycled")
    lease.release()  # present-unheld = owner provably dead

    hits = [r for r in _find_resumable_runs() if r["loop_id"] == "looprecycled"]
    assert len(hits) == 1


def test_find_resumable_closure_window_owner_alive_not_listed(
        workspace, monkeypatch):
    """Lease released but the run's owner process is still alive (post-loop
    closure/quality-gate window, before close_run stamps a status) → NOT
    resumable. Lease-False means "loop over", not "process dead"; the run
    metadata pid is the corroborating evidence (adversarial review
    2026-07-15)."""
    from run_lease import acquire_run_lease
    from heartbeat import _find_resumable_runs

    rd = _make_checkpoint_run(workspace, monkeypatch,
                              loop_id="loopclosure", handle_id="hclo")
    (rd / "metadata.json").write_text(json.dumps(
        {"handle_id": "hclo", "status": None, "pid": os.getpid()}))
    lease = acquire_run_lease("loopclosure")
    lease.release()  # loop finalized; this process is "mid-closure"

    assert not any(r["loop_id"] == "loopclosure"
                   for r in _find_resumable_runs())


def test_find_resumable_prelease_checkpoint_uses_pid_heuristics(
        workspace, monkeypatch):
    """No lease file → behavior identical to today: a live in_flight pid
    skips the run, a dead one lists it."""
    from heartbeat import _find_resumable_runs

    _make_checkpoint_run(workspace, monkeypatch,
                         loop_id="looppre", handle_id="hpre",
                         in_flight_pid=os.getpid())
    assert not any(r["loop_id"] == "looppre" for r in _find_resumable_runs())

    _make_checkpoint_run(workspace / "b", monkeypatch,
                         loop_id="looppre2", handle_id="hpre2",
                         in_flight_pid=999999999)
    assert any(r["loop_id"] == "looppre2" for r in _find_resumable_runs())


# ---------------------------------------------------------------------------
# Heartbeat: _backfill_stranded_run_cards
# ---------------------------------------------------------------------------

def _make_run_card(tmp_path, monkeypatch, name, *, loop_id, pid,
                   started_ago_secs=3600):
    from datetime import datetime, timedelta, timezone
    import runs as runs_module
    rd = tmp_path / "runs" / name
    (rd / "build").mkdir(parents=True, exist_ok=True)
    started = (datetime.now(timezone.utc)
               - timedelta(seconds=started_ago_secs)).isoformat()
    (rd / "metadata.json").write_text(json.dumps(
        {"handle_id": name.split("-")[0], "status": None,
         "started_at": started, "ended_at": None, "pid": pid}))
    (rd / "build" / "checkpoint.json").write_text(json.dumps(
        {"loop_id": loop_id, "goal": "g", "project": "",
         "handle_id": name.split("-")[0], "steps": ["s1", "s2"],
         "completed": [], "timestamp": "2026-07-14T00:00:00"}))
    monkeypatch.setattr(runs_module, "runs_root", lambda: tmp_path / "runs")
    return rd


def test_backfill_spares_run_whose_lease_is_held(workspace, monkeypatch):
    """Dead recorded pid but lease held (e.g. the loop lives in a respawned
    wrapper, or the pid record is stale) → NOT stamped stranded."""
    from heartbeat import _backfill_stranded_run_cards

    rd = _make_run_card(workspace, monkeypatch, "hlease-a",
                        loop_id="loopbf1", pid=999999999)
    holder = _spawn_lease_holder("loopbf1", 30, workspace)
    try:
        assert _backfill_stranded_run_cards() == []
        assert json.loads((rd / "metadata.json").read_text())["status"] is None
    finally:
        holder.kill()
        holder.communicate(timeout=10)


def test_backfill_lease_released_live_pid_spared(workspace, monkeypatch):
    """Lease present-unheld + LIVE metadata pid → NOT stamped.

    A released lease means the loop is over, not that the process is dead:
    the owner spends minutes in closure judging / the quality gate after
    loop finalize before close_run stamps a status. The adversarial review
    (2026-07-15) reproduced the false 'stranded' stamp live when
    lease-False overrode a live pid — the pid must be corroborated."""
    from run_lease import acquire_run_lease
    from heartbeat import _backfill_stranded_run_cards

    rd = _make_run_card(workspace, monkeypatch, "hdeadl-a",
                        loop_id="loopbf2", pid=os.getpid())
    lease = acquire_run_lease("loopbf2")
    lease.release()

    assert _backfill_stranded_run_cards() == []
    assert json.loads((rd / "metadata.json").read_text())["status"] is None


def test_backfill_lease_released_dead_pid_stamped(workspace, monkeypatch):
    """Lease present-unheld + dead metadata pid → stamped without waiting
    for the no-pid 24h age tier."""
    from run_lease import acquire_run_lease
    from heartbeat import _backfill_stranded_run_cards

    rd = _make_run_card(workspace, monkeypatch, "hdeadl-b",
                        loop_id="loopbf2b", pid=999999999)
    lease = acquire_run_lease("loopbf2b")
    lease.release()

    assert "hdeadl" in _backfill_stranded_run_cards()
    assert json.loads((rd / "metadata.json").read_text())["status"] == "stranded"


def test_backfill_prelease_live_pid_still_spared(workspace, monkeypatch):
    """No lease file → today's pid tier unchanged: live pid spares the run."""
    from heartbeat import _backfill_stranded_run_cards

    rd = _make_run_card(workspace, monkeypatch, "hpree-a",
                        loop_id="loopbf3", pid=os.getpid())
    assert _backfill_stranded_run_cards() == []
    assert json.loads((rd / "metadata.json").read_text())["status"] is None


# ---------------------------------------------------------------------------
# maro resume (cli seam)
# ---------------------------------------------------------------------------

def _write_legacy_checkpoint(tmp_path, monkeypatch, *, loop_id,
                             in_flight_pid=None):
    import checkpoint as ckpt_module
    d = tmp_path / "legacy"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir", lambda: d)
    ckpt = {"loop_id": loop_id, "goal": "finish the thing", "project": "",
            "steps": ["s1", "s2"], "completed": [
                {"index": 1, "text": "s1", "status": "done", "result": "",
                 "tokens_in": 0, "tokens_out": 0, "elapsed_ms": 0}],
            "timestamp": "2026-07-14T00:00:00"}
    if in_flight_pid is not None:
        ckpt["in_flight"] = {"index": 2,
                             "started_at": "2026-07-14T00:01:00",
                             "pid": in_flight_pid}
    (d / f"ckpt_{loop_id}.json").write_text(json.dumps(ckpt))


def test_resume_refuses_while_lease_held(workspace, monkeypatch, capsys):
    """THE fix: a healthy original loop between steps (checkpoint carries
    NO in_flight pid) holds its lease — resume must refuse instead of
    hijacking the live run."""
    import cli
    import agent_loop

    _write_legacy_checkpoint(workspace, monkeypatch, loop_id="loopalive")
    monkeypatch.setattr(
        agent_loop, "run_agent_loop",
        lambda *a, **k: pytest.fail("resume must not hijack a live run"),
    )
    holder = _spawn_lease_holder("loopalive", 30, workspace)
    try:
        rc = cli.main(["resume", "loopalive"])
    finally:
        holder.kill()
        holder.communicate(timeout=10)

    assert rc != 0
    err = capsys.readouterr().err
    assert "E_RESUME" in err
    assert "run lease" in err


def test_resume_lease_dead_overrides_live_in_flight_pid(
        workspace, monkeypatch):
    """Lease present-unheld → owner provably dead: a recycled-but-alive
    in_flight pid must NOT block the resume (today's pid check alone
    would refuse this)."""
    import cli
    import agent_loop
    import llm
    import loop_finalize
    from run_lease import acquire_run_lease

    _write_legacy_checkpoint(workspace, monkeypatch, loop_id="loopgone",
                             in_flight_pid=os.getpid())  # alive!
    lease = acquire_run_lease("loopgone")
    lease.release()

    monkeypatch.setattr(llm, "build_adapter", lambda **kw: object())
    monkeypatch.setattr(loop_finalize, "finalize_deferred_learning",
                        lambda result, **kw: None)
    calls = {}

    def fake_loop(goal, **kw):
        calls["goal"] = goal
        calls.update(kw)
        return SimpleNamespace(loop_id="loopgone", status="done", project="")

    monkeypatch.setattr(agent_loop, "run_agent_loop", fake_loop)
    rc = cli.main(["resume", "loopgone"])
    assert rc == 0
    assert calls["resume_from_loop_id"] == "loopgone"


def test_resume_refuses_during_closure_window(workspace, monkeypatch, capsys):
    """Lease released but the run's owner process is still alive (post-loop
    closure window, metadata status not yet stamped) → resume must refuse.
    Lease-False only overrides the in_flight pid heuristic; the run
    metadata pid is corroborating evidence that the owner is mid-closure."""
    import cli
    import agent_loop
    from run_lease import acquire_run_lease

    rd = _make_checkpoint_run(workspace, monkeypatch,
                              loop_id="loopclo2", handle_id="hclo2")
    (rd / "metadata.json").write_text(json.dumps(
        {"handle_id": "hclo2", "status": None, "pid": os.getpid()}))
    monkeypatch.setattr(
        agent_loop, "run_agent_loop",
        lambda *a, **k: pytest.fail(
            "resume must not hijack a run whose owner is mid-closure"),
    )
    lease = acquire_run_lease("loopclo2")
    lease.release()

    rc = cli.main(["resume", "loopclo2"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "E_RESUME" in err
    assert "finishing closure" in err


def test_resume_prelease_checkpoint_pid_check_unchanged(
        workspace, monkeypatch, capsys):
    """No lease file → today's behavior: a live in_flight pid refuses."""
    import cli
    import agent_loop

    _write_legacy_checkpoint(workspace, monkeypatch, loop_id="looppidref",
                             in_flight_pid=os.getpid())
    monkeypatch.setattr(
        agent_loop, "run_agent_loop",
        lambda *a, **k: pytest.fail("live-pid resume must not enter the loop"),
    )
    rc = cli.main(["resume", "looppidref"])
    assert rc != 0
    assert "appears to still be running" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Loop wire-in: lease acquired at init, released at finalize
# ---------------------------------------------------------------------------

def test_run_agent_loop_holds_then_releases_lease(workspace, monkeypatch):
    """A dry run acquires a lease keyed by its own loop_id and leaves it
    present-unheld at finalize."""
    from agent_loop import run_agent_loop
    from run_lease import lease_path, probe_owner_alive

    result = run_agent_loop("simple lease goal", project="lease-proj",
                            dry_run=True, verbose=False)
    assert result.status != "refused_busy"
    assert lease_path(result.loop_id).exists()
    assert probe_owner_alive(result.loop_id) is False  # released at finalize
