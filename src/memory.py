#!/usr/bin/env python3
"""Phase 5: Memory + Learning system for Poe orchestration.

Three memory layers:
1. Session bootstrap: every session loads prior outcomes for context
2. Outcome recording: after each run, record what happened + lessons
3. Reflexion: per-task reflection stored as structured lessons, injected on future similar tasks

File structure (under orch_root()):
    memory/
        YYYY-MM-DD.md          — daily narrative log (append-only)
        outcomes.jsonl          — structured outcome ledger (append-only)
        lessons.jsonl           — structured lessons from reflection (append-only)
        MEMORY.md               — human-readable index + recent highlights

DSPy-style principle: treat lessons as prompt modules. When a similar task
arrives, inject the most relevant lessons. Over time, lessons compound.

Reflexion principle: after each task, reflect on what went well/wrong.
Store the reflection as a structured lesson keyed by task_type + outcome.
On future similar tasks, prepend relevant lessons to the agent's system prompt.

Usage:
    from memory import record_outcome, load_lessons, bootstrap_context
    lessons = load_lessons(task_type="research", limit=5)
    context = bootstrap_context()  # for session start
    record_outcome(goal="...", status="done", summary="...", lessons=["..."])
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    outcome_id: str
    goal: str
    task_type: str          # "research" | "build" | "ops" | "general" | "now" | "agenda"
    status: str             # "done" | "stuck"
    summary: str            # what was accomplished or why it failed
    lessons: List[str]      # list of lesson strings extracted from this run
    project: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Lesson:
    lesson_id: str
    task_type: str          # what kind of task this lesson applies to
    outcome: str            # "done" | "stuck" — what happened
    lesson: str             # the insight
    source_goal: str        # which goal produced this lesson
    confidence: float       # 0.0-1.0 (starts at 0.7, adjusts with reinforcement)
    times_applied: int = 0
    times_reinforced: int = 0
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------

def _memory_dir() -> Path:
    try:
        from orch import orch_root
        d = orch_root() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        d = Path.cwd() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _outcomes_path() -> Path:
    return _memory_dir() / "outcomes.jsonl"


def _lessons_path() -> Path:
    return _memory_dir() / "lessons.jsonl"


def _daily_path(for_date: Optional[date] = None) -> Path:
    d = for_date or date.today()
    return _memory_dir() / f"{d.isoformat()}.md"


def _memory_index_path() -> Path:
    return _memory_dir() / "MEMORY.md"


# ---------------------------------------------------------------------------
# Outcome recording
# ---------------------------------------------------------------------------

def record_outcome(
    goal: str,
    status: str,
    summary: str,
    *,
    task_type: str = "general",
    project: Optional[str] = None,
    lessons: Optional[List[str]] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    elapsed_ms: int = 0,
) -> Outcome:
    """Record the outcome of a completed run.

    Appends to outcomes.jsonl and daily log. Also extracts lessons if provided.
    """
    import uuid
    outcome = Outcome(
        outcome_id=str(uuid.uuid4())[:8],
        goal=goal,
        task_type=task_type,
        status=status,
        summary=summary,
        project=project,
        lessons=lessons or [],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        elapsed_ms=elapsed_ms,
    )

    # Append to outcomes ledger
    with open(_outcomes_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(outcome)) + "\n")

    # Append to daily log
    _append_daily_log(outcome)

    # Store lessons
    for lesson_text in (lessons or []):
        if lesson_text.strip():
            _store_lesson(
                task_type=task_type,
                outcome=status,
                lesson=lesson_text,
                source_goal=goal,
            )

    # Update MEMORY.md index
    _update_memory_index()

    return outcome


def _append_daily_log(outcome: Outcome):
    """Append a human-readable entry to today's daily log."""
    path = _daily_path()
    status_icon = "✓" if outcome.status == "done" else "✗"
    tokens = f"{outcome.tokens_in}in+{outcome.tokens_out}out"
    entry = (
        f"\n## [{outcome.recorded_at[:10]}] {status_icon} {outcome.goal[:80]}\n"
        f"- **Status**: {outcome.status}\n"
        f"- **Type**: {outcome.task_type}\n"
        f"- **Summary**: {outcome.summary}\n"
        f"- **Tokens**: {tokens} in {outcome.elapsed_ms}ms\n"
    )
    if outcome.lessons:
        entry += "- **Lessons**:\n" + "".join(f"  - {l}\n" for l in outcome.lessons)
    if outcome.project:
        entry += f"- **Project**: {outcome.project}\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# Lesson storage + retrieval
