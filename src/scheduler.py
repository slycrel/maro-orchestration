"""Scheduler — persistent cron jobs that survive restarts.

From the 724-office steal list: scheduled missions should survive process
restarts. Jobs are stored in memory/jobs.json with timezone-aware scheduling.

Supports:
  - one-shot jobs ("run once at HH:MM today or tomorrow")
  - daily recurring jobs ("run every day at HH:MM")
  - interval jobs ("run every N minutes")

Usage:
    from scheduler import JobStore, add_job, check_due_jobs, mark_job_done

    # Add a daily research job
    add_job(goal="Check Polymarket top markets", schedule={"type": "daily", "time": "08:00"})

    # Check what's due
    due = check_due_jobs()
    for job in due:
        run(job["goal"])
        mark_job_done(job["job_id"])

    # CLI
    poe-schedule --list
    poe-schedule --add "Research X" --schedule daily --time 09:00
    poe-schedule --remove JOB_ID
    poe-schedule --run-due   # execute all due jobs now (one-shot mode)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.scheduler")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _jobs_path() -> Path:
    try:
        from orch_items import memory_dir
        return memory_dir() / "jobs.json"
    except Exception:
        return Path.cwd() / "memory" / "jobs.json"


def _load_jobs() -> List[Dict[str, Any]]:
    path = _jobs_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as exc:
        log.warning("scheduler: failed to load jobs.json: %s", exc)
    return []


def _save_jobs(jobs: List[Dict[str, Any]]) -> None:
    path = _jobs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("scheduler: failed to save jobs.json: %s", exc)


# ---------------------------------------------------------------------------
# Schedule computation
# ---------------------------------------------------------------------------

def _parse_hhmm(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute). Raises ValueError on bad input."""
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {time_str!r} (expected HH:MM)")
    return int(parts[0]), int(parts[1])


