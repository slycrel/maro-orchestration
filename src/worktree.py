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


def _git_hard(args: list, cwd: Path, *, timeout: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """`_git` with hooks + fsmonitor disabled — for HOST-side git run against an
    untrusted (worker-controlled) scratch clone. Command-line `-c` beats the
    clone's own `.git/config`, so a planted `core.hooksPath`/`core.fsmonitor`
    can't redirect execution (adversarial-review 2026-07-13, findings C/M1/A3)."""
    return _git(["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", *args],
                cwd, timeout=timeout)


# Local git config keys that can execute a command — a worker with rw on a
# scratch clone's .git could plant these to run code on our host-side git.
_EXEC_CONFIG_KEYS = {
    "core.fsmonitor", "core.sshcommand", "core.pager", "core.editor",
    "core.askpass", "core.hookspath", "uploadpack.packobjectshook",
    "sequence.editor", "credential.helper",
}


def _sanitize_untrusted_git(work_dir: Path) -> None:
    """Neutralize a worker-controlled clone's git control plane BEFORE any
    host-side git command touches it (design §4; adversarial-review 2026-07-13,
    findings C/M1/A3). By finalize the container has exited, so nothing races us.

    Removes planted hooks and strips exec-capable local config (fsmonitor,
    ssh/pager/editor, aliases which can be `!shell`, filter clean/smudge which
    fire on `git add`, uploadpack.packObjectsHook which fires on the merge-back
    `git fetch`, credential helpers, textconv). With the filter config gone, a
    hostile in-tree `.gitattributes` referencing a filter is inert (no matching
    driver). Belt-and-suspenders with _git_hard's command-line overrides.
    """
    gitdir = Path(work_dir) / ".git"
    try:
        shutil.rmtree(gitdir / "hooks", ignore_errors=True)
    except OSError:
        pass
    try:
        listing = _git(["config", "--local", "--list", "--name-only"], work_dir, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return
    if listing.returncode != 0:
        return
    for raw in listing.stdout.splitlines():
        key = raw.strip()
        k = key.lower()
        if not k:
            continue
        if (k in _EXEC_CONFIG_KEYS
                or k.startswith("filter.")
                or k.startswith("alias.")
                or k.endswith(".command")
                or k.endswith(".process")
                or k.endswith("hook")
                or k.endswith(".textconv")
                or k.endswith(".helper")):
            try:
                _git(["config", "--local", "--unset-all", key], work_dir, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass


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


def _commit_dirty(work_dir: Path, branch: str, message: str, *, git=_git) -> Optional[MergeResult]:
    """Commit any uncommitted work in `work_dir` to its CURRENT HEAD.

    Returns a terminal MergeResult ONLY on failure (caller retains the source) —
    every git-command failure is a failure, never silently treated as "clean" or
    "no changes" (adversarial-review 2026-07-13, finding S3: a swallowed status
    error could lead to deleting the only object store). None on success. `git`
    lets the caller pass the hardened runner for an untrusted clone.
    """
    try:
        r = git(["status", "--porcelain"], work_dir, timeout=30)
        if r.returncode != 0:
            return MergeResult(
                ok=False, branch=branch,
                detail=f"cannot read status; work preserved: {(r.stderr or r.stdout).strip()[:200]}",
            )
        if r.stdout.strip():
            a = git(["add", "-A"], work_dir)
            if a.returncode != 0:
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"git add failed; work preserved: {(a.stderr or a.stdout).strip()[:200]}",
                )
            c = git(["commit", "-m", message or f"wt: {branch}"], work_dir)
            if c.returncode != 0:
                return MergeResult(
                    ok=False, branch=branch,
                    detail=f"autocommit failed: {(c.stderr or c.stdout).strip()[:300]}",
                )
    except (OSError, subprocess.SubprocessError) as exc:
        return MergeResult(ok=False, branch=branch, detail=f"autocommit error: {exc}")
    return None


def _commit_leftovers(work_dir: Path, branch: str, base_ref: str, message: str) -> Optional[MergeResult]:
    """Commit leftovers, then report whether `branch` is ahead of `base_ref`.

    Returns a terminal MergeResult (autocommit failure, or `ok "no changes"`) or
    None when there ARE commits to merge. Used by the worktree path, where the
    worker stays on `branch`.
    """
    fail = _commit_dirty(work_dir, branch, message)
    if fail is not None:
        return fail
    try:
        ahead = _git(["rev-list", "--count", f"{base_ref}..{branch}"], work_dir, timeout=30)
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            return MergeResult(ok=True, branch=branch, detail="no changes")
    except (OSError, subprocess.SubprocessError) as exc:
        return MergeResult(ok=False, branch=branch, detail=f"ahead-check error: {exc}")
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

    # The clone captures COMMITTED state only; warn if the source has
    # uncommitted work (the worker won't see it, and merge-back later refuses a
    # dirty parent) so the surprise is visible (adversarial-review 2026-07-13, S5).
    try:
        d = _git(["status", "--porcelain"], repo_dir, timeout=30)
        if d.returncode == 0 and d.stdout.strip():
            log.warning("scratch-clone: source %s has uncommitted changes — the clone "
                        "sees only committed state and merge-back will refuse a dirty "
                        "parent; commit or stash before a containerized self-dev run", repo_dir)
    except (OSError, subprocess.SubprocessError):
        pass

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
            shutil.rmtree(clone_path, ignore_errors=True)  # drop any partial dest
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

    Steps: (1) neutralize the worker-controlled clone's git control plane so our
    host-side git can't be hijacked (findings C/M1/A3); (2) commit leftovers to
    the clone's CURRENT HEAD; (3) resolve what to merge from that ACTUAL HEAD —
    NOT an assumed branch name — so a worker that switched branches inside the
    container isn't silently treated as "no changes" and deleted (finding S3);
    (4) `git fetch` that commit into the parent and merge under the same per-repo
    lock as `merge_back`. Conflict/moved-base/dirty-base never drop work — the
    branch is preserved and named in the failure. All clone-side git runs
    hardened (hooks/fsmonitor disabled).
    """
    _sanitize_untrusted_git(clone.path)

    fail = _commit_dirty(clone.path, clone.branch, message, git=_git_hard)
    if fail is not None:
        return fail

    head = _git_hard(["rev-parse", "HEAD"], clone.path, timeout=15)
    if head.returncode != 0 or not head.stdout.strip():
        return MergeResult(
            ok=False, branch=clone.branch,
            detail=f"cannot resolve clone HEAD; work preserved in {clone.path}",
        )
    head_sha = head.stdout.strip()

    ahead = _git_hard(["rev-list", "--count", f"{clone.base_ref}..{head_sha}"], clone.path, timeout=30)
    if ahead.returncode == 0 and ahead.stdout.strip() == "0":
        return MergeResult(ok=True, branch=clone.branch, detail="no changes")
    # ahead rc != 0 → uncertain: fall through and attempt the merge rather than
    # declaring "no changes" (fail safe — never drop possibly-real work).

    # Bring the clone's actual HEAD commit into the parent under the branch name,
    # then merge. The fetch creates the local branch ref `_locked_merge` merges.
    try:
        fetch = _git_hard(
            ["fetch", str(clone.path), f"{head_sha}:{clone.branch}"],
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


# Stranded scratch-clone detection (C4 residual). A SIGKILL between
# provision_clone and finalize's cleanup_clone leaks a whole-repo scratch clone
# under worktrees/ that nothing else reclaims (`prune` handles git worktrees,
# not these). `surface_stranded_clones` SURFACES those for the operator — it
# never auto-deletes and never runs git inside the clone.
#
# Why detection-only, not auto-reclaim (adversarial review 2026-07-13, unanimous
# REJECT of an earlier reclaim-empty design): a scratch clone is entirely
# WORKER-CONTROLLED — its files, `.gitignore`/`.git/info/exclude`, `.git/refs`
# and `.git/config` are all writable by a possibly prompt-injected container
# worker. Two consequences make auto-reclaim unsafe:
#   1. Running git against it to classify it executes planted config
#      (core.fsmonitor, hooks, ...) on the HOST — the exact RCE that _git_hard /
#      _sanitize_untrusted_git exist to stop.
#   2. NO content check can prove "empty": ignored files, skip-worktree, data
#      under .git, commits on another local branch, or a rewritten
#      refs/remotes/origin all hide real worker bytes from `git status` /
#      `rev-list`. Age is not ownership either — a live run can compute for >24h
#      without touching a tracked path.
# The only retention-safe action on an untrusted directory that MIGHT hold
# unmerged work is to surface it and let the operator decide (retention decree,
# 2026-07-10: "the system never decides it's clutter"). Auto-reclaiming disk
# would require a hardened recover-then-remove (merge_back_clone, which already
# sanitizes + rescues the work, then cleanup_clone) — a live-repo mutation from a
# background heartbeat that is Jeremy's call, not a silent default.
_STRANDED_CLONE_GRACE_SECS = 24 * 3600


def _clone_mtime(clone_dir: Path) -> float:
    """Newest mtime across the clone dir + its git HEAD/index (host-side stat
    only — never runs git). HEAD/index move on every worker commit and
    `git add`, so an in-flight or mid-finalize clone always reads recent even
    when the checkout root's own mtime is stale."""
    newest = 0.0
    for p in (clone_dir, clone_dir / ".git" / "HEAD", clone_dir / ".git" / "index"):
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            pass
    return newest


def surface_stranded_clones(*, grace_secs: int = _STRANDED_CLONE_GRACE_SECS) -> dict:
    """Detect (never delete) crash-leaked scratch clones for operator review.

    Scans `worktrees/*/*-clone`. A clone whose newest mtime is older than
    `grace_secs` (age is a clone's only host-visible liveness signal, mirroring
    heartbeat's no-PID stranded grace) is reported as stranded. Nothing is
    deleted and NO git runs inside the worker-controlled clone; the branch name
    (`maro/<loop_id>/<name>`) is derived from the host-side path, so the operator
    can inspect the clone and merge its branch or remove it by hand.

    Warns once per clone via a host-side `.surfaced` marker in the clone's PARENT
    dir (which the container never mounts — the worker cannot forge or clear it),
    so a standing leak doesn't re-warn on every heartbeat.

    Returns {"stranded": [{"path","branch","age_hours"}], "skipped_recent": N}.
    """
    import time

    root = _worktrees_root()
    stranded: list = []
    skipped = 0
    if not root.exists():
        return {"stranded": stranded, "skipped_recent": skipped}
    now = time.time()
    for loop_dir in sorted(root.iterdir()):
        if not loop_dir.is_dir():
            continue
        for clone_dir in sorted(loop_dir.glob("*-clone")):
            if not clone_dir.is_dir():
                continue
            try:
                age = now - _clone_mtime(clone_dir)
                if age < grace_secs:
                    skipped += 1
                    continue
                name = clone_dir.name[:-len("-clone")] if clone_dir.name.endswith("-clone") else clone_dir.name
                entry = {
                    "path": str(clone_dir),
                    "branch": f"maro/{loop_dir.name}/{name}",
                    "age_hours": round(age / 3600.0, 1),
                }
                stranded.append(entry)
                marker = loop_dir / f".{clone_dir.name}.surfaced"
                if not marker.exists():
                    log.warning(
                        "stranded scratch clone (age %.0fh): %s (branch %s). Nothing "
                        "auto-deleted — inspect it, merge its branch if it holds work, "
                        "else remove it. See docs/CONTAINER_EXECUTOR_DESIGN.md §4.",
                        entry["age_hours"], clone_dir, entry["branch"],
                    )
                    try:
                        marker.write_text("surfaced\n", encoding="utf-8")
                    except OSError:
                        pass
            except Exception as exc:
                log.debug("stranded-clone surface skipped %s: %s", clone_dir, exc)
    return {"stranded": stranded, "skipped_recent": skipped}


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
