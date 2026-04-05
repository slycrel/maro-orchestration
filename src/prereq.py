"""Phase 27: Per-step knowledge prerequisite checking.

Extends the goal-level graveyard resurrection in agent_loop with per-step
topic detection and, optionally, sub-loop spawning when graveyard is empty.

Flow (called after _decompose returns steps):

1. For each step, extract knowledge-requiring topics (heuristic, no LLM).
2. For each topic, call search_graveyard(topic, resurrect=True).
   - Hits → inject resurrected lessons as context for that step.
3. If graveyard is empty AND knowledge_sub_goals=True AND continuation_depth==0:
   - Spawn a lightweight run_agent_loop sub-loop to acquire the knowledge.
   - Record result as tiered lesson tagged acquired_for=parent_goal_id.
   - Inject acquired context for the step.

Sub-loop spawn is opt-in (knowledge_sub_goals=True) to avoid unexpected
token cost. Graveyard resurrection is always-on (zero LLM cost).
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

log = logging.getLogger("poe.prereq")

# Keywords that signal a step is explicitly asking for domain knowledge.
# Longer patterns checked first; stop on first match.
_KNOWLEDGE_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?:learn|learning)\s+(?:about|how to)\s+(.+?)(?:\.|,|;|$)", re.I),
    re.compile(r"understand(?:ing)?\s+(?:the\s+)?(.+?)(?:\.|,|;|$)", re.I),
    re.compile(r"research(?:ing)?\s+(.+?)(?:\.|,|;|$)", re.I),
    re.compile(r"study(?:ing)?\s+(.+?)(?:\.|,|;|$)", re.I),
    re.compile(r"(?:acquire|acquiring)\s+knowledge\s+(?:of|about)\s+(.+?)(?:\.|,|;|$)", re.I),
    re.compile(r"(?:domain\s+)?knowledge\s+(?:of|about)\s+(.+?)(?:\.|,|;|$)", re.I),
]

# Cap extracted topic length so graveyard search stays focused.
_MAX_TOPIC_LEN = 60


def detect_knowledge_topics(step_text: str) -> List[str]:
    """Extract knowledge-requiring topics from a step description.

    Uses heuristic regex patterns — no LLM calls. Returns up to 3 topics.
    Falls back to using the whole step text (truncated) when no pattern matches
    but the step contains a knowledge signal word.

    Examples:
        "Learn about Japanese writing systems" → ["Japanese writing systems"]
        "Research stroke order in kanji calligraphy" → ["stroke order in kanji calligraphy"]
        "Build the API endpoint" → []
    """
    found: List[str] = []
    for pat in _KNOWLEDGE_PATTERNS:
        for m in pat.finditer(step_text):
            topic = m.group(1).strip().rstrip(".")
            if topic and len(topic) > 3:
                found.append(topic[:_MAX_TOPIC_LEN])
        if found:
            break  # stop at first matching pattern class

    return found[:3]


def check_prerequisites(
    steps: List[str],
    *,
    goal_id: str,
    adapter=None,
    continuation_depth: int = 0,
    knowledge_sub_goals: bool = False,
    verbose: bool = False,
) -> Dict[int, str]:
    """Check each step for knowledge prerequisites; resurrect or acquire.

    Args:
        steps:               Decomposed step list (0-indexed).
        goal_id:             Parent loop ID — used to tag acquired lessons.
        adapter:             LLMAdapter for sub-loop spawning. Required when
                             knowledge_sub_goals=True; unused otherwise.
        continuation_depth:  Parent loop depth. Sub-loops only spawn at depth 0.
        knowledge_sub_goals: If True and graveyard is empty for a topic, spawn a
                             lightweight research sub-loop to acquire the knowledge.
                             Default False (graveyard resurrection only).
        verbose:             Log progress to stderr.

    Returns:
        Dict mapping step index → additional context string to inject before
        that step executes. Empty dict if no prerequisites were found.
    """
    result: Dict[int, str] = {}

    for idx, step in enumerate(steps):
        topics = detect_knowledge_topics(step)
        if not topics:
            continue

        step_context_parts: List[str] = []

        for topic in topics:
            # 1. Graveyard resurrection — always-on, zero LLM cost.
            resurrected = _try_resurrect(topic, verbose=verbose)
            if resurrected:
                step_context_parts.append(
                    f"[prereq: resurrected from memory for step {idx + 1}]\n"
                    + "\n".join(f"- {r}" for r in resurrected)
                )
                continue  # graveyard hit — no sub-loop needed

            # 2. Sub-loop spawn — opt-in, depth-guarded.
            if knowledge_sub_goals and continuation_depth == 0 and adapter is not None:
                acquired = _spawn_knowledge_sub_loop(
                    topic, goal_id=goal_id, adapter=adapter, verbose=verbose
                )
                if acquired:
                    step_context_parts.append(
                        f"[prereq: acquired via research sub-loop for step {idx + 1}]\n"
                        + acquired
                    )

        if step_context_parts:
            result[idx] = "\n\n".join(step_context_parts)

    return result


def _try_resurrect(topic: str, *, verbose: bool = False) -> List[str]:
    """Search graveyard for topic; resurrect hits. Returns lesson texts (up to 3)."""
    try:
        from memory import search_graveyard
        hits = search_graveyard(topic, resurrect=True, limit=3)
        if hits:
            if verbose:
                log.info("prereq: resurrected %d lesson(s) for topic %r", len(hits), topic[:40])
            return [h.lesson for h in hits]
    except Exception as exc:
        log.debug("prereq: graveyard search failed for %r: %s", topic, exc)
    return []


def _spawn_knowledge_sub_loop(
    topic: str,
    *,
    goal_id: str,
    adapter,
    verbose: bool = False,
) -> str:
    """Run a lightweight sub-loop to acquire knowledge about *topic*.

    Records the acquired knowledge as a medium-tier lesson tagged with
    ``acquired_for=goal_id``. Returns the acquired context as a string,
    or empty string on failure.

    Sub-loops run at continuation_depth=1 so they cannot spawn further
    sub-loops themselves.
    """
    try:
        from agent_loop import run_agent_loop
        sub_goal = (
            f"Research and summarize key concepts for: {topic}. "
            f"Output 5 key facts as bullet points. Be concise."
        )
        if verbose:
            log.info("prereq: spawning knowledge sub-loop for topic %r", topic[:40])

        sub_result = run_agent_loop(
            sub_goal,
            adapter=adapter,
            max_steps=3,
            max_iterations=10,
            continuation_depth=1,  # prevents further sub-loop spawning
            verbose=verbose,
        )

        # Extract meaningful output from sub-loop outcomes.
        summary = _extract_sub_loop_summary(sub_result)
        if not summary:
            return ""

        # Record as a medium-tier lesson so it's available in future loops.
        try:
            from memory import record_tiered_lesson, MemoryTier
            record_tiered_lesson(
                f"[{topic}] {summary}",
                task_type="research",
                outcome="acquired",
                source_goal=f"prereq:{topic[:40]}",
                tier=MemoryTier.MEDIUM,
                acquired_for=goal_id,
            )
            if verbose:
                log.info("prereq: recorded acquired knowledge for topic %r", topic[:40])
        except Exception as exc:
            log.debug("prereq: failed to record lesson: %s", exc)

        return summary

    except Exception as exc:
        log.warning("prereq: sub-loop spawn failed for topic %r: %s", topic[:40], exc)
        return ""


def _extract_sub_loop_summary(result) -> str:
    """Pull a concise summary from a LoopResult for context injection."""
    try:
        # Prefer the last successful step's result.
        for outcome in reversed(result.steps):
            if outcome.status == "done" and outcome.result:
                return outcome.result[:500]
        # Fall back to stuck_reason if all steps stuck.
        if result.stuck_reason:
            return result.stuck_reason[:300]
    except Exception:
        pass
    return ""
