#!/usr/bin/env python3
"""
FileTaskStore — file-per-task JSON store with fcntl advisory locking.

Replaces task-queue.sh (~1260 lines bash) with ~300 lines Python.
Inspired by ClawTeam's FileTaskStore pattern.

Storage: output/queues/tasks/<job_id>.json (one file per task)
Locking: fcntl advisory lock per task file (no global lock)
Writes:  atomic via tempfile.mkstemp + os.rename
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import pathlib
import tempfile
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from config import workspace_root as _workspace_root


def _tasks_dir() -> pathlib.Path:
    """Resolve tasks dir at call time, not import time. Respects env var changes."""
    return _workspace_root() / "output" / "queues" / "tasks"


def _archive_dir() -> pathlib.Path:
    """Resolve archive dir at call time, not import time."""
    return _workspace_root() / "output" / "queues" / "archive"

VALID_STATUSES = ("queued", "claimed", "done", "failed", "archived")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_job_id() -> str:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"task-{ts}-{short}"


def task_path(job_id: str) -> pathlib.Path:
    return _tasks_dir() / f"{job_id}.json"


def make_task(
    job_id: str,
    lane: str = "now",
    source: str = "task_store",
    reason: str = "",
    parent_job_id: str = "",
    blocked_by: Optional[List[str]] = None,
    continuation_depth: int = 0,
) -> Dict[str, Any]:
    now = utc_now()
    return {
        "job_id": job_id,
        "run_id": str(uuid.uuid4()),
        "lane": lane,
        "source": source,
        "reason": reason,
        "status": "queued",
        "attempt": 0,
        "parent_job_id": parent_job_id,
        "blocked_by": blocked_by or [],
        "continuation_depth": continuation_depth,
        "timestamps": {
            "queued_at_utc": now,
            "claimed_at_utc": "",
            "finished_at_utc": "",
        },
        "artifact_paths": {},
        "claimed_by_pid": None,
    }


# --- Atomic file ops ---

def _atomic_write(path: pathlib.Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically: write to tempfile in same dir, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.rename(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _lock_task(path: pathlib.Path, shared: bool = False):
    """Advisory lock on the task file. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    lock_path.touch(exist_ok=True)
    fp = open(lock_path, "r")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        fp.close()


