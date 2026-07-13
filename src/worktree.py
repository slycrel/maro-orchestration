"""Git worktree isolation for concurrent workers (concurrency phase 3b).

The incident class this kills: parallel fan-out steps (and, opt-in, whole
concurrent runs) executing in the SAME checkout — git-stash races, forks
writing over each other's working tree, half-committed states. Each parallel
worker gets its own worktree (private working tree + index, shared object
store), works on branch maro/<loop_id>/<name>, and merge-back into the base
branch is serialized under a per-repo file lock as workers complete.

Merge conflicts never silently drop work: the merge is aborted, the branch
is kept, and the caller gets a structured failure naming the branch.

Non-git directories return None from provision() — callers fall through to
executing in place, byte-identical to pre-3b behavior.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("maro.worktree")

_GIT_TIMEOUT_S = 120


@dataclass
class Worktree:
    path: Path         # the worktree checkout workers run in
    branch: str        # maro/<loop_id>/<name>
    repo_dir: Path     # the original checkout
    base_ref: str      # branch (or sha when detached) the run started on


@dataclass
class ScratchClone:
    """A throwaway full clone of a repo the container edits (design §4 self-dev).

    Containers never mount a live repo rw — a prompt-injected worker could only
    corrupt its OWN copy. The worker edits/commits this clone (a separate object
    store, `--no-hardlinks`); merge-back is a HOST-side `git fetch` from it into
    the parent under the SAME serialized-merge semantics as `Worktree`.
    """
    path: Path         # the clone checkout the container mounts rw + runs in
    branch: str        # maro/<loop_id>/<name>
    repo_dir: Path     # the live repo it was cloned from (never mounted)
    base_ref: str      # branch (or sha) the parent was on at clone time


@dataclass
class MergeResult:
    ok: bool
    conflict: bool = False
    branch: str = ""
    detail: str = ""
    merged_commit: str = ""


def _git(args: list, cwd: Path, *, timeout: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def is_git_repo(path: Path) -> bool:
    try:
        r = _git(["rev-parse", "--is-inside-work-tree"], Path(path), timeout=15)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _worktrees_root() -> Path:
    try:
        from config import workspace_root
        return workspace_root() / "worktrees"
    except Exception:
        return Path.home() / ".maro" / "workspace" / "worktrees"


def _current_ref(repo_dir: Path) -> Optional[str]:
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo_dir, timeout=15)
    if r.returncode != 0:
        return None
    ref = r.stdout.strip()
    if ref == "HEAD":  # detached — pin to the sha
        r = _git(["rev-parse", "HEAD"], repo_dir, timeout=15)
        ref = r.stdout.strip() if r.returncode == 0 else None
    return ref or None


def provision(project_dir, name: str, *, loop_id: str) -> Optional[Worktree]:
    """Create an isolated worktree of project_dir for one worker.

    Returns None (no behavior change) when project_dir isn't a git repo or
    provisioning fails — isolation is an upgrade, never a gate on the work.
    """
    repo_dir = Path(project_dir)
    if not is_git_repo(repo_dir):
        return None

    base_ref = _current_ref(repo_dir)
    if not base_ref:
        log.warning("worktree provision: cannot resolve HEAD of %s", repo_dir)
        return None

    safe_name = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)[:60]
    branch = f"maro/{loop_id}/{safe_name}"
    wt_path = _worktrees_root() / loop_id / safe_name
    try:
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        r = _git(["worktree", "add", str(wt_path), "-b", branch, "HEAD"], repo_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("worktree provision failed for %s: %s", repo_dir, exc)
        return None
    if r.returncode != 0:
        log.warning(
            "worktree provision failed for %s: %s", repo_dir,
            (r.stderr or r.stdout).strip()[:300],
        )
        return None
    log.info("worktree provisioned: %s on %s (base %s)", wt_path, branch, base_ref)
    return Worktree(path=wt_path, branch=branch, repo_dir=repo_dir, base_ref=base_ref)


def _merge_lock_path(repo_dir: Path) -> Path:
    # Sidecar in the workspace, not inside .git (bare-ish setups, and the
    # repo may be the user's own checkout — don't litter it).
    key = "".join(c if c.isalnum() else "-" for c in str(repo_dir.resolve()))[-100:]
    return _worktrees_root() / f"merge-{key}"


def _commit_leftovers(work_dir: Path, branch: str, base_ref: str, message: str) -> Optional[MergeResult]:
    """Commit any uncommitted work in `work_dir` and report whether there's
    anything to merge. Shared by worktree and scratch-clone merge-back.

    Returns a terminal MergeResult when the caller should stop — an autocommit
    failure/error, or `ok=True "no changes"` when the branch is not ahead of
    base — and None when there ARE commits to merge (proceed to the locked
    merge). Never raises.
    """
    try:
        r = _git(["status", "--porcelain"], work_dir, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            _git(["add", "-A"], work_dir)
            c = _git(["commit", "-m", message or f"wt: {branch}"], work_dir)
            if c.returncode != 0:
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"autocommit failed: {(c.stderr or c.stdout).strip()[:300]}",
                )
        # Nothing to merge at all? (no commits and clean tree)
        ahead = _git(["rev-list", "--count", f"{base_ref}..{branch}"], work_dir, timeout=30)
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            return MergeResult(ok=True, branch=branch, detail="no changes")
    except (OSError, subprocess.SubprocessError) as exc:
        return MergeResult(ok=False, branch=branch, detail=f"autocommit error: {exc}")
    return None


def _locked_merge(repo_dir: Path, branch: str, base_ref: str) -> MergeResult:
    """Merge `branch` into `base_ref` in `repo_dir`, serialized per-repo.

    The `branch` ref must already exist in `repo_dir` (a worktree shares the
    object store; a scratch clone fetches it in first). file_lock serializes
    concurrent finishers. On conflict: merge --abort, branch preserved,
    structured failure naming it. Never silently drops work.
    """
    from file_lock import locked_write
    try:
        with locked_write(_merge_lock_path(repo_dir)):
            cur = _current_ref(repo_dir)
            if cur != base_ref:
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"repo moved off base ref ({base_ref} -> {cur}); "
                           f"work preserved on {branch}",
                )
            dirty = _git(["status", "--porcelain"], repo_dir, timeout=30)
            if dirty.returncode == 0 and dirty.stdout.strip():
                # Merging into a dirty checkout risks entangling the user's
                # in-flight edits with the merge — keep the branch instead.
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"base checkout dirty; work preserved on {branch}",
                )
            m = _git(
                ["merge", "--no-ff", branch, "-m", f"merge {branch}"],
                repo_dir,
            )
            if m.returncode != 0:
                _git(["merge", "--abort"], repo_dir)
                return MergeResult(
                    ok=False, conflict=True, branch=branch,
                    detail=f"merge conflict; work preserved on {branch}: "
                           f"{(m.stderr or m.stdout).strip()[:300]}",
                )
            sha = _git(["rev-parse", "HEAD"], repo_dir, timeout=15)
            return MergeResult(
                ok=True, branch=branch,
                merged_commit=sha.stdout.strip() if sha.returncode == 0 else "",
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return MergeResult(ok=False, branch=branch, detail=f"merge error: {exc}")


def merge_back(wt: Worktree, *, message: str = "") -> MergeResult:
    """Commit the worker's leftovers and merge its branch into the base ref.

    Serialized per-repo via file_lock — workers finishing simultaneously
    merge one at a time. On conflict: merge --abort, branch preserved,
    structured failure naming it. Never silently drops work.
    """
    prep = _commit_leftovers(wt.path, wt.branch, wt.base_ref, message)
    if prep is not None:
        return prep
    return _locked_merge(wt.repo_dir, wt.branch, wt.base_ref)


# ---------------------------------------------------------------------------
# Scratch-clone flow — self-development runs under the containerized executor
# (docs/CONTAINER_EXECUTOR_DESIGN.md §4). A live repo is NEVER mounted rw into a
# container; it is cloned into a throwaway scratch the worker edits + commits,
# and merge-back rides the same serialized `_locked_merge` as worktrees.
# ---------------------------------------------------------------------------

def provision_clone(project_dir, name: str, *, loop_id: str) -> Optional[ScratchClone]:
    """Clone `project_dir` into an isolated scratch checkout for one run.

    Returns None (caller falls back to mounting the working dir directly) when
    project_dir isn't a git repo or the clone fails — isolation is an upgrade,
    never a gate. The clone uses `--no-hardlinks` so it shares NO object-store
    inode with the live repo (the container runs as the host uid and could
    otherwise reach shared objects).
    """
    repo_dir = Path(project_dir)
    if not is_git_repo(repo_dir):
        return None

    base_ref = _current_ref(repo_dir)
    if not base_ref:
        log.warning("scratch-clone provision: cannot resolve HEAD of %s", repo_dir)
        return None

    safe_name = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)[:60]
    branch = f"maro/{loop_id}/{safe_name}"
    clone_path = _worktrees_root() / loop_id / f"{safe_name}-clone"
    try:
        clone_path.parent.mkdir(parents=True, exist_ok=True)
        r = _git(["clone", "--no-hardlinks", str(repo_dir), str(clone_path)], repo_dir)
        if r.returncode != 0:
            log.warning(
                "scratch-clone provision failed for %s: %s", repo_dir,
                (r.stderr or r.stdout).strip()[:300],
            )
            return None
        b = _git(["checkout", "-b", branch], clone_path)
        if b.returncode != 0:
            log.warning(
                "scratch-clone branch checkout failed for %s: %s", clone_path,
                (b.stderr or b.stdout).strip()[:300],
            )
            shutil.rmtree(clone_path, ignore_errors=True)
            return None
        # Carry the parent's commit identity into the clone so the worker's
        # in-container commits (and the host-side leftover commit) are attributed
        # — a fresh clone doesn't inherit the source's *local* git config.
        for _key in ("user.name", "user.email"):
            _v = _git(["config", "--get", _key], repo_dir, timeout=15)
            if _v.returncode == 0 and _v.stdout.strip():
                _git(["config", _key, _v.stdout.strip()], clone_path, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("scratch-clone provision error for %s: %s", repo_dir, exc)
        shutil.rmtree(clone_path, ignore_errors=True)
        return None
    log.info("scratch clone provisioned: %s on %s (base %s)", clone_path, branch, base_ref)
    return ScratchClone(path=clone_path, branch=branch, repo_dir=repo_dir, base_ref=base_ref)


def merge_back_clone(clone: ScratchClone, *, message: str = "") -> MergeResult:
    """Merge a scratch clone's work back into the live repo, host-side.

    Commits the worker's leftovers in the clone, then `git fetch`es the clone's
    branch into the parent (separate object stores) and merges it under the same
    per-repo lock as `merge_back`. Conflict/moved-base/dirty-base never drop
    work — the branch is preserved and named in the failure.
    """
    prep = _commit_leftovers(clone.path, clone.branch, clone.base_ref, message)
    if prep is not None:
        return prep
    # Bring the clone's branch (and its objects) into the parent repo, then
    # merge. The fetch creates the local branch ref `_locked_merge` merges.
    try:
        fetch = _git(
            ["fetch", str(clone.path), f"{clone.branch}:{clone.branch}"],
            clone.repo_dir,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return MergeResult(
            ok=False, branch=clone.branch,
            detail=f"fetch from scratch clone failed: {exc}; work preserved in {clone.path}",
        )
    if fetch.returncode != 0:
        return MergeResult(
            ok=False, branch=clone.branch,
            detail=f"fetch from scratch clone failed: "
                   f"{(fetch.stderr or fetch.stdout).strip()[:300]}; "
                   f"work preserved in {clone.path}",
        )
    return _locked_merge(clone.repo_dir, clone.branch, clone.base_ref)


def cleanup_clone(clone: ScratchClone, *, keep_on_failure: bool = False) -> None:
    """Remove the scratch clone (and the branch fetched into the parent).

    keep_on_failure=True leaves both the clone dir and the fetched branch for
    inspection — the failure detail names them, so nothing is lost.
    """
    if keep_on_failure:
        log.warning(
            "scratch clone kept for inspection: %s (branch %s)", clone.path, clone.branch,
        )
        return
    try:
        # The branch was only fetched into the parent on a merge attempt (skipped
        # for the "no changes" path); -D is best-effort and may find nothing.
        b = _git(["branch", "-D", clone.branch], clone.repo_dir, timeout=30)
        if b.returncode != 0:
            log.debug("scratch-clone branch delete: %s", (b.stderr or b.stdout).strip()[:200])
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("scratch-clone branch delete failed: %s", exc)
    shutil.rmtree(clone.path, ignore_errors=True)


def cleanup(wt: Worktree, *, keep_on_failure: bool = False) -> None:
    """Remove the worktree (and its branch on success).

    keep_on_failure=True leaves both worktree and branch for inspection —
    the failure detail names the branch, so nothing is lost.
    """
    if keep_on_failure:
        log.warning(
            "worktree kept for inspection: %s (branch %s)", wt.path, wt.branch,
        )
        return
    try:
        r = _git(["worktree", "remove", "--force", str(wt.path)], wt.repo_dir)
        if r.returncode != 0:
            log.warning("worktree remove failed: %s", (r.stderr or r.stdout).strip()[:200])
        b = _git(["branch", "-D", wt.branch], wt.repo_dir, timeout=30)
        if b.returncode != 0:
            log.debug("branch delete failed: %s", (b.stderr or b.stdout).strip()[:200])
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("worktree cleanup error for %s: %s", wt.path, exc)


def prune(repo_dir) -> None:
    """Best-effort `git worktree prune` at loop finalize."""
    repo = Path(repo_dir)
    if not is_git_repo(repo):
        return
    try:
        _git(["worktree", "prune"], repo, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
