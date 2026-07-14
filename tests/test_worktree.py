"""Concurrency phase 3b: git worktree isolation for concurrent workers.

The incident class: parallel fan-out steps (and opt-in whole runs) sharing
one checkout — forks writing over each other's working tree. Each worker
gets its own worktree; merge-back is serialized; conflicts never silently
drop work (branch preserved + named in the structured failure).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _git(args, cwd):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture()
def workspace(tmp_path):
    # conftest's autouse _isolate_workspace already routes MARO_WORKSPACE
    # to tmp_path; this fixture just names the dir for readability.
    return tmp_path


@pytest.fixture()
def repo(workspace):
    """A tmp git repo with one committed file on branch main."""
    repo = workspace / "proj"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@test"], repo)
    _git(["config", "user.name", "test"], repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    r = _git(["commit", "-m", "base"], repo)
    assert r.returncode == 0, r.stderr
    return repo


def test_provision_non_git_returns_none(workspace):
    from worktree import provision

    plain = workspace / "plain"
    plain.mkdir()
    assert provision(plain, "step1", loop_id="l1") is None


def test_provision_merge_cleanup_roundtrip(repo):
    from worktree import provision, merge_back, cleanup, prune

    wt = provision(repo, "step1", loop_id="loop-a")
    assert wt is not None
    assert wt.path.is_dir()
    assert wt.branch == "maro/loop-a/step1"
    # worker writes in the worktree, NOT the main checkout
    (wt.path / "work.txt").write_text("did work\n", encoding="utf-8")
    assert not (repo / "work.txt").exists()

    merge = merge_back(wt)
    assert merge.ok, merge.detail
    assert (repo / "work.txt").read_text(encoding="utf-8") == "did work\n"

    cleanup(wt)
    prune(repo)
    listed = _git(["worktree", "list"], repo).stdout
    assert str(wt.path) not in listed
    branches = _git(["branch", "--list", wt.branch], repo).stdout
    assert branches.strip() == ""


def test_merge_no_changes_is_ok(repo):
    from worktree import provision, merge_back, cleanup

    wt = provision(repo, "noop", loop_id="loop-b")
    merge = merge_back(wt)
    assert merge.ok
    assert merge.detail == "no changes"
    cleanup(wt)


def test_nonconflicting_parallel_workers_both_merge(repo):
    from worktree import provision, merge_back, cleanup

    a = provision(repo, "stepA", loop_id="loop-c")
    b = provision(repo, "stepB", loop_id="loop-c")
    (a.path / "a.txt").write_text("A\n", encoding="utf-8")
    (b.path / "b.txt").write_text("B\n", encoding="utf-8")

    ma = merge_back(a)
    mb = merge_back(b)
    assert ma.ok and mb.ok, (ma.detail, mb.detail)
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    cleanup(a)
    cleanup(b)


def test_conflict_blocks_with_branch_preserved(repo):
    from worktree import provision, merge_back, cleanup

    a = provision(repo, "stepA", loop_id="loop-d")
    b = provision(repo, "stepB", loop_id="loop-d")
    (a.path / "base.txt").write_text("edit from A\n", encoding="utf-8")
    (b.path / "base.txt").write_text("edit from B\n", encoding="utf-8")

    ma = merge_back(a)
    assert ma.ok
    mb = merge_back(b)
    assert not mb.ok
    assert mb.conflict
    assert mb.branch == "maro/loop-d/stepB"
    # main checkout untouched by the aborted merge — A's edit survives
    assert (repo / "base.txt").read_text(encoding="utf-8") == "edit from A\n"
    # work preserved on the branch
    show = _git(["show", f"{mb.branch}:base.txt"], repo)
    assert show.stdout == "edit from B\n"

    cleanup(a)
    cleanup(b, keep_on_failure=True)
    assert b.path.is_dir()  # kept for inspection


def test_merge_refuses_dirty_base_checkout(repo):
    from worktree import provision, merge_back, cleanup

    wt = provision(repo, "step1", loop_id="loop-e")
    (wt.path / "work.txt").write_text("work\n", encoding="utf-8")
    # user (or another run) has in-flight edits in the main checkout
    (repo / "base.txt").write_text("uncommitted local edit\n", encoding="utf-8")

    merge = merge_back(wt)
    assert not merge.ok
    assert "dirty" in merge.detail
    assert merge.branch in merge.detail
    cleanup(wt, keep_on_failure=True)


def test_merge_refuses_when_base_ref_moved(repo):
    from worktree import provision, merge_back, cleanup

    wt = provision(repo, "step1", loop_id="loop-f")
    (wt.path / "work.txt").write_text("work\n", encoding="utf-8")
    _git(["checkout", "-b", "elsewhere"], repo)
    try:
        merge = merge_back(wt)
        assert not merge.ok
        assert "moved off base ref" in merge.detail
    finally:
        _git(["checkout", "main"], repo)
        cleanup(wt, keep_on_failure=True)


# ---------------------------------------------------------------------------
# loop_parallel wire-in
# ---------------------------------------------------------------------------

def test_step_worktree_helper_isolates_and_merges(repo, monkeypatch):
    """_run_in_step_worktree: step's subprocess cwd is a worktree, and the
    step's file lands in the main checkout after merge."""
    from llm import set_default_subprocess_cwd, get_default_subprocess_cwd
    from loop_parallel import _run_in_step_worktree

    set_default_subprocess_cwd(str(repo))
    try:
        seen = {}

        def _fake_step():
            cwd = Path(get_default_subprocess_cwd())
            seen["cwd"] = cwd
            (cwd / "step-output.txt").write_text("out\n", encoding="utf-8")
            return {"status": "done", "result": "ok", "summary": "did it"}

        outcome = _run_in_step_worktree("stepX", _fake_step)
        assert outcome["status"] == "done"
        assert seen["cwd"] != repo  # ran isolated
        assert (repo / "step-output.txt").exists()  # merged back
        listed = _git(["worktree", "list"], repo).stdout
        assert str(seen["cwd"]) not in listed  # cleaned up
    finally:
        set_default_subprocess_cwd(None)


