#!/usr/bin/env python3
"""Interrupt queue for the Poe orchestration system.

Source-agnostic: Telegram, CLI, Slack, heartbeat — any interface can post an
interrupt. The agent loop polls between steps and handles it.

Interrupt intents:
    additive   — "Also research X" → append new steps after current
    corrective — "Actually focus on Y instead" → replace remaining steps
    priority   — "First do Z" → prepend new steps before current
    stop       — "Stop" / "Halt" / "Cancel" → gracefully terminate loop

Usage (producer side — any interface):
    from interrupt import InterruptQueue
    q = InterruptQueue()
    q.post("also check for rate limiting issues", source="telegram")

Usage (consumer side — agent loop):
    q = InterruptQueue()
    interrupts = q.poll()
    for intr in interrupts:
        handle_interrupt(intr, remaining_steps)
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

INTENT_ADDITIVE = "additive"
INTENT_CORRECTIVE = "corrective"
INTENT_PRIORITY = "priority"
INTENT_STOP = "stop"

VALID_INTENTS = {INTENT_ADDITIVE, INTENT_CORRECTIVE, INTENT_PRIORITY, INTENT_STOP}

# Stop keywords that don't need LLM classification
_STOP_KEYWORDS = frozenset({
    "stop", "halt", "cancel", "abort", "quit", "exit", "kill",
    "nevermind", "never mind", "forget it",
})


@dataclass
class Interrupt:
    id: str
    message: str
    source: str                # "telegram" | "cli" | "slack" | "heartbeat" | "api"
    intent: str                # one of VALID_INTENTS
    new_steps: List[str] = field(default_factory=list)   # for additive/priority/corrective
    replacement_goal: Optional[str] = None               # for corrective
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    applied: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Interrupt":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()


class InterruptQueue:
    """File-backed interrupt queue. Thread-safe, process-safe via line-append."""

    def __init__(self, queue_path: Optional[Path] = None):
        if queue_path is None:
            queue_path = _default_queue_path()
        self.path = Path(queue_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def post(
        self,
        message: str,
        source: str = "cli",
        *,
        adapter=None,
        intent: Optional[str] = None,
    ) -> Interrupt:
        """Post an interrupt. Classifies intent via LLM if not provided.

        Args:
            message: The interrupt message text.
            source: Who sent it ("telegram", "cli", "slack", etc.).
            adapter: LLM adapter for intent parsing (uses cheap model).
                     If None, uses heuristic classification.
            intent: Override intent classification (skip LLM).

        Returns:
            The created Interrupt (already persisted).
        """
        if intent is None:
            intent, new_steps, replacement_goal = _classify_intent(message, adapter=adapter)
        else:
            intent = intent if intent in VALID_INTENTS else INTENT_ADDITIVE
            new_steps, replacement_goal = _extract_steps_heuristic(message, intent)

        intr = Interrupt(
            id=str(uuid.uuid4())[:8],
            message=message,
            source=source,
            intent=intent,
            new_steps=new_steps,
            replacement_goal=replacement_goal,
        )
        self._append(intr)
        return intr

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def poll(self) -> List[Interrupt]:
        """Read and remove all pending (unapplied) interrupts.

        Returns list in arrival order. Marks them applied in the file.
        Thread/process-safe via lock + rewrite.
        """
        with _LOCK:
            lines = self._read_lines()
            if not lines:
                return []

            pending = []
            updated = []
            for line in lines:
                try:
                    d = json.loads(line)
                    if not d.get("applied", False):
                        d["applied"] = True
                        pending.append(Interrupt.from_dict(d))
                    updated.append(json.dumps(d))
                except (json.JSONDecodeError, TypeError):
                    updated.append(line)

            if pending:
                self.path.write_text("\n".join(updated) + "\n", encoding="utf-8")

            return pending

    def peek(self) -> List[Interrupt]:
        """Return pending interrupts without marking them applied."""
        lines = self._read_lines()
        result = []
        for line in lines:
            try:
                d = json.loads(line)
                if not d.get("applied", False):
                    result.append(Interrupt.from_dict(d))
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def clear(self) -> int:
        """Clear all pending interrupts. Returns count cleared."""
        with _LOCK:
            lines = self._read_lines()
            count = 0
            updated = []
            for line in lines:
                try:
                    d = json.loads(line)
                    if not d.get("applied", False):
                        d["applied"] = True
                        count += 1
                    updated.append(json.dumps(d))
                except (json.JSONDecodeError, TypeError):
                    updated.append(line)
            if count:
                self.path.write_text("\n".join(updated) + "\n", encoding="utf-8")
            return count

    def is_empty(self) -> bool:
        """Return True if no pending interrupts."""
        return len(self.peek()) == 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _append(self, intr: Interrupt) -> None:
        with _LOCK:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(intr.to_dict()) + "\n")

    def _read_lines(self) -> List[str]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8").strip()
        return [l for l in text.splitlines() if l.strip()] if text else []


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """\
You are a message classifier for an autonomous AI agent loop.
Given a user message sent while an agent is running a task, classify it.

Output ONLY valid JSON with this shape:
{
  "intent": "additive|corrective|priority|stop",
  "new_steps": ["step1", "step2"],
  "replacement_goal": null
}

Intent rules:
- "stop": user wants to halt execution ("stop", "cancel", "abort", "never mind", etc.)
- "priority": user wants something done FIRST, before current remaining steps
- "corrective": user wants to change direction / replace the current goal
- "additive": user wants to add more work after the current plan

