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

The escalation-class events (escalation / backend_actionable / stranded_run /
resume_refused_busy / resume_lock_unavailable
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
import math
import os
import subprocess
from typing import Optional, Any

log = logging.getLogger("notify")

_MAX_TIMEOUT_S = 86400.0

# backend_actionable: auth/billing/context failures with a fix the user must
# apply (BACKEND_RESILIENCE_DESIGN §2) — default-on because a headless box's
# notify channel is the only surface an away-from-keyboard user actually sees.
# recursion_checkin: deep-recursion progress conversation (non-blocking; the
# goal keeps running) — docs/RECURSIVE_CHECKIN_DESIGN.md. Default-on for the
# same away-from-keyboard reason: the user should get a chance to redirect or
# stop a goal that's now several passes deep.
# self_improvement_verdict: a V2 cadence verdict acted on an applied change
# (VERIFY_LEARN_ARC §3). Two shapes, told apart by the `blocking` field:
#   blocking=False → the system auto-reverted a degraded change it had applied
#                    itself (FYI: it cleaned up its own mess).
#   blocking=True  → a HUMAN-applied change degraded and was NOT auto-reverted
#                    (authority asymmetry) — it sits in the review queue.
# Default-on for the same away-from-keyboard reason as the other escalation
# classes: a headless box's notify channel is the only surface the user sees.
DEFAULT_EVENTS = ["run_completed", "escalation", "backend_actionable",
                  "stranded_run", "resume_refused_busy",
                  "resume_lock_unavailable", "recursion_checkin",
                  "self_improvement_verdict",
                  # A worker's question to the operator (operator_ask,
                  # decision 1d1ad8b0) and its time-box expiry: the run is
                  # paused on it — a headless box's notify channel is the
                  # only way the question reaches anyone.
                  "operator_question", "operator_question_expired",
                  # Async-tail phase 2: the verdict follow-up to an
                  # answer-first run_completed (which went out with
                  # verdict_pending). Default-on — the split is only
                  # honest if both halves arrive.
                  "run_verdict"]

# The event types that are notify-worthy AND easy to miss with no
# notify.command lane configured (run_completed already has a durable home
# via run_curation's run_card.json). These ship to a dedicated, always-on
# output file — GOAL_BRAIN Decisions 2026-07-12 ("escalation channel
# DECREED"): the substrate LLM go-between is the official escalation
# surface, but a headless/CLI-only setup still needs a findable output
# file, not a beacon trying to get someone's attention. recursion_checkin
# rides this file too (design §2) — it's the same "human might miss it"
# class; consumers tell it apart from a park-the-goal escalation by its
# explicit `"blocking": False` payload field.
ESCALATION_FILE_EVENTS = {"escalation", "backend_actionable", "stranded_run",
                          "resume_refused_busy", "resume_lock_unavailable",
                          "recursion_checkin", "self_improvement_verdict",
                          "operator_question", "operator_question_expired"}


def escalations_path():
    """Path to the durable escalation-class event log (output/escalations.jsonl).

    Ships unconditionally — exists whether or not a notify.command lane is
    configured, independent of whether that lane succeeds.
    """
    from config import workspace_root
    return workspace_root() / "output" / "escalations.jsonl"


def _write_escalation_file(event_type: str, payload: dict) -> None:
    from datetime import datetime, timezone
    from context_budget import clip as _esc_clip
    from file_lock import locked_append
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **payload,
    }
    # The durable escalation ledger owns its bounds (round-14 review: a
    # sender passed 5,000 chars of raw navigator reasoning straight to
    # disk while the captain's-log copy was clipped — per-sender bounding
    # cannot be trusted at a shared boundary).
    # Field inventory includes the live sender aliases (round-15 review:
    # recursion check-ins ride "reasoning"/"summary_for_user", which
    # reached disk unbounded while the three canonical names clipped).
    # A typed per-event schema is the deeper fix if senders keep minting
    # aliases; until then, keep this list synced with emit() callers.
    for _k in ("summary", "reason", "detail", "reasoning",
               "summary_for_user", "revert_detail"):
        if isinstance(entry.get(_k), str):
            entry[_k] = _esc_clip(entry[_k], 2000)
    locked_append(escalations_path(), json.dumps(entry, default=str))


