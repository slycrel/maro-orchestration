"""Lightweight file locking for shared data store writes.

Uses fcntl.flock for advisory locking on Linux. Protects both full-rewrite
operations (skills.jsonl, tiered lessons, hypotheses, rules) and append-only
JSONL streams (outcomes.jsonl, captains_log.jsonl, step-costs.jsonl, etc.)
from concurrent corruption when multiple agent loops run simultaneously.

Note: Linux's append-write atomicity guarantee only applies to writes under
PIPE_BUF (4096 bytes). JSON payloads (step outcomes, traces, lessons) easily
exceed this limit, so bare open('a').write() is NOT safe under concurrent writers.

Behavior: FAIL-CLOSED with a bounded wait. The lock is advisory — it
prevents concurrent Maro processes from corrupting each other's writes,
but can't enforce against external tools. If the lock can't be acquired
within the deadline (config `file_lock.timeout_s`, env
MARO_FILELOCK_TIMEOUT_S, default 30s), FileLockTimeout is raised —
an OSError subclass, so existing narrow `except OSError` guards contain
it. Corruption of the learning ledgers is permanent and silent; a bounded
loud stall is neither. flock is kernel-released when the holder dies, so
a crashed process can never wedge a waiter — only a live slow holder can,
and every critical section here is a local file read/rewrite.

Escape hatch: MARO_FILELOCK_FAIL_OPEN=1 (or config `file_lock.fail_open:
true`) restores the historical warn-and-proceed-unlocked behavior, so an
operator can un-wedge an unattended box without a deploy. (Fail-open was
the deliberate default until 2026-07; reversed in the concurrency-
hardening arc because contention is exactly when unlocked writes corrupt.)

A lock file that cannot even be *created* (read-only fs, permissions)
still falls through unlocked with a WARNING — that's an environment
problem, not contention, and blocking the write wouldn't protect anything.

Usage:
    from file_lock import locked_write, locked_append, locked_rmw, atomic_write

    # Full rewrite
    with locked_write(path):
        path.write_text(content)

    # JSONL line append
    locked_append(path, json.dumps(entry))

    # Read-modify-write without lost updates (read happens under the lock)
    locked_rmw(path, lambda old: transform(old))
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)


class FileLockTimeout(OSError):
    """Raised when a lock can't be acquired within the deadline (fail-closed).

    OSError subclass so callers' existing narrow `except OSError` blocks
    contain it — a skipped write degrades the same way an fs error would.
    """


def _lock_timeout_s() -> float:
    env = os.environ.get("MARO_FILELOCK_TIMEOUT_S")
    if env:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    try:
        from config import get as _get
        return max(0.0, float(_get("file_lock.timeout_s", 30.0)))
    except Exception:
        return 30.0


def _fail_open() -> bool:
    env = os.environ.get("MARO_FILELOCK_FAIL_OPEN")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        from config import get as _get
        return bool(_get("file_lock.fail_open", False))
    except Exception:
        return False


# Track which lock files this thread already holds to avoid self-deadlock.
_held_locks: threading.local = threading.local()


def _get_held() -> set:
    if not hasattr(_held_locks, "paths"):
        _held_locks.paths = set()
    return _held_locks.paths


@contextmanager
def locked_write(path: Path) -> Generator[None, None, None]:
    """Acquire an exclusive lock on path.lock, yield, then release.

    Uses a separate .lock file so the data file can be safely rewritten.
    Waits up to the configured deadline (default 30s), then raises
    FileLockTimeout — unless fail-open is enabled, in which case it logs
    a WARNING and proceeds unlocked (the pre-2026-07 behavior).

    For reentrant calls (same thread already holds the lock), skips
    acquisition to avoid deadlock.
    """
    lock_path = path.parent / (path.name + ".lock")
    lock_key = str(lock_path.resolve())
    held = _get_held()

    # Reentrant: this thread already holds this lock — skip to avoid deadlock
    if lock_key in held:
        yield
        return

    lock_fd = None
    acquired = False
    timed_out = False
    waited = 0.0
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_path, "w")
        deadline_s = _lock_timeout_s()
        start = time.monotonic()
        sleep_s = 0.05  # mild backoff: 0.05 → 0.5s cap
        while True:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                waited = time.monotonic() - start
                if waited >= deadline_s:
                    timed_out = True
                    break
                time.sleep(min(sleep_s, deadline_s - waited))
                sleep_s = min(sleep_s * 2, 0.5)
        if acquired and waited > 1.0:
            logger.warning("file_lock: waited %.1fs for %s", waited, lock_path)
        if timed_out:
            lock_fd.close()
            lock_fd = None
            _report_timeout(lock_path, waited)
            if not _fail_open():
                raise FileLockTimeout(
                    f"file_lock: could not acquire {lock_path} within "
                    f"{waited:.1f}s (holder alive?). Set "
                    f"MARO_FILELOCK_FAIL_OPEN=1 to restore unlocked-write "
                    f"fallback."
                )
            logger.warning(
                "file_lock: fail-open enabled — proceeding with UNLOCKED "
                "write to %s. Data corruption is possible if concurrent "
                "writes overlap.", lock_path,
            )
    except FileLockTimeout:
        raise
    except Exception as exc:
        # Lock file can't be created/locked at all (RO fs, permissions):
        # environment problem, not contention — blocking wouldn't protect
        # anything, so fall through unlocked with a warning.
        logger.warning(
            "file_lock: failed to acquire lock on %s: %s — proceeding unlocked",
            lock_path, exc,
        )
        if lock_fd is not None:
            try:
                lock_fd.close()
            except Exception:
                pass
            lock_fd = None

    if acquired:
        held.add(lock_key)

    try:
        yield
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
        if acquired:
            held.discard(lock_key)


def _report_timeout(lock_path: Path, waited: float) -> None:
    """Loud, best-effort visibility for lock timeouts (log + events feed)."""
    logger.error(
        "file_lock: TIMEOUT after %.1fs acquiring %s — another process holds it",
        waited, lock_path,
    )
    try:
        from observe import write_event
        write_event("file_lock_timeout", detail=f"{lock_path} waited={waited:.1f}s")
    except Exception:
        pass


def atomic_write(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Crash-safe full rewrite: mkstemp in path's dir, write, fsync, os.replace.

    A reader (or a crash mid-write) sees either the old complete file or the
    new complete file — never a partial. Does NOT take the .lock file; pair
    with locked_write()/locked_rmw() when concurrent writers are possible.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates 0600 and os.replace keeps the tmp's perms, so without
    # correction every file this touches ends up 0600 (data-r2-03: rewrites
    # silently narrow existing ledgers; new files — the live specimen was a
    # promoted skill .md — never get normal umask-derived perms at all).
    # Preserve the target's existing mode; give new files 0666 & ~umask like
    # a plain open() would. (The umask read-back briefly sets the process
    # umask — momentary, and file writes here are multiprocess, not
    # multithreaded, so the window is acceptable.)
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        _umask = os.umask(0)
        os.umask(_umask)
        mode = 0o666 & ~_umask
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def locked_append(path: Path, line: str) -> None:
    """Append a newline-terminated line to path atomically via flock.

    Acquires the same .lock file used by locked_write(), so append and
    rewrite callers are mutually exclusive. The line must NOT end with \\n
    — this function adds the newline.

    Fail-closed like locked_write: raises FileLockTimeout past the deadline
    (unless fail-open is enabled).
    """
    with locked_write(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def locked_rmw(path: Path, fn, *, default: str = "") -> str:
    """Read-modify-write without lost updates: read UNDER the lock, write
    the transformed content via atomic_write while still holding it.

    fn: Callable[[str], str] — receives the current text (or `default`
    when the file doesn't exist), returns the full new text. Callers do
    their own parsing (JSONL, markdown, ...). Keep fn cheap — it runs
    inside the critical section; never call an LLM or subprocess from it.

    Reentrant like locked_write. Returns the new content.
    """
    with locked_write(path):
        try:
            old = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            old = default
        new = fn(old)
        atomic_write(path, new)
        return new
