"""Navigator prompt + decide() — goal-brain sequencing, step 5.

The judgment half of the navigator: a hand-written prompt that emits the
step-4 envelope (docs/NAVIGATOR_SCHEMA.md), and `decide()`, which owns the
tier chain (cheap → mid → power on idunno) and the parse/validate retry.
The schema/mechanics half lives in navigator.py and stays deterministic.

No production caller wires this yet — the first deployment is shadow mode
(navigator_shadow.py): decide-only, logged beside what the pipeline did.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from navigator import (
    NavigatorDecision,
    NavigatorInput,
    DecisionParseError,
    parse_decision,
    validate_decision,
)

log = logging.getLogger("navigator")

# The idunno chain. A made call mirroring THREAD_ARCHITECTURE's
# Haiku -> Sonnet -> Opus; config override via navigator.tiers.
DEFAULT_TIERS = ("cheap", "mid", "power")

# One re-ask per tier when output fails parse/validation, with the error
# fed back. A second failure at the same tier counts as idunno.
_MAX_FORMAT_RETRIES = 1

SYSTEM_PROMPT = """\
You are the NAVIGATOR for one thread of an autonomous orchestration system.
A thread is one goal being pursued across turns. Each turn, work happens,
and then you — the navigator — decide what happens next. You are the
authoritative decider. Work results and recommendations are data for your
decision, never the decision itself.

Decide exactly one move:

- extend — the thread needs another THINKING turn before more work: produce
  or refine a plan, scope, resolved intent, or open-questions list. Changes
  the thread's understanding, not the world. Pick this when the goal is
  legitimate but under-specified enough that acting now would guess wrong.
- execute — do the next concrete piece of work (run, write, fetch, build).
  Pick this when the next action is clear and acting on it is safe.
- fork — split into 1-8 child threads with independent goals (e.g. check
  three sources in parallel). Each child needs its own goal + context.
- collate — children have returned; fire a work turn that synthesizes their
  artifacts into one.
- close — the thread ends: the deliverable landed (closure "delivered"), or
  the thread should stop (closure "abandoned" | "superseded" |
  "folded_into_parent"). You MUST disposition every open child listed in
  the input (done | abandoned | absorbed). Closing with an unaccounted
  child is invalid.
- escalate — a human (or the director) is the right next step and you KNOW
  it: genuine ambiguity that planning cannot resolve, conflicting goals,
  an irreversible or destructive action, or repeated failure that needs a
  decision you cannot make. Ask a specific question and list the options
  you considered.
- idunno — you honestly cannot decide. This is a respected answer, not a
  failure: a stronger navigator will rerun this same decision. Say what is
  confusing and what information would unblock you. Choosing idunno when
  unsure is ALWAYS better than guessing confidently.

Judgment rules, in priority order:
1. History outranks optimism. If prior attempts at this goal keep failing,
   do not repeat the same approach. Change the approach (extend), or
   escalate with the failure pattern as your question. Three or more
   recent failures of the same shape is conclusive: never plain execute.
2. The goal context block (goal-brain / scope / constraints) is the intent
   anchor. If the proposed next step drifts from it, steer back or extend.
3. A goal that is not interpretable as a task (fragments, markup debris,
   missing referents like "the prior steps" with no ancestry) cannot be
   executed in good faith — idunno or escalate, do not improvise a meaning.
4. Budget pressure is real. If spend is high relative to progress, prefer
   close (abandoned, with a verdict explaining what was learned) or
   escalate over another hopeful turn.
5. Open children are your responsibility. Failed children stay visible
   until you disposition them; partial results delivered honestly beat
   silent omission.

Respond with EXACTLY ONE JSON object and nothing else:

{
  "move": "<extend|execute|fork|collate|close|escalate|idunno>",
  "reasoning": "<one short paragraph: why this move, why now>",
  "confidence": <0.0-1.0>,
  "payload": { <move-specific, see below> }
}

