#!/usr/bin/env python3
"""Scope A/B experiment runner for slycrel-go blind test.

Runs the blind-test harness with `scope_ab_skip` flipped per arm:
  - treat:   scope_generation=true, scope_ab_skip=false  (scope injected into plan)
  - control: scope_generation=true, scope_ab_skip=true   (scope recorded, NOT injected)

Both arms generate the scope so we can compare what it would have said.
Artifacts land at ~/.poe/experiments/scope-ab-<DATE>/run-NN-<arm>/ with:
  - handle.log                 full stdout/stderr
  - project_workspace/         ~/.poe/workspace/projects/<slug>/ snapshot
  - captains_log_slice.jsonl   captain's log events during this run
  - config.yml                 snapshot of the config used
  - metadata.json              arm, timings, rc, prompt

Usage: scope_ab_runner.py --arm treat|control --run N [--exp-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = Path.home() / ".poe" / "config.yml"
CAPTAINS_LOG = Path.home() / ".poe" / "workspace" / "memory" / "captains_log.jsonl"
PROJECT_SLUG = "ive-set-up-a-working"  # derived from prompt.txt
PROJECT_DIR = Path.home() / ".poe" / "workspace" / "projects" / PROJECT_SLUG
DEFAULT_EXP_DIR = Path.home() / ".poe" / "experiments" / "scope-ab-2026-04-22"
TEST_REPO_DIR = Path("/tmp/slycrel-blind-run/repo")


def set_scope_flags(ab_skip: bool) -> None:
    """Line-surgical flip of scope_ab_skip in ~/.poe/config.yml; preserves comments."""
    text = CONFIG.read_text()
    lines = text.splitlines()
    target = f"scope_ab_skip: {'true' if ab_skip else 'false'}"
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("scope_ab_skip:"):
            lines[i] = target
            replaced = True
            break
    if not replaced:
        for i, line in enumerate(lines):
            if line.strip().startswith("scope_generation:"):
                lines.insert(i + 1, target)
                replaced = True
                break
    if not replaced:
        lines.append(target)
    CONFIG.write_text("\n".join(lines) + "\n")


def archive_existing_workspace(run_dir: Path) -> None:
    """Move any prior project workspace to a stamped archive so runs start clean."""
    if not PROJECT_DIR.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = PROJECT_DIR.with_name(PROJECT_DIR.name + f".archive-{ts}")
    PROJECT_DIR.rename(archive)
    (run_dir / "archived_prior_workspace.txt").write_text(str(archive) + "\n")


def prepare_run_branch(run_num: int, arm: str) -> tuple[str, str]:
    """Create a uniquely-named branch in the test repo and return (branch, base_sha).

    Front-loads the branch into the test repo so the agent commits its work to
    a known, isolated ref. The base_sha is captured before any changes so we
    can diff at end-of-run.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    branch = f"scope-ab-r{run_num:02d}-{arm}-{ts}"
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(TEST_REPO_DIR), text=True,
    ).strip()
    subprocess.check_call(
        ["git", "checkout", "-b", branch], cwd=str(TEST_REPO_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    return branch, base_sha


def snapshot_test_repo(run_dir: Path, branch: str, base_sha: str) -> None:
    """Capture the test repo state into the run dir before the next run wipes it.

    Writes:
      - repo.bundle    — `git bundle --all`, restorable via `git clone repo.bundle`
      - git_log.txt    — `git log --all --graph --oneline` for at-a-glance history
      - branch_diff.patch  — `git diff <base>..HEAD` showing what this run produced
      - branch.txt     — name of the branch the run committed to
    """
    if not TEST_REPO_DIR.exists():
        (run_dir / "repo_snapshot_skipped.txt").write_text(
            f"test repo missing: {TEST_REPO_DIR}\n"
        )
        return
    cwd = str(TEST_REPO_DIR)
    (run_dir / "branch.txt").write_text(branch + "\n")
    try:
        subprocess.check_call(
            ["git", "bundle", "create", str(run_dir / "repo.bundle"), "--all"],
            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        (run_dir / "repo.bundle.failed").write_text(f"{exc}\n")
    try:
        log_out = subprocess.check_output(
            ["git", "log", "--all", "--graph", "--oneline", "--decorate"],
            cwd=cwd, text=True,
        )
        (run_dir / "git_log.txt").write_text(log_out)
    except subprocess.CalledProcessError as exc:
        (run_dir / "git_log.txt").write_text(f"git log failed: {exc}\n")
    try:
        diff_out = subprocess.check_output(
            ["git", "diff", f"{base_sha}..HEAD"], cwd=cwd, text=True,
        )
        (run_dir / "branch_diff.patch").write_text(diff_out)
    except subprocess.CalledProcessError as exc:
        (run_dir / "branch_diff.patch").write_text(f"git diff failed: {exc}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["treat", "control"])
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--exp-dir", type=Path, default=DEFAULT_EXP_DIR)
    args = ap.parse_args()

    arm: str = args.arm
    run_num: int = args.run
    exp_dir: Path = args.exp_dir
    out = exp_dir / f"run-{run_num:02d}-{arm}"
    out.mkdir(parents=True, exist_ok=True)

    ab_skip = (arm == "control")
    print(f"[scope-ab] run={run_num:02d} arm={arm} ab_skip={ab_skip} out={out}")

    # 1. Flip config.
    set_scope_flags(ab_skip)
    (out / "config.yml").write_text(CONFIG.read_text())

    # 2. Sterilize repo (blind-test-slycrel.sh --setup-only).
    setup_log = out / "setup.log"
    with setup_log.open("wb") as f:
        rc = subprocess.call(
            [str(REPO / "scripts" / "blind-test-slycrel.sh"), "--setup-only"],
            cwd=str(REPO), stdout=f, stderr=subprocess.STDOUT,
        )
    if rc != 0:
        print(f"[scope-ab] setup failed rc={rc}; see {setup_log}", file=sys.stderr)
        return rc

    # 3. Archive the old project workspace (harness only handles the old slug).
    archive_existing_workspace(out)

    # 4. Front-load a per-run branch into the test repo so the agent's commits
    #    land on a known, isolated ref. Captured at end-of-run via git bundle.
    branch, base_sha = prepare_run_branch(run_num, arm)
    print(f"[scope-ab] branch={branch} base={base_sha[:8]}")

    # 5. Snapshot captain's log offset + start timestamp.
    log_offset_start = CAPTAINS_LOG.stat().st_size if CAPTAINS_LOG.exists() else 0
    started = datetime.now(timezone.utc).isoformat()

    # 6. Run handle.py foreground (long-running). Prompt names the branch so
    #    the agent doesn't create its own (`headless-server` etc.) and we know
    #    where to find the work.
    prompt_file = REPO / "scripts" / "blind-test-slycrel" / "prompt.txt"
    base_prompt = prompt_file.read_text().strip()
    prompt = (
        f"{base_prompt}\n\n"
        f"Working branch: `{branch}` (already created and checked out in the repo). "
        f"Commit all changes to this branch — do not create another."
    )
    env = os.environ.copy()
    env["POE_LOG_LEVEL"] = "INFO"
    env["PYTHONPATH"] = str(REPO / "src")

    handle_log = out / "handle.log"
    print(f"[scope-ab] launching handle.py (log: {handle_log})")
    # --repo points handle.py at the *test* repo so closure (verify_goal_completion)
    # runs git log / find / grep against the work-tree the agent edited, not the
    # openclaw repo cwd. Without this, closure checks ran 0/8 even for a clean
    # treat run because they were probing the wrong directory (2026-04-26).
    with handle_log.open("wb") as f:
        rc = subprocess.call(
            ["python3", "-u", "-m", "handle", prompt, "--repo", str(TEST_REPO_DIR)],
            cwd=str(REPO), env=env, stdout=f, stderr=subprocess.STDOUT,
        )
    ended = datetime.now(timezone.utc).isoformat()

    # 7. Copy the project workspace the run just produced.
    if PROJECT_DIR.exists():
        shutil.copytree(PROJECT_DIR, out / "project_workspace", dirs_exist_ok=True)

    # 8. Captain's log slice covering this run.
    if CAPTAINS_LOG.exists():
        with CAPTAINS_LOG.open("rb") as src, (out / "captains_log_slice.jsonl").open("wb") as dst:
            src.seek(log_offset_start)
            shutil.copyfileobj(src, dst)

    # 9. Snapshot the test repo (bundle + log + diff) before the next run wipes it.
    snapshot_test_repo(out, branch, base_sha)

    # 10. Metadata.
    (out / "metadata.json").write_text(json.dumps({
        "arm": arm,
        "run_num": run_num,
        "started_utc": started,
        "ended_utc": ended,
        "return_code": rc,
        "prompt": prompt,
        "scope_generation": True,
        "scope_ab_skip": ab_skip,
        "captains_log_offset_start": log_offset_start,
        "project_slug": PROJECT_SLUG,
        "branch": branch,
        "base_sha": base_sha,
    }, indent=2) + "\n")

    print(f"[scope-ab] run={run_num:02d}-{arm} done rc={rc} -> {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
