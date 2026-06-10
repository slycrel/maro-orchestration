"""recall() — the unified memory read seam (goal-brain sequencing, step 3).

One question, one function: "what do I already know that's relevant right now?"
Behind the signature the substrates compose (run metadata, outcomes, tiered
lessons, standing rules, decisions, knowledge nodes); callers never talk to a
substrate directly. Design: docs/RECALL_DESIGN.md.

Slices (same seam, different depth):
- "dispatch" — identity + history only. No LLM calls, pure local file reads,
  cheap enough for every task dequeue. This is the answer to the 2026-06-10
  pressure-test findings 1+3: the same goal ran ~25x in 35 minutes on
  2026-05-17 because nothing at the requeue boundary asked "have we seen this
  before, and how did it go?"
- "loop" — dispatch plus the knowledge injections agent_loop reads today.
  Currently a PARTIAL composition with no caller — see the correction note
  at the slice implementation below before wiring anything to it.
- "navigator" — defined in RECALL_DESIGN.md, not implemented; no consumer
  exists until the navigator schema (step 4) ships.

This module writes nothing except its own instrumentation events
(RECALL_PERFORMED). Lifecycle stays in knowledge_web.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("recall")

# Newest-first cap on run-dir metadata reads per recall() call. Keeps dispatch
# O(recent activity), not O(lifetime run count) — 478 dirs and growing.
_METADATA_SCAN_CAP = 200

# Ancestry walk depth limit (a chain longer than this is itself a runaway
# signal; the walk is for identity, not archaeology).
_CHAIN_DEPTH_CAP = 5

_NEAR_MATCH_THRESHOLD = 0.9


@dataclass
class PriorAttempt:
    """A recent run whose goal matches the incoming one."""
    goal: str
    handle_id: str
    status: str          # done | stuck | error | unknown (never finalized)
    when: str            # started_at, ISO-8601
    match: str           # "exact" | "near"


@dataclass
class ThreadIdentity:
    """Where this goal came from, walked via origin ancestry (runs metadata)."""
    parent_goal: str
    parent_handle_id: str
    chain: List[str]     # handle_id chain, immediate parent first
    source: str          # task_store | agent_loop | director | direct | ...


@dataclass
class RecallResult:
    thread: Optional[ThreadIdentity]
    prior_attempts: List[PriorAttempt]
    lessons: str = ""
    standing_rules: str = ""
    decisions: str = ""
    knowledge: str = ""
    sources: Dict[str, Any] = field(default_factory=dict)

    def dispatch_signals(self, *, window_minutes: float = 60.0) -> Dict[str, Any]:
        """Repeat-pressure signals for the dispatch guard.

        repeat_count counts attempts inside the window; all_failing is True
        only when every one of them ended non-done (status done anywhere in
        the window disarms the guard — the goal CAN succeed, repeats may be
        legitimate).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        in_window: List[PriorAttempt] = []
        for a in self.prior_attempts:
            try:
                when = datetime.fromisoformat(a.when)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if when >= cutoff:
                in_window.append(a)
        return {
            "repeat_count": len(in_window),
            "all_failing": bool(in_window) and all(
                a.status != "done" for a in in_window
            ),
            "window_minutes": window_minutes,
        }

    def as_context_block(self, *, max_chars: int = 1200) -> str:
        """One injectable string for ancestry context. Empty when nothing known."""
        parts: List[str] = []
        if self.thread and self.thread.parent_goal:
            parts.append(
                f"This goal descends from: {self.thread.parent_goal!r} "
                f"(handle {self.thread.parent_handle_id or '?'}, "
                f"via {self.thread.source})."
            )
        if self.prior_attempts:
            by_status: Dict[str, int] = {}
            for a in self.prior_attempts:
                by_status[a.status] = by_status.get(a.status, 0) + 1
            breakdown = ", ".join(f"{n} {s}" for s, n in sorted(by_status.items()))
            parts.append(
                f"Prior attempts at this goal (recent window): "
                f"{len(self.prior_attempts)} runs — {breakdown}. "
                f"Newest: {self.prior_attempts[0].when} "
                f"({self.prior_attempts[0].status}). "
                f"Do not repeat an approach that already failed; if every "
                f"prior attempt failed the same way, change the approach or "
                f"surface the blocker instead of retrying."
            )
        for block in (self.lessons, self.standing_rules, self.decisions, self.knowledge):
            if block:
                parts.append(block)
        if not parts:
            return ""
        text = "== Recall (what the system already knows) ==\n" + "\n\n".join(parts)
        return text[:max_chars]


