"""Navigator decision schema (goal-brain sequencing, step 4).

Types + validation + parsing ONLY. No decision logic, no LLM calls, no
callers — the navigator prompt is step 5, and the first deployment is
shadow-mode (decide-only, logged alongside what the pipeline actually did).
Design: docs/NAVIGATOR_SCHEMA.md. Do not build a turn runner against this
before the prompt exists.

The split is deliberate (capability-form paradigm, GOAL_BRAIN Intent):
*when* to extend vs execute is judgment and lives in language (the prompt);
envelope shape, required keys, and the close-must-disposition-children rule
are deterministic mechanics and live here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# The six moves + the admission. idunno is not a move the turn runner ever
# executes — the harness intercepts it and re-runs the same input at the
# next tier (Haiku -> Sonnet -> Opus); top-tier idunno converts to escalate.
MOVES = frozenset({
    "extend", "execute", "fork", "collate", "close", "escalate", "idunno",
})

CLOSURE_TYPES = frozenset({
    "delivered", "abandoned", "superseded", "folded_into_parent",
})

CHILD_STATES = frozenset({"open", "done", "failed", "abandoned"})

# Every open child must be dispositioned at close. Abandoning is allowed;
# silently forgetting is not (the 2026-05-18 fan-out lesson as a validator).
CHILD_DISPOSITIONS = frozenset({"done", "abandoned", "absorbed"})

# Runaway-fan-out backstop, same spirit as the recall dispatch guard.
# A made call — revisit against NAVIGATOR_DECIDED data.
FORK_CHILD_CAP = 8

# Planning-depth shadow (thread-arch #5, MILESTONES 1.5, decided 2026-07-09
# GOAL_BRAIN Decisions "#5 planning-vs-Tesla-mode"). A second, independent
# judgment riding the SAME envelope as the move — how much planning this
# goal needs, not what to do next. "plan" (the normal/full pipeline) is the
# default/prior; the lighter shapes fire only on positive evidence in the
# navigator's judgment (prompt, not code — inference-not-taxonomy). Every
# code path treats an absent or unrecognized value as "plan" — fail-closed
# to the conservative default, same posture as the budget-gate coercion.
# "spawn-sub-goal" is a legal shape by the 2026-07-09 recursion decree, not
# an enum afterthought: it must never be dropped to make this set smaller.
PLANNING_DEPTHS = frozenset({"plan", "one-shot", "thin-plan", "spawn-sub-goal"})
DEFAULT_PLANNING_DEPTH = "plan"

# Required payload keys per move (optional keys documented in the design doc).
_REQUIRED_PAYLOAD: Dict[str, tuple] = {
    "extend": ("instruction", "expected_artifact"),
    "execute": ("instruction",),
    "fork": ("children",),
    "collate": ("instruction", "child_handle_ids"),
    "close": ("closure", "verdict"),
    "escalate": ("question", "why"),
    "idunno": ("confusion",),
}


@dataclass
class WorkReport:
    """What the last work turn returned (the 2026-06-10 visibility decision:
    recommendation + structured signals by default; full output pullable
    via output_ref, never injected wholesale)."""
    move: str             # extend | execute | collate — which move produced it
    status: str           # ok | failed | partial
    summary: str          # work LLM's compact self-report
    recommendation: str   # advisory next-move suggestion; data, not authority
    signals: Dict[str, Any] = field(default_factory=dict)
    output_ref: str = ""  # path to full output on disk


@dataclass
class ChildSummary:
    """A child thread as the parent navigator sees it. Children not yet
    dispositioned ride in every NavigatorInput — structurally impossible
    to forget."""
    handle_id: str
    goal: str
    state: str            # open | done | failed | abandoned
    artifact_ref: str = ""


@dataclass
class NavigatorInput:
    """Everything the navigator sees for one turn. The goal-brain is injected
    whole, every turn — it is the intent-preservation mechanism."""
    goal: str
    goal_brain: str = ""
    thread: Dict[str, Any] = field(default_factory=dict)   # ThreadIdentity shape
    turn_index: int = 0
    last_work: Optional[WorkReport] = None
    open_children: List[ChildSummary] = field(default_factory=list)
    recall_block: str = ""        # recall(slice="navigator") output
    budget: Dict[str, Any] = field(default_factory=dict)
    constraints: str = ""

    def digest(self) -> Dict[str, Any]:
        """Compact snapshot for the NAVIGATOR_DECIDED tuple. Full state lives
        in the run dir; instrumentation carries shape, not content."""
        return {
            "goal_preview": self.goal[:120],
            "turn_index": self.turn_index,
            "open_children": len(self.open_children),
            "has_last_work": self.last_work is not None,
            "last_work_status": self.last_work.status if self.last_work else "",
            "recall_chars": len(self.recall_block),
            "goal_brain_chars": len(self.goal_brain),
            "budget": dict(self.budget),
        }


@dataclass
class NavigatorDecision:
    """The single envelope every navigator turn returns, idunno included.
    reasoning is mandatory for every move — a decision without reasoning is
    unlearnable-from.

    planning_depth (thread-arch #5, MILESTONES 1.5): a second judgment riding
    the same envelope — how much planning this goal needs, independent of
    which move fires. Defaults to "plan" (the normal/full pipeline); see
    PLANNING_DEPTHS. Shadow-only in this chunk — nothing reads it for control
    flow yet, it rides NAVIGATOR_DECIDED beside pipeline-actual."""
    move: str
    reasoning: str
    confidence: float
    payload: Dict[str, Any] = field(default_factory=dict)
    planning_depth: str = DEFAULT_PLANNING_DEPTH

    def to_dict(self) -> Dict[str, Any]:
        return {
            "move": self.move,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "payload": dict(self.payload),
            "planning_depth": self.planning_depth,
        }

    def payload_digest(self) -> Dict[str, Any]:
        """Payload shape for instrumentation — keys + sizes, not full text."""
        out: Dict[str, Any] = {}
        for k, v in self.payload.items():
            if isinstance(v, str):
                out[k] = f"str:{len(v)}"
            elif isinstance(v, (list, tuple)):
                out[k] = f"list:{len(v)}"
            elif isinstance(v, dict):
                out[k] = f"dict:{len(v)}"
            else:
                out[k] = repr(v)[:40]
        return out


class DecisionParseError(ValueError):
    """Raised when text cannot be parsed into a NavigatorDecision envelope."""


def _extract_json(text: str) -> Optional[dict]:
    """Find the decision object in LLM output: raw JSON, fenced block, or the
    first balanced top-level object. Boring on purpose."""
    candidates: List[str] = [text.strip()]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    candidates.extend(b.strip() for b in fenced)
    brace = text.find("{")
    if brace != -1:
        depth = 0
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[brace:i + 1])
                    break
    for c in candidates:
        if not c:
            continue
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def parse_decision(text: str) -> NavigatorDecision:
    """Parse LLM output into a NavigatorDecision. Shape errors raise
    DecisionParseError with a readable reason (the prompt-side retry message).
    Semantic validity is validate_decision's job — parse, then validate.

    planning_depth is deliberately NOT part of that hard-fail contract: it is
    an advisory shadow field (MILESTONES 1.5), not core decision mechanics.
    Absent, non-string, or unrecognized values fail closed to "plan" (the
    conservative default) rather than raising — a malformed or missing depth
    judgment must never block or retry the underlying move decision."""
    obj = _extract_json(text or "")
    if obj is None:
        raise DecisionParseError("no JSON object found in navigator output")
    move = str(obj.get("move") or "").strip().lower()
    reasoning = str(obj.get("reasoning") or "").strip()
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        raise DecisionParseError(
            f"confidence is not a number: {obj.get('confidence')!r}")
    payload = obj.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise DecisionParseError(
            f"payload must be an object, got {type(payload).__name__}")
    planning_depth = str(obj.get("planning_depth") or "").strip().lower()
    if planning_depth not in PLANNING_DEPTHS:
        planning_depth = DEFAULT_PLANNING_DEPTH
    return NavigatorDecision(
        move=move, reasoning=reasoning, confidence=confidence, payload=payload,
        planning_depth=planning_depth,
    )


def validate_decision(
    decision: NavigatorDecision,
    nav_input: Optional[NavigatorInput] = None,
) -> List[str]:
    """Semantic validation. Returns a list of readable problems (empty = valid).
    nav_input enables the cross-checks (close-must-disposition-children)."""
    errors: List[str] = []
    if decision.move not in MOVES:
        errors.append(
            f"unknown move {decision.move!r} (valid: {', '.join(sorted(MOVES))})")
        return errors  # nothing below is meaningful for an unknown move
    if not decision.reasoning:
        errors.append("reasoning is required and must be non-empty")
    if not 0.0 <= decision.confidence <= 1.0:
        errors.append(f"confidence {decision.confidence} outside [0, 1]")

    payload = decision.payload
    for key in _REQUIRED_PAYLOAD[decision.move]:
        value = payload.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{decision.move} payload missing required key {key!r}")

    if decision.move == "fork":
        children = payload.get("children")
        if not isinstance(children, list) or not children:
            errors.append("fork payload 'children' must be a non-empty list")
        elif len(children) > FORK_CHILD_CAP:
            errors.append(
                f"fork spawns {len(children)} children, cap is {FORK_CHILD_CAP}")
        else:
            for i, child in enumerate(children):
                if not isinstance(child, dict) or not str(child.get("goal") or "").strip():
                    errors.append(f"fork child {i} missing 'goal'")

    if decision.move == "collate":
        ids = payload.get("child_handle_ids")
        if not isinstance(ids, list) or not ids:
            errors.append("collate payload 'child_handle_ids' must be a non-empty list")
        elif nav_input is not None:
            known = {c.handle_id for c in nav_input.open_children}
            for hid in ids:
                if hid not in known:
                    errors.append(
                        f"collate references unknown child {hid!r} "
                        f"(known: {sorted(known) or 'none'})")

    if decision.move == "close":
        closure = str(payload.get("closure") or "")
        if closure and closure not in CLOSURE_TYPES:
            errors.append(
                f"unknown closure type {closure!r} "
                f"(valid: {', '.join(sorted(CLOSURE_TYPES))})")
        if nav_input is not None and nav_input.open_children:
            disposition = payload.get("children_disposition")
            if not isinstance(disposition, dict):
                disposition = {}
            for child in nav_input.open_children:
                got = str(disposition.get(child.handle_id) or "")
                if not got:
                    errors.append(
                        f"close leaves child {child.handle_id!r} "
                        f"({child.goal[:60]!r}) undispositioned — every open "
                        f"child needs done | abandoned | absorbed")
                elif got not in CHILD_DISPOSITIONS:
                    errors.append(
                        f"child {child.handle_id!r} disposition {got!r} invalid "
                        f"(valid: {', '.join(sorted(CHILD_DISPOSITIONS))})")

    return errors
