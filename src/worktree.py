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


def merge_back(wt: Worktree, *, message: str = "") -> MergeResult:
    """Commit the worker's leftovers and merge its branch into the base ref.

    Serialized per-repo via file_lock — workers finishing simultaneously
    merge one at a time. On conflict: merge --abort, branch preserved,
    structured failure naming it. Never silently drops work.
    """
    branch = wt.branch
    # 1. Commit any uncommitted agent work in the worktree.
    try:
        r = _git(["status", "--porcelain"], wt.path, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            _git(["add", "-A"], wt.path)
            c = _git(["commit", "-m", message or f"wt: {branch}"], wt.path)
            if c.returncode != 0:
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"autocommit failed: {(c.stderr or c.stdout).strip()[:300]}",
                )
        # Nothing to merge at all? (no commits and clean tree)
        ahead = _git(["rev-list", "--count", f"{wt.base_ref}..{branch}"], wt.path, timeout=30)
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            return MergeResult(ok=True, branch=branch, detail="no changes")
    except (OSError, subprocess.SubprocessError) as exc:
        return MergeResult(ok=False, branch=branch, detail=f"autocommit error: {exc}")

    # 2. Merge into the base ref, one worker at a time.
    from file_lock import locked_write
    try:
        with locked_write(_merge_lock_path(wt.repo_dir)):
            cur = _current_ref(wt.repo_dir)
            if cur != wt.base_ref:
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"repo moved off base ref ({wt.base_ref} -> {cur}); "
                           f"work preserved on {branch}",
                )
            dirty = _git(["status", "--porcelain"], wt.repo_dir, timeout=30)
            if dirty.returncode == 0 and dirty.stdout.strip():
                # Merging into a dirty checkout risks entangling the user's
                # in-flight edits with the merge — keep the branch instead.
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"base checkout dirty; work preserved on {branch}",
                )
            m = _git(
                ["merge", "--no-ff", branch, "-m", f"merge {branch}"],
                wt.repo_dir,
            )
            if m.returncode != 0:
                _git(["merge", "--abort"], wt.repo_dir)
                return MergeResult(
                    ok=False, conflict=True, branch=branch,
                    detail=f"merge conflict; work preserved on {branch}: "
                           f"{(m.stderr or m.stdout).strip()[:300]}",
                )
            sha = _git(["rev-parse", "HEAD"], wt.repo_dir, timeout=15)
            return MergeResult(
                ok=True, branch=branch,
                merged_commit=sha.stdout.strip() if sha.returncode == 0 else "",
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return MergeResult(ok=False, branch=branch, detail=f"merge error: {exc}")


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