# ---------------------------------------------------------------------------

def _store_lesson(
    task_type: str,
    outcome: str,
    lesson: str,
    source_goal: str,
    confidence: float = 0.7,
) -> Lesson:
    """Append a lesson to the lessons ledger."""
    import uuid
    # Check for near-duplicate (same lesson text for same task type)
    existing = load_lessons(task_type=task_type, limit=50)
    for ex in existing:
        if _text_similarity(ex.lesson, lesson) > 0.8:
            # Reinforce existing lesson
            ex.times_reinforced += 1
            ex.confidence = min(1.0, ex.confidence + 0.05)
            # We'd need to rewrite the file to update — keep simple for now
            # (just append the new one; dedup on load)
            break

    l = Lesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type=task_type,
        outcome=outcome,
        lesson=lesson,
        source_goal=source_goal,
        confidence=confidence,
    )
    with open(_lessons_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(l)) + "\n")
    return l


def load_lessons(
    task_type: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    limit: int = 10,
) -> List[Lesson]:
    """Load relevant lessons from the lessons ledger.

    Args:
        task_type: Filter by task type (None = all types).
        outcome_filter: Filter by outcome ("done" | "stuck" | None = all).
        limit: Maximum number of lessons to return (most recent first).

    Returns:
        List of Lesson objects, most recent first.
    """
    path = _lessons_path()
    if not path.exists():
        return []

    lessons = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                l = Lesson(**{k: d[k] for k in Lesson.__dataclass_fields__ if k in d})
                if task_type and l.task_type != task_type:
                    continue
                if outcome_filter and l.outcome != outcome_filter:
                    continue
                lessons.append(l)
            except Exception:
                continue
    except Exception:
        pass

    # Return most recent, deduplicated by lesson text
    seen = set()
    deduped = []
    for l in reversed(lessons):
        key = l.lesson.strip()[:100]
        if key not in seen:
            seen.add(key)
            deduped.append(l)
        if len(deduped) >= limit:
            break
    return deduped


def load_outcomes(limit: int = 20) -> List[Outcome]:
    """Load recent outcomes from the ledger."""
    path = _outcomes_path()
    if not path.exists():
        return []

    outcomes = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                o = Outcome(**{k: d[k] for k in Outcome.__dataclass_fields__ if k in d})
                outcomes.append(o)
            except Exception:
                continue
    except Exception:
        pass

    return list(reversed(outcomes))[:limit]


# ---------------------------------------------------------------------------
# Session bootstrap
# ---------------------------------------------------------------------------

def bootstrap_context(*, max_outcomes: int = 5, max_lessons: int = 10) -> str:
    """Build a context string for session startup.

    Returns a string that can be prepended to the system prompt to give
    the agent memory of recent work and accumulated lessons.
    """
    parts = []

    # Recent outcomes
    outcomes = load_outcomes(limit=max_outcomes)
    if outcomes:
        parts.append("## Recent Work")
        for o in outcomes[:max_outcomes]:
            icon = "✓" if o.status == "done" else "✗"
            parts.append(f"- {icon} {o.goal[:60]} ({o.task_type}, {o.recorded_at[:10]}): {o.summary[:80]}")

    # Key lessons (high-confidence, recent)
    lessons = load_lessons(limit=max_lessons)
    high_conf = [l for l in lessons if l.confidence >= 0.7]
    if high_conf:
        parts.append("\n## Accumulated Lessons")
        for l in high_conf[:max_lessons]:
            parts.append(f"- [{l.task_type}] {l.lesson}")

    if not parts:
        return ""

    return "# Memory Context (from prior sessions)\n\n" + "\n".join(parts)


