"""Concurrency phase 3: per-project admission gate + daemon pidfile singleton.

The gate is a flock on loop-<project>.lock held for the run's lifetime —
set_loop_running() only ever *advertised* a loop (write, no check: TOCTOU);
acquire_project_slot() actually excludes. Cross-process tests use real
subprocesses because flock semantics only bite across open file descriptions.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture()
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("MARO_ADMISSION_WAIT_S", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# In-process slot semantics
# ---------------------------------------------------------------------------

def test_slot_acquire_release_reacquire(workspace):
    from interrupt import acquire_project_slot

    slot = acquire_project_slot("proj-a", loop_id="loop1", goal="first")
    assert slot is not None
    payload = json.loads(slot.path.read_text(encoding="utf-8"))
    assert payload["loop_id"] == "loop1"
    assert payload["pid"] == os.getpid()
    assert payload["project"] == "proj-a"

    slot.release()
    # payload cleared but file NOT unlinked (unlink/reacquire race)
    assert slot.path.exists()
    assert slot.path.read_text(encoding="utf-8") == ""

    slot2 = acquire_project_slot("proj-a", loop_id="loop2", goal="second")
    assert slot2 is not None
    slot2.release()


def test_slot_same_process_siblings_share(workspace):
    """In-process sibling loops (mission fan-out, parallel goals) are one
    cooperating run: the second acquire SHARES the slot instead of
    refusing, and the flock only drops when the last handle releases."""
    from interrupt import acquire_project_slot

    slot = acquire_project_slot("proj-b", loop_id="holder-loop", goal="g")
    sibling = acquire_project_slot("proj-b", loop_id="second-loop", goal="g2")
    assert sibling is not None
    # original holder's payload stays — the sibling joined, didn't replace
    payload = json.loads(slot.path.read_text(encoding="utf-8"))
    assert payload["loop_id"] == "holder-loop"

    slot.release()
    # still held by the sibling: payload intact, not truncated
    assert slot.path.read_text(encoding="utf-8") != ""
    sibling.release()
    # last handle gone → flock dropped, payload cleared
    assert slot.path.read_text(encoding="utf-8") == ""
    again = acquire_project_slot("proj-b", loop_id="third")
    assert again is not None
    again.release()


def test_slot_different_projects_dont_conflict(workspace):
    from interrupt import acquire_project_slot

    a = acquire_project_slot("proj-c", loop_id="l1")
    b = acquire_project_slot("proj-d", loop_id="l2")
    assert a is not None and b is not None
    a.release()
    b.release()


def test_slot_empty_project_skips_gate(workspace):
    from interrupt import acquire_project_slot

    assert acquire_project_slot("", loop_id="l1") is None


def test_slot_wait_succeeds_after_release(workspace):
    """wait_s > 0 polls; a slot released by another process mid-wait is
    acquired (cross-process — in-process siblings share instead)."""
    from interrupt import acquire_project_slot

    holder = _spawn_holder("proj-e", 1.0, workspace)  # holds ~1s then releases
    try:
        start = time.monotonic()
        slot2 = acquire_project_slot("proj-e", loop_id="l2", wait_s=10.0)
        waited = time.monotonic() - start
        assert slot2 is not None
        assert waited < 8
        slot2.release()
    finally:
        holder.kill()
        holder.communicate()


def test_slot_del_backstop_releases(workspace):
    """Dropping the last reference releases the flock (exception paths
    that skip finalize must not wedge the project in a live process)."""
    from interrupt import acquire_project_slot

    slot = acquire_project_slot("proj-f", loop_id="l1")
    assert slot is not None
    del slot
    slot2 = acquire_project_slot("proj-f", loop_id="l2")
    assert slot2 is not None
    slot2.release()


def test_readers_still_parse_slot_payload(workspace):
    """get_running_project_loop reads the same file/schema the slot writes."""
    from interrupt import acquire_project_slot, get_running_project_loop

    slot = acquire_project_slot("proj-g", loop_id="reader-check", goal="visible goal")
    try:
        info = get_running_project_loop("proj-g")
        assert info is not None
        assert info["loop_id"] == "reader-check"
        assert info["goal"] == "visible goal"
    finally:
        slot.release()
    assert get_running_project_loop("proj-g") is None


# ---------------------------------------------------------------------------
# Cross-process
# ---------------------------------------------------------------------------

HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[3])
from interrupt import acquire_project_slot
slot = acquire_project_slot(sys.argv[1], loop_id="xproc-holder", goal="held")
print("HELD", flush=True)
time.sleep(float(sys.argv[2]))
slot.release()
"""


def _spawn_holder(project: str, seconds: float, tmp_path) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["OPENCLAW_WORKSPACE"] = str(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, project, str(seconds), SRC],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == "HELD", proc.stderr.read()
    return proc


