"""
SlowUpdateScheduler — gates heavy background work to idle windows.

State machine:
  IDLE_WAIT   → WINDOW_OPEN  : mission not active + cooldown elapsed
  WINDOW_OPEN → UPDATING     : background worker(s) start
  WINDOW_OPEN → IDLE_WAIT    : mission becomes active before any work starts
  UPDATING    → PAUSING      : mission becomes active while worker(s) running
  UPDATING    → WINDOW_OPEN  : all workers finish, still idle
  PAUSING     → IDLE_WAIT    : all workers finish (drain complete)

Intended use in heartbeat_loop():
  sched = SlowUpdateScheduler(idle_cooldown=30)
  while True:
      ...
      if sched.should_run(is_busy=mission_active):
          sched.start_work()
          launch_background_worker(...)
      if worker_finished:
          sched.finish_work()
      sched.tick(is_busy=mission_active)
"""

from __future__ import annotations

import threading
import time
from enum import Enum


class State(Enum):
    IDLE_WAIT = "IDLE_WAIT"
    WINDOW_OPEN = "WINDOW_OPEN"
    UPDATING = "UPDATING"
    PAUSING = "PAUSING"


class SlowUpdateScheduler:
    """Thread-safe state machine that gates background work to idle windows.

    Parameters
    ----------
    idle_cooldown:
        Seconds of continuous non-busy time required before IDLE_WAIT → WINDOW_OPEN.
    """

    def __init__(self, idle_cooldown: float = 30.0) -> None:
        self._lock = threading.Lock()
        self._state = State.IDLE_WAIT
        self._idle_cooldown = idle_cooldown
        self._idle_since: float | None = None   # when we first saw is_busy=False
        self._active_workers: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def tick(self, *, is_busy: bool) -> None:
        """Call once per heartbeat tick to advance the state machine.

        Parameters
        ----------
        is_busy:
            True when a mission drain (or other heavy foreground work) is running.
        """
        with self._lock:
            self._advance(is_busy=is_busy)

    def should_run(self, *, is_busy: bool) -> bool:
        """Return True if it is safe to launch a new background worker right now.

        Internally calls tick() so the caller doesn't need to call both.
        """
        with self._lock:
            self._advance(is_busy=is_busy)
            return self._state == State.WINDOW_OPEN

    def start_work(self) -> None:
        """Record that a background worker has started.  Transitions WINDOW_OPEN → UPDATING."""
        with self._lock:
            if self._state == State.WINDOW_OPEN:
                self._active_workers += 1
                self._state = State.UPDATING
            elif self._state == State.UPDATING:
                # Additional worker starting during an existing update window — fine.
                self._active_workers += 1
            # If called from any other state it's a no-op (defensive).

    def finish_work(self, *, is_busy: bool = False) -> None:
        """Record that a background worker has finished.

        Parameters
        ----------
        is_busy:
            Current busy status — used to decide next state when workers drain.
        """
        with self._lock:
            if self._active_workers > 0:
                self._active_workers -= 1
            if self._active_workers == 0:
                if self._state == State.UPDATING:
                    if is_busy:
                        self._state = State.PAUSING
                        # Will transition to IDLE_WAIT on next _advance call
                        self._advance(is_busy=is_busy)
                    else:
                        self._state = State.WINDOW_OPEN
                elif self._state == State.PAUSING:
                    self._state = State.IDLE_WAIT
                    self._idle_since = None

    # ------------------------------------------------------------------
    # Internal helpers (must be called with _lock held)
    # ------------------------------------------------------------------

    def _advance(self, *, is_busy: bool) -> None:
        now = time.monotonic()

        if self._state == State.IDLE_WAIT:
            if is_busy:
                self._idle_since = None
            else:
                if self._idle_since is None:
                    self._idle_since = now
                elif now - self._idle_since >= self._idle_cooldown:
                    self._state = State.WINDOW_OPEN

        elif self._state == State.WINDOW_OPEN:
            if is_busy:
                # Mission became active before any work launched — back to waiting.
                self._state = State.IDLE_WAIT
                self._idle_since = None

        elif self._state == State.UPDATING:
            if is_busy:
                # Mission became active while workers are running — pause mode.
                self._state = State.PAUSING

        elif self._state == State.PAUSING:
            if self._active_workers == 0:
                # All workers drained — safe to return to idle.
                self._state = State.IDLE_WAIT
                self._idle_since = None

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return a snapshot dict for observability / poe-doctor."""
        with self._lock:
            return {
                "state": self._state.value,
                "active_workers": self._active_workers,
                "idle_since": self._idle_since,
                "idle_cooldown": self._idle_cooldown,
            }

    def __repr__(self) -> str:
        s = self.status()
        return (
            f"SlowUpdateScheduler(state={s['state']}, "
            f"workers={s['active_workers']}, "
            f"cooldown={s['idle_cooldown']}s)"
        )
