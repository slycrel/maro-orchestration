"""Daemon singleton via pidfile + flock.

One heartbeat, one scheduler — per workspace, by construction. The pidfile
is held with an exclusive flock for the process's lifetime, so the kernel
releases it on any death (crash, SIGKILL) and a stale file can never block
a restart. The JSON payload is informational for humans/tools; the flock
is the actual mutex.

Usage:
    from proc_lock import hold_pidfile

    with hold_pidfile("heartbeat") as acquired:
        if not acquired:
            sys.exit(1)  # another live instance holds it
        run_forever()
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)


def _run_dir() -> Path:
    try:
        from config import workspace_root
        return workspace_root() / "run"
    except Exception:
        # Mirror config.workspace_root()'s env resolution before touching the
        # home fallback. ops-r2-05: a test stubbing sys.modules["config"] with
        # a partial namespace lands here, and a home-only fallback stamps the
        # REAL workspace's heartbeat.pid despite MARO_WORKSPACE isolation.
        for var in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
            val = os.environ.get(var)
            if val:
                return Path(val).expanduser().resolve() / "run"
        return Path.home() / ".maro" / "workspace" / "run"


def pidfile_path(name: str) -> Path:
    return _run_dir() / f"{name}.pid"


def read_holder(name: str) -> Optional[dict]:
    """Best-effort read of the current holder's payload (informational)."""
    try:
        holder = json.loads(pidfile_path(name).read_text(encoding="utf-8"))
        return holder if isinstance(holder, dict) else None
    except Exception:
        return None


@dataclass(frozen=True)
class PidfileAcquireResult:
    """Typed nonblocking acquisition result for finite critical sections."""

    status: str  # acquired | busy | unavailable
    handle: object = None
    error: str = ""


def acquire_pidfile(name: str, *, payload: Optional[dict] = None) -> PidfileAcquireResult:
    """Acquire one pidfile lock without conflating contention and I/O failure."""
    path = pidfile_path(name)
    fh = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+", encoding="utf-8")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if fh is not None:
            fh.close()
        return PidfileAcquireResult("busy")
    except OSError as exc:
        if fh is not None:
            fh.close()
        return PidfileAcquireResult("unavailable", error=str(exc))

    holder = dict(payload or {})
    holder.update({
        "name": name,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(holder))
        fh.flush()
    except OSError as exc:
        fh.close()
        return PidfileAcquireResult("unavailable", error=str(exc))
    return PidfileAcquireResult("acquired", handle=fh)


def try_hold_pidfile(name: str, *, fail_open: bool = True,
                     payload: Optional[dict] = None):
    """Non-contextmanager variant for run-forever daemons: acquire and hold
    until process death (the kernel releases the flock). Returns an opaque
    handle to keep referenced, or None if a live holder exists. Environment
    errors degrade to a warning + a truthy sentinel (daemon still runs) unless
    ``fail_open=False``. ``payload`` adds caller-specific holder identity while
    the lock layer retains its canonical name/pid/start fields.
    """
    acquired = acquire_pidfile(name, payload=payload)
    if acquired.status == "busy":
        return None
    if acquired.status == "unavailable":
        action = "proceeding UNLOCKED" if fail_open else "refusing (fail-closed)"
        logger.warning("pidfile %s unavailable (%s) — %s",
                       pidfile_path(name), acquired.error, action)
        return object() if fail_open else None
    return acquired.handle


@contextlib.contextmanager
def hold_pidfile(name: str, *, fail_open: bool = True) -> Generator[bool, None, None]:
    """Hold <workspace>/run/<name>.pid exclusively for the with-block.

    Yields True if acquired (payload written, lock held until exit),
    False if a live holder exists. Never blocks. On environment errors
    (read-only fs etc.) yields True unlocked with a warning by default — a
    broken fs shouldn't stop a daemon. Callers protecting paid or
    non-idempotent work may set ``fail_open=False`` to skip instead.
    """
    path = pidfile_path(name)
    fh = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+", encoding="utf-8")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        yield False
        return
    except OSError as exc:
        action = "proceeding UNLOCKED" if fail_open else "skipping (fail-closed)"
        logger.warning("pidfile %s unavailable (%s) — %s", path, exc, action)
        if fh is not None:
            fh.close()
        yield fail_open
        return

    try:
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({
            "name": name,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }))
        fh.flush()
        yield True
    finally:
        # Leave the file in place (never unlink — see interrupt.py slot
        # notes on the unlink/reacquire race); the flock release is the
        # actual "not running" signal, and payload pid-liveness covers
        # human readers.
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        except OSError:
            pass