def test_cross_process_busy_refuses_fast(workspace):
    from interrupt import acquire_project_slot, LoopBusy

    holder = _spawn_holder("proj-x", 15, workspace)
    try:
        start = time.monotonic()
        with pytest.raises(LoopBusy) as exc_info:
            acquire_project_slot("proj-x", loop_id="refused")
        assert time.monotonic() - start < 2  # refuse-by-default is immediate
        assert exc_info.value.holder.get("pid") == holder.pid
    finally:
        holder.kill()
        holder.communicate()


def test_cross_process_holder_death_frees_slot(workspace):
    """SIGKILL the holder — the kernel releases the flock; no stale-lock
    cleanup ritual required."""
    from interrupt import acquire_project_slot

    holder = _spawn_holder("proj-y", 60, workspace)
    holder.kill()
    holder.communicate()
    slot = acquire_project_slot("proj-y", loop_id="after-death", wait_s=3.0)
    assert slot is not None
    slot.release()


# ---------------------------------------------------------------------------
# Loop wire-in
# ---------------------------------------------------------------------------

def test_run_agent_loop_refused_busy(workspace):
    """A loop on a project held by ANOTHER process returns
    status=refused_busy without executing any steps."""
    from agent_loop import run_agent_loop

    holder = _spawn_holder("gated-proj", 30, workspace)
    try:
        result = run_agent_loop(
            "do something", project="gated-proj", dry_run=True, verbose=False,
        )
        assert result.status == "refused_busy"
        assert result.steps == []
        assert "xproc-holder" in (result.stuck_reason or "")
    finally:
        holder.kill()
        holder.communicate()


def test_run_agent_loop_same_process_siblings_not_refused(workspace):
    """A sibling loop in the SAME process shares the slot (mission feature
    fan-out runs 2 features on one project in threads — must not refuse)."""
    from interrupt import acquire_project_slot
    from agent_loop import run_agent_loop

    slot = acquire_project_slot("shared-proj", loop_id="occupant", goal="busy")
    try:
        result = run_agent_loop(
            "do something", project="shared-proj", dry_run=True, verbose=False,
        )
        assert result.status != "refused_busy"
    finally:
        slot.release()


def test_run_agent_loop_releases_slot_at_finalize(workspace):
    """A completed run leaves the project immediately reacquirable."""
    from interrupt import acquire_project_slot
    from agent_loop import run_agent_loop

    result = run_agent_loop("simple goal", project="release-proj", dry_run=True, verbose=False)
    assert result.status != "refused_busy"
    slot = acquire_project_slot("release-proj", loop_id="after-run")
    assert slot is not None
    slot.release()


# ---------------------------------------------------------------------------
# Daemon pidfile singleton
# ---------------------------------------------------------------------------

def test_pidfile_second_holder_refused(workspace):
    from proc_lock import hold_pidfile, read_holder

    with hold_pidfile("testd") as acquired:
        assert acquired is True
        holder = read_holder("testd")
        assert holder["pid"] == os.getpid()
        with hold_pidfile("testd") as second:
            assert second is False
    # released on exit
    with hold_pidfile("testd") as third:
        assert third is True


def test_typed_pidfile_acquire_distinguishes_busy_from_unavailable(
        workspace, monkeypatch):
    import errno
    import fcntl
    from proc_lock import acquire_pidfile

    first = acquire_pidfile("typed-lock")
    assert first.status == "acquired"
    try:
        assert acquire_pidfile("typed-lock").status == "busy"
    finally:
        first.handle.close()

    monkeypatch.setattr(
        fcntl, "flock",
        lambda *a, **k: (_ for _ in ()).throw(
            OSError(errno.ENOLCK, "no locks available")),
    )
    unavailable = acquire_pidfile("typed-lock-unavailable")
    assert unavailable.status == "unavailable"
    assert "no locks available" in unavailable.error


def test_read_holder_rejects_non_object_json(workspace):
    from proc_lock import pidfile_path, read_holder

    path = pidfile_path("malformed-holder")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('["not", "a", "holder"]')
    assert read_holder("malformed-holder") is None


def test_pidfile_cross_process(workspace):
    from proc_lock import hold_pidfile

    code = """
import sys, time
sys.path.insert(0, sys.argv[1])
from proc_lock import hold_pidfile
with hold_pidfile("xprocd") as acquired:
    print("HELD" if acquired else "REFUSED", flush=True)
    if acquired:
        time.sleep(10)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["OPENCLAW_WORKSPACE"] = str(workspace)
    proc = subprocess.Popen(
        [sys.executable, "-c", code, SRC],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "HELD"
        with hold_pidfile("xprocd") as acquired:
            assert acquired is False
    finally:
        proc.kill()
        proc.communicate()