def test_step_worktree_helper_conflict_marks_blocked(repo):
    """Merge conflict → step blocked, branch named, work preserved."""
    from llm import set_default_subprocess_cwd, get_default_subprocess_cwd
    from loop_parallel import _run_in_step_worktree

    set_default_subprocess_cwd(str(repo))
    try:
        def _conflicting_step():
            cwd = Path(get_default_subprocess_cwd())
            (cwd / "base.txt").write_text("worker edit\n", encoding="utf-8")
            return {"status": "done", "result": "ok", "summary": "edited base"}

        # move the base ahead with a conflicting commit before merge-back
        def _step_then_race():
            out = _conflicting_step()
            (repo / "base.txt").write_text("raced edit\n", encoding="utf-8")
            _git(["add", "-A"], repo)
            _git(["commit", "-m", "race"], repo)
            return out

        outcome = _run_in_step_worktree("stepY", _step_then_race)
        assert outcome["status"] == "blocked"
        assert outcome["worktree_branch"]
        assert "worktree merge failed" in outcome["stuck_reason"]
        show = _git(["show", f"{outcome['worktree_branch']}:base.txt"], repo)
        assert show.stdout == "worker edit\n"  # work preserved
    finally:
        set_default_subprocess_cwd(None)


def test_step_worktree_helper_non_git_runs_in_place(workspace):
    from llm import set_default_subprocess_cwd, get_default_subprocess_cwd
    from loop_parallel import _run_in_step_worktree

    plain = workspace / "plain-proj"
    plain.mkdir()
    set_default_subprocess_cwd(str(plain))
    try:
        def _step():
            return {"status": "done", "cwd": get_default_subprocess_cwd()}

        outcome = _run_in_step_worktree("step1", _step)
        assert outcome["cwd"] == str(plain)  # unchanged — ran in place
    finally:
        set_default_subprocess_cwd(None)