def _read_task(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive via /proc."""
    if pid is None or pid <= 0:
        return False
    return os.path.isdir(f"/proc/{pid}")


# --- Core operations ---

def enqueue(
    lane: str = "now",
    source: str = "task_store",
    reason: str = "",
    parent_job_id: str = "",
    blocked_by: Optional[List[str]] = None,
    job_id: Optional[str] = None,
    continuation_depth: int = 0,
) -> Dict[str, Any]:
    """Create a new task and write it to disk."""
    jid = job_id or new_job_id()
    task = make_task(jid, lane=lane, source=source, reason=reason,
                     parent_job_id=parent_job_id, blocked_by=blocked_by,
                     continuation_depth=continuation_depth)

    # If blocked_by contains task IDs, verify they exist
    if task["blocked_by"]:
        _check_cycle(jid, task["blocked_by"])

    path = task_path(jid)
    with _lock_task(path):
        _atomic_write(path, task)
    return task


def _check_cycle(job_id: str, blocked_by: List[str], visited: Optional[set] = None) -> None:
    """Detect cycles in dependency graph.

    Walks blocked_by chains transitively starting from ``job_id``.
    Raises if ``job_id`` appears as its own transitive dependency, OR if
    any cycle exists in the reachable chain (which would cause infinite
    loops at drain time).

    ``visited`` tracks nodes on the CURRENT path (not all seen nodes)
    so that revisiting a node on the same DFS path signals a cycle.
    """
    if visited is None:
        visited = {job_id}  # seed with the new task's ID
    for dep_id in blocked_by:
        if dep_id in visited:
            raise ValueError(
                f"cycle detected: {dep_id} appears in dependency chain of {job_id}"
            )
        visited.add(dep_id)
        dep_path = task_path(dep_id)
        dep = _read_task(dep_path)
        if dep and dep.get("blocked_by"):
            _check_cycle(job_id, dep["blocked_by"], visited)
        visited.discard(dep_id)  # backtrack for other branches


def claim(job_id: str, pid: Optional[int] = None) -> Dict[str, Any]:
    """Claim a queued task. Returns the updated task dict."""
    pid = pid or os.getpid()
    path = task_path(job_id)
    with _lock_task(path):
        task = _read_task(path)
        if task is None:
            raise FileNotFoundError(f"task not found: {job_id}")

        # Stale claim recovery
        if task["status"] == "claimed":
            claimed_pid = task.get("claimed_by_pid")
            if claimed_pid and not _pid_alive(claimed_pid):
                # Previous claimer is dead, release
                task["status"] = "queued"
                task["claimed_by_pid"] = None
            else:
                raise RuntimeError(f"task {job_id} already claimed by pid {claimed_pid}")

        if task["status"] != "queued":
            raise RuntimeError(f"task {job_id} has status '{task['status']}', expected 'queued'")

        # Check blocked_by
        for dep_id in task.get("blocked_by", []):
            dep = _read_task(task_path(dep_id))
            if dep is None or dep["status"] != "done":
                dep_status = dep["status"] if dep else "missing"
                raise RuntimeError(f"task {job_id} blocked by {dep_id} (status={dep_status})")

        task["status"] = "claimed"
        task["claimed_by_pid"] = pid
        task["attempt"] += 1
        task["timestamps"]["claimed_at_utc"] = utc_now()
        _atomic_write(path, task)
    return task


def complete(job_id: str, artifact_paths: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Mark task as done and resolve dependents."""
    path = task_path(job_id)
    with _lock_task(path):
        task = _read_task(path)
        if task is None:
            raise FileNotFoundError(f"task not found: {job_id}")
        if task["status"] not in ("claimed", "queued"):
            raise RuntimeError(f"task {job_id} has status '{task['status']}', cannot complete")
        task["status"] = "done"
        task["timestamps"]["finished_at_utc"] = utc_now()
        task["claimed_by_pid"] = None
        if artifact_paths:
            task["artifact_paths"].update(artifact_paths)
        _atomic_write(path, task)

    _resolve_dependents(job_id)
    return task


def fail(job_id: str, error: str = "") -> Dict[str, Any]:
    """Mark task as failed."""
    path = task_path(job_id)
    with _lock_task(path):
        task = _read_task(path)
        if task is None:
            raise FileNotFoundError(f"task not found: {job_id}")
        task["status"] = "failed"
        task["timestamps"]["finished_at_utc"] = utc_now()
        task["claimed_by_pid"] = None
        if error:
            task["error"] = error
        _atomic_write(path, task)
    return task


def _resolve_dependents(completed_job_id: str) -> None:
    """Scan all tasks; for any that list completed_job_id in blocked_by, remove it.
    If blocked_by becomes empty, task stays queued and is now claimable."""
    _tasks_dir().mkdir(parents=True, exist_ok=True)
    for p in _tasks_dir().glob("*.json"):
        with _lock_task(p):
            task = _read_task(p)
            if task is None:
                continue
            blocked = task.get("blocked_by", [])
            if completed_job_id in blocked:
                blocked.remove(completed_job_id)
                task["blocked_by"] = blocked
                _atomic_write(p, task)


def list_tasks(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all tasks, optionally filtered by status."""
    _tasks_dir().mkdir(parents=True, exist_ok=True)
    tasks = []
    for p in sorted(_tasks_dir().glob("*.json")):
        task = _read_task(p)
        if task is None:
            continue
        if status_filter and task.get("status") != status_filter:
            continue
        tasks.append(task)
    return tasks


def status_summary() -> Dict[str, int]:
    """Return counts by status."""
    counts: Dict[str, int] = {}
    _tasks_dir().mkdir(parents=True, exist_ok=True)
    for p in _tasks_dir().glob("*.json"):
        task = _read_task(p)
        if task is None:
            continue
        s = task.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return counts


def archive(job_id: str) -> Dict[str, Any]:
    """Move a done/failed task to the archive directory."""
    path = task_path(job_id)
    with _lock_task(path):
        task = _read_task(path)
        if task is None:
            raise FileNotFoundError(f"task not found: {job_id}")
        if task["status"] not in ("done", "failed"):
            raise RuntimeError(f"task {job_id} has status '{task['status']}', can only archive done/failed")
        task["status"] = "archived"
        _archive_dir().mkdir(parents=True, exist_ok=True)
        archive_path = _archive_dir() / f"{job_id}.json"
        _atomic_write(archive_path, task)
        path.unlink(missing_ok=True)
        # Clean up lock file
        path.with_suffix(".lock").unlink(missing_ok=True)
    return task


def recover_stale_claims() -> List[str]:
    """Find claimed tasks whose PID is dead and reset them to queued."""
    recovered = []
    _tasks_dir().mkdir(parents=True, exist_ok=True)
    for p in _tasks_dir().glob("*.json"):
        with _lock_task(p):
            task = _read_task(p)
            if task is None:
                continue
            if task["status"] == "claimed":
                pid = task.get("claimed_by_pid")
                if pid and not _pid_alive(pid):
                    task["status"] = "queued"
                    task["claimed_by_pid"] = None
                    _atomic_write(p, task)
                    recovered.append(task["job_id"])
    return recovered


# --- CLI ---

def main() -> int:
    parser = argparse.ArgumentParser(description="FileTaskStore — file-per-task queue")
    sub = parser.add_subparsers(dest="command")

    p_enq = sub.add_parser("enqueue", help="Create a new task")
    p_enq.add_argument("--lane", default="now")
    p_enq.add_argument("--source", default="cli")
    p_enq.add_argument("--reason", default="")
    p_enq.add_argument("--parent-job-id", default="")
    p_enq.add_argument("--blocked-by", default="", help="Comma-separated job IDs")

    p_claim = sub.add_parser("claim", help="Claim a queued task")
    p_claim.add_argument("job_id")

    p_done = sub.add_parser("complete", help="Mark task as done")
    p_done.add_argument("job_id")

    p_fail = sub.add_parser("fail", help="Mark task as failed")
    p_fail.add_argument("job_id")
    p_fail.add_argument("--error", default="")

    p_list = sub.add_parser("list", help="List tasks")
    p_list.add_argument("--status", default=None)

    sub.add_parser("status", help="Show status summary")

    p_arch = sub.add_parser("archive", help="Archive a done/failed task")
    p_arch.add_argument("job_id")

    sub.add_parser("recover", help="Recover stale claimed tasks")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "enqueue":
        blocked = [b.strip() for b in args.blocked_by.split(",") if b.strip()] if args.blocked_by else []
        task = enqueue(
            lane=args.lane, source=args.source, reason=args.reason,
            parent_job_id=args.parent_job_id, blocked_by=blocked,
        )
        print(json.dumps(task, indent=2))

    elif args.command == "claim":
        task = claim(args.job_id)
        print(json.dumps(task, indent=2))

    elif args.command == "complete":
        task = complete(args.job_id)
        print(json.dumps(task, indent=2))

    elif args.command == "fail":
        task = fail(args.job_id, error=args.error)
        print(json.dumps(task, indent=2))

    elif args.command == "list":
        tasks = list_tasks(status_filter=args.status)
        print(json.dumps(tasks, indent=2))

    elif args.command == "status":
        counts = status_summary()
        total = sum(counts.values())
        print(json.dumps({"total": total, **counts}, indent=2))

    elif args.command == "archive":
        task = archive(args.job_id)
        print(json.dumps(task, indent=2))

    elif args.command == "recover":
        recovered = recover_stale_claims()
        print(json.dumps({"recovered": recovered, "count": len(recovered)}, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
