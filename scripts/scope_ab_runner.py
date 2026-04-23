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

    # 4. Snapshot captain's log offset + start timestamp.
    log_offset_start = CAPTAINS_LOG.stat().st_size if CAPTAINS_LOG.exists() else 0
    started = datetime.now(timezone.utc).isoformat()

    # 5. Run handle.py foreground (long-running).
    prompt_file = REPO / "scripts" / "blind-test-slycrel" / "prompt.txt"
    prompt = prompt_file.read_text().strip()
    env = os.environ.copy()
    env["POE_LOG_LEVEL"] = "INFO"
    env["PYTHONPATH"] = str(REPO / "src")

    handle_log = out / "handle.log"
    print(f"[scope-ab] launching handle.py (log: {handle_log})")
    with handle_log.open("wb") as f:
        rc = subprocess.call(
            ["python3", "-u", "-m", "handle", prompt],
            cwd=str(REPO), env=env, stdout=f, stderr=subprocess.STDOUT,
        )
    ended = datetime.now(timezone.utc).isoformat()

    # 6. Copy the project workspace the run just produced.
    if PROJECT_DIR.exists():
        shutil.copytree(PROJECT_DIR, out / "project_workspace", dirs_exist_ok=True)

    # 7. Captain's log slice covering this run.
    if CAPTAINS_LOG.exists():
        with CAPTAINS_LOG.open("rb") as src, (out / "captains_log_slice.jsonl").open("wb") as dst:
            src.seek(log_offset_start)
            shutil.copyfileobj(src, dst)

    # 8. Metadata.
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
    }, indent=2) + "\n")

    print(f"[scope-ab] run={run_num:02d}-{arm} done rc={rc} -> {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
