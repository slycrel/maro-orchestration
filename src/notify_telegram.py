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

_CLASS_ICON = {
    "success": "✅",             # ✅ verified achieved
    "done-unverified": "☑",     # ☑ finished, no verdict
    "done-not-achieved": "⚠",   # ⚠ finished but verdict says no
    "partial": "⚠",
    "failed": "❌",              # ❌
}


def format_message(payload: dict) -> str:
    """Render one event payload into a short plain-text Telegram message."""
    event = str(payload.get("event_type", ""))
    goal = str(payload.get("goal", "")).strip()
    goal_line = goal[:200] + ("…" if len(goal) > 200 else "")

    if event == "escalation":
        lines = ["\U0001f514 maro needs a human"]  # 🔔
        if goal_line:
            lines.append(f"Goal: {goal_line}")
        summary = str(payload.get("summary", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        lines.append(summary or reason or "escalated with no summary")
        if summary and reason and reason not in summary:
            lines.append(f"Why: {reason[:300]}")
        point = payload.get("point")
        if point:
            lines.append(f"(at {point}; job {payload.get('job_id', '?')})")
        return "\n".join(lines)

    # run_completed (and anything unrecognized — degrade to a status line)
    cls = str(payload.get("success_class", "") or payload.get("status", "?"))
    icon = _CLASS_ICON.get(cls, "ℹ")  # ℹ
    lines = [f"{icon} maro run {cls}"]
    if goal_line:
        lines.append(f"Goal: {goal_line}")
    excerpt = str(payload.get("result_excerpt", "")).strip()
    if excerpt:
        lines.append(excerpt[:800])
    hid = payload.get("handle_id")
    if hid:
        lines.append(f"run: {hid}  (maro-runs result {hid})")
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