def _next_run_for_schedule(schedule: Dict[str, Any], after: Optional[datetime] = None) -> str:
    """Compute the next run datetime for a schedule spec.

    Schedule types:
      {"type": "once", "time": "HH:MM"}           — run today at HH:MM (or tomorrow if past)
      {"type": "daily", "time": "HH:MM"}           — run every day at HH:MM
      {"type": "interval", "minutes": N}            — run every N minutes from now

    Returns ISO-format UTC datetime string.
    """
    now = after or datetime.now(timezone.utc)
    stype = schedule.get("type", "once")

    if stype in ("once", "daily"):
        time_str = schedule.get("time", "09:00")
        hour, minute = _parse_hhmm(time_str)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    elif stype == "interval":
        minutes = int(schedule.get("minutes", 60))
        return (now + timedelta(minutes=minutes)).isoformat()

    else:
        raise ValueError(f"Unknown schedule type: {stype!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_job(
    goal: str,
    schedule: Dict[str, Any],
    *,
    job_id: Optional[str] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Add a new scheduled job. Returns the job dict."""
    jid = job_id or uuid.uuid4().hex[:12]
    next_run = _next_run_for_schedule(schedule)
    job: Dict[str, Any] = {
        "job_id": jid,
        "goal": goal,
        "schedule": schedule,
        "next_run": next_run,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "enabled": enabled,
        "run_count": 0,
    }
    jobs = _load_jobs()
    jobs.append(job)
    _save_jobs(jobs)
    log.info("scheduler: added job %s goal=%r next_run=%s", jid, goal[:60], next_run)
    return job


def remove_job(job_id: str) -> bool:
    """Remove a job by ID. Returns True if found and removed."""
    jobs = _load_jobs()
    before = len(jobs)
    jobs = [j for j in jobs if j.get("job_id") != job_id]
    if len(jobs) < before:
        _save_jobs(jobs)
        log.info("scheduler: removed job %s", job_id)
        return True
    return False


def list_jobs(*, enabled_only: bool = False) -> List[Dict[str, Any]]:
    """Return all jobs, optionally filtered to enabled ones."""
    jobs = _load_jobs()
    if enabled_only:
        jobs = [j for j in jobs if j.get("enabled", True)]
    return sorted(jobs, key=lambda j: j.get("next_run", ""))


def check_due_jobs(*, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Return all enabled jobs whose next_run is <= now."""
    _now = now or datetime.now(timezone.utc)
    _now_str = _now.isoformat()
    due = []
    for job in _load_jobs():
        if not job.get("enabled", True):
            continue
        next_run = job.get("next_run", "")
        if next_run and next_run <= _now_str:
            due.append(job)
    return due


def mark_job_done(job_id: str) -> bool:
    """Mark a job as executed.

    - For recurring jobs (daily/interval): advance next_run.
    - For one-shot jobs: disable the job (keep in history).
    Returns True if the job was found.
    """
    jobs = _load_jobs()
    found = False
    for job in jobs:
        if job.get("job_id") != job_id:
            continue
        found = True
        job["run_count"] = job.get("run_count", 0) + 1
        job["last_run"] = datetime.now(timezone.utc).isoformat()
        stype = job.get("schedule", {}).get("type", "once")
        if stype == "once":
            job["enabled"] = False
        else:
            try:
                job["next_run"] = _next_run_for_schedule(job["schedule"])
            except Exception as exc:
                log.warning("scheduler: failed to advance next_run for %s: %s", job_id, exc)
                job["enabled"] = False
        break
    if found:
        _save_jobs(jobs)
    return found


def enable_job(job_id: str, *, enabled: bool = True) -> bool:
    """Enable or disable a job. Returns True if found."""
    jobs = _load_jobs()
    for job in jobs:
        if job.get("job_id") == job_id:
            job["enabled"] = enabled
            _save_jobs(jobs)
            return True
    return False


# ---------------------------------------------------------------------------
# Heartbeat integration
# ---------------------------------------------------------------------------

def drain_due_jobs(
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Check for due jobs and submit them via handle().

    Called from heartbeat_loop() on each tick (or a sub-multiple).
    Returns: number of jobs submitted (0 if none due or dry_run).
    """
    due = check_due_jobs()
    if not due:
        return 0

    if verbose:
        print(f"[scheduler] {len(due)} job(s) due", flush=True)

    if dry_run:
        return 0

    submitted = 0
    for job in due:
        goal = job.get("goal", "")
        job_id = job.get("job_id", "")
        if not goal:
            mark_job_done(job_id)
            continue
        try:
            import threading as _threading
            from handle import handle

            def _run_job(g: str = goal, jid: str = job_id) -> None:
                try:
                    result = handle(g)
                    log.info("scheduler: job %s completed status=%s", jid, result.status)
                except Exception as exc:
                    log.warning("scheduler: job %s failed: %s", jid, exc)
                finally:
                    mark_job_done(jid)

            t = _threading.Thread(target=_run_job, daemon=True, name=f"sched-{job_id[:8]}")
            t.start()
            submitted += 1
            if verbose:
                print(f"[scheduler] submitted job {job_id[:8]}: {goal[:60]}", flush=True)
        except Exception as exc:
            log.warning("scheduler: failed to submit job %s: %s", job_id, exc)
            mark_job_done(job_id)  # prevent loop re-run on error

    return submitted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import sys

    p = argparse.ArgumentParser(description="poe-schedule — manage persistent cron jobs")
    sub = p.add_subparsers(dest="cmd")

    # list
    ls = sub.add_parser("list", aliases=["ls"], help="List all jobs")
    ls.add_argument("--all", dest="show_all", action="store_true",
                    help="Include disabled jobs")

    # add
    add = sub.add_parser("add", help="Add a new job")
    add.add_argument("goal", help="Goal text to run")
    add.add_argument("--type", choices=["once", "daily", "interval"], default="once",
                     help="Schedule type (default: once)")
    add.add_argument("--time", default="09:00", metavar="HH:MM",
                     help="Time for once/daily schedules (default: 09:00)")
    add.add_argument("--minutes", type=int, default=60, metavar="N",
                     help="Interval in minutes for --type interval (default: 60)")
    add.add_argument("--disabled", action="store_true", help="Add in disabled state")

    # remove
    rm = sub.add_parser("remove", aliases=["rm", "del"], help="Remove a job")
    rm.add_argument("job_id", help="Job ID to remove")

    # enable/disable
    en = sub.add_parser("enable", help="Enable a job")
    en.add_argument("job_id")
    dis = sub.add_parser("disable", help="Disable a job")
    dis.add_argument("job_id")

    # run-due
    sub.add_parser("run-due", help="Submit all currently-due jobs via handle() and exit")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(0)

    cmd = args.cmd

    if cmd in ("list", "ls"):
        jobs = list_jobs(enabled_only=not getattr(args, "show_all", False))
        if not jobs:
            print("(no jobs)")
            return
        now_str = datetime.now(timezone.utc).isoformat()
        for j in jobs:
            status = "enabled" if j.get("enabled", True) else "disabled"
            overdue = " [OVERDUE]" if j.get("next_run", "") <= now_str and j.get("enabled") else ""
            print(f"  {j['job_id']}  [{status}]  next={j.get('next_run','?')[:19]}{overdue}")
            print(f"    goal: {j['goal'][:80]}")
            print(f"    schedule: {json.dumps(j.get('schedule', {}))}")

    elif cmd == "add":
        schedule: Dict[str, Any] = {"type": args.type}
        if args.type in ("once", "daily"):
            schedule["time"] = args.time
        elif args.type == "interval":
            schedule["minutes"] = args.minutes
        job = add_job(args.goal, schedule, enabled=not args.disabled)
        print(f"Added: {job['job_id']}  next_run={job['next_run'][:19]}")

    elif cmd in ("remove", "rm", "del"):
        if remove_job(args.job_id):
            print(f"Removed: {args.job_id}")
        else:
            print(f"Not found: {args.job_id}")
            sys.exit(1)

    elif cmd == "enable":
        if enable_job(args.job_id, enabled=True):
            print(f"Enabled: {args.job_id}")
        else:
            print(f"Not found: {args.job_id}")
            sys.exit(1)

    elif cmd == "disable":
        if enable_job(args.job_id, enabled=False):
            print(f"Disabled: {args.job_id}")
        else:
            print(f"Not found: {args.job_id}")
            sys.exit(1)

    elif cmd == "run-due":
        n = drain_due_jobs(verbose=True)
        print(f"Submitted {n} job(s)")
        if n > 0:
            import time
            time.sleep(2)  # give threads a moment to start


if __name__ == "__main__":
    main()
