"""Conversation channel abstraction — Phase 62.

ConversationChannel is the base interface for bidirectional communication
between the agent loop and a user. ThreadChannel is the dashboard
implementation backed by an in-memory event log and a blocking reply queue.

Usage:
    from conversation import create_channel, get_channel, list_channels

    ch = create_channel("abc123", "research polymarket strategies")
    ch.emit("step", text="Doing X")
    reply = ch.ask("Which dataset should I use?", timeout=300)
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class ConversationChannel:
    """Abstract bidirectional channel between an agent loop and a user."""

    def emit(self, event_type: str, text: str = "", **metadata: Any) -> None:
        raise NotImplementedError

    def ask(self, question: str, *, timeout: int = 300) -> Optional[str]:
        raise NotImplementedError

    def notify_low_confidence(
        self,
        decision: str,
        confidence: float,
        reasoning: str,
    ) -> None:
        """Non-blocking: emit a low_confidence advisory event."""
        self.emit(
            "low_confidence",
            text=decision,
            confidence=confidence,
            reasoning=reasoning,
        )

    def complete(self, result: str) -> None:
        """Mark the conversation as complete and emit a completion event."""
        self.emit("complete", text=result)


# ---------------------------------------------------------------------------
# ThreadChannel — dashboard implementation
# ---------------------------------------------------------------------------

def _threads_dir() -> Path:
    """Return path to the threads persistence directory."""
    try:
        from config import memory_dir  # type: ignore
        return memory_dir() / "threads"
    except Exception:
        return Path.home() / ".poe" / "workspace" / "memory" / "threads"


class ThreadChannel(ConversationChannel):
    """In-memory conversation channel backed by a JSONL file.

    Thread-safe. Designed to be created once per agent run and polled
    by the dashboard's /api/thread/<handle_id> endpoint.
    """

    def __init__(self, handle_id: str, goal: str) -> None:
        self.handle_id: str = handle_id
        self.goal: str = goal
        self.status: str = "running"
        self.waiting_for_reply: bool = False
        self.created_at: str = datetime.now(timezone.utc).isoformat()

        self._events: List[Dict[str, Any]] = []
        self._lock: threading.Lock = threading.Lock()
        self._inbox: "queue.Queue[str]" = queue.Queue()

        # Persistence path — created lazily
        self._jsonl_path: Optional[Path] = None

        # Emit the initial goal event
        self.emit("user_goal", text=goal)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_jsonl_path(self) -> Optional[Path]:
        if self._jsonl_path is not None:
            return self._jsonl_path
        try:
            d = _threads_dir()
            d.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = d / f"{self.handle_id}.jsonl"
        except Exception:
            self._jsonl_path = None
        return self._jsonl_path

    def _append_to_file(self, event: Dict[str, Any]) -> None:
        try:
            path = self._get_jsonl_path()
            if path is None:
                return
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
        except Exception:
            pass  # persistence failures must never disrupt the loop

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event_type: str, text: str = "", **metadata: Any) -> None:
        """Append an event to the in-memory list and JSONL file."""
        event: Dict[str, Any] = {
            "type": event_type,
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        event.update(metadata)

        with self._lock:
            self._events.append(event)

        self._append_to_file(event)

        # Update channel status when relevant events arrive
        if event_type == "complete":
            self.status = "complete"
        elif event_type == "error":
            self.status = "error"

    def ask(self, question: str, *, timeout: int = 300) -> Optional[str]:
        """Emit a question event, block until reply arrives (or timeout).

        Returns the reply string, or None on timeout.
        """
        self.emit("question", text=question)
        self.waiting_for_reply = True
        try:
            reply = self._inbox.get(timeout=timeout)
            self.emit("user_reply", text=reply)
            return reply
        except queue.Empty:
            self.emit("question_timeout", text=question)
            return None
        finally:
            self.waiting_for_reply = False

    def receive_reply(self, text: str) -> None:
        """Put a user reply into the inbox queue (called by API endpoint)."""
        self._inbox.put(text)

    def events_since(self, idx: int) -> List[Dict[str, Any]]:
        """Return a thread-safe slice of events starting from idx."""
        with self._lock:
            return list(self._events[idx:])


# ---------------------------------------------------------------------------
# Global channel registry
# ---------------------------------------------------------------------------

_registry: Dict[str, ThreadChannel] = {}
_registry_lock = threading.Lock()


def create_channel(handle_id: str, goal: str) -> ThreadChannel:
    """Create a new ThreadChannel and register it globally."""
    ch = ThreadChannel(handle_id, goal)
    with _registry_lock:
        _registry[handle_id] = ch
    return ch


def get_channel(handle_id: str) -> Optional[ThreadChannel]:
    """Look up a channel by handle_id. Returns None if not found."""
    with _registry_lock:
        return _registry.get(handle_id)


def list_channels() -> List[Dict[str, Any]]:
    """Return summary dicts for all registered channels (newest last)."""
    with _registry_lock:
        channels = list(_registry.values())
    return [
        {
            "handle_id": ch.handle_id,
            "goal": ch.goal,
            "status": ch.status,
            "waiting": ch.waiting_for_reply,
            "created_at": ch.created_at,
            "event_count": len(ch._events),
        }
        for ch in sorted(channels, key=lambda c: c.created_at)
    ]


# ---------------------------------------------------------------------------
# Disk-based channel recovery (survives service restarts)
# ---------------------------------------------------------------------------

def _load_channel_from_jsonl(path: Path) -> Optional[ThreadChannel]:
    """Reconstruct a read-only channel snapshot from a JSONL file.

    Channels that were still 'running' at shutdown are marked 'interrupted'
    since their execution process no longer exists.
    """
    try:
        events: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        if not events:
            return None

        # Extract metadata from the first user_goal event
        goal_event = next((e for e in events if e.get("type") == "user_goal"), None)
        goal = goal_event["text"] if goal_event else ""
        created_at = events[0].get("ts", datetime.now(timezone.utc).isoformat())
        handle_id = path.stem

        # Determine terminal status from events
        terminal_types = {"complete", "error"}
        last_type = events[-1].get("type", "")
        if last_type in terminal_types:
            status = last_type
        else:
            # Process was killed mid-run — mark as interrupted
            status = "interrupted"

        ch = ThreadChannel.__new__(ThreadChannel)
        ch.handle_id = handle_id
        ch.goal = goal
        ch.status = status
        ch.waiting_for_reply = False
        ch.created_at = created_at
        ch._events = events
        ch._lock = threading.Lock()
        ch._inbox: "queue.Queue[str]" = queue.Queue()
        ch._jsonl_path = path

        # Append the interrupted marker if needed (once, don't re-add on reload)
        if status == "interrupted" and last_type not in ("interrupted",):
            interrupted_event = {
                "type": "interrupted",
                "text": "Service restarted — run did not complete.",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            ch._events.append(interrupted_event)
            try:
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(interrupted_event) + "\n")
            except Exception:
                pass

        return ch
    except Exception:
        return None


def load_channels_from_disk(max_age_days: int = 7) -> int:
    """Scan the threads directory and reload recent channels into the registry.

    Called once at service startup. Skips channels already in the registry.
    Returns the number of channels loaded.
    """
    import time as _time
    cutoff = _time.time() - max_age_days * 86400
    loaded = 0
    try:
        threads_dir = _threads_dir()
        if not threads_dir.exists():
            return 0
        paths = sorted(threads_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths:
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                handle_id = path.stem
                with _registry_lock:
                    if handle_id in _registry:
                        continue  # already live
                ch = _load_channel_from_jsonl(path)
                if ch is not None:
                    with _registry_lock:
                        _registry[handle_id] = ch
                    loaded += 1
            except Exception:
                continue
    except Exception:
        pass
    return loaded
