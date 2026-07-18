#!/usr/bin/env python3
"""Intent classification: route incoming requests to NOW or AGENDA lane.

NOW lane:  trivial, completable in a single LLM call (~seconds)
AGENDA lane: multi-step, requires planning + loop execution (~minutes)

Classification uses LLM with a lightweight system prompt. Falls back to
heuristic keyword matching if the LLM call fails.

Usage:
    from intent import classify
    lane, confidence, reason, introspects_self = classify("what time is it?")
    # → ("now", 0.95, "Simple factual question", False)

    lane, confidence, reason, introspects_self = classify(
        "why did your last run fail?")
    # → ("agenda", 0.9, "Requires reading run records", True)
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
) -> Tuple[Lane, float, str, bool]:
    """Classify a message as NOW or AGENDA lane.

    Returns:
        (lane, confidence, reason, introspects_self)
        - lane: "now" or "agenda"
        - confidence: 0.0–1.0
        - reason: one-sentence explanation
        - introspects_self: the goal asks about THIS system's own runs/
          behavior/source (decree 2026-07-18: such runs get read-only run
          records + maro source inside the executor container). Fails open
          to False on the heuristic path — isolation is the safe default.
    """
    # Deterministic link-triage shortcut, ahead of any LLM opinion
    # (conversational-compute decree, 2026-07-17): the canonical "is this
    # worth my time? <link>" must route NOW every time — live smoke same day
    # had the LLM classifier route it agenda@0.95 despite a verbatim prompt
    # example, then stack the clarification gate on top. Conservative on
    # purpose: triage phrasing + a URL + no file deliverable + a short ask.
    if _is_link_triage(message):
        return ("now", 0.9,
                "Link triage — provided link is pre-fetched and read inline",
                False)

    needs_live_data = False
    introspects_self = False
    if dry_run or adapter is None:
        lane, confidence, reason = _heuristic_classify(message)
    else:
        try:
            lane, confidence, reason, needs_live_data, introspects_self = (
                _llm_classify(message, adapter))
        except Exception:
            lane, confidence, reason = _heuristic_classify(message)

    # Capability override, not a classification opinion: the NOW lane answers
    # inline and cannot write files, so a goal that names a file deliverable
    # is mechanically un-fulfillable there. Burn-in batch 3 (2026-07-02):
    # "Summarize what 'comm' does ... saved to artifacts/comm-examples.md"
    # routed NOW, answered inline, and the self-verdict correctly demoted the
    # run — honest negative, wrong lane.
    if lane == "now" and _requires_file_output(message):
        return (
            "agenda",
            max(confidence, 0.8),
            "Names a file deliverable — NOW lane cannot write files",
            introspects_self,
        )

    # Capability override, same class as the file-output one above: NOW is a
    # single tool-less completion, so a question whose correct answer depends
    # on live/local data ("gas near Manti, Utah" — 2026-07-10, the canonical
    # simple-case failure) is mechanically un-answerable there — the model
    # falls back to a how-to-search list, the passenger-does-the-steps
    # anti-pattern. See docs/history/2026-07-12-routing-and-probe-synthesis-design.md
    # Part A. Gated the same as the heuristic-path flip below (adversarial-review
    # finding, 2026-07-12: this override fired unconditionally, contradicting
    # DEFAULTS.md's documented "flag OFF makes both paths inert" contract).
    # 2026-07-17 (conversational-compute decree): asks that CARRY an explicit
    # URL are exempt — _run_now pre-fetches provided links (reply-aware for X)
    # so "is this worth my time? <link>" is answerable inline. Only
    # source-less live-data asks (searches) still escalate.
    if (lane == "now" and needs_live_data
            and _config_get("now_lane.live_data_routing", True)
            and not _message_has_url(message)):
        return (
            "agenda",
            max(confidence, 0.8),
            "Needs live external data — NOW lane cannot fetch it",
            introspects_self,
        )
    return (lane, confidence, reason, introspects_self)


def _message_has_url(message: str) -> bool:
    """Explicit URL in the message. The NOW lane pre-fetches provided links
    itself (web_fetch enrichment), so a live-data ask that carries its own
    source stays NOW-eligible."""
    try:
        from web_fetch import extract_urls_from_text
        return bool(extract_urls_from_text(message))
    except Exception:
        return False


# Triage-shaped phrasings: an opinion about a provided link, not a task on
# it. Deliberately narrow — "fix the bug at <url>" or "port this repo" must
# NOT match; those are real work even when short.
_LINK_TRIAGE_RE = re.compile(
    r"(\bworth\s+(?:my|your|our|the|a)\s+(?:time|while|look|read)\b"
    r"|\bworth\s+(?:looking\s+at|reading|checking(?:\s+out)?)\b"
    r"|\bshould\s+i\s+(?:care|bother|look|read)\b"
    r"|\bis\s+this\s+(?:legit|real|hype|useful|any\s+good|interesting)\b"
    r"|\bwhat(?:'s|\s+is)\s+this\b"
    r"|\bquick\s+(?:take|read|look|opinion)\b"
    r"|\btl;?dr\b)",
    re.I,
)
_TRIAGE_MAX_WORDS = 25  # excluding URLs — longer asks carry real instructions


def _is_link_triage(message: str) -> bool:
    """Deterministic match for the canonical conversational-compute ask:
    a short, triage-phrased question about link(s) the message itself
    carries. Everything else keeps normal classification."""
    if not _LINK_TRIAGE_RE.search(message or ""):
        return False
    if _requires_file_output(message):
        return False
    if not _message_has_url(message):
        return False
    try:
        from web_fetch import extract_urls_from_text
        stripped = message
        for u in extract_urls_from_text(message):
            stripped = stripped.replace(u, " ")
    except Exception:
        stripped = message
    return len(stripped.split()) <= _TRIAGE_MAX_WORDS


_FILE_OUTPUT_RE = re.compile(
    r"(\bartifacts?/|"
    r"\b(?:save|write|output|export)\b[^.;\n]{0,40}\bto\s+\S*[\w-]+\.[a-z]{1,6}\b|"
    r"\bto\s+(?:a\s+)?file\b|"
    r"\bas\s+(?:its\s+own\s+)?(?:markdown|csv|json|yaml|text)\s+files?\b)",
    re.I,
)


def _requires_file_output(message: str) -> bool:
    """Goal explicitly asks for output on disk (path, artifacts/, 'to a file')."""
    return bool(_FILE_OUTPUT_RE.search(message))


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """You are a routing agent. Classify the user's request as either:

NOW: Completable in a single step with one LLM call. Examples:
- Factual questions ("what time is it?", "what does HTTP 429 mean?")
- Simple generation ("write a haiku", "summarize this paragraph")
- Short transforms ("translate this to Spanish")

AGENDA: Requires multiple steps, research, iteration, or planning. Examples:
- Research tasks ("research winning polymarket strategies")
- Build tasks ("build a research report on X")
- Analysis tasks ("analyze competitor pricing and recommend action")
- Ongoing projects ("set up monitoring for Y")
- Live-data lookups ("what is the current BTC price?", "gas stations near
  Manti, Utah") — these need needs_live_data=true (below); NOW cannot fetch
  live data, so a live-data ask is AGENDA even though it reads like a quick
  question.

EXCEPTION — link reads: when the request itself CONTAINS the URL(s) to look
at, the system pre-fetches those links before the NOW call, so a quick read
of provided links IS completable in one step. "Is this worth my time?
<link>", "summarize this page <url>", "what's the catch here? <link>" are
NOW. Still AGENDA when the ask demands multi-source verification, file
outputs, or open-ended research beyond the provided link(s).

Also decide needs_live_data: true when a correct answer requires information
that changes over time or is locally situated — current prices/availability/
hours, "near me"/named-place inventory, weather, schedules, recent events —
i.e. anything you cannot know reliably from training data. false otherwise.

Also decide introspects_self: true when the request asks about THIS system's
own behavior — its past runs, failures, decisions, logs, configuration, or
source code ("why did the last run fail?", "diagnose your step retries",
"what did you work on yesterday?", "audit your own planner"). false for
ordinary tasks about the outside world, even technical ones about other
software.

Respond ONLY with a JSON object:
{"lane": "now" or "agenda", "confidence": 0.0-1.0, "reason": "one sentence", "needs_live_data": true or false, "introspects_self": true or false}
"""


def _llm_classify(message: str, adapter) -> Tuple[Lane, float, str, bool, bool]:
    from llm import LLMMessage
    import json

    resp = adapter.complete(
        [
            LLMMessage("system", _CLASSIFY_SYSTEM),
            LLMMessage("user", f"Request: {message}"),
        ],
        max_tokens=128,
        temperature=0.1,
        no_tools=True,
        purpose="routing",
    )
    data = extract_json(content_or_empty(resp), dict, log_tag="intent.classify")
    if data:
        lane = safe_str(data.get("lane", "agenda")).lower()
        if lane not in ("now", "agenda"):
            lane = "agenda"
        confidence = safe_float(data.get("confidence"), default=0.7, min_val=0.0, max_val=1.0)
        reason = safe_str(data.get("reason"))
        # Absent/malformed field fails open to today's behavior (False —
        # no override fires). A stray string value ("true"/"false") from a
        # sloppier model is still read correctly rather than truthy-coerced.
        raw_ld = data.get("needs_live_data", False)
        needs_live_data = raw_ld is True or (
            isinstance(raw_ld, str) and raw_ld.strip().lower() == "true"
        )
        # Same fail-open shape: absent/malformed → False → containerized
        # steps keep pre-decree isolation (safe, just less capable).
        raw_is = data.get("introspects_self", False)
        introspects_self = raw_is is True or (
            isinstance(raw_is, str) and raw_is.strip().lower() == "true"
        )
        return (lane, confidence, reason, needs_live_data, introspects_self)

    # Couldn't parse — fall back (heuristic has no schema field to read;
    # its own lexical approximation of live-data-ness already lives in
    # _heuristic_classify's pattern scoring, so no override needed here)
    lane, confidence, reason = _heuristic_classify(message)
    return (lane, confidence, reason, False, False)


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

def _config_get(key: str, default):
    try:
        from config import get as _get
        return _get(key, default)
    except Exception:
        return default


# Patterns that strongly suggest NOW lane
_NOW_PATTERNS = [
    r"\b(what|who|when|where|how much|how many)\b.{0,60}\?",
    r"\b(write a? (haiku|poem|joke|summary|headline|tweet|caption))\b",
    r"\b(translate|convert|format|calculate)\b",
    r"\b(summarize|tldr|give me a summary)\b",
    r"\b(quick(ly)?|fast|one-?line|brief)\b",
]

# "what's the current BTC price", "today's weather" — literally live-data
# phrasing (Part A design doc), so under now_lane.live_data_routing (default
# ON) this counts toward AGENDA, not NOW; the pre-existing NOW-leaning
# behavior survives as the explicit opt-out.
#
# Deliberately narrow: this is a lexical approximation, not the real
# semantic signal (that's needs_live_data on the LLM path — see classify()
# above). Named-place availability asks like "where can I get non-ethanol
# gas near Manti, Utah" don't match and still fall through to NOW here; the
# design doc calls this out as an accepted residual gap of the no-LLM
# fallback, not an oversight (docs/history/2026-07-12-routing-and-probe-
# synthesis-design.md, DECISION at line 70: "The heuristic fallback ... gets
# a *small* lexical approximation ... only because it must work with no
# LLM"). Confirmed still-open by 3 independent adversarial reviewers,
# 2026-07-12 — left as-is per that decision, not a bug to chase.
_LIVE_DATA_RE = re.compile(
    r"\b(what('s| is) (the |a |an )?(current|latest|today'?s?))\b", re.I
)

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

    if _LIVE_DATA_RE.search(msg_lower):
        if _config_get("now_lane.live_data_routing", True):
            agenda_score += 1
        else:
            now_score += 1

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
            no_tools=True,
            purpose="clarity check",
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
# "next" only counts clause-initially — as a bare word it matched noun
# phrases like "an exact safe next action" and dragged outcome-shaped goals
# through the rewriter (hermes dispatch specimen, 2026-07-16).
_IMPERATIVE_MARKERS = re.compile(
    r"\b(first,?\s|then\s|step\s*\d|step\s*one|finally,?\s|afterwards?\s"
    r"|start by\s|begin by\s|start with\s|proceed to\s|make sure to\s"
    r"|run the\s.*then\s|do\s.*,?\s*then\s|check\s.*,?\s*then\s)"
    r"|(?:^|[.;:!?]\s+)next,?\s",
    re.IGNORECASE,
)


def _is_imperative_heavy(goal: str) -> bool:
    """Quick heuristic: does the goal prescribe execution steps?"""
    return bool(_IMPERATIVE_MARKERS.search(goal)) and len(goal.split()) > 15


_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")


def _rewrite_loses_referent(original: str, rewritten: str) -> bool:
    """True when the rewrite dropped a URL the original goal carried.

    Rule 3 of the rewriter prompt ("preserve all proper nouns, tool names,
    constraints") is advisory to the LLM; this is the enforced invariant for
    the one referent class a run cannot re-derive. A cheap-model rewrite that
    replaced an explicit X-thread URL with "the referenced thread" turned a
    fetchable goal into a clarification dead-end (2026-07-16, cobalt-pine).
    """
    for url in _URL_RE.findall(original):
        if url.rstrip(".,;:!?") not in rewritten:
            return True
    return False


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
            no_tools=True,
            purpose="goal rewrite",
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="intent.ble_rewrite")
        if data and data.get("changed"):
            rewritten = safe_str(data.get("rewritten"))
            if rewritten and len(rewritten) >= 10:
                import logging as _logging
                if _rewrite_loses_referent(goal, rewritten):
                    _logging.getLogger(__name__).warning(
                        "BLE rewrite dropped a URL from the goal — keeping original: %r",
                        goal[:80],
                    )
                    return goal
                _logging.getLogger(__name__).info(
                    "BLE rewrite applied: %r → %r", goal[:60], rewritten[:60]
                )
                return rewritten
    except Exception:
        pass  # Rewrite failures must never block a run

    return goal
