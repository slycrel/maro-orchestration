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

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from process_identity import owner_is_current, pid_alive as process_pid_alive, process_start_token

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

# Owner sidecar — the crash-recovery breadcrumb the stale-clone sweep keys on.
# A SIGKILL between provision and finalize leaks a whole-repo clone under
# worktrees/ with no in-memory ScratchClone to merge it back. The sidecar
# records — HOST-side, at provision time — the trusted fields the sweep needs
# to reconstruct that ScratchClone (owner PID + process birth for liveness,
# live repo, base ref, branch). It lives as a SIBLING of the clone dir, NOT inside it: the container
# mounts only the clone dir (the cwd), so a hostile worker can't tamper with the
# sidecar to redirect the sweep's host-side merge at an arbitrary repo (the same
# never-trust-worker-controlled-repo_dir invariant merge_back_clone already
# holds by using the trusted in-memory ScratchClone, not the clone's .git).
_OWNER_SIDECAR_SUFFIX = ".owner.json"


def _clone_sidecar_path(clone_path) -> Path:
    """Sibling manifest path for a clone dir: `<clone>.owner.json` (outside the
    container-mounted clone dir, so the worker cannot write it)."""
    p = Path(clone_path)
    return p.with_name(p.name + _OWNER_SIDECAR_SUFFIX)


def _write_clone_owner(clone: ScratchClone) -> None:
    """Record the owner breadcrumb next to the clone (best-effort).

    Written LAST, only after provisioning fully succeeds, so a failed
    provision never leaves a sidecar to clean up. A write failure is non-fatal:
    the clone still works; only the sweep loses its recovery metadata and will
    fall back to surfacing (never auto-removing) the clone.
    """
    sidecar = _clone_sidecar_path(clone.path)
    payload = {
        "owner_pid": os.getpid(),
        "owner_start": process_start_token(os.getpid()),
        "repo_dir": str(clone.repo_dir),
        "base_ref": clone.base_ref,
        "branch": clone.branch,
        "created": time.time(),
    }
    try:
        from file_lock import atomic_write
        atomic_write(sidecar, json.dumps(payload))
    except Exception as exc:  # noqa: BLE001 — metadata write must never break a run
        log.warning("scratch-clone owner sidecar write failed for %s: %s", clone.path, exc)


