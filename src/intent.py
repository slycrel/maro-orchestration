#!/usr/bin/env python3
"""Intent classification: route incoming requests to NOW or AGENDA lane.

NOW lane:  trivial, completable in a single LLM call (~seconds)
AGENDA lane: multi-step, requires planning + loop execution (~minutes)

Classification uses LLM with a lightweight system prompt. Falls back to
heuristic keyword matching if the LLM call fails.

Usage:
    from intent import classify
    lane, confidence, reason = classify("what time is it?")
    # → ("now", 0.95, "Simple factual question")

    lane, confidence, reason = classify("research winning polymarket strategies")
    # → ("agenda", 0.92, "Requires research and multi-step analysis")
"""

from __future__ import annotations

import re
from typing import Tuple
from llm_parse import extract_json, safe_float, safe_str, content_or_empty


# ---------------------------------------------------------------------------
# Classification result type
# ---------------------------------------------------------------------------

Lane = str  # "now" | "agenda"


def classify(
    message: str,
    *,
    adapter=None,
    dry_run: bool = False,
) -> Tuple[Lane, float, str]:
    """Classify a message as NOW or AGENDA lane.

    Returns:
        (lane, confidence, reason)
        - lane: "now" or "agenda"
        - confidence: 0.0–1.0
        - reason: one-sentence explanation
    """
    if dry_run or adapter is None:
        return _heuristic_classify(message)

    try:
        return _llm_classify(message, adapter)
    except Exception:
        return _heuristic_classify(message)


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """You are a routing agent. Classify the user's request as either:

NOW: Completable in a single step with one LLM call. Examples:
- Factual questions ("what time is it?", "who is X?")
- Simple generation ("write a haiku", "summarize this paragraph")
- Quick lookups ("what is the current BTC price?")
- Short transforms ("translate this to Spanish")

AGENDA: Requires multiple steps, research, iteration, or planning. Examples:
- Research tasks ("research winning polymarket strategies")
- Build tasks ("build a research report on X")
- Analysis tasks ("analyze competitor pricing and recommend action")
- Ongoing projects ("set up monitoring for Y")

Respond ONLY with a JSON object:
{"lane": "now" or "agenda", "confidence": 0.0-1.0, "reason": "one sentence"}
"""


def _llm_classify(message: str, adapter) -> Tuple[Lane, float, str]:
    from llm import LLMMessage
    import json

    resp = adapter.complete(
        [
            LLMMessage("system", _CLASSIFY_SYSTEM),
            LLMMessage("user", f"Request: {message}"),
        ],
        max_tokens=128,
        temperature=0.1,
    )
    data = extract_json(content_or_empty(resp), dict, log_tag="intent.classify")
    if data:
        lane = safe_str(data.get("lane", "agenda")).lower()
        if lane not in ("now", "agenda"):
            lane = "agenda"
        confidence = safe_float(data.get("confidence"), default=0.7, min_val=0.0, max_val=1.0)
        reason = safe_str(data.get("reason"))
        return (lane, confidence, reason)

    # Couldn't parse — fall back
    return _heuristic_classify(message)


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

# Patterns that strongly suggest NOW lane
_NOW_PATTERNS = [
    r"\b(what|who|when|where|how much|how many)\b.{0,60}\?",
    r"\b(write a? (haiku|poem|joke|summary|headline|tweet|caption))\b",
    r"\b(translate|convert|format|calculate)\b",
    r"\b(summarize|tldr|give me a summary)\b",
    r"\b(what('s| is) (the |a |an )?(current|latest|today'?s?))\b",
    r"\b(quick(ly)?|fast|one-?line|brief)\b",
]

# Patterns that strongly suggest AGENDA lane
_AGENDA_PATTERNS = [
    r"\b(research|investigate|analyze|study|explore)\b",
    r"\b(build|create|develop|implement|design|architect)\b",
    r"\b(report|analysis|strategy|plan|roadmap)\b",
    r"\b(monitor|track|watch|follow)\b",
    r"\b(compare|evaluate|benchmark|assess)\b",
    r"\b(deep (dive|research|analysis))\b",
    r"\b(step[- ]by[- ]step|multi[- ]step|phase)\b",
    r"\b(and then|first.*then|multiple|several)\b",
]

_SHORT_THRESHOLD = 8  # words — very short messages tend to be NOW


def _heuristic_classify(message: str) -> Tuple[Lane, float, str]:
    msg_lower = message.lower().strip()
    word_count = len(msg_lower.split())

    now_score = 0
    agenda_score = 0

    for p in _NOW_PATTERNS:
        if re.search(p, msg_lower):
            now_score += 1

    for p in _AGENDA_PATTERNS:
        if re.search(p, msg_lower):
            agenda_score += 1

    # Very short messages lean NOW
    if word_count <= _SHORT_THRESHOLD and not agenda_score:
        now_score += 1

    if now_score > agenda_score:
        confidence = min(0.5 + now_score * 0.15, 0.9)
        return ("now", confidence, "Short or simple request; single-call execution sufficient")

    if agenda_score > 0:
        confidence = min(0.5 + agenda_score * 0.15, 0.9)
        return ("agenda", confidence, "Multi-step or research task; loop execution required")

    # Default: AGENDA is safer (won't miss work)
    return ("agenda", 0.55, "Defaulting to AGENDA lane for thoroughness")


# ---------------------------------------------------------------------------
# Goal clarity check — Clarification milestone (Jeremy request)
# ---------------------------------------------------------------------------

