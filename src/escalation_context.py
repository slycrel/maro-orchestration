"""Escalation payload context — §9.6 simple-first (Jeremy decree 2026-07-27).

An escalation asks a human ONE decision about ONE chasm, plus one line of
goal-family context so the reader can judge whether this chasm is a
recurring pattern worth a capability investment ("Escalation payload:
simple first — single-chasm decision + one family-ROI context line;
complex later" — GOAL_BRAIN Decisions 2026-07-27, chunk-9 agenda item 3).

Both helpers are pure/deterministic — no LLM calls, no config keys. The
fields ride the existing "escalation" notify event additively: every
consumer (output/escalations.jsonl durable file, notify.command hook,
telegram bridge) sees them; none requires them.

The family key is the Phase 44 diagnosis taxonomy (introspect.diagnose_loop
— pure heuristics, persisted to diagnoses.jsonl with a recorded_at stamp
since VERIFY_LEARN_ARC V3). Window counts use only stamped rows and say so;
the all-time count covers every row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("maro.escalation")

# One deterministic ask per emit point. The chasm specifics are interpolated;
# the option set names only actions the reader can actually take at that
# point (honest options — no "resume" verb for a stuck run that has none).
_DECISION_TEMPLATES = {
    "blocked_step": (
        "Decide this chasm: a step is blocked — {reason}. "
        "Options: re-send the goal with guidance, or drop it."
    ),
    "dispatch": (
        "Decide this chasm: the run was parked before starting — {reason}. "
        "Options: adjust the goal (or clear the blocker) and re-send, or drop it."
    ),
    "director_escalation": (
        "Decide: {reason}"
    ),
}

_REASON_MAX = 220


def decision_line(point: str, *, reason: str = "", step: str = "") -> str:
    """The single-chasm ask for an escalation payload. Never raises.

    Unknown points get a generic-but-honest ask rather than "" — an
    escalation with no decision line is the pre-§9.6 shape this exists to
    replace.
    """
    try:
        short = " ".join(str(reason).split())[:_REASON_MAX] or "no reason recorded"
        if step:
            short = f"{' '.join(str(step).split())[:120]} — {short}"
        template = _DECISION_TEMPLATES.get(point)
        if template is None:
            return f"Decide: {short}"
        return template.format(reason=short)
    except Exception:
        return "Decide: escalation raised (context unavailable)"


def family_roi_line(failure_class: str, *, window_days: int = 30) -> str:
    """One line of goal-family context: how often this failure class recurs.

    Returns "" for empty/"healthy" classes or when the diagnosis ledger is
    unreadable — the line is context, and silence beats noise. First
    occurrence is signal too ("first ... on record"): a brand-new chasm
    reads differently from a recurring one.
    """
    if not failure_class or failure_class == "healthy":
        return ""
    try:
        from introspect import load_diagnoses
        rows = [d for d in load_diagnoses(limit=200)
                if d.failure_class == failure_class]
        if not rows:
            return f"Family context: first '{failure_class}' failure on record."
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        recent = 0
        for d in rows:
            stamp = getattr(d, "recorded_at", "") or ""
            try:
                ts = datetime.fromisoformat(stamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    recent += 1
            except ValueError:
                continue  # pre-V3 row without a stamp — all-time count only
        line = (f"Family context: '{failure_class}' has {len(rows)} prior "
                f"diagnos{'is' if len(rows) == 1 else 'es'} on record")
        if recent:
            line += f", {recent} in the last {window_days} days"
        return line + "."
    except Exception:
        log.debug("family_roi_line failed for %s", failure_class, exc_info=True)
        return ""