def _read_clone_owner(sidecar: Path) -> Optional[dict]:
    """Load a clone owner sidecar; None if absent/unreadable/malformed."""
    try:
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "owner_pid" not in data:
        return None
    return data


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
    clone = ScratchClone(path=clone_path, branch=branch, repo_dir=repo_dir, base_ref=base_ref)
    # Owner breadcrumb LAST — provisioning fully succeeded, so a leaked clone
    # (crash before finalize) can be recovered by the stale-clone sweep.
    _write_clone_owner(clone)
    log.info("scratch clone provisioned: %s on %s (base %s)", clone_path, branch, base_ref)
    return clone


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
    inspection — the failure detail names them, so nothing is lost. The owner
    sidecar is kept alongside a kept clone so a later sweep still recognizes it.
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
    # Drop the owner breadcrumb alongside the now-removed clone (metadata only —
    # the clone's work already merged back, or there was none).
    _clone_sidecar_path(clone.path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Stale-clone sweep — recover-then-remove, retention-safe
# (CONTAINER_EXECUTOR_DESIGN §9 C3 residual: "Crash-leaked scratch clones. A
# SIGKILL between provision and finalize leaks a whole-repo clone under
# worktrees/"). Rides heartbeat.stranded_state_sweep next to the container reap.
# ---------------------------------------------------------------------------

# Belt-and-suspenders grace: never touch a clone younger than this, even with a
# dead owner PID — guards against racing a clone whose owning run is mid-startup
# (PID briefly not yet visible) or a resume that just re-provisioned. Liveness is
# authoritative; this only narrows the window further. Mirrors the heartbeat
# stranded-run grace.
_CLONE_SWEEP_GRACE_S = 15 * 60


@dataclass
class CloneSweepResult:
    """Outcome of one stale-clone sweep — every clone lands in exactly one list.

    Retention invariant: a clone is REMOVED only when its owner is verifiably
    dead AND its work provably reached the live repo (merged) or provably never
    existed ("no changes"). Every other outcome preserves the clone on disk.
    """
    recovered: list = field(default_factory=list)     # (clone, branch, merged_commit) — merged then removed
    removed_empty: list = field(default_factory=list)  # (clone, branch) — no unmerged work, removed
    preserved: list = field(default_factory=list)      # (clone, branch, reason) — dead owner, work KEPT
    skipped_live: list = field(default_factory=list)   # (clone, owner_pid) — owner still running
    skipped_young: list = field(default_factory=list)  # (clone, owner_pid) — owner dead but within grace
    surfaced: list = field(default_factory=list)       # (clone, reason) — cannot decide, KEPT + logged

    def as_dict(self) -> dict:
        return {
            "recovered": self.recovered,
            "removed_empty": self.removed_empty,
            "preserved": self.preserved,
            "skipped_live": self.skipped_live,
            "skipped_young": self.skipped_young,
            "surfaced": self.surfaced,
        }

    def acted(self) -> bool:
        """Did the sweep do anything worth surfacing to the operator?"""
        return bool(self.recovered or self.preserved or self.surfaced)


def sweep_stranded_clones(
    pid_alive: Optional[Callable[[int], bool]] = None,
    *,
    min_age_s: float = _CLONE_SWEEP_GRACE_S,
    process_token: Optional[Callable[[int], Optional[str]]] = None,
) -> CloneSweepResult:
    """Recover + reap scratch clones leaked by crashed self-dev runs.

    For each clone under `worktrees/` carrying an owner sidecar:

    - **owner PID + birth token match** → skip (never touch in-flight work);
    - **owner dead, clone younger than `min_age_s`** → skip (grace);
    - **owner dead, old enough** → reconstruct the trusted ScratchClone from the
      sidecar and attempt `merge_back_clone` to RECOVER any unmerged work:
        * merged / "no changes" → the work is provably safe → `cleanup_clone`
          removes the throwaway;
        * conflict / moved-base / dirty-base / error → the work is NOT recovered
          → the clone is PRESERVED (branch kept, reason named) — retention wins.

    A clone dir WITHOUT a readable sidecar is only SURFACED (logged), never
    auto-removed: we can't prove whose it is or whether it holds unmerged work.
    Removal happens solely through `cleanup_clone` (the one allowlisted deletion
    site) after a provably-safe merge — this function deletes nothing directly.

    `pid_alive` is injectable for tests; docker/worktrees absent → empty result.
    """
    alive = pid_alive or process_pid_alive
    token_reader = process_token or process_start_token
    result = CloneSweepResult()

    root = _worktrees_root()
    if not root.is_dir():
        return result

    now = time.time()
    seen_dirs: set = set()

    # Sidecar-carrying clones first (the recoverable ones).
    for sidecar in sorted(root.glob(f"*/*{_OWNER_SIDECAR_SUFFIX}")):
        clone_path = sidecar.with_name(sidecar.name[: -len(_OWNER_SIDECAR_SUFFIX)])
        if not clone_path.is_dir():
            # Orphan sidecar (clone already gone) — pure litter, no work at risk.
            # Leave it (removing it would be a second, unnecessary deletion site);
            # it is harmless and a future clone at the same path overwrites it.
            log.debug("stale-clone sweep: orphan owner sidecar (no clone dir): %s", sidecar)
            continue
        seen_dirs.add(str(clone_path.resolve()))

        meta = _read_clone_owner(sidecar)
        if meta is None:
            result.surfaced.append((str(clone_path), "unreadable owner sidecar"))
            log.warning("stale-clone sweep: unreadable owner sidecar for %s — left for inspection", clone_path)
            continue

        owner_pid = meta.get("owner_pid")
        if isinstance(owner_pid, int) and owner_is_current(
                owner_pid, meta.get("owner_start"), alive=alive,
                token_reader=token_reader):
            result.skipped_live.append((str(clone_path), owner_pid))
            continue

        try:
            age = now - clone_path.stat().st_mtime
        except OSError:
            age = min_age_s + 1  # can't stat → don't let a stat error block recovery
        if age < min_age_s:
            # Owner dead but clone too young — wait out the grace before acting
            # (guards against racing a just-provisioned clone / a fast resume).
            result.skipped_young.append((str(clone_path), owner_pid))
            continue

        repo_dir = Path(str(meta.get("repo_dir") or ""))
        base_ref = meta.get("base_ref") or ""
        branch = meta.get("branch") or f"maro/stale/{clone_path.name}"
        if not repo_dir or not is_git_repo(repo_dir) or not base_ref:
            result.surfaced.append((str(clone_path), f"live repo unresolved ({repo_dir}) — cannot merge back"))
            log.warning("stale-clone sweep: %s owner dead but live repo %s unresolved — "
                        "clone preserved for manual recovery", clone_path, repo_dir)
            continue

        clone = ScratchClone(path=clone_path, branch=branch, repo_dir=repo_dir, base_ref=base_ref)
        log.info("stale-clone sweep: owner PID %s dead — attempting merge-back of %s (branch %s)",
                 owner_pid, clone_path, branch)
        try:
            merge = merge_back_clone(clone, message=f"stale-clone recovery: {clone_path.name}")
        except Exception as exc:  # noqa: BLE001 — never let one bad clone abort the sweep
            result.preserved.append((str(clone_path), branch, f"merge-back error: {exc}"))
            log.warning("stale-clone sweep: merge-back errored for %s — preserved: %s", clone_path, exc)
            continue

        if merge.ok and merge.detail == "no changes":
            cleanup_clone(clone)
            result.removed_empty.append((str(clone_path), branch))
            log.info("stale-clone sweep: %s had no unmerged work — removed", clone_path)
        elif merge.ok:
            cleanup_clone(clone)
            result.recovered.append((str(clone_path), branch, merge.merged_commit))
            log.info("stale-clone sweep: recovered work from %s into %s (%s) — removed",
                     clone_path, repo_dir, merge.merged_commit[:12])
        else:
            # Work NOT recovered — keep the clone + its branch, name the reason.
            cleanup_clone(clone, keep_on_failure=True)
            result.preserved.append((str(clone_path), branch, merge.detail))
            log.warning("stale-clone sweep: could not merge %s back — preserved (branch %s): %s",
                        clone_path, branch, merge.detail)

    # Sidecar-less clone dirs (crashed before the sidecar landed, or pre-sweep
    # leaks): SURFACE only. Without the trusted breadcrumb we can't prove
    # ownership/liveness or safely target a merge — retention forbids removing.
    for clone_dir in sorted(root.glob("*/*-clone")):
        if not clone_dir.is_dir() or str(clone_dir.resolve()) in seen_dirs:
            continue
        result.surfaced.append((str(clone_dir), "no owner sidecar — cannot recover automatically"))
        log.warning("stale-clone sweep: clone %s has no owner sidecar — left for manual inspection "
                    "(cannot prove ownership or unmerged-work state)", clone_dir)

    return result


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