def inject_lessons_for_task(task_type: str, goal: str, max_lessons: int = 3) -> str:
    """Build a lessons injection string for a specific task type.

    Used to prepend relevant lessons to an agent's system prompt.
    """
    lessons = load_lessons(task_type=task_type, limit=max_lessons)
    if not lessons:
        # Try general lessons
        lessons = load_lessons(task_type="general", limit=max_lessons)

    if not lessons:
        return ""

    lines = ["## Lessons from Prior Runs (apply these)"]
    for l in lessons:
        icon = "✓" if l.outcome == "done" else "✗"
        lines.append(f"- {icon} {l.lesson}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reflexion: post-run lesson extraction
# ---------------------------------------------------------------------------

_REFLECT_SYSTEM = textwrap.dedent("""\
    You are a meta-learning agent. After each completed run, extract durable lessons.
    A lesson is a generalizable insight that would improve future similar runs.
    Good lessons are: specific, actionable, and generalize beyond this one case.
    Bad lessons are: too specific to this one task, or trivially obvious.

    Respond with a JSON array of 1-3 lesson strings.
    Each lesson should be a single sentence.
    Example: ["Research tasks produce better output when the goal includes success criteria",
              "Stuck detection triggers prematurely on research tasks that need multiple iterations"]
""").strip()


def extract_lessons_via_llm(
    goal: str,
    status: str,
    result_summary: str,
    task_type: str,
    *,
    adapter=None,
    dry_run: bool = False,
) -> List[str]:
    """Use LLM to extract generalizable lessons from a completed run.

    Returns list of lesson strings. Falls back to empty list on failure.
    """
    if dry_run or adapter is None:
        # Generate a dry-run lesson
        icon = "succeeded" if status == "done" else "failed"
        return [f"[dry-run lesson] {task_type} task {icon}: {goal[:40]}"]

    from llm import LLMMessage

    user_msg = (
        f"Task type: {task_type}\n"
        f"Goal: {goal}\n"
        f"Outcome: {status}\n"
        f"Summary: {result_summary[:500]}\n\n"
        "Extract 1-3 generalizable lessons."
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _REFLECT_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=256,
            temperature=0.3,
        )
        content = resp.content.strip()
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            lessons = json.loads(content[start:end])
            if isinstance(lessons, list) and all(isinstance(l, str) for l in lessons):
                return [l.strip() for l in lessons if l.strip()][:3]
    except Exception:
        pass

    return []


def reflect_and_record(
    goal: str,
    status: str,
    result_summary: str,
    *,
    task_type: str = "general",
    project: Optional[str] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    elapsed_ms: int = 0,
    adapter=None,
    dry_run: bool = False,
) -> Outcome:
    """Reflect on a completed run and record the outcome + lessons.

    This is the main hook to call after run_agent_loop or handle() completes.
    """
    lessons = extract_lessons_via_llm(
        goal=goal,
        status=status,
        result_summary=result_summary,
        task_type=task_type,
        adapter=adapter,
        dry_run=dry_run,
    )

    return record_outcome(
        goal=goal,
        status=status,
        summary=result_summary,
        task_type=task_type,
        project=project,
        lessons=lessons,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Memory index
# ---------------------------------------------------------------------------

def _update_memory_index():
    """Rewrite MEMORY.md with a current index of memory files."""
    try:
        mem_dir = _memory_dir()
        daily_files = sorted(mem_dir.glob("????-??-??.md"), reverse=True)[:7]

        outcomes = load_outcomes(limit=10)
        done_count = sum(1 for o in outcomes if o.status == "done")
        stuck_count = sum(1 for o in outcomes if o.status == "stuck")
        total_tokens = sum(o.tokens_in + o.tokens_out for o in outcomes)

        lines = [
            "# Memory Index",
            "",
            f"*Auto-updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
            "",
            "## Stats (last 10 runs)",
            f"- Done: {done_count} | Stuck: {stuck_count}",
            f"- Total tokens: {total_tokens:,}",
            "",
            "## Daily Logs",
        ]
        for f in daily_files:
            lines.append(f"- [{f.stem}]({f.name})")

        lines += ["", "## Lessons Count"]
        lesson_path = _lessons_path()
        if lesson_path.exists():
            n = sum(1 for l in lesson_path.read_text().splitlines() if l.strip())
            lines.append(f"- {n} lessons stored in lessons.jsonl")
        else:
            lines.append("- 0 lessons stored")

        _memory_index_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Text similarity (simple — for dedup)
# ---------------------------------------------------------------------------

def _text_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity for lesson deduplication."""
    words_a = set(re.sub(r"[^a-z0-9 ]", "", a.lower()).split())
    words_b = set(re.sub(r"[^a-z0-9 ]", "", b.lower()).split())
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0
