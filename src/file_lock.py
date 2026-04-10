"""Lightweight file locking for shared data store writes.

Uses fcntl.flock for advisory locking on Linux. Protects full-rewrite
operations (skills.jsonl, tiered lessons, hypotheses, rules) from
concurrent corruption when multiple agent loops run simultaneously.

Append-only files (outcomes.jsonl, captains_log.jsonl, events.jsonl)
don't need this — small appends are atomic on Linux.

Usage:
    from file_lock import locked_write

    with locked_write(path):
        path.write_text(content)
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
    Blocks up to ~5s waiting for the lock. On any lock failure (timeout,
    OS error, or reentrant call), proceeds without the lock (fail-open)
    to avoid blocking execution.
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
            # Last resort: proceed without lock (fail-open)
            logger.debug("file_lock: timeout acquiring lock on %s, proceeding unlocked", lock_path)
            lock_fd.close()
            lock_fd = None
    except Exception as exc:
        logger.debug("file_lock: failed to acquire lock on %s: %s", lock_path, exc)
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
