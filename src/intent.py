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
    content = resp.content.strip()
    # Extract JSON
    start = content.find("{")
    end = content.rfind("}") + 1
    if start >= 0 and end > start:
        data = json.loads(content[start:end])
        lane = data.get("lane", "agenda").lower()
        if lane not in ("now", "agenda"):
            lane = "agenda"
        confidence = float(data.get("confidence", 0.7))
        reason = data.get("reason", "")
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
