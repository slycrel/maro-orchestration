"""Transport-agnostic core shared by telegram_listener.py and slack_listener.py.

Extracted 2026-07-02 (Tier 2 consolidation, docs/REFACTOR_PLAN.md) after
slack_listener's command dispatch was found to have drifted from
telegram_listener's working equivalent (same day's Tier 0 bugfix pass fixed
several bugs caused by that drift).

Deliberately narrow: only pure, side-effect-free logic lives here. The
actual `InterruptQueue`/`is_loop_running`/`get_running_loop` bindings stay
imported locally in each listener file, because both `tests/test_telegram_listener.py`
and `tests/test_slack_listener.py` patch those names at the transport-module
level (`patch("telegram_listener.is_loop_running", ...)` etc.) to keep
parallel test runs from tripping real interrupt routing — centralizing the
binding here would silently break that isolation. The command *set* each
platform supports, response wording/tone, and platform-specific API
mechanics (polling vs Socket Mode, message chunking) also stay per-platform;
those differ intentionally, not by drift.
"""
from __future__ import annotations

from typing import Optional

_INTENT_LABELS = {
    "additive": "added to plan",
    "corrective": "plan updated",
    "priority": "prioritized",
    "stop": "stop signal sent",
}


def parse_slash_command(text: str, *, strip_at_suffix: bool = False) -> tuple[Optional[str], str]:
    """Parse "/cmd args" into (cmd, args). Returns (None, text) if not a slash command.

    `strip_at_suffix` handles Telegram's "/cmd@botname" form; Slack has no
    such suffix so it's off by default.
    """
    text = text.strip()
    if not text.startswith("/"):
        return None, text
    parts = text[1:].split(None, 1)
    cmd = parts[0].lower()
    if strip_at_suffix:
        cmd = cmd.split("@")[0]
    args = parts[1] if len(parts) > 1 else ""
    return cmd, args


def is_chat_allowed(chat_id, allowed) -> bool:
    """True if chat_id passes the allowlist. An empty/falsy allowlist allows all."""
    return not allowed or chat_id in allowed


def intent_label(intent: str) -> str:
    """Human-readable label for an Interrupt's classified intent."""
    return _INTENT_LABELS.get(intent, intent)
