"""Telegram target for the notify hook — `maro-notify-telegram`.

Wire it up in config and Maro's completion/escalation events land in Telegram:

    # ~/.maro/config.yml
    notify:
      command: "maro-notify-telegram"

Reads the event payload (JSON) from stdin — the run_card for run_completed,
the escalation record for escalation — formats a short human message, and
sends it to the allowed chats. Token + chat resolution reuses
telegram_listener (env TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID first, then the
legacy openclaw.json channel config), so no credentials live here or in any
shell script.

Messages are sent as plain text: result excerpts are arbitrary content and
Telegram's Markdown parser rejects unbalanced entities.

Exit codes: 0 sent, 1 nothing sent (no token/chats/payload) — notify.emit
logs nonzero exits at warning level.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from context_budget import clip as _tg_clip

# Plain-language outcome headers — the first line IS the message for a user
# glancing at a phone. The old format led with the internal class name
# ("maro run done-not-achieved"); a user shouldn't need the taxonomy.
_CLASS_LABEL = {
    "success": ("✅", "Done — goal achieved"),
    "done-unverified": ("☑", "Done (not verified)"),
    "done-verdict-pending": ("☑", "Done — verdict pending"),
    "done-not-achieved": ("⚠", "Finished — but goal NOT achieved"),
    "achieved-not-done": ("✅", "Goal achieved (run ended early)"),
    "partial": ("⚠", "Partial — stopped before finishing"),
    "failed": ("❌", "Failed"),
    "interrupted": ("⏸", "Interrupted"),
}
_STATUS_LABEL = {
    "done": ("✅", "Done"),
    "error": ("❌", "Failed"),
    "blocked": ("🛑", "Blocked — needs input"),
    "incomplete": ("⚠", "Incomplete"),
}

# Escalation-class events that carry their ask in summary/reason/user_action
# (notify.ESCALATION_FILE_EVENTS minus `escalation`, which has its own richer
# shape). Kept as a local literal so this command stays runnable standalone;
# tests pin it against notify's set so the two can't drift.
_ESCALATION_CLASS_EVENTS = frozenset({
    "backend_actionable", "stranded_run", "resume_refused_busy",
    "resume_lock_unavailable", "recursion_checkin",
    "self_improvement_verdict",
})


def _cfg(key: str, default):
    try:
        from config import get as _get
        return _get(key, default)
    except Exception:
        return default


def _run_stats_line(payload: dict) -> str:
    """'cost $0.71 | 37m' — best-effort from run-card fields, '' when absent."""
    parts = []
    cost = payload.get("total_cost_usd")
    if cost is not None:
        try:
            parts.append(f"cost ${float(cost):.2f}")
        except Exception:
            pass
    try:
        from datetime import datetime
        started, ended = payload.get("started_at"), payload.get("ended_at")
        if started and ended:
            secs = (datetime.fromisoformat(str(ended))
                    - datetime.fromisoformat(str(started))).total_seconds()
            if secs >= 90:
                parts.append(f"{secs / 60:.0f}m")
            elif secs > 0:
                parts.append(f"{secs:.0f}s")
    except Exception:
        pass
    return " | ".join(parts)


def _deliverable_excerpt(payload: dict, limit: int = 600) -> str:
    """The run's findings, not its paperwork.

    RESULT.md (and the card's result_excerpt mirror of it) opens with a
    '# Result: <full goal echo>' header plus a telemetry status line —
    exactly the content a completion message should NOT lead with. Read
    result_path when available, skip that preamble, and return the first
    real body content.
    """
    text = ""
    path = str(payload.get("result_path", "") or "")
    if path:
        try:
            text = Path(path).read_text(errors="replace")
        except Exception:
            text = ""
    if not text:
        text = str(payload.get("result_excerpt", "") or "")
    lines_out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not lines_out and (
            not s or s.startswith("# Result:") or s.startswith("Status: ")
            or s == "---"
        ):
            continue
        lines_out.append(ln)
    out = "\n".join(lines_out).strip()
    if not out:
        return ""
    return out[:limit] + ("…" if len(out) > limit else "")


def _viewer_link(payload: dict) -> str:
    """Servable URL for the run's output when a viz base is configured.

    Prefers the curated deliverable (deliverable_link_path — the actual
    report, copied into <run>/artifact/ by run_curation.locate_deliverables);
    falls back to the loop report html derived from result_path.
    """
    base = str(_cfg("notify.viewer_url", "") or "").rstrip("/")
    if not base:
        return ""
    deliverable = str(payload.get("deliverable_link_path", "") or "").strip("/")
    if deliverable:
        return f"{base}/{deliverable}"
    path = str(payload.get("result_path", "") or "")
    if not path or not path.endswith("-RESULT.md"):
        return ""
    try:
        from runs import runs_root
        rel = Path(path).resolve().relative_to(Path(runs_root()).resolve())
    except Exception:
        return ""
    rel_report = str(rel)[: -len("-RESULT.md")] + "-report.html"
    return f"{base}/{rel_report}"


def format_message(payload: dict) -> str:
    """Render one event payload into a short plain-text Telegram message."""
    event = str(payload.get("event_type", ""))
    goal = str(payload.get("goal", "")).strip()
    goal_line = goal[:200] + ("…" if len(goal) > 200 else "")

    if event == "escalation":
        lines = ["\U0001f514 maro needs a human"]  # 🔔
        if goal_line:
            lines.append(f"Goal: {goal_line}")
        # §9.6 (2026-07-27): the single-chasm decision line leads when
        # present — it IS the ask; summary/reason become the supporting
        # detail. Older payloads without it keep the previous shape.
        decision = str(payload.get("decision", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        if decision:
            lines.append(decision)
            if summary and summary not in decision:
                lines.append(f"Detail: {_tg_clip(summary, 300)}")
        else:
            lines.append(summary or reason or "escalated with no summary")
            if summary and reason and reason not in summary:
                lines.append(f"Why: {reason[:300]}")
        family = str(payload.get("family_roi", "")).strip()
        if family:
            lines.append(family)
        point = payload.get("point")
        if point:
            lines.append(f"(at {point}; job {payload.get('job_id', '?')})")
        return "\n".join(lines)

    # Async-tail phase 2: the verdict follow-up to an answer-first
    # run_completed. Short by design — the user already has the answer; this
    # is the verifier signing off (or dissenting). A revised answer (gate
    # escalation re-ran the loop after the early notify) is the exception
    # that re-sends content.
    if event == "run_verdict":
        achieved = payload.get("goal_achieved")
        if achieved is True:
            head = "✅ Verdict: goal achieved"
        elif achieved is False:
            head = "⚠ Verdict: goal NOT achieved"
        else:
            src = str(payload.get("goal_verdict_source", "") or "unverified")
            head = f"☑ Verdict: not verified ({src})"
        lines = [head]
        goal = str(payload.get("goal", "")).strip()
        if goal:
            lines.append(f"Re: {goal[:100]}")
        summary = str(payload.get("goal_verdict_summary", "") or "").strip()
        if summary and achieved is not True:
            # The why matters when the verifier dissents or abstains; a
            # self-grade on success doesn't earn the screen space. 300 is a
            # deliberate screen cap — announced, not silent.
            lines.append(_tg_clip(summary, 300))
        if payload.get("answer_changed"):
            answer = str(payload.get("answer_summary", "") or "").strip()
            if answer:
                lines.append("")
                lines.append("Revised answer:")
                lines.append(answer)
        return "\n".join(lines)

    # Escalation-class events other than `escalation` (backend_actionable,
    # stranded_run, …) previously fell through to the run-completed formatter,
    # which dropped their summary/reason — the auth-breaker's re-seed
    # instructions rendered as "ℹ run auth_expired" (review 2026-08-13).
    # Render the class generically: headline, then the actionable detail.
    if event in _ESCALATION_CLASS_EVENTS:
        lines = [f"⚠ maro: {event.replace('_', ' ')}"]
        if goal_line:
            lines.append(f"Goal: {goal_line}")
        summary = str(payload.get("summary", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        action = str(payload.get("user_action", "")).strip()
        lines.append(summary or reason or "(no detail recorded)")
        if reason and summary and reason not in summary:
            lines.append(f"Why: {reason[:300]}")
        if action and action not in summary and action not in reason:
            lines.append(f"Fix: {action[:200]}")
        return "\n".join(lines)

    # run_completed (and anything unrecognized — degrade to a status line)
    status = str(payload.get("status", "") or "")
    hid = str(payload.get("handle_id", "") or "")
    nickname = str(payload.get("nickname", "") or "")
    run_ref = hid + (f" ({nickname})" if nickname else "")

    if status == "clarification_needed":
        # The question IS the payload — relay it, say how to answer.
        lines = ["❓ Maro needs an answer before it can run this"]
        if goal_line:
            lines.append(f"Goal: {goal_line}")
        question = str(payload.get("clarification_question", "") or "").strip()
        excerpt = str(payload.get("result_excerpt", "") or "").strip()
        lines.append(question or excerpt[:400] or "(no question recorded)")
        lines.append("Re-send the goal with the answer included.")
        if run_ref:
            lines.append(f"run: {run_ref}")
        return "\n".join(lines)

    cls = str(payload.get("success_class", "") or "")
    icon, label = (
        _CLASS_LABEL.get(cls)
        or _STATUS_LABEL.get(status)
        or ("ℹ", f"run {cls or status or '?'}")
    )
    # Answer-first (2026-07-17): the user asked a question — the message body
    # is the ANSWER (curation's answer_summary), not the run's paperwork.
    # Verdict prose only earns a line when the goal was NOT achieved (the
    # why-not matters; the verifier's self-grade on a success doesn't).
    answer = str(payload.get("answer_summary", "") or "").strip()
    lines = [f"{icon} {label}"]
    if answer:
        if goal:
            g = goal[:100]
            if len(goal) > 100:
                # Word-boundary trim — "Extract concrete Herme…" reads broken.
                g = g.rsplit(" ", 1)[0] if " " in g else g
                g += "…"
            lines.append(f"Re: {g}")
        lines.append("")
        lines.append(answer)
        if payload.get("answer_truncated"):
            # A capped list must not read as "that's everything".
            lines.append("⋯ more in the full report")
        lines.append("")
    elif goal_line:
        lines.append(f"Goal: {goal_line}")
    verdict = str(payload.get("goal_verdict_summary", "") or "").strip()
    if verdict and (not answer or payload.get("goal_achieved") is False):
        lines.append("Verdict: " + verdict[:300] + ("…" if len(verdict) > 300 else ""))
    gaps = payload.get("goal_verdict_gaps") or []
    if gaps:
        gap_text = "; ".join(str(g) for g in gaps)
        lines.append("Missing: " + gap_text[:300] + ("…" if len(gap_text) > 300 else ""))
    if not answer:
        excerpt = _deliverable_excerpt(payload)
        if excerpt:
            lines.extend(["", excerpt, ""])
    link = _viewer_link(payload)
    if link:
        lines.append(f"📄 Full report: {link}")
    tail = f"run: {run_ref}" if run_ref else ""
    stats = _run_stats_line(payload)
    if stats:
        tail = f"{tail} | {stats}" if tail else stats
    if tail:
        lines.append(tail)
    if hid:
        lines.append(f"Full result: maro-runs result {hid}")
    return "\n".join(lines)


def send(text: str) -> bool:
    """Send to all allowed chats. Returns True if at least one send worked."""
    from telegram_listener import TelegramBot, _resolve_token, _resolve_allowed_chats
    token = _resolve_token()
    if not token:
        print("no telegram token resolved", file=sys.stderr)
        return False
    chats = _resolve_allowed_chats()
    if not chats:
        print("no allowed telegram chats resolved", file=sys.stderr)
        return False
    bot = TelegramBot(token)
    sent = False
    for chat_id in chats:
        try:
            # Plain text, chunked to Telegram's 4096 limit.
            for i in range(0, len(text), 4096):
                bot._call("sendMessage", chat_id=chat_id, text=text[i:i + 4096])
            sent = True
        except Exception as exc:
            print(f"telegram send to {chat_id} failed: {exc}", file=sys.stderr)
    return sent


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Send a Maro notify event to Telegram")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the formatted message instead of sending")
    args = ap.parse_args(argv)

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {"event_type": "unknown", "goal": raw[:200]}
    if not payload:
        print("empty payload", file=sys.stderr)
        return 1

    text = format_message(payload)
    if args.dry_run:
        print(text)
        return 0
    return 0 if send(text) else 1


if __name__ == "__main__":
    raise SystemExit(main())
