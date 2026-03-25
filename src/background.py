#!/usr/bin/env python3
"""Phase 10: Background execution primitive for Poe orchestration.

Non-blocking subprocess runner with polling. Start a long-running command,
return immediately, poll/wait for result asynchronously.

Usage:
    from background import start_background, poll_background, wait_background
    task = start_background("sleep 5 && echo done")
    task = wait_background(task.id, timeout_seconds=10)
    print(task.status, task.exit_code)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BackgroundTask:
    id: str               # uuid[:8]
    command: str          # shell command that was run
    pid: int
    status: str           # "running" | "done" | "failed" | "timeout"
    started_at: str
    completed_at: Optional[str] = None
    exit_code: Optional[int] = None
    output_file: str = ""  # path to captured stdout/stderr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _orch():
    import orch
    return orch


def _bg_log_path() -> Path:
    """Path to background-tasks.jsonl."""
    o = _orch()
    mem = o.orch_root() / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem / "background-tasks.jsonl"


def _task_to_dict(task: BackgroundTask) -> dict:
    return {
        "id": task.id,
        "command": task.command,
        "pid": task.pid,
        "status": task.status,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "exit_code": task.exit_code,
        "output_file": task.output_file,
    }


def _dict_to_task(d: dict) -> BackgroundTask:
    return BackgroundTask(
        id=d["id"],
        command=d["command"],
        pid=d["pid"],
        status=d["status"],
        started_at=d["started_at"],
        completed_at=d.get("completed_at"),
        exit_code=d.get("exit_code"),
        output_file=d.get("output_file", ""),
    )


def _is_pid_alive(pid: int) -> bool:
    """Check if a PID is still alive on this system."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def _append_task_log(task: BackgroundTask) -> None:
    """Append or update a task in background-tasks.jsonl."""
    path = _bg_log_path()
    # Load existing, replace if id matches, append if new
    lines: List[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("id") == task.id:
                    continue  # will be replaced
                lines.append(entry)
            except Exception:
                continue
    lines.append(_task_to_dict(task))
    path.write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_background(command: str, timeout_seconds: int = 300) -> BackgroundTask:
    """Start a shell command non-blocking. Returns immediately with a BackgroundTask.

    Args:
        command: Shell command to run.
        timeout_seconds: Not enforced at start — used as metadata for wait_background.

    Returns:
        BackgroundTask with pid and status="running".
    """
    task_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc).isoformat()

    # Temp file for output
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"poe-bg-{task_id}-",
        suffix=".log",
        delete=False,
        mode="w",
    )
    output_file = tmp.name
    tmp.close()

    # Launch process — non-blocking
    with open(output_file, "w") as out_fh:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=out_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    task = BackgroundTask(
        id=task_id,
        command=command,
        pid=proc.pid,
        status="running",
        started_at=started_at,
        output_file=output_file,
    )

    _append_task_log(task)
    return task


def poll_background(task_id: str) -> BackgroundTask:
    """Check the current status of a background task.

    Args:
        task_id: Task ID returned by start_background.

    Returns:
        Updated BackgroundTask. status="running" if still alive, "done"/"failed" if complete.
    """
    # Load from log
    task = _load_task(task_id)
    if task is None:
        raise KeyError(f"background task not found: {task_id}")

    if task.status != "running":
        return task

    if _is_pid_alive(task.pid):
        # Still running — check if process has actually exited (zombie check)
        try:
            result = subprocess.run(
                ["ps", "-p", str(task.pid), "-o", "stat="],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                # Process no longer exists
                task.status = "done"
                task.exit_code = 0
                task.completed_at = datetime.now(timezone.utc).isoformat()
                _append_task_log(task)
        except Exception:
            pass
        return task

    # PID is dead — determine exit code
    task.completed_at = datetime.now(timezone.utc).isoformat()
    # Try to get exit code via wait (non-blocking)
    try:
        pid, exit_status = os.waitpid(task.pid, os.WNOHANG)
        if pid == task.pid:
            code = os.waitstatus_to_exitcode(exit_status)
        else:
            code = 0
    except Exception:
        code = 0

    task.exit_code = code
    task.status = "done" if code == 0 else "failed"
    _append_task_log(task)
    return task


def wait_background(task_id: str, timeout_seconds: int = 60) -> BackgroundTask:
    """Poll until a background task completes or timeout is reached.

    Args:
        task_id: Task ID returned by start_background.
        timeout_seconds: Maximum seconds to wait.

    Returns:
        BackgroundTask with final status. status="timeout" if exceeded.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        task = poll_background(task_id)
        if task.status != "running":
            return task
        time.sleep(2)

    # Timeout
    task = _load_task(task_id)
    if task and task.status == "running":
        task.status = "timeout"
        task.completed_at = datetime.now(timezone.utc).isoformat()
        _append_task_log(task)
        return task
    return poll_background(task_id)


def list_background_tasks() -> List[BackgroundTask]:
    """Load all background tasks from jsonl, checking live status of running ones.

    Returns:
        List of BackgroundTask, most recent first.
    """
    path = _bg_log_path()
    if not path.exists():
        return []

    tasks: List[BackgroundTask] = []
    seen_ids: set = set()
    lines = path.read_text(encoding="utf-8").splitlines()
    # Process in reverse to dedupe (keep most recent version of each id)
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            tid = d.get("id", "")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            task = _dict_to_task(d)
            # Live check for running tasks
            if task.status == "running":
                try:
                    task = poll_background(task.id)
                except Exception:
                    pass
            tasks.append(task)
        except Exception:
            continue

    return tasks


def _load_task(task_id: str) -> Optional[BackgroundTask]:
    """Load a single task from the log by id."""
    path = _bg_log_path()
    if not path.exists():
        return None
    # Scan in reverse to get most recent version
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if d.get("id") == task_id:
                return _dict_to_task(d)
        except Exception:
            continue
    return None