_CLARITY_SYSTEM = """\
You are a goal clarity assessor. A user submitted a goal for an autonomous agent to execute.
Assess whether the goal has enough specificity for the agent to proceed without asking questions.

CLEAR: the agent knows what to do. Mark clear if:
- A URL, repo, or file path is provided (agent can fetch/read it — don't ask about its contents)
- The target is named or linked, even if details are unknown
- Minor details or current state can be discovered by the agent (via web fetch, repo read, etc.)
- The goal just requires research or execution the agent can figure out

UNCLEAR: only flag if the goal has a genuine blocker the agent CANNOT resolve itself. Examples:
- Pronouns with no referent and no URL ("make it work", "fix that thing")
- Conflicting interpretations where user preference determines the approach
- Scope is so open-ended that any result would be a guess (e.g. "improve my project" with no project named)

NEVER ask about things that are discoverable:
- Do NOT ask "what is the current architecture?" if a repo URL is provided
- Do NOT ask "what does the code do?" if a file/URL is provided
- Do NOT ask about technical details the agent can fetch

Only ask about genuinely subjective choices the user hasn't stated and that materially change
the outcome (e.g. "should this be a REST API or GraphQL?" when neither is mentioned or implied).

Respond with JSON only:
{"clear": true|false, "question": "one specific question if not clear, else empty string"}

Default to clear. Only return clear=false if proceeding would require a coin-flip on something
the user definitely cares about and cannot be inferred or discovered.
"""


def check_goal_clarity(
    goal: str,
    *,
    adapter=None,
    dry_run: bool = False,
) -> dict:
    """Check whether a goal has enough specificity for the agent to proceed.

    Returns:
        {"clear": bool, "question": str}
        clear=True means proceed without asking.
        clear=False means surface the question to the user.

    Non-fatal — returns clear=True on any error so the check never blocks execution.
    """
    if dry_run or adapter is None:
        return {"clear": True, "question": ""}

    if len(goal.split()) < 4:
        # Very short goals are fine — probably a NOW-lane item anyway
        return {"clear": True, "question": ""}

    try:
        import json as _json
        from llm import LLMMessage

        resp = adapter.complete(
            [
                LLMMessage("system", _CLARITY_SYSTEM),
                LLMMessage("user", f"Goal: {goal}"),
            ],
            max_tokens=128,
            temperature=0.1,
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="intent.check_clarity")
        if data:
            is_clear = bool(data.get("clear", True))
            question = safe_str(data.get("question"))
            return {"clear": is_clear, "question": question}
    except Exception:
        pass  # Clarity check failures must never block a run

    return {"clear": True, "question": ""}


# ---------------------------------------------------------------------------
# Bitter Lesson Goal Rewriter — "What vs How"
# ---------------------------------------------------------------------------

_BLE_SYSTEM = """\
You are a Bitter Lesson goal rewriter. Your task: convert imperative-step goals
("do X, then Y, then check Z") into outcome-focused goals ("achieve X given context Y").

The Bitter Lesson principle: embed the *what* (desired outcome + user context + tools available),
not the *how* (execution steps). The AI should figure out how.

Rules:
1. If the goal is ALREADY outcome-focused (no step-by-step instructions), return it unchanged.
2. If the goal contains explicit sequencing ("first", "then", "step 1", "afterwards"), rewrite
   it as a single outcome statement that preserves intent but removes prescribed method.
3. Preserve all proper nouns, tool names, constraints, and output requirements.
4. The rewritten goal should be clear, specific, and completable by an autonomous agent.
5. Never add steps or structure the original didn't have. Just convert form.

Respond with JSON only:
{"rewritten": "rewritten goal or original if already outcome-focused", "changed": true|false}
"""

# Heuristic: detect imperative-heavy goals without LLM call
_IMPERATIVE_MARKERS = re.compile(
    r"\b(first,?\s|then\s|step\s*\d|step\s*one|next,?\s|finally,?\s|afterwards?\s"
    r"|start by\s|begin by\s|start with\s|proceed to\s|make sure to\s"
    r"|run the\s.*then\s|do\s.*,?\s*then\s|check\s.*,?\s*then\s)",
    re.IGNORECASE,
)


def _is_imperative_heavy(goal: str) -> bool:
    """Quick heuristic: does the goal prescribe execution steps?"""
    return bool(_IMPERATIVE_MARKERS.search(goal)) and len(goal.split()) > 15


def rewrite_imperative_goal(
    goal: str,
    *,
    adapter=None,
    dry_run: bool = False,
) -> str:
    """Bitter Lesson goal rewriter — strip prescribed execution steps, keep outcome intent.

    Returns the rewritten goal (or the original if no rewrite is needed or safe).
    Non-fatal — returns original on any error.

    Only calls LLM when the heuristic detects imperative-heavy language.
    """
    if dry_run or not _is_imperative_heavy(goal):
        return goal

    if adapter is None:
        return goal

    try:
        from llm import LLMMessage
        resp = adapter.complete(
            [
                LLMMessage("system", _BLE_SYSTEM),
                LLMMessage("user", f"Goal: {goal}"),
            ],
            max_tokens=256,
            temperature=0.1,
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="intent.ble_rewrite")
        if data and data.get("changed"):
            rewritten = safe_str(data.get("rewritten"))
            if rewritten and len(rewritten) >= 10:
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "BLE rewrite applied: %r → %r", goal[:60], rewritten[:60]
                )
                return rewritten
    except Exception:
        pass  # Rewrite failures must never block a run

    return goal
