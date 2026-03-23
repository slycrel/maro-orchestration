"""Telegram listener for Poe — polls for messages and routes through handle().

Reads bot token and allowed chat IDs from:
  1. TELEGRAM_BOT_TOKEN env var
  2. openclaw.json at ~/.openclaw/openclaw.json

Usage:
    python3 telegram_listener.py          # run forever (poll loop)
    python3 telegram_listener.py --once   # process pending updates once and exit
    python3 telegram_listener.py --dry-run # process but don't send responses
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

# Module-level imports so tests can patch cleanly
try:
    from handle import handle
except ImportError:  # pragma: no cover
    handle = None  # type: ignore[assignment]

try:
    from sheriff import check_system_health, check_all_projects, read_heartbeat_state
except ImportError:  # pragma: no cover
    check_system_health = None  # type: ignore[assignment]
    check_all_projects = None  # type: ignore[assignment]
    read_heartbeat_state = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_OPENCLAW_CFG = Path.home() / ".openclaw" / "openclaw.json"
_OFFSET_FILE = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace")) / "telegram_offset.txt"


def _load_openclaw_cfg() -> dict:
    if _OPENCLAW_CFG.exists():
        try:
            return json.loads(_OPENCLAW_CFG.read_text())
        except Exception:
            pass
    return {}


def _resolve_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        return token
    cfg = _load_openclaw_cfg()
    raw = cfg.get("channels", {}).get("telegram", {}).get("botToken", "")
    if raw.startswith("env:"):
        return os.environ.get(raw[4:], "")
    return raw


def _resolve_allowed_chats() -> set[int]:
    """Return set of chat IDs we'll respond to. Empty = allow all."""
    chat_id_env = os.environ.get("TELEGRAM_CHAT_ID", "") or os.environ.get("TELEGRAM_NOTIFY_CHAT_ID", "")
    if chat_id_env:
        try:
            return {int(chat_id_env)}
        except ValueError:
            pass

    cfg = _load_openclaw_cfg()
    tg = cfg.get("channels", {}).get("telegram", {})
    chat_id = tg.get("chatId", "")
    if chat_id:
        try:
            return {int(chat_id)}
        except ValueError:
            pass

    # allowFrom list
    allow_from = cfg.get("tools", {}).get("elevated", {}).get("allowFrom", {}).get("telegram", [])
    if allow_from:
        return {int(x) for x in allow_from if str(x).lstrip("-").isdigit()}

    return set()


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self._base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, **params) -> dict:
        resp = requests.post(f"{self._base}/{method}", json=params, timeout=30)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data.get('description', data)}")
        return data.get("result", {})

    def get_updates(self, offset: int = 0, timeout: int = 20) -> list[dict]:
        try:
            return self._call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"])
        except requests.exceptions.Timeout:
            return []

    def send_message(self, chat_id: int, text: str) -> None:
        # Telegram max message length is 4096 chars
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for chunk in chunks:
            self._call("sendMessage", chat_id=chat_id, text=chunk, parse_mode="Markdown")

    def send_message_returning_id(self, chat_id: int, text: str) -> int:
        """Send a short message and return its message_id (for later editing)."""
        result = self._call("sendMessage", chat_id=chat_id, text=text[:4096])
        return result.get("message_id", 0)

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Edit an existing message (splits into multiple if > 4096 chars)."""
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        try:
            self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=chunks[0], parse_mode="Markdown")
        except Exception:
            # Edit failed (e.g. message too old) — send new messages instead
            for chunk in chunks:
                self._call("sendMessage", chat_id=chat_id, text=chunk, parse_mode="Markdown")
            return
        for chunk in chunks[1:]:
            self._call("sendMessage", chat_id=chat_id, text=chunk, parse_mode="Markdown")

    def send_typing(self, chat_id: int) -> None:
        try:
            self._call("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Offset persistence
# ---------------------------------------------------------------------------

def _load_offset() -> int:
    try:
        return int(_OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(str(offset))


# ---------------------------------------------------------------------------
# Slash command dispatch
# ---------------------------------------------------------------------------

def _parse_slash_command(text: str):
    """Parse /command [args]. Returns (command, args_str) or (None, text)."""
    text = text.strip()
    if not text.startswith("/"):
        return None, text
    parts = text[1:].split(None, 1)
    cmd = parts[0].lower().split("@")[0]  # strip @botname suffix
    args = parts[1] if len(parts) > 1 else ""
    return cmd, args


def _dispatch_slash(
    cmd: str,
    args: str,
    *,
    project: str,
    dry_run: bool,
    verbose: bool,
) -> str:
    """Route a slash command to the appropriate handler."""
    if cmd == "status":
        # System health summary
        try:
            health = check_system_health()
            projects = check_all_projects()
            stuck = [r.project for r in projects if r.status in ("stuck", "warning")]
            state = read_heartbeat_state() or {}
            last_check = state.get("checked_at", "never")
            lines = [
                f"*Poe Status*",
                f"Health: {health.status}",
                f"Last heartbeat: {last_check}",
                f"Stuck projects: {', '.join(stuck) if stuck else 'none'}",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"Status check failed: {e}"

    elif cmd in ("director", "research", "build", "ops"):
        # Force the director/worker path
        if not args:
            return f"Usage: /{cmd} <directive>"
        try:
            from director import run_director
            from llm import build_adapter, MODEL_CHEAP
            adapter = build_adapter(model=MODEL_CHEAP) if not dry_run else None
            result = run_director(args, project=project, adapter=adapter, dry_run=dry_run, verbose=verbose)
            return result.report or "(director produced no report)"
        except Exception as e:
            return f"Director error: {e}"

    elif cmd == "ancestry":
        if not args:
            return "Usage: /ancestry <project-slug>"
        try:
            from ancestry import orch_ancestry, get_project_ancestry
            from orch import project_dir
            d = project_dir(args.strip())
            chain = orch_ancestry(args.strip(), d)
            return "\n".join(chain)
        except Exception as e:
            return f"Ancestry error: {e}"

    elif cmd == "help":
        return (
            "*Poe commands*\n"
            "/status — system health and stuck projects\n"
            "/director <directive> — run full Director/Worker pipeline\n"
            "/research <goal> — run a research worker\n"
            "/build <goal> — run a build worker\n"
            "/ops <command> — run an ops worker\n"
            "/ancestry <project> — show goal ancestry chain\n"
            "/help — show this message\n"
            "\nOr just send a natural language message."
        )

    else:
        return f"Unknown command /{cmd}. Try /help."


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

_ACK_MESSAGE = "⏳ Working on it..."


def _process_message(
    bot: TelegramBot,
    message: dict[str, Any],
    allowed_chats: set[int],
    *,
    dry_run: bool = False,
    project: str = "poe-telegram",
    verbose: bool = True,
) -> None:
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    from_user = message.get("from", {}).get("username", "unknown")

    if not text or not chat_id:
        return

    if allowed_chats and chat_id not in allowed_chats:
        if verbose:
            print(f"[telegram] ignoring message from unauthorized chat {chat_id}", file=sys.stderr)
        return

    if verbose:
        print(f"[telegram] @{from_user} → {text[:80]}", file=sys.stderr)

    # Parse slash command or natural language
    cmd, args = _parse_slash_command(text)

    if dry_run:
        # In dry-run, just process without sending
        if cmd:
            _dispatch_slash(cmd, args, project=project, dry_run=True, verbose=verbose)
        else:
            try:
                result = handle(text, project=project, dry_run=True, verbose=verbose)
            except Exception as e:
                if verbose:
                    print(f"[telegram] dry-run handle() failed: {e}", file=sys.stderr)
        return

    # Send immediate ack for non-trivial commands
    ack_id = 0
    if cmd in ("director", "research", "build", "ops") or (cmd is None and len(text) > 20):
        try:
            ack_id = bot.send_message_returning_id(chat_id, _ACK_MESSAGE)
        except Exception:
            pass
    else:
        bot.send_typing(chat_id)

    # Execute
    try:
        if cmd:
            response = _dispatch_slash(cmd, args, project=project, dry_run=False, verbose=verbose)
        else:
            result = handle(text, project=project, dry_run=False, verbose=verbose)
            response = result.response or "(no response)"
    except Exception as e:
        response = f"Error: {e}"
        if verbose:
            print(f"[telegram] execution failed: {e}", file=sys.stderr)

    if verbose:
        print(f"[telegram] → {response[:120]}", file=sys.stderr)

    # Send/edit response
    try:
        if ack_id:
            bot.edit_message(chat_id, ack_id, response)
        else:
            bot.send_message(chat_id, response)
    except Exception as e:
        print(f"[telegram] send/edit failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def poll_once(
    *,
    dry_run: bool = False,
    project: str = "poe-telegram",
    verbose: bool = True,
) -> int:
    """Process all pending updates once. Returns number of updates processed."""
    token = _resolve_token()
    if not token:
        raise RuntimeError("No Telegram bot token found (set TELEGRAM_BOT_TOKEN or configure openclaw.json)")

    bot = TelegramBot(token)
    allowed = _resolve_allowed_chats()
    offset = _load_offset()

    updates = bot.get_updates(offset=offset, timeout=0)
    count = 0
    for update in updates:
        update_id = update.get("update_id", 0)
        message = update.get("message")
        if message:
            _process_message(bot, message, allowed, dry_run=dry_run, project=project, verbose=verbose)
            count += 1
        _save_offset(update_id + 1)

    return count


def poll_loop(
    *,
    poll_interval: float = 1.0,
    dry_run: bool = False,
    project: str = "poe-telegram",
    verbose: bool = True,
) -> None:
    """Long-poll Telegram indefinitely, routing messages through handle()."""
    token = _resolve_token()
    if not token:
        raise RuntimeError("No Telegram bot token found (set TELEGRAM_BOT_TOKEN or configure openclaw.json)")

    bot = TelegramBot(token)
    allowed = _resolve_allowed_chats()

    if verbose:
        print(f"[telegram] Poe listening (allowed_chats={allowed or 'all'})", file=sys.stderr)

    offset = _load_offset()

    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=20)
        except Exception as e:
            print(f"[telegram] getUpdates error: {e}", file=sys.stderr)
            time.sleep(5)
            continue

        for update in updates:
            update_id = update.get("update_id", 0)
            message = update.get("message")
            if message:
                _process_message(bot, message, allowed, dry_run=dry_run, project=project, verbose=verbose)
            _save_offset(update_id + 1)
            offset = update_id + 1

        if not updates:
            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# CLI entry point (standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Poe Telegram listener")
    parser.add_argument("--once", action="store_true", help="Process pending updates once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Process but don't send responses")
    parser.add_argument("--project", default="poe-telegram", help="Project slug for memory")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    if args.once:
        n = poll_once(dry_run=args.dry_run, project=args.project, verbose=args.verbose)
        print(f"Processed {n} updates.")
    else:
        poll_loop(dry_run=args.dry_run, project=args.project, verbose=args.verbose)