new_steps: concrete steps extracted from the message (empty list for "stop")
replacement_goal: full new goal string for "corrective", null otherwise
"""


def _classify_intent(
    message: str,
    adapter=None,
) -> tuple[str, List[str], Optional[str]]:
    """Return (intent, new_steps, replacement_goal).

    Uses LLM if adapter provided, otherwise heuristic.
    """
    # Fast heuristic for stop commands
    normalized = message.lower().strip().rstrip("!.").strip()
    if normalized in _STOP_KEYWORDS or any(normalized.startswith(kw + " ") for kw in _STOP_KEYWORDS):
        return INTENT_STOP, [], None

    if adapter is None:
        return _classify_heuristic(message)

    try:
        from llm import LLMMessage, MODEL_CHEAP
        resp = adapter.complete(
            [
                LLMMessage("system", _CLASSIFY_SYSTEM),
                LLMMessage("user", f"Message: {message}"),
            ],
            max_tokens=512,
            temperature=0.1,
        )
        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            d = json.loads(content[start:end])
            intent = d.get("intent", INTENT_ADDITIVE)
            if intent not in VALID_INTENTS:
                intent = INTENT_ADDITIVE
            new_steps = [s for s in d.get("new_steps", []) if isinstance(s, str)]
            replacement_goal = d.get("replacement_goal") or None
            return intent, new_steps, replacement_goal
    except Exception:
        pass

    return _classify_heuristic(message)


def _classify_heuristic(message: str) -> tuple[str, List[str], Optional[str]]:
    """Simple keyword-based fallback classification."""
    lower = message.lower()

    if any(kw in lower for kw in ["stop", "halt", "cancel", "abort", "never mind", "forget it"]):
        return INTENT_STOP, [], None

    if any(lower.startswith(kw) for kw in ["first ", "before that ", "prioritize ", "urgently "]):
        steps = _extract_steps_heuristic(message, INTENT_PRIORITY)[0]
        return INTENT_PRIORITY, steps, None

    if any(kw in lower for kw in ["instead", "actually", "change the goal", "new goal", "switch to", "focus on"]):
        steps = _extract_steps_heuristic(message, INTENT_CORRECTIVE)[0]
        # Treat the whole message as replacement goal
        return INTENT_CORRECTIVE, steps, message

    # Default: additive
    steps = _extract_steps_heuristic(message, INTENT_ADDITIVE)[0]
    return INTENT_ADDITIVE, steps, None


def _extract_steps_heuristic(message: str, intent: str) -> tuple[List[str], Optional[str]]:
    """Extract steps from a message without LLM."""
    if intent == INTENT_STOP:
        return [], None

    # Simple: treat message as a single step
    step = message.strip()
    # Remove leading filler words
    for prefix in ["also ", "additionally ", "first ", "then ", "and ", "also, ", "please "]:
        if step.lower().startswith(prefix):
            step = step[len(prefix):]
            break

    replacement_goal = message if intent == INTENT_CORRECTIVE else None
    return [step] if step else [], replacement_goal


# ---------------------------------------------------------------------------
# Loop integration helper
# ---------------------------------------------------------------------------

def apply_interrupt_to_steps(
    interrupt: Interrupt,
    remaining_steps: List[str],
    goal: str,
) -> tuple[List[str], str, bool]:
    """Apply an interrupt to the current step plan.

    Returns:
        (new_remaining_steps, new_goal, should_stop)
    """
    if interrupt.intent == INTENT_STOP:
        return [], goal, True

    if interrupt.intent == INTENT_ADDITIVE:
        new_steps = remaining_steps + interrupt.new_steps
        return new_steps, goal, False

    if interrupt.intent == INTENT_PRIORITY:
        new_steps = interrupt.new_steps + remaining_steps
        return new_steps, goal, False

    if interrupt.intent == INTENT_CORRECTIVE:
        new_goal = interrupt.replacement_goal or goal
        new_steps = interrupt.new_steps if interrupt.new_steps else remaining_steps
        return new_steps, new_goal, False

    return remaining_steps, goal, False


# ---------------------------------------------------------------------------
# Loop lock — lets any interface know a loop is currently running
# ---------------------------------------------------------------------------

def _default_lock_path() -> Path:
    try:
        import orch
        return orch.orch_root() / "memory" / "loop.lock"
    except Exception:
        pass
    try:
        from config import memory_dir
        return memory_dir() / "loop.lock"
    except Exception:
        return Path.home() / ".poe" / "workspace" / "memory" / "loop.lock"


def set_loop_running(loop_id: str, goal: str = "") -> None:
    """Write a lockfile marking an agent loop as active."""
    path = _default_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "loop_id": loop_id,
        "goal": goal[:120],
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def clear_loop_running() -> None:
    """Remove the loop lockfile."""
    path = _default_lock_path()
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def get_running_loop() -> Optional[dict]:
    """Return info about the currently running loop, or None if idle.

    Verifies the PID is still alive before trusting the lockfile.
    """
    path = _default_lock_path()
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        pid = d.get("pid", 0)
        if pid:
            try:
                os.kill(pid, 0)  # signal 0 = just check if process exists
            except OSError:
                # PID is dead — stale lock
                path.unlink(missing_ok=True)
                return None
        return d
    except Exception:
        return None


def is_loop_running() -> bool:
    """Return True if an agent loop is currently active."""
    return get_running_loop() is not None


# ---------------------------------------------------------------------------
# Default queue path
# ---------------------------------------------------------------------------

def _default_queue_path() -> Path:
    """Return the default interrupt queue file path."""
    # Try to resolve via orch module; fall back to CWD-relative path
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import orch
        return orch.orch_root() / "memory" / "interrupts.jsonl"
    except Exception:
        return Path.home() / ".openclaw" / "workspace" / "memory" / "interrupts.jsonl"