def _read(merged: dict, key: str, default):
    """`config.get`'s dotted walk over ONE snapshot's mapping (review r19:
    the policy reads every `notify.*` key from the same published load, so
    the section and its faults can never come from different loads)."""
    node = merged
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def _policy(event_type: str) -> tuple[Optional[bool], str, Any]:
    """Validate one snapshot's hook obligation, command, and raw timeout.

    A null, blank, or False command explicitly disables the hook; command:
    false is an operator's deliberate off switch. Other non-strings are unknown.
    Returns (owed, command, timeout_seconds as configured — unconverted).
    """
    from config import snapshot
    merged, faults = snapshot()
    # review r19: obligation and execution must use the same validation rules,
    # read from ONE snapshot (r18's separate get/load_faults reads could pair a
    # faulted section with a clean publish landing between them).
    if faults:
        return None, "", None
    section = _read(merged, "notify", None)
    if section is None:
        return False, "", None
    if not isinstance(section, dict):
        return None, "", None
    command = _read(merged, "notify.command", None)
    if command is None or command is False:
        return False, "", None
    if not isinstance(command, str):
        return None, "", None
    command = command.strip()
    if not command:
        return False, "", None
    events = _read(merged, "notify.events", DEFAULT_EVENTS)
    if events is None or events == "" or events == []:
        events = DEFAULT_EVENTS
    if isinstance(events, str) or not isinstance(events, (list, tuple, set, frozenset)):
        return None, "", None
    if not all(isinstance(e, str) for e in events):
        return None, "", None
    timeout = _read(merged, "notify.timeout_seconds", 30)
    return event_type in events, command, timeout


def hook_owed(event_type: str) -> Optional[bool]:
    """Whether a notify.command lane is owed `event_type`: True when one
    is configured AND subscribes to it, False when there is confirmed no
    such lane (absent, or an explicit `command: false`/empty), None when
    that cannot be known — the config could not be read, the `notify`
    section or its `command` is not the right shape, or `notify.events`
    is not a list of names (review 2026-09-13 r16–r19: each of those had
    read as "no hook owed", and a journal row then acknowledged a story
    the configured recipient never got). One snapshot decides: the
    section and its faults come from the same published load (r19)."""
    try:
        return _policy(event_type)[0]
    except Exception:
        return None


def hook_configured(event_type: str) -> bool:
    """True when a notify.command lane is configured AND it subscribes to
    `event_type` — i.e. a False from `emit` means a configured recipient
    received nothing, not "no channel" (review 2026-09-13 r13: the
    finalize recorded delivery on either). Unknowable reads as False
    here; `tell` uses `hook_owed` and treats unknowable as unacknowledged."""
    return hook_owed(event_type) is True


def early_reached(marker: dict) -> bool:
    """Whether the answer-first notify recorded in a `verdict_pending`
    marker REACHED the user — the routing fact for the verdict follow-up
    (`run_verdict` only when it did; else the full `run_completed`).
    `early_told` is the early sender's owed-channel word (`tell`); a
    marker from before it (review r16) is read the legacy way: reached
    unless a configured hook failed."""
    if not isinstance(marker, dict) or not marker.get("notified_early"):
        return False
    if "early_told" in marker:
        return bool(marker.get("early_told"))
    return bool(not marker.get("hook_configured") or marker.get("hook_delivered"))


def emit(event_type: str, payload: dict, *, run_dir: Optional[str] = None,
         _journaled: bool = False) -> bool:
    """Fire a notification event. Returns True if the hook command ran cleanly.

    Always appends to events.jsonl (best-effort; `_journaled=True` says the
    caller already wrote that row — `tell` does). Runs notify.command only
    when configured AND event_type is in notify.events. Never raises.
    """
    try:
        return _emit(event_type, payload or {}, run_dir=run_dir, journaled=_journaled)
    except Exception:
        log.debug("notify.emit(%s) failed", event_type, exc_info=True)
        return False


def tell(event_type: str, payload: dict, *, run_dir: Optional[str] = None) -> bool:
    """Fire a notification event and return whether its OWED channel
    acknowledged it: the hook ran cleanly when one is configured for the
    event, else the journal row was written (`emit` reports only the hook,
    and reports False for "no hook" — review 2026-09-13 r15: a journal
    write that failed with no hook configured was recorded as the story
    told). The finalize and the repair sweeps stamp `final_notified_at`
    on this word alone. Never raises."""
    try:
        journal_ok = _journal(event_type, payload or {})
    except Exception:
        journal_ok = False
    try:
        hook_ok = emit(event_type, payload or {}, run_dir=run_dir, _journaled=True)
    except Exception:
        hook_ok = False
    try:
        owed = hook_owed(event_type)
    except Exception:
        owed = None
    # an unknowable channel (unreadable config, malformed subscription)
    # acknowledges nothing but a clean hook run — the story stays owed
    # until the configuration can be read (review r16)
    return bool(journal_ok) if owed is False else bool(hook_ok)


