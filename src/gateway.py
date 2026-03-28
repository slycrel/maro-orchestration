#!/usr/bin/env python3
"""Phase 15: OpenClaw gateway integration for Poe orchestration.

Connects to the OpenClaw WebSocket gateway at ws://127.0.0.1:18789.
Reads auth from ~/.openclaw/openclaw.json (never logs or prints the token).
Gracefully handles missing websockets library by falling back to TCP check.

Usage:
    from gateway import check_gateway_connection, send_to_gateway, GatewayResult
    connected = check_gateway_connection()
    result = send_to_gateway("hello from poe")
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Optional websockets import — graceful fallback if not installed
try:
    import websockets  # type: ignore[import]
    import asyncio
    _WEBSOCKETS_AVAILABLE = True
except ImportError:
    _WEBSOCKETS_AVAILABLE = False
    websockets = None  # type: ignore[assignment]
    asyncio = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

try:
    from config import openclaw_cfg_path as _openclaw_cfg_path
    _OPENCLAW_CFG = _openclaw_cfg_path()
except Exception:
    _OPENCLAW_CFG = Path.home() / ".openclaw" / "openclaw.json"
_DEFAULT_GATEWAY_URL = "ws://127.0.0.1:18789"
_DEFAULT_GATEWAY_HOST = "127.0.0.1"
_DEFAULT_GATEWAY_PORT = 18789


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GatewayMessage:
    type: str           # "request" | "response" | "status" | "error"
    content: str
    source: str         # "poe" | "openclaw"
    timestamp: str
    message_id: str     # uuid[:8]
    auth_token: str = ""   # only on outbound, never logged


@dataclass
class GatewayResult:
    connected: bool
    sent: bool
    response: Optional[str]
    error: Optional[str]
    elapsed_ms: int


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_gateway_config() -> dict:
    """Read gateway URL and auth token from ~/.openclaw/openclaw.json.

    Returns:
        Dict with "url" and "auth_token" keys. Defaults if file not found.
        Auth token is never logged or printed.
    """
    defaults = {"url": _DEFAULT_GATEWAY_URL, "auth_token": ""}

    if not _OPENCLAW_CFG.exists():
        return defaults

    try:
        raw = _OPENCLAW_CFG.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return defaults

    # Gateway section: {"port": 18789, "mode": ..., "auth": {"token": "..."}, ...}
    gw = data.get("gateway", {})
    port = gw.get("port", _DEFAULT_GATEWAY_PORT)
    host = gw.get("bind", _DEFAULT_GATEWAY_HOST)
    # Normalize host
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    url = f"ws://{host}:{port}"

    # Auth token — read but never logged
    auth_token = ""
    gw_auth = gw.get("auth", {})
    if isinstance(gw_auth, dict):
        auth_token = gw_auth.get("token", "") or ""
    elif isinstance(gw_auth, str):
        auth_token = gw_auth

    return {"url": url, "auth_token": auth_token}


# ---------------------------------------------------------------------------
# Connection check
# ---------------------------------------------------------------------------

def check_gateway_connection() -> bool:
    """Try to open a connection to the gateway URL.

    Tries websockets first (if available), falls back to TCP socket check.
    Uses a 1s timeout. Never raises — returns False on any failure.

    Returns:
        True if connected, False otherwise.
    """
    config = _load_gateway_config()
    url = config.get("url", _DEFAULT_GATEWAY_URL)

    # Parse host/port from URL
    try:
        # ws://host:port[/path]
        without_scheme = url.split("://", 1)[-1]
        host_port = without_scheme.split("/")[0]
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = _DEFAULT_GATEWAY_PORT
    except Exception:
        host = _DEFAULT_GATEWAY_HOST
        port = _DEFAULT_GATEWAY_PORT

    # Try websockets if available
    if _WEBSOCKETS_AVAILABLE and asyncio is not None:
        try:
            async def _try_ws():
                async with websockets.connect(url, open_timeout=1):
                    return True

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_try_ws())
            finally:
                loop.close()
        except Exception:
            pass
        return False

    # Fallback: TCP socket check
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Send / receive
# ---------------------------------------------------------------------------

def send_to_gateway(message: str, *, timeout_seconds: int = 10) -> GatewayResult:
    """Connect to gateway, send a JSON message, wait for response.

    Auth token is included in the message payload but never logged.

    Args:
        message:         Text content to send.
        timeout_seconds: Max seconds to wait for response (default: 10).

    Returns:
        GatewayResult with connected/sent/response/error/elapsed_ms fields.
    """
    start_ms = time.monotonic()
    config = _load_gateway_config()
    url = config.get("url", _DEFAULT_GATEWAY_URL)
    auth_token = config.get("auth_token", "")

    msg = GatewayMessage(
        type="request",
        content=message,
        source="poe",
        timestamp=datetime.now(timezone.utc).isoformat(),
        message_id=str(uuid.uuid4())[:8],
        auth_token=auth_token,  # included in payload, not logged
    )

    payload = json.dumps({
        "type": msg.type,
        "content": msg.content,
        "source": msg.source,
        "timestamp": msg.timestamp,
        "message_id": msg.message_id,
        "auth_token": msg.auth_token,  # sent to gateway, not logged here
    })

    if _WEBSOCKETS_AVAILABLE and asyncio is not None:
        try:
            async def _send():
                async with websockets.connect(url, open_timeout=timeout_seconds) as ws:
                    await ws.send(payload)
                    try:
                        import asyncio as _aio
                        raw = await _aio.wait_for(ws.recv(), timeout=timeout_seconds)
                        return str(raw)
                    except Exception:
                        return None

            loop = asyncio.new_event_loop()
            try:
                response = loop.run_until_complete(_send())
            finally:
                loop.close()

            elapsed = int((time.monotonic() - start_ms) * 1000)
            return GatewayResult(
                connected=True,
                sent=True,
                response=response,
                error=None,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - start_ms) * 1000)
            # Don't include auth token in error messages
            err_msg = str(exc)
            return GatewayResult(
                connected=False,
                sent=False,
                response=None,
                error=err_msg,
                elapsed_ms=elapsed,
            )

    # No websockets — try raw TCP to at least check connectivity
    elapsed = int((time.monotonic() - start_ms) * 1000)
    connected = check_gateway_connection()
    return GatewayResult(
        connected=connected,
        sent=False,
        response=None,
        error="websockets library not available — cannot send messages" if connected else "gateway not reachable",
        elapsed_ms=elapsed,
    )


async def send_to_gateway_async(message: str, *, timeout_seconds: int = 10) -> GatewayResult:
    """Async version of send_to_gateway.

    Available when asyncio is importable. Falls back to sync check if
    websockets not available.

    Args:
        message:         Text content to send.
        timeout_seconds: Max seconds to wait for response (default: 10).

    Returns:
        GatewayResult with connected/sent/response/error/elapsed_ms fields.
    """
    start_ms = time.monotonic()
    config = _load_gateway_config()
    url = config.get("url", _DEFAULT_GATEWAY_URL)
    auth_token = config.get("auth_token", "")

    if not _WEBSOCKETS_AVAILABLE:
        elapsed = int((time.monotonic() - start_ms) * 1000)
        return GatewayResult(
            connected=False,
            sent=False,
            response=None,
            error="websockets library not available",
            elapsed_ms=elapsed,
        )

    msg_payload = json.dumps({
        "type": "request",
        "content": message,
        "source": "poe",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message_id": str(uuid.uuid4())[:8],
        "auth_token": auth_token,
    })

    try:
        import asyncio as _aio
        async with websockets.connect(url, open_timeout=timeout_seconds) as ws:
            await ws.send(msg_payload)
            try:
                raw = await _aio.wait_for(ws.recv(), timeout=timeout_seconds)
                response = str(raw)
            except ImportError:
                response = None

        elapsed = int((time.monotonic() - start_ms) * 1000)
        return GatewayResult(
            connected=True,
            sent=True,
            response=response,
            error=None,
            elapsed_ms=elapsed,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - start_ms) * 1000)
        return GatewayResult(
            connected=False,
            sent=False,
            response=None,
            error=str(exc),
            elapsed_ms=elapsed,
        )


def receive_from_gateway(
    handler: Callable[[str], None],
    *,
    timeout_seconds: int = 30,
) -> None:
    """Connect and listen for incoming messages, call handler for each.

    Used by telegram_listener to forward Telegram messages to OpenClaw.
    Stops after timeout_seconds or when connection closes.

    Args:
        handler:         Callable to invoke for each received message string.
        timeout_seconds: Max seconds to listen (default: 30).
    """
    if not _WEBSOCKETS_AVAILABLE or asyncio is None:
        return

    config = _load_gateway_config()
    url = config.get("url", _DEFAULT_GATEWAY_URL)

    async def _listen():
        try:
            import asyncio as _aio
            async with websockets.connect(url, open_timeout=5) as ws:
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        raw = await _aio.wait_for(ws.recv(), timeout=min(remaining, 5.0))
                        try:
                            handler(str(raw))
                        except Exception:
                            pass
                    except _aio.TimeoutError:
                        continue
                    except Exception:
                        break
        except Exception:
            pass

    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_listen())
        finally:
            loop.close()
    except Exception:
        pass