def _read_run_metadata(rd) -> Optional[dict]:
    try:
        return json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _resolve_thread(origin: Optional[dict]) -> Optional[ThreadIdentity]:
    """Walk origin ancestry through run metadata, immediate parent first."""
    if not origin:
        return None
    parent_handle = str(origin.get("parent_handle_id") or "")
    parent_goal = str(origin.get("parent_goal") or "")
    source = str(origin.get("source") or "direct")
    if not parent_handle and not parent_goal:
        return None

    from runs import run_dir
    chain: List[str] = []
    cursor = parent_handle
    while cursor and len(chain) < _CHAIN_DEPTH_CAP:
        chain.append(cursor)
        meta = _read_run_metadata(run_dir(cursor))
        if not meta:
            break
        cursor = str((meta.get("origin") or {}).get("parent_handle_id") or "")
        if cursor in chain:  # cycle guard
            break
    return ThreadIdentity(
        parent_goal=parent_goal,
        parent_handle_id=parent_handle,
        chain=chain,
        source=source,
    )


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _find_prior_attempts(goal: str, *, window_hours: float) -> List[PriorAttempt]:
    """Scan recent run dirs (mtime-ordered, capped) for goal matches."""
    from runs import runs_root
    from memory_ledger import _text_similarity

    root = runs_root()
    if not root.is_dir():
        return []
    try:
        dirs = sorted(
            (d for d in root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    goal_norm = _normalize(goal)
    attempts: List[PriorAttempt] = []
    for rd in dirs[:_METADATA_SCAN_CAP]:
        meta = _read_run_metadata(rd)
        if not meta:
            continue
        started = meta.get("started_at") or ""
        try:
            when = datetime.fromisoformat(started)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if when < cutoff:
            continue
        prompt = str(meta.get("prompt") or "")
        if not prompt:
            continue
        if _normalize(prompt) == goal_norm:
            match = "exact"
        elif _text_similarity(prompt, goal) >= _NEAR_MATCH_THRESHOLD:
            match = "near"
        else:
            continue
        attempts.append(PriorAttempt(
            goal=prompt,
            handle_id=str(meta.get("handle_id") or rd.name.split("-", 1)[0]),
            status=str(meta.get("status") or "unknown"),
            when=started,
            match=match,
        ))
    attempts.sort(key=lambda a: a.when, reverse=True)
    return attempts


def recall(
    goal: str,
    *,
    slice: str = "loop",
    origin: Optional[dict] = None,
    project: str = "",
    window_hours: float = 24.0,
) -> RecallResult:
    """The seam. Read-only; every failure degrades to "knows nothing"."""
    t0 = time.monotonic()
    sources: Dict[str, Any] = {"slice": slice}

    try:
        thread = _resolve_thread(origin)
    except Exception as exc:
        log.debug("recall: thread resolution failed: %s", exc)
        thread = None
    sources["thread_chain_len"] = len(thread.chain) if thread else 0

    try:
        prior = _find_prior_attempts(goal, window_hours=window_hours)
    except Exception as exc:
        log.debug("recall: prior-attempt scan failed: %s", exc)
        prior = []
    sources["prior_attempts"] = len(prior)

    result = RecallResult(thread=thread, prior_attempts=prior, sources=sources)

    if slice in ("loop", "navigator"):
        # PARTIAL composition (4 of 8 substrates) with no production caller
        # yet. agent_loop's `_build_loop_context` already composes all 8 —
        # this slice becomes real when its memory half relocates here (see
        # the RECALL_DESIGN.md correction). Each substrate degrades
        # independently — a broken one never takes the seam down.
        # (navigator slice additionally wants goal-brain + correspondence
        # walk — not implemented.)
        try:
            from memory import inject_lessons_for_task
            result.lessons = inject_lessons_for_task("agenda", goal, max_lessons=3)
        except Exception:
            pass
        try:
            from memory import inject_standing_rules
            result.standing_rules = inject_standing_rules(domain=project)
        except Exception:
            pass
        try:
            from memory import inject_decisions
            result.decisions = inject_decisions(goal, domain=project)
        except Exception:
            pass
        try:
            from knowledge_web import inject_knowledge_for_goal
            result.knowledge = inject_knowledge_for_goal(goal, max_chars=600)
        except Exception:
            pass
        sources["knowledge_blocks"] = sum(
            1 for b in (result.lessons, result.standing_rules,
                        result.decisions, result.knowledge) if b
        )

    sources["elapsed_ms"] = int((time.monotonic() - t0) * 1000)

    # Instrument every call from day one (2026-05-18 decision: static now,
    # logged tuples are the crystallization substrate later).
    try:
        from captains_log import log_event, RECALL_PERFORMED
        log_event(
            RECALL_PERFORMED,
            subject="recall",
            summary=f"recall slice={slice}: {sources['prior_attempts']} prior attempts, "
                    f"thread chain {sources['thread_chain_len']}.",
            context={"goal_preview": goal[:200], **sources},
        )
    except Exception:
        pass

    return result