# ---------------------------------------------------------------------------
# busy_policy=worktree (cross-run)
# ---------------------------------------------------------------------------

SRC = str(Path(__file__).resolve().parents[1] / "src")

HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[3])
from interrupt import acquire_project_slot
slot = acquire_project_slot(sys.argv[1], loop_id="xproc-holder", goal="held")
print("HELD", flush=True)
time.sleep(float(sys.argv[2]))
slot.release()
"""


def _spawn_holder(project: str, seconds: float = 30.0) -> subprocess.Popen:
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, project, str(seconds), SRC],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == "HELD", proc.stderr.read()
    return proc


def test_busy_policy_worktree_second_run_proceeds_and_merges(workspace, monkeypatch):
    """Project slot held by another process + busy_policy=worktree → the run
    proceeds in an isolated worktree and merges at finalize."""
    import os
    import orch_items as oi
    import config as config_mod
    from agent_loop import run_agent_loop

    # make the project dir a git repo
    proj = "wt-policy-proj"
    repo = oi.project_dir(proj)
    repo.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "seed"], repo)

    ws = config_mod.workspace_root()
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config.yml").write_text("loop:\n  busy_policy: worktree\n", encoding="utf-8")
    config_mod.load_config(reload=True)

    holder = _spawn_holder(proj)
    try:
        result = run_agent_loop(
            "do something", project=proj, dry_run=True, verbose=False,
        )
        assert result.status != "refused_busy"
        # worktree merged + cleaned at finalize
        listed = _git(["worktree", "list"], repo).stdout
        assert "maro/" not in listed
    finally:
        holder.kill()
        holder.communicate()


def test_busy_policy_worktree_non_git_still_refuses(workspace, monkeypatch):
    """Non-git project dir can't isolate — falls back to refuse."""
    import os
    import orch_items as oi
    import config as config_mod
    from agent_loop import run_agent_loop

    proj = "wt-nongit-proj"
    oi.project_dir(proj).mkdir(parents=True, exist_ok=True)
    ws = config_mod.workspace_root()
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config.yml").write_text("loop:\n  busy_policy: worktree\n", encoding="utf-8")
    config_mod.load_config(reload=True)

    holder = _spawn_holder(proj)
    try:
        result = run_agent_loop(
            "do something", project=proj, dry_run=True, verbose=False,
        )
        assert result.status == "refused_busy"
    finally:
        holder.kill()
        holder.communicate()


# ---------------------------------------------------------------------------
# Scratch-clone flow — self-development runs under the containerized executor
# (docs/CONTAINER_EXECUTOR_DESIGN.md §4). Live repo is cloned into a throwaway
# scratch the container edits; merge-back rides the same serialized semantics.
# ---------------------------------------------------------------------------

def test_provision_clone_non_git_returns_none(workspace):
    from worktree import provision_clone

    plain = workspace / "plain"
    plain.mkdir()
    assert provision_clone(plain, "container", loop_id="c1") is None


def test_clone_roundtrip_merges_new_file_and_cleans_up(repo):
    from worktree import provision_clone, merge_back_clone, cleanup_clone

    clone = provision_clone(repo, "container", loop_id="loop-cc")
    assert clone is not None
    assert clone.path.is_dir()
    assert clone.branch == "maro/loop-cc/container"
    # A full, independent clone — the live repo's base file came along.
    assert (clone.path / "base.txt").read_text(encoding="utf-8") == "base\n"

    # Worker edits happen in the CLONE, never the live repo.
    (clone.path / "work.txt").write_text("did work\n", encoding="utf-8")
    assert not (repo / "work.txt").exists()

    merge = merge_back_clone(clone)
    assert merge.ok, merge.detail
    assert (repo / "work.txt").read_text(encoding="utf-8") == "did work\n"

    cleanup_clone(clone)
    assert not clone.path.exists()  # scratch removed
    branches = _git(["branch", "--list", clone.branch], repo).stdout
    assert branches.strip() == ""  # fetched branch removed


