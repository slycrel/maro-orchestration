"""Run-lifetime lease: a per-loop flock held from loop init to process death.

Why this exists (BACKLOG 2026-07-14 adversarial follow-up): checkpoints only
carry an ``in_flight.pid`` while a step is executing — the post-step write
clears it. So every pid heuristic (``maro resume``'s liveness check, the
heartbeat's stranded sweep) has a blind spot: a healthy ``run_agent_loop``
process sitting BETWEEN steps looks exactly like a dead one, and a stale
``stranded_run`` notification could prompt a resume that hijacks a live run.
The per-run resume lock only serializes resume-vs-resume; the original loop
process never holds it.

The lease closes that blind spot with the same mechanism as the admission
gate (interrupt.acquire_project_slot): a ``fcntl.flock`` acquired at loop
init and held for the LOOP's lifetime — released at loop finalize, and
kernel-released on any process death (no stale-lock rituals). Three states:

    lease held             → owner alive (even between steps)
    present but acquirable → no loop holds it. Strong evidence of a dead
                             owner, but NOT proof the process is dead: the
                             owning process outlives its loop through the
                             closure/quality-gate window (status stamps at
                             close_run, minutes later). Callers MUST
                             corroborate False with run-metadata pid
                             liveness before acting on it (adversarial
                             review 2026-07-15 reproduced a false
                             "stranded" stamp on a live run without this).
    lease file absent      → pre-lease-era checkpoint: the caller falls
                             back to today's pid heuristics

``probe_owner_alive()`` is the read side and returns exactly that
True/False/None contract (None also on any OSError — unknown means "fall
back", never "block" and never raise). The file's JSON payload (pid,
loop_id, handle_id, started_at) is informational only, for operator
debugging — the flock is the truth.

Posture mirrors ``acquire_project_slot``: an fs problem (unwritable lock
dir, ENOLCK) degrades UNGATED with a warning — a lease is a liveness
signal, not an admission gate, and it must never refuse work. Lease files
are cleared on release but NEVER unlinked (unlink/reacquire lets a prober
lock an orphaned inode while a fresh file appears — two truths).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("maro.run_lease")

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _lock_dir() -> Path:
    """The directory loop.lock lives in — same chain as interrupt._default_lock_path()."""
    try:
        import orch
        return orch.memory_dir()
    except Exception:
        pass
    try:
        from config import memory_dir
        return memory_dir()
    except Exception:
        return Path.home() / ".maro" / "workspace" / "memory"


def _safe_name(loop_id: str) -> str:
    """Path-safe lease-file stem for a loop id.

    Loop ids are short uuid hex today, so the common case is a no-op; any
    scrubbed/truncated id gets a digest suffix so two distinct ids can never
    collide on one lease file (same approach as cli._resume_lock_name).
    """
    safe = _UNSAFE_RE.sub("_", loop_id)[:40]
    if safe != loop_id:
        digest = hashlib.sha256(loop_id.encode("utf-8")).hexdigest()[:10]
        safe = f"{safe}-{digest}" if safe else digest
    return safe


def lease_path(loop_id: str) -> Path:
    """Convention-derivable lease file path for a loop id."""
    return _lock_dir() / "leases" / f"run-{_safe_name(loop_id)}.lease"


class RunLease:
    """Held run lease. Keep a reference for the loop's lifetime; call
    release() at loop finalize. Process death releases the flock via the
    kernel either way; __del__ is the in-process crash-path backstop."""

    def __init__(self, loop_id: str, path: Path, fh) -> None:
        self.loop_id = loop_id
        self.path = path
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return  # idempotent
        try:
            # Clear the payload so operators see idle, but NEVER unlink:
            # probe_owner_alive() could lock an orphaned inode while a new
            # holder locks a fresh file. An empty, unflocked file is
            # unambiguously "owner gone".
            fh.seek(0)
            fh.truncate()
            fh.flush()
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        except OSError:
            pass

    def __del__(self) -> None:
        self.release()


def acquire_run_lease(
    loop_id: str,
    *,
    handle_id: str = "",
    goal: str = "",
) -> Optional[RunLease]:
    """Acquire the run-lifetime lease for this loop, or None (degrade ungated).

    None means the loop proceeds WITHOUT a lease — probes then return None
    (absent) or, in the already-held anomaly, True; neither blocks work.
    Loop ids are minted fresh per run, so a held lease for our own id is a
    fatal-ish anomaly worth a warning, but an fs problem must not refuse
    work (same posture as acquire_project_slot).
    """
    if not loop_id:
        return None
    path = lease_path(loop_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+", encoding="utf-8")
    except OSError as exc:
        log.warning(
            "run lease unavailable for loop %s (%s) — proceeding UNGATED",
            loop_id, exc,
        )
        return None
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        log.warning(
            "run lease for loop %s is already held — loop ids are unique per "
            "run, so this is an anomaly; proceeding UNGATED", loop_id,
        )
        return None
    except OSError as exc:
        fh.close()
        # Don't leave behind a file we just created: present-but-unheld
        # reads as "owner dead" to probes, which is an active wrong answer
        # for a live-but-ungated loop. Unlinking restores the honest
        # "unknown → pid heuristics" state. Safe: nobody else creates this
        # loop_id's lease, and a raced unlink is caught by probe's OSError
        # → None path.
        try:
            if path.stat().st_size == 0:
                path.unlink()
        except OSError:
            pass
        log.warning(
            "run lease flock failed for loop %s (%s) — proceeding UNGATED",
            loop_id, exc,
        )
        return None
    # Holder info for operator debugging — informational only, the flock is
    # the truth (probe never parses this).
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({
            "loop_id": loop_id,
            "handle_id": handle_id,
            "goal": goal[:120],
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }))
        fh.flush()
    except OSError as exc:
        log.debug("run lease holder-info write failed (non-fatal): %s", exc)
    return RunLease(loop_id, path, fh)


def probe_owner_alive(loop_id: str) -> Optional[bool]:
    """Is the loop that owns this lease still alive?

    Returns:
        True  — lease file exists and is currently flocked: owner alive
                (a healthy loop between steps counts).
        False — lease file exists but is NOT held: no loop owns it. The
                loop is over, but the process may still be finishing
                closure (see module docstring) — corroborate with the run
                metadata pid before treating the run as stranded.
        None  — no lease file (pre-lease-era checkpoint) or any OSError:
                unknown; the caller falls back to pid heuristics.

    Never raises. The shared probe lock is taken non-blocking and dropped
    immediately, so concurrent probes never mislead each other (SH+SH is
    compatible) and the transient SH can only momentarily delay an
    acquire — which is itself non-blocking and degrades ungated.
    """
    if not loop_id:
        return None
    try:
        fh = open(lease_path(loop_id), "rb")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True  # held → owner alive
        except OSError:
            return None  # can't tell — fall back to pid heuristics
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        return False  # present but unheld → owner dead
    finally:
        try:
            fh.close()
        except OSError:
            pass
