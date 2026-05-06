#!/usr/bin/env python3
"""Dedicated autonomous build-loop runner for poe-orchestration.

This is the thing the cron should wake, instead of poking a generic reminder
session and hoping it opportunistically does useful work.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from orch import run_loop, select_global_next, select_next_item, worker_session_bridge
from orch_items import now_utc_iso, orch_root, output_root


def build_loop_status_path() -> Path:
    return output_root() / "build-loop-status.json"


def build_loop_lock_path() -> Path:
    return output_root() / "build-loop.lock"


def _write_status(payload: dict) -> dict:
    path = build_loop_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


@contextmanager
def _try_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} started_at={now_utc_iso()}\n")
        fh.flush()
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def run_build_loop(
    *,
    project: Optional[str] = None,
    worker: str = "handle",
    worker_session: str = "handle",
    max_runs: int = 8,
    max_retry_streak: Optional[int] = 2,
    max_attempts_per_item: Optional[int] = 3,
    continue_on_retry: bool = True,
    continue_on_blocked: bool = False,
) -> dict:
    started_at = now_utc_iso()
    next_item = select_next_item(project) if project else None
    if project is None:
        global_next = select_global_next()
        if global_next is not None:
            next_project, next_item = global_next
        else:
            next_project = None
    else:
        next_project = project if next_item else None

    if next_item is None:
        return _write_status(
            {
                "status": "idle",
                "reason": "no_work",
                "started_at": started_at,
                "finished_at": now_utc_iso(),
                "project": next_project,
                "worker": worker,
                "worker_session": worker_session,
                "runs": 0,
                "orch_root": str(orch_root()),
            }
        )

    with _try_lock(build_loop_lock_path()) as acquired:
        if not acquired:
            return _write_status(
                {
                    "status": "busy",
                    "reason": "lock_held",
                    "started_at": started_at,
                    "finished_at": now_utc_iso(),
                    "project": next_project,
                    "worker": worker,
                    "worker_session": worker_session,
                    "runs": 0,
                    "orch_root": str(orch_root()),
                }
            )

        ticks = run_loop(
            project=project,
            worker=worker,
            source="build-loop",
            max_runs=max_runs,
            max_retry_streak=max_retry_streak,
            max_attempts_per_item=max_attempts_per_item,
            execution=worker_session_bridge(worker_session),
            continue_on_retry=continue_on_retry,
            continue_on_blocked=continue_on_blocked,
        )

        statuses = [tick.validation.status for tick in ticks]
        summary = {
            "status": "ok" if ticks else "idle",
            "reason": "ran" if ticks else "no_claimable_work",
            "started_at": started_at,
            "finished_at": now_utc_iso(),
            "project": project,
            "selected_project": next_project,
            "worker": worker,
            "worker_session": worker_session,
            "runs": len(ticks),
            "validation_statuses": statuses,
            "run_ids": [tick.run.run_id for tick in ticks],
            "items": [
                {
                    "project": tick.run.project,
                    "index": tick.run.index,
                    "attempt": tick.run.attempt,
                    "status": tick.validation.status,
                }
                for tick in ticks
            ],
            "orch_root": str(orch_root()),
        }
        return _write_status(summary)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the dedicated poe-orchestration build loop")
    parser.add_argument("--project")
    parser.add_argument("--worker", default="handle")
    parser.add_argument("--worker-session", default="handle")
    parser.add_argument("--max-runs", type=int, default=8)
    parser.add_argument("--max-retry-streak", type=int, default=2)
    parser.add_argument("--max-attempts-per-item", type=int, default=3)
    parser.add_argument("--no-continue-on-retry", action="store_true")
    parser.add_argument("--continue-on-blocked", action="store_true")
    parser.add_argument("--format", choices=["json", "path"], default="json")
    args = parser.parse_args(argv)

    summary = run_build_loop(
        project=args.project,
        worker=args.worker,
        worker_session=args.worker_session,
        max_runs=args.max_runs,
        max_retry_streak=args.max_retry_streak,
        max_attempts_per_item=args.max_attempts_per_item,
        continue_on_retry=not args.no_continue_on_retry,
        continue_on_blocked=args.continue_on_blocked,
    )
    if args.format == "path":
        print(build_loop_status_path())
    else:
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
