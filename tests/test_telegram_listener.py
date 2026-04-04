"""Tests for telegram_listener.py — Telegram → handle() bridge."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from telegram_listener import (
    TelegramBot,
    _resolve_token,
    _resolve_allowed_chats,
    _parse_slash_command,
    _dispatch_slash,
    poll_once,
    _process_message,
)


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def test_resolve_token_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    assert _resolve_token() == "test-token-123"


def test_resolve_token_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # Point at non-existent openclaw.json
    with patch("telegram_listener._OPENCLAW_CFG", tmp_path / "nofile.json"):
        token = _resolve_token()
    assert token == ""


def test_resolve_allowed_chats_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    chats = _resolve_allowed_chats()
    assert 123456789 in chats


def test_resolve_allowed_chats_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_NOTIFY_CHAT_ID", raising=False)
    with patch("telegram_listener._OPENCLAW_CFG", tmp_path / "nofile.json"):
        chats = _resolve_allowed_chats()
    assert chats == set()


# ---------------------------------------------------------------------------
# TelegramBot helpers
# ---------------------------------------------------------------------------

def test_telegram_bot_send_message_chunks():
    """Long messages should be split into 4096-char chunks."""
    bot = TelegramBot("fake-token")
    calls = []
    bot._call = lambda method, **params: calls.append((method, params)) or {}

    long_text = "x" * 9000
    bot.send_message(12345, long_text)
    assert len(calls) == 3  # ceil(9000/4096)
    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["chat_id"] == 12345


def test_telegram_bot_send_message_short():
    bot = TelegramBot("fake-token")
    calls = []
    bot._call = lambda method, **params: calls.append((method, params)) or {}

    bot.send_message(99, "hello")
    assert len(calls) == 1
    assert calls[0][1]["text"] == "hello"


# ---------------------------------------------------------------------------
# _process_message — routing and filtering
# ---------------------------------------------------------------------------

def _make_tg_message(text: str, chat_id: int = 111, username: str = "testuser") -> dict:
    return {
        "message_id": 1,
        "from": {"id": chat_id, "username": username},
        "chat": {"id": chat_id, "type": "private"},
        "text": text,
    }


def test_process_message_ignored_if_not_allowed():
    bot = MagicMock()
    _process_message(bot, _make_tg_message("hello", chat_id=999), allowed_chats={111}, dry_run=True)
    bot.send_message.assert_not_called()


def test_process_message_dry_run_no_send():
    bot = MagicMock()
    with patch("telegram_listener.handle") as mock_handle:
        mock_handle.return_value = MagicMock(response="dry run reply")
        _process_message(bot, _make_tg_message("hello", chat_id=111), allowed_chats={111}, dry_run=True)
    bot.send_message.assert_not_called()


def test_process_message_routes_to_handle():
    # Phase 13: natural language messages now route through poe_handle first.
    # Patch poe_handle to return a known response.
    # Also patch is_loop_running to False — parallel test workers can leave loop state set,
    # causing interrupt routing instead of poe_handle routing.
    bot = MagicMock()
    with patch("telegram_listener.poe_handle") as mock_poe, \
         patch("telegram_listener.is_loop_running", return_value=False):
        from poe import PoeResponse
        mock_poe.return_value = PoeResponse(message="great answer", routed_to="now_lane")
        _process_message(bot, _make_tg_message("what time is it?", chat_id=111), allowed_chats={111}, dry_run=False)
    mock_poe.assert_called_once()
    bot.send_message.assert_called_once_with(111, "great answer")


def test_process_message_handle_error_sends_error():
    # Both poe_handle and handle raise — should fall through to error response
    bot = MagicMock()
    with patch("telegram_listener.poe_handle", side_effect=RuntimeError("poe boom")), \
         patch("telegram_listener.handle", side_effect=RuntimeError("boom")), \
         patch("telegram_listener.is_loop_running", return_value=False):
        _process_message(bot, _make_tg_message("fail this", chat_id=111), allowed_chats=set(), dry_run=False)
    bot.send_message.assert_called_once()
    assert "Error" in bot.send_message.call_args[0][1]


def test_process_message_empty_text_ignored():
    bot = MagicMock()
    _process_message(bot, {"chat": {"id": 111}, "text": ""}, allowed_chats=set(), dry_run=True)
    bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# poll_once — offset persistence
# ---------------------------------------------------------------------------

def test_poll_once_no_token_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with patch("telegram_listener._OPENCLAW_CFG", tmp_path / "nofile.json"):
        with pytest.raises(RuntimeError, match="bot token"):
            poll_once(dry_run=True)


def test_poll_once_processes_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")

    fake_updates = [
        {"update_id": 100, "message": _make_tg_message("hello", chat_id=111)},
        {"update_id": 101, "message": _make_tg_message("world", chat_id=111)},
    ]

    with patch("telegram_listener._OFFSET_FILE", tmp_path / "offset.txt"), \
         patch("telegram_listener.TelegramBot.get_updates", return_value=fake_updates), \
         patch("telegram_listener._process_message") as mock_proc:
        count = poll_once(dry_run=True, project="test")

    assert count == 2
    assert mock_proc.call_count == 2


def test_poll_once_saves_offset(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    fake_updates = [{"update_id": 42, "message": _make_tg_message("hi", chat_id=5)}]
    offset_file = tmp_path / "offset.txt"

    with patch("telegram_listener._OFFSET_FILE", offset_file), \
         patch("telegram_listener.TelegramBot.get_updates", return_value=fake_updates), \
         patch("telegram_listener._process_message"):
        poll_once(dry_run=True)

    assert offset_file.read_text() == "43"


def test_poll_once_no_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    with patch("telegram_listener._OFFSET_FILE", tmp_path / "offset.txt"), \
         patch("telegram_listener.TelegramBot.get_updates", return_value=[]):
        count = poll_once(dry_run=True)

    assert count == 0


# ---------------------------------------------------------------------------
# Slash command parsing
# ---------------------------------------------------------------------------

def test_parse_slash_command_basic():
    cmd, args = _parse_slash_command("/status")
    assert cmd == "status"
    assert args == ""


def test_parse_slash_command_with_args():
    cmd, args = _parse_slash_command("/director research polymarket")
    assert cmd == "director"
    assert args == "research polymarket"


def test_parse_slash_command_at_suffix():
    cmd, args = _parse_slash_command("/status@mybotname")
    assert cmd == "status"


def test_parse_slash_command_natural_language():
    cmd, args = _parse_slash_command("what time is it?")
    assert cmd is None
    assert args == "what time is it?"


def test_parse_slash_command_help():
    cmd, _ = _parse_slash_command("/help")
    assert cmd == "help"


# ---------------------------------------------------------------------------
# Slash command dispatch
# ---------------------------------------------------------------------------

def test_dispatch_slash_help():
    response = _dispatch_slash("help", "", project="test", dry_run=True, verbose=False)
    assert "/status" in response
    assert "/director" in response


def test_dispatch_slash_unknown():
    response = _dispatch_slash("foobar", "", project="test", dry_run=True, verbose=False)
    assert "Unknown" in response or "foobar" in response


def test_dispatch_slash_director_no_args():
    response = _dispatch_slash("director", "", project="test", dry_run=True, verbose=False)
    assert "Usage" in response


def test_dispatch_slash_status():
    # Phase 13: /status routes through poe_handle for executive summary.
    # Patch poe_handle to return a predictable response.
    from poe import PoeResponse
    with patch("telegram_listener.poe_handle") as mock_poe:
        mock_poe.return_value = PoeResponse(
            message="Executive summary: system healthy, no active missions.",
            routed_to="status",
        )
        response = _dispatch_slash("status", "", project="test", dry_run=False, verbose=False)
    assert "healthy" in response.lower() or "summary" in response.lower() or "Executive" in response


# ---------------------------------------------------------------------------
# Response timing — ack + edit
# ---------------------------------------------------------------------------

def test_process_message_sends_ack_for_long_message():
    """Long natural-language messages should get an immediate ack, then an edit."""
    # Phase 13: poe_handle is called first for natural language.
    # Patch is_loop_running=False so parallel test workers don't trigger interrupt routing.
    from poe import PoeResponse
    bot = MagicMock()
    bot.send_message_returning_id.return_value = 42

    with patch("telegram_listener.poe_handle") as mock_poe, \
         patch("telegram_listener.is_loop_running", return_value=False):
        mock_poe.return_value = PoeResponse(message="detailed answer here", routed_to="now_lane")
        _process_message(
            bot,
            _make_tg_message("this is a longer message that should get an ack", chat_id=111),
            allowed_chats={111},
            dry_run=False,
        )

    bot.send_message_returning_id.assert_called_once()
    bot.edit_message.assert_called_once_with(111, 42, "detailed answer here")
    bot.send_message.assert_not_called()


def test_process_message_short_message_no_ack():
    """Short messages (<=20 chars) skip the ack, use typing indicator."""
    # Patch is_loop_running=False so parallel test workers don't trigger interrupt routing.
    from poe import PoeResponse
    bot = MagicMock()
    bot.send_message_returning_id.return_value = 0

    with patch("telegram_listener.poe_handle") as mock_poe, \
         patch("telegram_listener.is_loop_running", return_value=False):
        mock_poe.return_value = PoeResponse(message="hi", routed_to="now_lane")
        _process_message(
            bot,
            _make_tg_message("hi", chat_id=111),
            allowed_chats={111},
            dry_run=False,
        )

    bot.send_message_returning_id.assert_not_called()
    bot.send_message.assert_called_once_with(111, "hi")


def test_process_message_slash_director_sends_ack():
    """Slash director commands should always get an ack."""
    bot = MagicMock()
    bot.send_message_returning_id.return_value = 99

    with patch("telegram_listener._dispatch_slash", return_value="done"):
        _process_message(
            bot,
            _make_tg_message("/director do something", chat_id=111),
            allowed_chats={111},
            dry_run=False,
        )

    bot.send_message_returning_id.assert_called_once()
    bot.edit_message.assert_called_once()


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_telegram_no_token(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with patch("telegram_listener._OPENCLAW_CFG", tmp_path / "nofile.json"):
        import cli
        rc = cli.main(["poe-telegram", "--once"])
    assert rc != 0


def test_cli_poe_telegram_once(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    with patch("telegram_listener._OFFSET_FILE", tmp_path / "offset.txt"), \
         patch("telegram_listener.TelegramBot.get_updates", return_value=[]):
        import cli
        rc = cli.main(["poe-telegram", "--once"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "processed=0" in out
