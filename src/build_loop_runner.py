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
import re
import signal
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from orch import (
    _load_run_records,
    finalize_run,
    run_loop,
    select_global_next,
    select_next_item,
    worker_session_bridge,
)
from orch_items import now_utc_iso, orch_root, output_root, relative_display_path
from orch_bridges import resolve_worker_session_spec


# Agenda-heavy handle worker sessions routinely exceed 15 minutes when they
# complete multiple artifact-writing steps plus closure/quality passes.
DEFAULT_BUILD_LOOP_SESSION_TIMEOUT_SECONDS = 1800.0


class BuildLoopInterrupted(RuntimeError):
    """Raised when the build loop receives a termination signal."""

    def __init__(self, signum: int):
        self.signum = int(signum)
        sig = signal.Signals(signum)
        super().__init__(f"received {sig.name}")


def build_loop_status_path() -> Path:
    return output_root() / "build-loop-status.json"


def build_loop_lock_path() -> Path:
    return output_root() / "build-loop.lock"


def heartbeat_runs_root() -> Path:
    return output_root() / "heartbeat" / "runs"


def _write_status(payload: dict) -> dict:
    path = build_loop_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _status_exists() -> bool:
    return build_loop_status_path().exists()


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return cleaned or "run"


def _build_loop_session_timeout_seconds() -> float:
    raw = os.environ.get("POE_BUILD_LOOP_SESSION_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_BUILD_LOOP_SESSION_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BUILD_LOOP_SESSION_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_BUILD_LOOP_SESSION_TIMEOUT_SECONDS
    return value


def _worker_session_already_active(worker_session: str) -> bool:
    spec = resolve_worker_session_spec(worker_session)
    if spec is None:
        return False

    command = spec.command.strip()
    if not command:
        return False

    try:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", command],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return False

    if result.returncode != 0 or not result.stdout.strip():
        return False

    current_pid = os.getpid()
    parent_pid = os.getppid()
    for raw_pid in result.stdout.splitlines():
        raw_pid = raw_pid.strip()
        if not raw_pid:
            continue
        try:
            pid = int(raw_pid)
        except ValueError:
            continue
        if pid in {current_pid, parent_pid}:
            continue
        return True
    return False


def _write_heartbeat_run(summary: dict) -> str:
    runs_dir = heartbeat_runs_root()
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_utc_iso().replace(":", "").replace("-", "")
    summary_json = json.dumps(summary, indent=2) + "\n"
    selected_project = summary.get("selected_project") or summary.get("project") or "global"
    status = str(summary.get("status") or "unknown")
    path = runs_dir / f"{stamp}-{_safe_filename(status)}-{_safe_filename(str(selected_project))}.json"
    payload = {
        "generated_at": now_utc_iso(),
        "duration_seconds": summary.get("duration_seconds"),
        "status": summary.get("status"),
        "reason": summary.get("reason"),
        "project": summary.get("project"),
        "selected_project": summary.get("selected_project"),
        "worker": summary.get("worker"),
        "worker_session": summary.get("worker_session"),
        "runs": summary.get("runs"),
        "validation_statuses": summary.get("validation_statuses"),
        "run_ids": summary.get("run_ids"),
        "items": summary.get("items"),
        "exit_code": 0,
        "stdout_excerpt": summary_json[-4000:],
        "stderr_excerpt": "",
        "status_path": relative_display_path(build_loop_status_path()),
        "orch_root": summary.get("orch_root"),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return relative_display_path(path)


@contextmanager
def _interrupt_guard() -> Iterator[None]:
    handlers: dict[int, object] = {}

    def _handle(signum, _frame):
        raise BuildLoopInterrupted(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        handlers[int(sig)] = signal.getsignal(sig)
        signal.signal(sig, _handle)
    try:
        yield
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def _cleanup_running_build_loop_runs(note: str) -> None:
    for record in _load_run_records():
        if record.status != "running" or record.source != "build-loop":
            continue
        try:
            finalize_run(record.run_id, "blocked", note=note)
        except ValueError:
            continue


@contextmanager
def _temporary_env(name: str, value: Optional[str]) -> Iterator[None]:
    previous = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


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
    started_wall = os.times().elapsed
    if not any(os.environ.get(var) for var in ("POE_ORCH_ROOT", "POE_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT")):
        os.environ.setdefault("POE_ORCH_ROOT", str(orch_root()))
    started_at = now_utc_iso()

    def finalize(summary: dict, *, write_status: bool = True) -> dict:
        summary["duration_seconds"] = round(max(0.0, os.times().elapsed - started_wall), 3)
        heartbeat_record = _write_heartbeat_run(summary)
        summary["heartbeat_run_path"] = heartbeat_record
        if write_status:
            return _write_status(summary)
        return summary

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
        return finalize(
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

    if _worker_session_already_active(worker_session):
        return finalize(
            {
                "status": "busy",
                "reason": "worker_session_active",
                "started_at": started_at,
                "finished_at": now_utc_iso(),
                "project": project,
                "selected_project": next_project,
                "worker": worker,
                "worker_session": worker_session,
                "runs": 0,
                "orch_root": str(orch_root()),
            }
        )

    with _try_lock(build_loop_lock_path()) as acquired:
        if not acquired:
            return finalize(
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
                },
                write_status=not _status_exists(),
            )

        _write_status(
            {
                "status": "running",
                "reason": "lock_acquired",
                "started_at": started_at,
                "finished_at": None,
                "project": project,
                "selected_project": next_project,
                "worker": worker,
                "worker_session": worker_session,
                "runs": 0,
                "orch_root": str(orch_root()),
            }
        )

        # The dedicated build loop is an unattended execution path. Force the
        # handle worker into YOLO mode so ambiguity checks do not dead-end on
        # tasks generated by orchestration itself.
        try:
            with _interrupt_guard(), _temporary_env("POE_YOLO", "true"):
                ticks = run_loop(
                    project=project,
                    worker=worker,
                    source="build-loop",
                    max_runs=max_runs,
                    max_retry_streak=max_retry_streak,
                    max_attempts_per_item=max_attempts_per_item,
                    execution=worker_session_bridge(
                        worker_session,
                        timeout_seconds=_build_loop_session_timeout_seconds(),
                    ),
                    continue_on_retry=continue_on_retry,
                    continue_on_blocked=continue_on_blocked,
                )
        except (BuildLoopInterrupted, KeyboardInterrupt) as exc:
            if isinstance(exc, BuildLoopInterrupted):
                reason = signal.Signals(exc.signum).name.lower()
            else:
                reason = "keyboard_interrupt"
            note = f"build loop interrupted: {reason}"
            _cleanup_running_build_loop_runs(note)
            return finalize(
                {
                    "status": "interrupted",
                    "reason": reason,
                    "started_at": started_at,
                    "finished_at": now_utc_iso(),
                    "project": project,
                    "selected_project": next_project,
                    "worker": worker,
                    "worker_session": worker_session,
                    "runs": 0,
                    "orch_root": str(orch_root()),
                }
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
        return finalize(summary)


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
    if summary.get("status") == "interrupted":
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
