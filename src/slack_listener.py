"""Slack listener for Poe — Socket Mode polling, routes through handle().

Mirrors telegram_listener.py: same slash commands, same interrupt routing,
same dry_run / verbose API. Uses Slack's Socket Mode (no public endpoint needed —
works on a headless box behind NAT).

Configuration (in priority order):
  1. Environment: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_ALLOWED_CHANNELS
  2. Workspace secrets: {workspace}/secrets/.env  (SLACK_BOT_TOKEN=... lines)
  3. Legacy openclaw.json: slack.bot_token, slack.app_token, slack.allowed_channels

Dependencies:
  pip install slack-sdk   (or add to pyproject.toml [project.optional-dependencies] slack)

Socket Mode requires two tokens:
  - Bot token:  xoxb-...  (SLACK_BOT_TOKEN) — sends messages
  - App token:  xapp-...  (SLACK_APP_TOKEN) — Socket Mode connection
  Both are created in api.slack.com/apps → your app → OAuth / Socket Mode.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional Slack SDK import (graceful degradation)
# ---------------------------------------------------------------------------

try:
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    _SLACK_AVAILABLE = True
except ImportError:
    _SLACK_AVAILABLE = False
    WebClient = None          # type: ignore[assignment,misc]
    SocketModeClient = None   # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Same handle imports as telegram_listener
# ---------------------------------------------------------------------------

try:
    from handle import handle
except ImportError:
    handle = None  # type: ignore[assignment]

try:
    from poe import poe_handle
except ImportError:
    poe_handle = None  # type: ignore[assignment]

try:
    from interrupt import InterruptQueue, is_loop_running, get_running_loop
except ImportError:
    InterruptQueue = None       # type: ignore[assignment]
    is_loop_running = None      # type: ignore[assignment]
    get_running_loop = None     # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _resolve_secrets_env() -> dict[str, str]:
    try:
        from config import credentials_env_file
        return _load_dotenv(credentials_env_file())
    except Exception:
        return {}


def _load_openclaw_cfg() -> dict:
    try:
        from config import openclaw_cfg_path, load_openclaw_cfg
        return load_openclaw_cfg()
    except Exception:
        return {}


def _resolve_bot_token() -> str:
    # 1. Env var
    t = os.environ.get("SLACK_BOT_TOKEN", "")
    if t:
        return t
    # 2. secrets/.env
    env = _resolve_secrets_env()
    t = env.get("SLACK_BOT_TOKEN", "")
    if t:
        return t
    # 3. openclaw.json
    cfg = _load_openclaw_cfg()
    return cfg.get("slack", {}).get("bot_token", "")


def _resolve_app_token() -> str:
    t = os.environ.get("SLACK_APP_TOKEN", "")
    if t:
        return t
    env = _resolve_secrets_env()
    t = env.get("SLACK_APP_TOKEN", "")
    if t:
        return t
    cfg = _load_openclaw_cfg()
    return cfg.get("slack", {}).get("app_token", "")


def _resolve_allowed_channels() -> set[str]:
    raw = os.environ.get("SLACK_ALLOWED_CHANNELS", "")
    if not raw:
        env = _resolve_secrets_env()
        raw = env.get("SLACK_ALLOWED_CHANNELS", "")
    if not raw:
        cfg = _load_openclaw_cfg()
        raw = cfg.get("slack", {}).get("allowed_channels", "")
    if isinstance(raw, list):
        return {str(c) for c in raw}
    return {c.strip() for c in str(raw).split(",") if c.strip()} if raw else set()


# ---------------------------------------------------------------------------
# Slash command dispatch (mirrors telegram_listener._dispatch_slash)
# ---------------------------------------------------------------------------

def _parse_slash_command(text: str) -> tuple[str, str]:
    """Return (cmd, args) from a Slack message.

    Slack slash commands arrive as separate payloads but users can also type
    /cmd in a DM or channel — handle both.
    """
    text = text.strip()
    if text.startswith("/"):
        parts = text[1:].split(None, 1)
        return parts[0].lower(), parts[1] if len(parts) > 1 else ""
    return "", text


def _dispatch_slash(
    cmd: str,
    args: str,
    channel_id: str,
    client: Any,   # WebClient
    *,
    dry_run: bool = False,
    project: str = "poe-slack",
    verbose: bool = True,
) -> str:
    """Route a Slack slash command to the appropriate handler. Returns response text."""
    if cmd == "status":
        if poe_handle is not None:
            return poe_handle("/status", dry_run=dry_run)
        return "[poe_handle not available]"

    elif cmd == "observe":
        # Quick snapshot via poe-observe
        try:
            from observe import print_snapshot
            import io
            buf = io.StringIO()
            _orig_stdout = sys.stdout
            sys.stdout = buf
            try:
                print_snapshot()
            finally:
                sys.stdout = _orig_stdout
            return buf.getvalue()
        except Exception as e:
            return f"observe error: {e}"

    elif cmd == "knowledge":
        try:
            from knowledge import print_dashboard
            import io
            buf = io.StringIO()
            _orig_stdout = sys.stdout
            sys.stdout = buf
            try:
                print_dashboard()
            finally:
                sys.stdout = _orig_stdout
            return buf.getvalue()
        except Exception as e:
            return f"knowledge error: {e}"

    elif cmd in ("director", "research", "build", "ops"):
        if not args:
            return f"Usage: /{cmd} <goal>"
        if is_loop_running and is_loop_running():
            # Interrupt routing
            if InterruptQueue is not None:
                q = InterruptQueue()
                q.post({"type": "message", "text": args, "source": "slack", "channel_id": channel_id})
                return "⏳ Loop is active — message queued as interrupt"
        from agent_loop import run_agent_loop
        try:
            result = run_agent_loop(args, project=project, dry_run=dry_run)
            return f"✅ Done: {result.summary[:400]}" if hasattr(result, "summary") else f"✅ Done ({result.status})"
        except Exception as e:
            return f"❌ Error: {e}"

    elif cmd == "stop":
        if is_loop_running and is_loop_running():
            loop_id = get_running_loop() if get_running_loop else None
            if InterruptQueue is not None:
                q = InterruptQueue()
                q.post({"type": "stop", "source": "slack"})
                return f"🛑 Stop signal posted (loop: {loop_id})"
        return "No loop is currently running."

    elif cmd == "help":
        return (
            "*Poe Slack commands*\n"
            "/status — executive summary\n"
            "/observe — execution snapshot (loop, heartbeat, outcomes, audit)\n"
            "/knowledge — crystallization dashboard\n"
            "/director <directive> — run Director/Worker pipeline\n"
            "/research <goal> — run research worker\n"
            "/build <goal> — run build worker\n"
            "/ops <command> — run ops worker\n"
            "/stop — stop the currently running loop\n"
            "/help — this message\n"
            "\nOr send a natural language message in the allowed channel."
        )

    else:
        # Unknown slash command or natural language
        return ""


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

def _process_message(
    client: Any,   # WebClient
    channel_id: str,
    text: str,
    user: str = "unknown",
    *,
    dry_run: bool = False,
    project: str = "poe-slack",
    verbose: bool = True,
) -> None:
    """Route a Slack message through the handle layer."""
    if not text or not channel_id:
        return

    if verbose:
        print(f"[slack] @{user} → {text[:80]}", file=sys.stderr)

    cmd, args = _parse_slash_command(text)

    if dry_run:
        if verbose:
            print(f"[slack] dry_run — would process cmd={cmd!r} args={args[:60]!r}", file=sys.stderr)
        return

    # Check for interrupt routing when loop is active
    if is_loop_running and is_loop_running() and not cmd:
        if InterruptQueue is not None:
            q = InterruptQueue()
            q.post({"type": "message", "text": text, "source": "slack", "channel_id": channel_id})
            _send(client, channel_id, "⏳ Loop is running — message queued as interrupt")
            return

    if cmd:
        response = _dispatch_slash(cmd, args, channel_id, client, dry_run=dry_run, project=project, verbose=verbose)
        if not response:
            # Treat as natural language if dispatch returned empty
            response = _run_natural(text, dry_run=dry_run, project=project, verbose=verbose)
    else:
        response = _run_natural(text, dry_run=dry_run, project=project, verbose=verbose)

    if response:
        _send(client, channel_id, response)


def _run_natural(text: str, *, dry_run: bool, project: str, verbose: bool) -> str:
    """Run a natural language message through the agent loop."""
    if poe_handle is not None:
        try:
            return poe_handle(text, dry_run=dry_run)
        except Exception as e:
            return f"Error: {e}"
    elif handle is not None:
        try:
            return handle(text)
        except Exception as e:
            return f"Error: {e}"
    return "poe_handle not available"


def _send(client: Any, channel_id: str, text: str) -> None:
    try:
        client.chat_postMessage(channel=channel_id, text=text)
    except Exception as e:
        print(f"[slack] send failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Socket Mode listener
# ---------------------------------------------------------------------------

def listen_socket_mode(
    *,
    dry_run: bool = False,
    project: str = "poe-slack",
    verbose: bool = True,
) -> None:
    """Connect via Socket Mode and listen for messages indefinitely.

    Requires slack_sdk: pip install slack-sdk
    Requires App Token (xapp-...) with connections:write scope.
    Requires Bot Token (xoxb-...) with channels:read, chat:write, app_mentions:read.
    """
    if not _SLACK_AVAILABLE:
        raise RuntimeError(
            "slack-sdk not installed. Run: pip install slack-sdk\n"
            "Or: pip install 'poe-orchestration[slack]'"
        )

    bot_token = _resolve_bot_token()
    app_token = _resolve_app_token()
    allowed = _resolve_allowed_channels()

    if not bot_token:
        raise RuntimeError("No SLACK_BOT_TOKEN found (env, secrets/.env, or openclaw.json)")
    if not app_token:
        raise RuntimeError("No SLACK_APP_TOKEN found — needed for Socket Mode")

    web_client = WebClient(token=bot_token)
    socket_client = SocketModeClient(app_token=app_token, web_client=web_client)

    if verbose:
        print(f"[slack] Poe listening via Socket Mode (allowed_channels={allowed or 'all'})", file=sys.stderr)

    def _on_event(client: SocketModeClient, req: SocketModeRequest) -> None:
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        payload = req.payload
        event = payload.get("event", {})
        event_type = event.get("type", "")

        if event_type in ("message", "app_mention"):
            channel_id = event.get("channel", "")
            text = event.get("text", "").strip()
            user = event.get("user", "unknown")
            bot_id = event.get("bot_id")

            if bot_id:
                return  # ignore our own messages

            if allowed and channel_id not in allowed:
                if verbose:
                    print(f"[slack] ignoring message from non-allowed channel {channel_id}", file=sys.stderr)
                return

            _process_message(
                web_client, channel_id, text, user=user,
                dry_run=dry_run, project=project, verbose=verbose,
            )

    socket_client.socket_mode_request_listeners.append(_on_event)
    socket_client.connect()

    if verbose:
        print("[slack] connected — waiting for events", file=sys.stderr)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if verbose:
            print("[slack] shutting down", file=sys.stderr)
        socket_client.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="poe-slack",
        description="Slack Socket Mode listener for Poe",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log messages but don't execute")
    parser.add_argument("--project", default="poe-slack", help="Project name for loop runs")
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = parser.parse_args(argv)

    listen_socket_mode(
        dry_run=args.dry_run,
        project=args.project,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
