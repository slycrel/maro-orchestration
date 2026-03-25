"""Phase 15: Tests for gateway.py — OpenClaw WebSocket gateway integration.

Tests cover connection checking, send/receive, config loading, and graceful
fallbacks when websockets library is not installed.
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gateway as gw_mod
from gateway import (
    GatewayMessage,
    GatewayResult,
    _load_gateway_config,
    check_gateway_connection,
    send_to_gateway,
)


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------

def test_gateway_result_fields():
    """GatewayResult dataclass has connected, sent, response, error, elapsed_ms."""
    field_names = {f.name for f in fields(GatewayResult)}
    assert "connected" in field_names
    assert "sent" in field_names
    assert "response" in field_names
    assert "error" in field_names
    assert "elapsed_ms" in field_names


def test_gateway_message_fields():
    """GatewayMessage dataclass has expected fields."""
    field_names = {f.name for f in fields(GatewayMessage)}
    assert "type" in field_names
    assert "content" in field_names
    assert "source" in field_names
    assert "timestamp" in field_names
    assert "message_id" in field_names
    assert "auth_token" in field_names


def test_gateway_result_construction():
    """GatewayResult can be constructed with all fields."""
    r = GatewayResult(
        connected=False,
        sent=False,
        response=None,
        error="test error",
        elapsed_ms=42,
    )
    assert r.connected is False
    assert r.sent is False
    assert r.response is None
    assert r.error == "test error"
    assert r.elapsed_ms == 42


def test_gateway_message_construction():
    """GatewayMessage can be constructed and auth_token has a default."""
    msg = GatewayMessage(
        type="request",
        content="hello",
        source="poe",
        timestamp="2026-01-01T00:00:00+00:00",
        message_id="abc12345",
    )
    assert msg.type == "request"
    assert msg.content == "hello"
    assert msg.source == "poe"
    assert msg.auth_token == ""  # default


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------

def test_load_gateway_config_missing_file(tmp_path):
    """Returns defaults gracefully when ~/.openclaw/openclaw.json doesn't exist."""
    missing = tmp_path / "does_not_exist.json"
    with patch.object(gw_mod, "_OPENCLAW_CFG", missing):
        config = _load_gateway_config()
    assert "url" in config
    assert "auth_token" in config
    assert config["url"].startswith("ws://")
    # No exception raised


def test_load_gateway_config_corrupt_json(tmp_path):
    """Returns defaults gracefully when file contains invalid JSON."""
    bad = tmp_path / "openclaw.json"
    bad.write_text("NOT VALID JSON {{{", encoding="utf-8")
    with patch.object(gw_mod, "_OPENCLAW_CFG", bad):
        config = _load_gateway_config()
    assert config["url"] == gw_mod._DEFAULT_GATEWAY_URL
    assert config["auth_token"] == ""


def test_load_gateway_config_present(tmp_path):
    """Reads url field from valid openclaw.json."""
    cfg_file = tmp_path / "openclaw.json"
    cfg_file.write_text(json.dumps({
        "gateway": {
            "port": 19999,
            "bind": "127.0.0.1",
            "auth": {"token": "secret-test-token"},
        }
    }), encoding="utf-8")
    with patch.object(gw_mod, "_OPENCLAW_CFG", cfg_file):
        config = _load_gateway_config()
    assert "19999" in config["url"]
    assert config["auth_token"] == "secret-test-token"


def test_load_gateway_config_binds_0000(tmp_path):
    """Normalizes 0.0.0.0 bind to 127.0.0.1 in the URL."""
    cfg_file = tmp_path / "openclaw.json"
    cfg_file.write_text(json.dumps({
        "gateway": {
            "port": 18789,
            "bind": "0.0.0.0",
            "auth": {},
        }
    }), encoding="utf-8")
    with patch.object(gw_mod, "_OPENCLAW_CFG", cfg_file):
        config = _load_gateway_config()
    assert "0.0.0.0" not in config["url"]
    assert "127.0.0.1" in config["url"]


def test_load_gateway_config_auth_string(tmp_path):
    """Handles auth as a plain string instead of dict."""
    cfg_file = tmp_path / "openclaw.json"
    cfg_file.write_text(json.dumps({
        "gateway": {
            "port": 18789,
            "auth": "plain-string-token",
        }
    }), encoding="utf-8")
    with patch.object(gw_mod, "_OPENCLAW_CFG", cfg_file):
        config = _load_gateway_config()
    assert config["auth_token"] == "plain-string-token"


