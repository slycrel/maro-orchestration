"""Outbound notification hook — how a substrate learns a run finished.

Maro is a program, not an operating system: there is no server listening and
no daemon polling. Instead, the substrate (OpenClaw, Hermes, a shell script)
registers a command in config and Maro invokes it at the moment something
notification-worthy happens, inside the run's own lifecycle:

    # ~/.maro/config.yml (or workspace config.yml)
    notify:
      command: "bash ~/.openclaw/workspace/scripts/maro-notify.sh"
      events: [run_completed, escalation]   # default; omit for both
      timeout_seconds: 30

The command receives the event payload as JSON on stdin (the run_card for
run_completed; the escalation record for escalation) plus env vars
MARO_EVENT_TYPE / MARO_HANDLE_ID / MARO_STATUS / MARO_RUN_DIR for cheap shell
dispatch without a JSON parser.

Off by default (no command configured = no-op). Every event is also appended
to memory/events.jsonl via observe.write_event regardless, so a polling
substrate can tail that instead. emit() never raises — notification must
never affect the run outcome.

See docs/SUBSTRATE_INTEGRATION.md for the full substrate contract.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Optional

log = logging.getLogger("notify")

DEFAULT_EVENTS = ["run_completed", "escalation"]


def _config_get(key: str, default):
    try:
        from config import get as _get
        return _get(key, default)
    except Exception:
        return default


def emit(event_type: str, payload: dict, *, run_dir: Optional[str] = None) -> bool:
    """Fire a notification event. Returns True if the hook command ran cleanly.

    Always appends to events.jsonl (best-effort). Runs notify.command only when
    configured AND event_type is in notify.events. Never raises.
    """
    try:
        return _emit(event_type, payload or {}, run_dir=run_dir)
    except Exception:
        log.debug("notify.emit(%s) failed", event_type, exc_info=True)
        return False


def _emit(event_type: str, payload: dict, *, run_dir: Optional[str]) -> bool:
    handle_id = str(payload.get("handle_id", ""))
    status = str(payload.get("status", ""))

    # 1) Structured event for polling substrates — always, even with no hook.
    try:
        from observe import write_event
        write_event(
            event_type,
            goal=str(payload.get("goal", payload.get("reason", "")))[:200],
            status=status,
            detail=str(payload.get("result_excerpt", payload.get("summary", "")))[:300],
        )
    except Exception:
        pass

    # 2) The hook command, if the substrate registered one.
    command = str(_config_get("notify.command", "") or "").strip()
    if not command:
        return False
    events = _config_get("notify.events", DEFAULT_EVENTS) or DEFAULT_EVENTS
    if event_type not in events:
        return False
    timeout = float(_config_get("notify.timeout_seconds", 30))

    env = dict(os.environ)
    env["MARO_EVENT_TYPE"] = event_type
    env["MARO_HANDLE_ID"] = handle_id
    env["MARO_STATUS"] = status
    if run_dir:
        env["MARO_RUN_DIR"] = str(run_dir)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            input=json.dumps({"event_type": event_type, **payload}, default=str),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if proc.returncode != 0:
            log.warning("notify.command exited %d for %s (%s): %s",
                        proc.returncode, event_type, handle_id,
                        (proc.stderr or "")[:200])
            return False
        return True
    except subprocess.TimeoutExpired:
        log.warning("notify.command timed out after %.0fs for %s (%s)",
                    timeout, event_type, handle_id)
        return False
