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

# handle() imported at module level so tests can patch telegram_listener.handle
try:
    from handle import handle
except ImportError:  # pragma: no cover
    handle = None  # type: ignore[assignment]


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
# Message handler
# ---------------------------------------------------------------------------

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

    if not dry_run:
        bot.send_typing(chat_id)

    try:
        result = handle(text, project=project, dry_run=dry_run, verbose=verbose)
        response = result.response or "(no response)"
    except Exception as e:
        response = f"Error: {e}"
        if verbose:
            print(f"[telegram] handle() failed: {e}", file=sys.stderr)

    if verbose:
        print(f"[telegram] → {response[:120]}", file=sys.stderr)

    if not dry_run:
        try:
            bot.send_message(chat_id, response)
        except Exception as e:
            print(f"[telegram] send_message failed: {e}", file=sys.stderr)


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
