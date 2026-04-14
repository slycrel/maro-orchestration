"""Lightweight file locking for shared data store writes.

Uses fcntl.flock for advisory locking on Linux. Protects both full-rewrite
operations (skills.jsonl, tiered lessons, hypotheses, rules) and append-only
JSONL streams (outcomes.jsonl, captains_log.jsonl, step-costs.jsonl, etc.)
from concurrent corruption when multiple agent loops run simultaneously.

Note: Linux's append-write atomicity guarantee only applies to writes under
PIPE_BUF (4096 bytes). JSON payloads (step outcomes, traces, lessons) easily
exceed this limit, so bare open('a').write() is NOT safe under concurrent writers.

Behavior: best-effort lock with explicit warning on fallback. The lock
is advisory — it prevents concurrent Poe processes from corrupting each
other's writes, but can't enforce against external tools. If the lock
can't be acquired within ~5s, the write proceeds with a WARNING log.
This is a deliberate tradeoff: blocking indefinitely is worse than a
rare unlocked write, but the warning makes degradation visible.

Usage:
    from file_lock import locked_write, locked_append

    # Full rewrite
    with locked_write(path):
        path.write_text(content)

    # JSONL line append
    locked_append(path, json.dumps(entry))
"""

from __future__ import annotations

import fcntl
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

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
    Blocks up to ~5s waiting for the lock. If the lock cannot be acquired
    (timeout, OS error), logs a WARNING and proceeds — this makes the
    degradation visible in logs rather than silently dropping protection.

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
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_path, "w")
        # Try non-blocking first; retry a few times with short sleeps
        for _ in range(10):
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                time.sleep(0.5)
        if not acquired:
            logger.warning(
                "file_lock: timeout acquiring lock on %s after 5s — "
                "proceeding with UNLOCKED write. Another process may hold the lock. "
                "Data corruption is possible if concurrent writes overlap.",
                lock_path,
            )
            lock_fd.close()
            lock_fd = None
    except Exception as exc:
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


def locked_append(path: Path, line: str) -> None:
    """Append a newline-terminated line to path atomically via flock.

    Acquires the same .lock file used by locked_write(), so append and
    rewrite callers are mutually exclusive. The line must NOT end with \\n
    — this function adds the newline.

    Falls through on lock failure (logs WARNING) rather than blocking the
    caller indefinitely. The write still happens — degraded, not dropped.
    """
    with locked_write(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
