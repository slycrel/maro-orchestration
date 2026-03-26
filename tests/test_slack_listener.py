"""Tests for Slack listener (Phase 24 skeleton)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import slack_listener


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def test_resolve_bot_token_from_env(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    assert slack_listener._resolve_bot_token() == "xoxb-test-token"


def test_resolve_app_token_from_env(monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test-token")
    assert slack_listener._resolve_app_token() == "xapp-test-token"


def test_resolve_allowed_channels_from_env(monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_CHANNELS", "C01ABC,C02DEF")
    channels = slack_listener._resolve_allowed_channels()
    assert "C01ABC" in channels
    assert "C02DEF" in channels


def test_resolve_allowed_channels_empty(monkeypatch):
    monkeypatch.delenv("SLACK_ALLOWED_CHANNELS", raising=False)
    with patch.object(slack_listener, "_resolve_secrets_env", return_value={}):
        with patch.object(slack_listener, "_load_openclaw_cfg", return_value={}):
            channels = slack_listener._resolve_allowed_channels()
    assert channels == set()


def test_resolve_bot_token_falls_back_to_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    secrets_env = {"SLACK_BOT_TOKEN": "xoxb-from-env-file"}
    with patch.object(slack_listener, "_resolve_secrets_env", return_value=secrets_env):
        with patch.object(slack_listener, "_load_openclaw_cfg", return_value={}):
            token = slack_listener._resolve_bot_token()
    assert token == "xoxb-from-env-file"


def test_resolve_bot_token_falls_back_to_openclaw_cfg(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    cfg = {"slack": {"bot_token": "xoxb-from-cfg"}}
    with patch.object(slack_listener, "_resolve_secrets_env", return_value={}):
        with patch.object(slack_listener, "_load_openclaw_cfg", return_value=cfg):
            token = slack_listener._resolve_bot_token()
    assert token == "xoxb-from-cfg"


def test_resolve_bot_token_missing_returns_empty(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    with patch.object(slack_listener, "_resolve_secrets_env", return_value={}):
        with patch.object(slack_listener, "_load_openclaw_cfg", return_value={}):
            token = slack_listener._resolve_bot_token()
    assert token == ""


# ---------------------------------------------------------------------------
# _parse_slash_command
# ---------------------------------------------------------------------------

def test_parse_slash_command_basic():
    cmd, args = slack_listener._parse_slash_command("/status")
    assert cmd == "status"
    assert args == ""


def test_parse_slash_command_with_args():
    cmd, args = slack_listener._parse_slash_command("/director research polymarket")
    assert cmd == "director"
    assert args == "research polymarket"


def test_parse_slash_command_natural_language():
    cmd, args = slack_listener._parse_slash_command("research quantum computing")
    assert cmd == ""
    assert args == "research quantum computing"


def test_parse_slash_command_empty():
    cmd, args = slack_listener._parse_slash_command("")
    assert cmd == ""
    assert args == ""


# ---------------------------------------------------------------------------
# _dispatch_slash
# ---------------------------------------------------------------------------

def test_dispatch_slash_help_returns_text():
    mock_client = MagicMock()
    result = slack_listener._dispatch_slash("help", "", "C01", mock_client, dry_run=True)
    assert "Poe Slack commands" in result
    assert "/status" in result
    assert "/observe" in result


def test_dispatch_slash_unknown_returns_empty():
    mock_client = MagicMock()
    result = slack_listener._dispatch_slash("unknowncmd", "", "C01", mock_client, dry_run=True)
    assert result == ""


def test_dispatch_slash_stop_no_loop():
    mock_client = MagicMock()
    with patch.object(slack_listener, "is_loop_running", return_value=False):
        result = slack_listener._dispatch_slash("stop", "", "C01", mock_client, dry_run=True)
    assert "No loop" in result


def test_dispatch_slash_director_requires_args():
    mock_client = MagicMock()
    result = slack_listener._dispatch_slash("director", "", "C01", mock_client, dry_run=True)
    assert "Usage" in result


def test_dispatch_slash_observe_runs():
    mock_client = MagicMock()
    with patch("observe.print_snapshot") as mock_snap:
        mock_snap.return_value = None
        result = slack_listener._dispatch_slash("observe", "", "C01", mock_client, dry_run=True)
    # Should not error; result may be empty string (mocked print)
    assert result is not None


def test_dispatch_slash_knowledge_runs():
    mock_client = MagicMock()
    with patch("knowledge.print_dashboard") as mock_dash:
        mock_dash.return_value = None
        result = slack_listener._dispatch_slash("knowledge", "", "C01", mock_client, dry_run=True)
    assert result is not None


# ---------------------------------------------------------------------------
# _process_message
# ---------------------------------------------------------------------------

def test_process_message_dry_run_no_send():
    mock_client = MagicMock()
    slack_listener._process_message(
        mock_client, "C01", "/help", user="testuser",
        dry_run=True, verbose=False,
    )
    # dry_run should prevent sending
    mock_client.chat_postMessage.assert_not_called()


def test_process_message_empty_text_no_op():
    mock_client = MagicMock()
    slack_listener._process_message(
        mock_client, "C01", "", user="testuser",
        dry_run=True, verbose=False,
    )
    mock_client.chat_postMessage.assert_not_called()


def test_process_message_empty_channel_no_op():
    mock_client = MagicMock()
    slack_listener._process_message(
        mock_client, "", "/help", user="testuser",
        dry_run=True, verbose=False,
    )
    mock_client.chat_postMessage.assert_not_called()


def test_process_message_help_sends_response():
    mock_client = MagicMock()
    slack_listener._process_message(
        mock_client, "C01", "/help", user="testuser",
        dry_run=False, verbose=False,
    )
    mock_client.chat_postMessage.assert_called_once()
    call_kwargs = mock_client.chat_postMessage.call_args[1]
    assert call_kwargs["channel"] == "C01"
    assert "Poe Slack commands" in call_kwargs["text"]


def test_process_message_routes_to_interrupt_when_loop_active():
    mock_client = MagicMock()
    mock_queue = MagicMock()
    with patch.object(slack_listener, "is_loop_running", return_value=True):
        with patch.object(slack_listener, "InterruptQueue", return_value=mock_queue):
            slack_listener._process_message(
                mock_client, "C01", "continue with the analysis", user="testuser",
                dry_run=False, verbose=False,
            )
    mock_queue.post.assert_called_once()
    posted = mock_queue.post.call_args[0][0]
    assert posted["source"] == "slack"
    assert "analysis" in posted["text"]


# ---------------------------------------------------------------------------
# listen_socket_mode error paths
# ---------------------------------------------------------------------------

def test_listen_socket_mode_raises_without_sdk():
    with patch.object(slack_listener, "_SLACK_AVAILABLE", False):
        with pytest.raises(RuntimeError, match="slack-sdk"):
            slack_listener.listen_socket_mode()


def test_listen_socket_mode_raises_without_bot_token():
    with patch.object(slack_listener, "_SLACK_AVAILABLE", True):
        with patch.object(slack_listener, "_resolve_bot_token", return_value=""):
            with patch.object(slack_listener, "_resolve_app_token", return_value="xapp-test"):
                with pytest.raises(RuntimeError, match="SLACK_BOT_TOKEN"):
                    slack_listener.listen_socket_mode()


def test_listen_socket_mode_raises_without_app_token():
    with patch.object(slack_listener, "_SLACK_AVAILABLE", True):
        with patch.object(slack_listener, "_resolve_bot_token", return_value="xoxb-test"):
            with patch.object(slack_listener, "_resolve_app_token", return_value=""):
                with pytest.raises(RuntimeError, match="SLACK_APP_TOKEN"):
                    slack_listener.listen_socket_mode()