# ---------------------------------------------------------------------------
# Connection check tests
# ---------------------------------------------------------------------------

def test_check_gateway_connection_refused():
    """Nothing listening on a random high port → returns False, no raise."""
    # Use a port that should never be listening in test environment
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        # Point to a port nothing is listening on
        with patch.object(gw_mod, "_load_gateway_config", return_value={
            "url": "ws://127.0.0.1:19876",
            "auth_token": "",
        }):
            result = check_gateway_connection()
    assert result is False


def test_check_gateway_connection_no_module():
    """Graceful fallback if websockets is not installed — returns bool, never raises."""
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "_load_gateway_config", return_value={
            "url": "ws://127.0.0.1:19877",
            "auth_token": "",
        }):
            result = check_gateway_connection()
    assert isinstance(result, bool)


def test_check_gateway_never_raises():
    """check_gateway_connection never raises regardless of bad input."""
    bad_inputs = [
        {"url": "ws://invalid-host-xyz:0", "auth_token": ""},
        {"url": "not-a-url", "auth_token": ""},
        {"url": "", "auth_token": ""},
        {"url": "ws://127.0.0.1:1", "auth_token": ""},  # port 1 should always be refused
    ]
    for cfg in bad_inputs:
        with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
            with patch.object(gw_mod, "_load_gateway_config", return_value=cfg):
                try:
                    result = check_gateway_connection()
                    assert isinstance(result, bool)
                except Exception as exc:
                    pytest.fail(f"check_gateway_connection raised with config {cfg}: {exc}")


def test_check_gateway_socket_error_returns_false():
    """Socket exception → returns False."""
    import socket
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "_load_gateway_config", return_value={
            "url": "ws://127.0.0.1:19999",
            "auth_token": "",
        }):
            with patch("socket.socket") as mock_socket_cls:
                mock_sock = MagicMock()
                mock_sock.connect_ex.side_effect = OSError("network down")
                mock_socket_cls.return_value = mock_sock
                result = check_gateway_connection()
    assert result is False


# ---------------------------------------------------------------------------
# send_to_gateway tests
# ---------------------------------------------------------------------------

def test_send_to_gateway_not_connected():
    """send_to_gateway returns GatewayResult(connected=False) when nothing is listening."""
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "check_gateway_connection", return_value=False):
            result = send_to_gateway("hello test")
    assert isinstance(result, GatewayResult)
    assert result.connected is False
    assert result.sent is False


def test_send_not_connected_result():
    """send_to_gateway returns GatewayResult with connected=False and error set."""
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "check_gateway_connection", return_value=False):
            result = send_to_gateway("some message")
    assert result.connected is False
    assert result.error is not None
    assert len(result.error) > 0


def test_send_to_gateway_no_websockets_connected():
    """When websockets not available but gateway reachable: connected=True, sent=False."""
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "check_gateway_connection", return_value=True):
            result = send_to_gateway("test message")
    assert result.connected is True
    assert result.sent is False
    assert result.error is not None
    assert "websockets" in result.error.lower()


def test_send_to_gateway_elapsed_ms():
    """GatewayResult always has a non-negative elapsed_ms."""
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "check_gateway_connection", return_value=False):
            result = send_to_gateway("timing test")
    assert isinstance(result.elapsed_ms, int)
    assert result.elapsed_ms >= 0


def test_send_to_gateway_auth_not_in_error():
    """Auth token must not appear in error messages."""
    cfg = {
        "url": "ws://127.0.0.1:19876",
        "auth_token": "super-secret-token-12345",
    }
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "_load_gateway_config", return_value=cfg):
            with patch.object(gw_mod, "check_gateway_connection", return_value=False):
                result = send_to_gateway("test")
    # Auth token must never appear in error
    if result.error:
        assert "super-secret-token-12345" not in result.error


def test_send_to_gateway_no_raise_on_bad_host():
    """send_to_gateway returns GatewayResult even when the host is unreachable."""
    with patch.object(gw_mod, "_WEBSOCKETS_AVAILABLE", False):
        with patch.object(gw_mod, "_load_gateway_config", return_value={
            "url": "ws://invalid-host-xyz-99999:9",
            "auth_token": "",
        }):
            with patch.object(gw_mod, "check_gateway_connection", return_value=False):
                try:
                    result = send_to_gateway("test")
                    assert isinstance(result, GatewayResult)
                except Exception as exc:
                    pytest.fail(f"send_to_gateway raised unexpectedly: {exc}")