def test_clone_merge_no_changes_is_ok(repo):
    from worktree import provision_clone, merge_back_clone, cleanup_clone

    clone = provision_clone(repo, "container", loop_id="loop-noop")
    assert clone is not None
    merge = merge_back_clone(clone)
    assert merge.ok
    assert merge.detail == "no changes"
    cleanup_clone(clone)


def test_clone_never_shares_objects_with_parent(repo):
    """--no-hardlinks: a commit in the clone is absent from the parent until
    merge-back — no shared object inode the container could reach."""
    from worktree import provision_clone

    clone = provision_clone(repo, "container", loop_id="loop-iso")
    assert clone is not None
    (clone.path / "secret.txt").write_text("clone-only\n", encoding="utf-8")
    _git(["add", "-A"], clone.path)
    c = _git(["commit", "-m", "in clone"], clone.path)
    assert c.returncode == 0, c.stderr
    sha = _git(["rev-parse", "HEAD"], clone.path).stdout.strip()
    # That commit object does not exist in the parent's store yet.
    present = _git(["cat-file", "-e", sha], repo)
    assert present.returncode != 0


def test_clone_conflict_preserves_work_on_branch(repo):
    from worktree import provision_clone, merge_back_clone, cleanup_clone

    clone = provision_clone(repo, "container", loop_id="loop-conf")
    assert clone is not None
    # Clone edits base.txt one way (left as a leftover edit)...
    (clone.path / "base.txt").write_text("edit from clone\n", encoding="utf-8")
    # ...while the parent moves base.txt another way and commits on main.
    (repo / "base.txt").write_text("edit from parent\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    assert _git(["commit", "-m", "parent change"], repo).returncode == 0

    merge = merge_back_clone(clone)
    assert not merge.ok
    assert merge.conflict
    assert merge.branch == "maro/loop-conf/container"
    # Parent checkout untouched by the aborted merge.
    assert (repo / "base.txt").read_text(encoding="utf-8") == "edit from parent\n"
    # Clone work preserved on the fetched branch.
    show = _git(["show", f"{merge.branch}:base.txt"], repo)
    assert show.stdout == "edit from clone\n"

    cleanup_clone(clone, keep_on_failure=True)
    assert clone.path.is_dir()  # kept for inspection


def test_clone_merges_work_committed_on_a_side_branch(repo):
    """A worker that switches branches inside the container must NOT be treated
    as 'no changes' and deleted — merge-back keys on the clone's actual HEAD."""
    from worktree import provision_clone, merge_back_clone, cleanup_clone

    clone = provision_clone(repo, "container", loop_id="loop-side")
    assert clone is not None
    _git(["checkout", "-b", "worker-side"], clone.path)
    (clone.path / "side.txt").write_text("side work\n", encoding="utf-8")
    _git(["add", "-A"], clone.path)
    assert _git(["commit", "-m", "on side"], clone.path).returncode == 0

    merge = merge_back_clone(clone)
    assert merge.ok, merge.detail
    assert (repo / "side.txt").read_text(encoding="utf-8") == "side work\n"
    cleanup_clone(clone)


def test_clone_merge_neutralizes_planted_hooks_and_exec_config(repo):
    """A hostile worker's planted git hooks / exec-capable config must NOT run
    on the host during merge-back (adversarial-review C/M1/A3)."""
    from worktree import provision_clone, merge_back_clone, cleanup_clone

    clone = provision_clone(repo, "container", loop_id="loop-evil")
    assert clone is not None
    marker = repo.parent / "PWNED"
    hooks = clone.path / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    # Worker also plants an exec-capable config key.
    _git(["config", "--local", "core.fsmonitor", f"touch '{marker}'"], clone.path)
    # A dirty file so merge-back runs `git commit` (would fire pre-commit).
    (clone.path / "work.txt").write_text("work\n", encoding="utf-8")

    merge = merge_back_clone(clone)
    assert merge.ok, merge.detail
    # The core security assertion: nothing the worker planted executed on host.
    assert not marker.exists(), "planted git hook/config executed on the host!"
    assert not hook.exists()  # hooks dir stripped by sanitize
    cfg = _git(["config", "--local", "--get", "core.fsmonitor"], clone.path)
    assert cfg.stdout.strip() == ""  # exec-capable config unset
    assert (repo / "work.txt").read_text(encoding="utf-8") == "work\n"  # work still merged
    cleanup_clone(clone)


# ---------------------------------------------------------------------------
# Stale-clone sweep — recover-then-remove, retention-safe (C4).
# A SIGKILL between provision and finalize leaks a whole-repo clone; the sweep
# recovers any unmerged work and only removes clones whose owner is verifiably
# dead AND whose work provably reached the live repo (or never existed).
# ---------------------------------------------------------------------------

def _paths(entries):
    """First element (the clone path) of each result tuple."""
    return {e[0] for e in entries}


def test_provision_writes_owner_sidecar_outside_the_clone(repo):
    from worktree import provision_clone, _clone_sidecar_path, _read_clone_owner

    clone = provision_clone(repo, "container", loop_id="loop-sc")
    assert clone is not None
    sidecar = _clone_sidecar_path(clone.path)
    # Sibling of the clone dir — NOT inside it, so the container-mounted clone
    # cannot let a hostile worker tamper with the recovery breadcrumb.
    assert sidecar.exists()
    assert sidecar.parent == clone.path.parent
    assert clone.path not in sidecar.parents
    meta = _read_clone_owner(sidecar)
    assert meta["owner_pid"] == os.getpid()
    assert meta["owner_start"]
    assert meta["repo_dir"] == str(repo)
    assert meta["base_ref"] == "main"
    assert meta["branch"] == clone.branch
    # Invisible to the clone's own git (it lives outside the work tree), so it
    # never gets swept into the worker's commits / merge-back.
    assert _git(["status", "--porcelain"], clone.path).stdout.strip() == ""


def test_cleanup_clone_removes_owner_sidecar(repo):
    from worktree import provision_clone, cleanup_clone, _clone_sidecar_path

    clone = provision_clone(repo, "container", loop_id="loop-cs")
    sidecar = _clone_sidecar_path(clone.path)
    assert sidecar.exists()
    cleanup_clone(clone)
    assert not clone.path.exists()
    assert not sidecar.exists()


def test_sweep_skips_live_owner(repo):
    from worktree import provision_clone, sweep_stranded_clones

    clone = provision_clone(repo, "container", loop_id="loop-live")
    (clone.path / "wip.txt").write_text("in flight\n", encoding="utf-8")
    res = sweep_stranded_clones(pid_alive=lambda p: True, min_age_s=0)
    assert str(clone.path) in _paths(res.skipped_live)
    assert clone.path.is_dir()  # a live run's clone is never touched
    assert not res.recovered and not res.removed_empty and not res.preserved


def test_sweep_recovers_clone_when_pid_was_reused(repo):
    from worktree import (
        provision_clone, sweep_stranded_clones, _clone_sidecar_path,
    )

    clone = provision_clone(repo, "container", loop_id="loop-reused")
    sidecar = _clone_sidecar_path(clone.path)
    meta = json.loads(sidecar.read_text())
    meta["owner_start"] = "original-birth"
    sidecar.write_text(json.dumps(meta))

    res = sweep_stranded_clones(
        pid_alive=lambda p: True,
        process_token=lambda p: "reused-birth",
        min_age_s=0,
    )

    assert str(clone.path) in _paths(res.removed_empty)
    assert not clone.path.exists()


def test_sweep_spares_legacy_sidecar_with_live_pid(repo):
    from worktree import (
        provision_clone, sweep_stranded_clones, _clone_sidecar_path,
    )

    clone = provision_clone(repo, "container", loop_id="loop-legacy-owner")
    sidecar = _clone_sidecar_path(clone.path)
    meta = json.loads(sidecar.read_text())
    meta.pop("owner_start")
    sidecar.write_text(json.dumps(meta))

    res = sweep_stranded_clones(
        pid_alive=lambda p: True,
        process_token=lambda p: "different-birth",
        min_age_s=0,
    )

    assert str(clone.path) in _paths(res.skipped_live)
    assert clone.path.exists()


def test_sweep_recovers_dead_owner_work_then_removes(repo):
    from worktree import provision_clone, sweep_stranded_clones, _clone_sidecar_path

    clone = provision_clone(repo, "container", loop_id="loop-dead")
    (clone.path / "recovered.txt").write_text("saved\n", encoding="utf-8")
    res = sweep_stranded_clones(pid_alive=lambda p: False, min_age_s=0)
    # Work merged into the LIVE repo before the clone was removed.
    assert (repo / "recovered.txt").read_text(encoding="utf-8") == "saved\n"
    assert not clone.path.exists()
    assert not _clone_sidecar_path(clone.path).exists()
    assert str(clone.path) in _paths(res.recovered)


def test_sweep_removes_empty_dead_owner_clone(repo):
    from worktree import provision_clone, sweep_stranded_clones

    clone = provision_clone(repo, "container", loop_id="loop-empty")
    res = sweep_stranded_clones(pid_alive=lambda p: False, min_age_s=0)
    # No unmerged work → provably safe to remove.
    assert not clone.path.exists()
    assert str(clone.path) in _paths(res.removed_empty)


def test_sweep_preserves_unmergeable_work(repo):
    from worktree import provision_clone, sweep_stranded_clones

    clone = provision_clone(repo, "container", loop_id="loop-keep")
    # Clone edits base.txt one way; the parent diverges → merge-back conflicts.
    (clone.path / "base.txt").write_text("clone side\n", encoding="utf-8")
    (repo / "base.txt").write_text("parent side\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    assert _git(["commit", "-m", "diverge"], repo).returncode == 0

    res = sweep_stranded_clones(pid_alive=lambda p: False, min_age_s=0)
    # Retention: unrecovered work is NEVER auto-removed.
    assert clone.path.is_dir()
    assert str(clone.path) in _paths(res.preserved)
    # And the work is still reachable on the preserved branch in the live repo.
    show = _git(["show", f"{clone.branch}:base.txt"], repo)
    assert show.stdout == "clone side\n"


def test_sweep_surfaces_sidecarless_clone_and_never_removes(repo):
    from worktree import provision_clone, sweep_stranded_clones, _clone_sidecar_path

    clone = provision_clone(repo, "container", loop_id="loop-nosc")
    _clone_sidecar_path(clone.path).unlink()  # crash before the sidecar landed
    (clone.path / "work.txt").write_text("orphan work\n", encoding="utf-8")
    res = sweep_stranded_clones(pid_alive=lambda p: False, min_age_s=0)
    # Without the trusted breadcrumb we can't prove ownership/unmerged state —
    # retention forbids removal; surface for manual inspection.
    assert clone.path.is_dir()
    assert str(clone.path) in _paths(res.surfaced)
    assert not res.recovered and not res.removed_empty


def test_sweep_respects_grace_window(repo):
    from worktree import provision_clone, sweep_stranded_clones

    clone = provision_clone(repo, "container", loop_id="loop-grace")
    # Owner dead, but a large grace window makes it not-yet-eligible.
    res = sweep_stranded_clones(pid_alive=lambda p: False, min_age_s=3600)
    assert clone.path.is_dir()
    assert str(clone.path) in _paths(res.skipped_young)
    assert not res.recovered and not res.removed_empty and not res.preserved


def test_sweep_empty_when_nothing_leaked(workspace):
    from worktree import sweep_stranded_clones

    res = sweep_stranded_clones(pid_alive=lambda p: False, min_age_s=0)
    assert not res.acted()