def answer_text(payload: dict) -> str:
    """Return the first non-empty answer carried by a notification."""
    if not isinstance(payload, dict):
        return ""
    # review r18: delivery qualification and journal text must name the same answer.
    for key in ("result_excerpt", "answer_summary", "summary"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    return ""


def _journal(event_type: str, payload: dict) -> bool:
    """The structured event row for polling substrates — always, even
    with no hook. Returns the writer's word (False on a torn row or any
    failure)."""
    handle_id = str(payload.get("handle_id", ""))
    status = str(payload.get("status", ""))
    try:
        from context_budget import clip as _cb_clip
        from observe import write_event
        # 300 is a deliberate event-lane projection cap (write_event's rows
        # are PIPE_BUF-bounded downstream) — announced, not silent.
        _excerpt = answer_text(payload)
        _detail = _cb_clip(_excerpt, 300)
        if event_type == "run_completed" and (
                "goal_achieved" in payload or payload.get("goal_verdict_source")):
            # review r17: reserve the verdict so an excerpt cannot hide failure.
            _detail = (
                f"[{handle_id}] goal_achieved={payload.get('goal_achieved')}"
                + (f" source={payload.get('goal_verdict_source')}"
                   if payload.get("goal_verdict_source") else "")
                + (" verdict_pending" if payload.get("verdict_pending") else ""))
            if _excerpt:
                _detail += "; " + _cb_clip(_excerpt, max(0, 300 - len(_detail) - 2))
        if event_type == "run_verdict":
            # The verdict IS this event's content — the generic projection
            # dropped it entirely and polling substrates received an empty
            # follow-up (review 2026-08-13). handle_id rides the detail:
            # write_event has no field for it.
            _detail = _cb_clip(
                f"[{handle_id}] goal_achieved="
                f"{payload.get('goal_achieved')}"
                + (f" source={payload.get('goal_verdict_source')}"
                   if payload.get("goal_verdict_source") else "")
                + (" answer_changed"
                   if payload.get("answer_changed") else "")
                + f"; {payload.get('goal_verdict_summary', '')}", 300)
        return bool(write_event(
            event_type,
            goal=str(payload.get("goal", payload.get("reason", "")))[:200],
            status=status,
            detail=_detail,
        ))
    except Exception:
        return False


def _emit(event_type: str, payload: dict, *, run_dir: Optional[str],
          journaled: bool = False) -> bool:
    handle_id = str(payload.get("handle_id", ""))
    status = str(payload.get("status", ""))

    # 1) Structured event for polling substrates — always, even with no hook.
    if not journaled:
        _journal(event_type, payload)

    # 1b) Durable escalation-class file — attempted unconditionally,
    # independent of whether a notify.command lane is configured or whether
    # it succeeds below. Best-effort: never blocks or fails the emit — but
    # unlike the general events.jsonl write above, this file is specifically
    # pitched as "the thing you check when nothing else is configured", so a
    # silent failure here defeats its purpose. Logged at warning (not
    # debug) for that reason (adversarial review 2026-07-12).
    if event_type in ESCALATION_FILE_EVENTS:
        try:
            _write_escalation_file(event_type, payload)
        except Exception:
            log.warning("escalation file write failed for %s", event_type, exc_info=True)

    # 2) The hook command, if the validated policy subscribes to this event.
    owed, command, timeout_raw = _policy(event_type)
    if owed is None:
        log.warning("notify configuration unknown for %s; hook skipped", event_type)
        return False
    if owed is False:
        return False
    try:
        timeout = float(timeout_raw if timeout_raw is not None else 30)
    except (TypeError, ValueError, OverflowError):
        timeout = float("nan")
    # review r21: even finite timeouts can overflow the subprocess clock.
    if not math.isfinite(timeout) or timeout <= 0 or timeout > _MAX_TIMEOUT_S:
        log.warning("invalid notify timeout for %s; using 30 seconds", event_type)
        timeout = 30

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