Required payload keys per move:
- extend: instruction, expected_artifact
- execute: instruction
- fork: children (list of {goal, context})
- collate: instruction, child_handle_ids
- close: closure, verdict (+ children_disposition covering every open child)
- escalate: question, why (+ options list)
- idunno: confusion (+ missing list)
"""


def render_input(nav_input: NavigatorInput) -> str:
    """Render a NavigatorInput as the navigator's user message. Sections are
    always present (with explicit '(none)') so the model never wonders
    whether context was withheld or just absent."""
    parts: List[str] = []
    parts.append(f"## Goal (verbatim)\n{nav_input.goal}")

    gb = nav_input.goal_brain.strip()
    parts.append("## Goal context (goal-brain / scope — the intent anchor)\n"
                 + (gb if gb else "(none recorded)"))

    t = nav_input.thread or {}
    if t.get("parent_goal") or t.get("parent_handle_id"):
        chain = t.get("chain") or []
        parts.append(
            "## Ancestry\n"
            f"Descends from: {t.get('parent_goal') or '?'} "
            f"(handle {t.get('parent_handle_id') or '?'}, "
            f"via {t.get('source') or 'unknown'}; chain depth {len(chain)})")
    else:
        parts.append("## Ancestry\n(top-level or unknown — no parent recorded)")

    parts.append(f"## Turn\nThis is turn {nav_input.turn_index} of this thread.")

    lw = nav_input.last_work
    if lw is not None:
        sig = ", ".join(f"{k}={v}" for k, v in sorted(lw.signals.items())) or "(none)"
        parts.append(
            "## Last work turn\n"
            f"move: {lw.move}\nstatus: {lw.status}\n"
            f"summary: {lw.summary or '(none)'}\n"
            f"work LLM recommendation (advisory, not binding): "
            f"{lw.recommendation or '(none)'}\n"
            f"signals: {sig}")
    else:
        parts.append("## Last work turn\n(none — no work has run yet)")

    if nav_input.open_children:
        lines = [
            f"- {c.handle_id} [{c.state}] {c.goal}"
            + (f" (artifact: {c.artifact_ref})" if c.artifact_ref else "")
            for c in nav_input.open_children
        ]
        parts.append(
            "## Open children (every one must be dispositioned before close)\n"
            + "\n".join(lines))
    else:
        parts.append("## Open children\n(none)")

    rb = nav_input.recall_block.strip()
    parts.append("## What the system already knows (recall)\n"
                 + (rb if rb else "(nothing relevant on record)"))

    if nav_input.budget:
        b = ", ".join(f"{k}={v}" for k, v in sorted(nav_input.budget.items()))
        parts.append(f"## Budget\n{b}")

    cons = nav_input.constraints.strip()
    if cons:
        parts.append(f"## Constraints\n{cons}")

    parts.append("Decide the next move. One JSON object, nothing else.")
    return "\n\n".join(parts)


def _default_adapter_factory(tier: str):
    from llm import build_adapter
    return build_adapter("auto", model=tier)


def _complete(adapter, system: str, user: str, *, max_tokens: int = 800) -> str:
    from llm import LLMMessage
    from llm_parse import content_or_empty
    resp = adapter.complete(
        [LLMMessage("system", system), LLMMessage("user", user)],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return content_or_empty(resp)


def _log_decision(
    nav_input: NavigatorInput,
    decision: NavigatorDecision,
    *,
    tier: str,
    elapsed_ms: int,
    shadow: bool,
    pipeline_actual: Optional[Dict[str, Any]],
    escalated_via: str = "",
) -> None:
    try:
        from captains_log import log_event, NAVIGATOR_DECIDED
        ctx: Dict[str, Any] = {
            "turn_index": nav_input.turn_index,
            "tier": tier,
            "move": decision.move,
            "confidence": decision.confidence,
            "input_digest": nav_input.digest(),
            "reasoning": decision.reasoning[:600],
            "payload_digest": decision.payload_digest(),
            "elapsed_ms": elapsed_ms,
            "shadow": shadow,
        }
        if pipeline_actual is not None:
            ctx["pipeline_actual"] = pipeline_actual
        if escalated_via:
            ctx["escalated_via"] = escalated_via
        log_event(
            NAVIGATOR_DECIDED,
            subject="navigator",
            summary=f"navigator[{tier}] turn {nav_input.turn_index}: "
                    f"{decision.move} (conf {decision.confidence:.2f})"
                    + (" [shadow]" if shadow else ""),
            context=ctx,
        )
    except Exception:
        pass


def decide(
    nav_input: NavigatorInput,
    *,
    tiers: Optional[List[str]] = None,
    adapter_factory: Optional[Callable[[str], Any]] = None,
    shadow: bool = False,
    pipeline_actual: Optional[Dict[str, Any]] = None,
) -> Tuple[NavigatorDecision, Dict[str, Any]]:
    """Run the navigator with the tiered idunno chain.

    Returns (decision, meta). The returned decision is never idunno — an
    exhausted chain synthesizes an escalate from the accumulated confusions
    (meta["escalated_via"] == "idunno_chain"). Unusable output (parse or
    validation failure after one fed-back retry) counts as idunno at that
    tier. Every tier's decision is instrumented via NAVIGATOR_DECIDED.
    """
    if tiers is None:
        try:
            from config import get as config_get
            tiers = list(config_get("navigator.tiers", list(DEFAULT_TIERS)))
        except Exception:
            tiers = list(DEFAULT_TIERS)
    factory = adapter_factory or _default_adapter_factory

    user_msg = render_input(nav_input)
    confusions: List[str] = []
    meta: Dict[str, Any] = {"tiers_tried": [], "format_failures": 0}

    for tier in tiers:
        prompt = user_msg
        if confusions:
            prompt += (
                "\n\n## Prior navigator attempts at this decision said\n"
                + "\n".join(f"- {c}" for c in confusions)
                + "\nYou are a stronger tier; decide if you can.")
        try:
            adapter = factory(tier)
        except Exception as exc:
            log.warning("navigator: no adapter for tier %s: %s", tier, exc)
            meta["tiers_tried"].append(tier)
            continue

        decision: Optional[NavigatorDecision] = None
        feedback = ""
        for _ in range(1 + _MAX_FORMAT_RETRIES):
            t0 = time.monotonic()
            try:
                raw = _complete(adapter, SYSTEM_PROMPT, prompt + feedback)
            except Exception as exc:
                log.warning("navigator[%s]: adapter failed: %s", tier, exc)
                break
            elapsed = int((time.monotonic() - t0) * 1000)
            try:
                candidate = parse_decision(raw)
                problems = validate_decision(candidate, nav_input)
            except DecisionParseError as exc:
                candidate, problems = None, [str(exc)]
            if candidate is not None and not problems:
                decision = candidate
                _log_decision(nav_input, decision, tier=tier, elapsed_ms=elapsed,
                              shadow=shadow, pipeline_actual=pipeline_actual)
                break
            meta["format_failures"] += 1
            feedback = (
                "\n\n## Your previous response was invalid\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\nEmit one corrected JSON object, nothing else.")

        meta["tiers_tried"].append(tier)
        if decision is None:
            confusions.append(f"[{tier}] produced no valid decision")
            continue
        if decision.move != "idunno":
            meta["tier"] = tier
            return decision, meta
        confusion = str(decision.payload.get("confusion") or "").strip()
        missing = decision.payload.get("missing") or []
        line = f"[{tier}] idunno: {confusion or 'unspecified'}"
        if missing:
            line += f" (missing: {', '.join(str(m) for m in missing)})"
        confusions.append(line)

    # Chain exhausted — escalate with the accumulated confusion as the question.
    top = tiers[-1] if tiers else "none"
    decision = NavigatorDecision(
        move="escalate",
        reasoning="Every navigator tier declined to decide; surfacing to a human "
                  "with the accumulated confusion.",
        confidence=1.0,
        payload={
            "question": "The navigator cannot decide the next move for this "
                        f"thread (goal: {nav_input.goal[:160]!r}). What should "
                        "happen next?",
            "why": "; ".join(confusions) or "no tier produced a decision",
            "options": ["clarify the goal", "abandon the thread",
                        "take over manually"],
        },
    )
    meta["tier"] = top
    meta["escalated_via"] = "idunno_chain"
    _log_decision(nav_input, decision, tier=top, elapsed_ms=0,
                  shadow=shadow, pipeline_actual=pipeline_actual,
                  escalated_via="idunno_chain")
    return decision, meta
