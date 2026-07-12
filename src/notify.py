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

The escalation-class events (escalation / backend_actionable / stranded_run
— things a human might miss if no notify lane is wired up, or if it fails)
additionally land in output/escalations.jsonl unconditionally
(ESCALATION_FILE_EVENTS, escalations_path()) — a durable, easy-to-find file
distinct from the generic mixed events.jsonl feed. This is the official
"headless/CLI, no substrate go-between" escalation surface (GOAL_BRAIN
Decisions 2026-07-12); `maro-doctor` reports whether a notify lane is ALSO
live.

See docs/SUBSTRATE_INTEGRATION.md for the full substrate contract.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Optional

log = logging.getLogger("notify")

# backend_actionable: auth/billing/context failures with a fix the user must
# apply (BACKEND_RESILIENCE_DESIGN §2) — default-on because a headless box's
# notify channel is the only surface an away-from-keyboard user actually sees.
DEFAULT_EVENTS = ["run_completed", "escalation", "backend_actionable",
                  "stranded_run"]

# The three event types that are notify-worthy AND easy to miss with no
# notify.command lane configured (run_completed already has a durable home
# via run_curation's run_card.json). These ship to a dedicated, always-on
# output file — GOAL_BRAIN Decisions 2026-07-12 ("escalation channel
# DECREED"): the substrate LLM go-between is the official escalation
# surface, but a headless/CLI-only setup still needs a findable output
# file, not a beacon trying to get someone's attention.
ESCALATION_FILE_EVENTS = {"escalation", "backend_actionable", "stranded_run"}


def _config_get(key: str, default):
    try:
        from config import get as _get
        return _get(key, default)
    except Exception:
        return default


def escalations_path():
    """Path to the durable escalation-class event log (output/escalations.jsonl).

    Ships unconditionally — exists whether or not a notify.command lane is
    configured, independent of whether that lane succeeds.
    """
    from config import workspace_root
    return workspace_root() / "output" / "escalations.jsonl"


def _write_escalation_file(event_type: str, payload: dict) -> None:
    from datetime import datetime, timezone
    from file_lock import locked_append
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **payload,
    }
    locked_append(escalations_path(), json.dumps(entry, default=str))


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

    # 1b) Durable escalation-class file — ships unconditionally, independent
    # of whether a notify.command lane is configured or whether it succeeds
    # below. Best-effort: never blocks or fails the emit.
    if event_type in ESCALATION_FILE_EVENTS:
        try:
            _write_escalation_file(event_type, payload)
        except Exception:
            log.debug("escalation file write failed for %s", event_type, exc_info=True)

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
