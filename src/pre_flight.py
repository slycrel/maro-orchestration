"""
pre_flight.py — cheap plan review before execution starts.

Plays skeptic on the proposed step list to catch obvious problems before
wasting the execution budget. A single Haiku call with a targeted critic
prompt — recommendations, not gates.

The "System 1" bridge: the planner (System 2, slow, explicit) decomposes
the goal into steps. This reviewer (System 1 proxy, fast, pattern-matching)
looks at the whole plan and asks: does this smell right? Is the scope
accurate? Are there hidden load-bearing assumptions? Which steps are actually
sub-goals that need their own planning pass?

Not a gate — flags are advisory. The loop proceeds regardless. But if a
scope explosion or critical assumption is flagged, the caller can surface it
to the user or log it prominently for post-run analysis.
"""

from __future__ import annotations

import json
import logging
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("poe.pre_flight")

_REVIEW_SYSTEM = textwrap.dedent("""\
    You are a plan critic. A planning agent has decomposed a goal into steps.
    Your job: find what's wrong BEFORE execution wastes budget on it.

    Assess the plan on four dimensions:

    1. SCOPE: Does the step count reflect the true size of the work?
       - "narrow": goal is simple, plan looks complete (3-5 steps, no hidden depth)
       - "medium": goal is moderate, plan looks roughly right (6-12 steps)
       - "wide": plan is likely incomplete — the goal is bigger than it looks,
         or key sub-problems are bundled into single steps that will explode
       - Flag "wide" when you see: "read all X", "analyze the entire Y",
         any step that would require knowing things we haven't discovered yet.

    2. ASSUMPTIONS: What does this plan assume that could be wrong?
       Especially: steps that depend on prior steps producing specific output,
       steps that assume access/credentials/state that isn't guaranteed,
       steps that assume the goal is well-specified when it might not be.

    3. MILESTONE CANDIDATES: Which steps look like sub-goals that need their
       own planning pass? Flag any step that is really "run a whole project"
       in disguise — these should be sub-loops, not single steps.

    4. UNKNOWN UNKNOWNS: What does this plan not know that it should?
       Things the agent will discover mid-execution that will require replanning.

    Be terse. One sentence per flag. Don't pad.

    Respond ONLY with this JSON structure (no prose, no markdown):
    {
      "scope": "narrow" | "medium" | "wide",
      "scope_note": "<one sentence explanation>",
      "assumptions": [{"step": <1-based int or 0 for whole plan>, "issue": "<string>"}],
      "milestone_candidates": [{"step": <1-based int>, "reason": "<string>"}],
      "unknown_unknowns": ["<string>", ...]
    }
""").strip()


@dataclass
class PlanFlag:
    kind: str          # "assumption" | "milestone" | "unknown"
    step: int          # 1-based step index, 0 = whole plan
    message: str
    severity: str      # "info" | "warn"


@dataclass
class PlanReview:
    scope: str                          # "narrow" | "medium" | "wide" | "unknown"
    scope_note: str
    flags: List[PlanFlag] = field(default_factory=list)
    milestone_step_indices: List[int] = field(default_factory=list)
    raw: str = ""                       # raw LLM output for debugging

    @property
    def has_concerns(self) -> bool:
        return self.scope == "wide" or any(f.severity == "warn" for f in self.flags)

    def summary(self) -> str:
        parts = [f"scope={self.scope}"]
        if self.milestone_step_indices:
            parts.append(f"milestone_candidates={self.milestone_step_indices}")
        warn_count = sum(1 for f in self.flags if f.severity == "warn")
        if warn_count:
            parts.append(f"warnings={warn_count}")
        return " ".join(parts)

    def format_for_log(self) -> str:
        lines = [f"Pre-flight review: {self.summary()}"]
        if self.scope_note:
            lines.append(f"  Scope: {self.scope_note}")
        for f in self.flags:
            step_str = f"step {f.step}" if f.step else "plan"
            lines.append(f"  [{f.kind}] {step_str}: {f.message}")
        return "\n".join(lines)


def review_plan(
    goal: str,
    steps: List[str],
    adapter,
    *,
    verbose: bool = False,
) -> PlanReview:
    """Run a cheap pre-flight review of the proposed plan.

    Returns a PlanReview with scope estimate, flags, and milestone candidates.
    Never raises — on any error returns a minimal PlanReview with scope="unknown".
    """
    if not steps:
        return PlanReview(scope="unknown", scope_note="no steps to review")

    try:
        from llm import LLMMessage, MODEL_CHEAP, build_adapter as _build_adapter
        # Always use cheap model — this is a fast pattern-match, not deep reasoning
        try:
            _reviewer = _build_adapter(model=MODEL_CHEAP)
        except Exception:
            _reviewer = adapter

        steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
        user_msg = f"Goal: {goal}\n\nProposed plan:\n{steps_text}"

        resp = _reviewer.complete(
            [LLMMessage("system", _REVIEW_SYSTEM), LLMMessage("user", user_msg)],
            max_tokens=512,
            temperature=0.1,
            timeout=30,
        )

        raw = resp.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:])
            if raw.endswith("```"):
                raw = raw[:-3].strip()

        data = json.loads(raw)
        scope = data.get("scope", "unknown")
        scope_note = data.get("scope_note", "")
        flags: List[PlanFlag] = []
        milestone_indices: List[int] = []

        for a in data.get("assumptions", []):
            flags.append(PlanFlag(
                kind="assumption",
                step=int(a.get("step", 0)),
                message=a.get("issue", ""),
                severity="warn",
            ))

        for m in data.get("milestone_candidates", []):
            idx = int(m.get("step", 0))
            milestone_indices.append(idx)
            flags.append(PlanFlag(
                kind="milestone",
                step=idx,
                message=m.get("reason", ""),
                severity="warn",
            ))

        for u in data.get("unknown_unknowns", []):
            flags.append(PlanFlag(kind="unknown", step=0, message=u, severity="info"))

        review = PlanReview(
            scope=scope,
            scope_note=scope_note,
            flags=flags,
            milestone_step_indices=milestone_indices,
            raw=raw,
        )

        _log_level = logging.WARNING if review.has_concerns else logging.INFO
        log.log(_log_level, review.format_for_log())
        if verbose:
            import sys
            print(f"[poe] pre-flight: {review.summary()}", file=sys.stderr, flush=True)
            if review.scope == "wide":
                print(f"[poe] pre-flight: scope WARNING — {scope_note}", file=sys.stderr, flush=True)
            for f in review.flags:
                if f.severity == "warn":
                    step_str = f"step {f.step}" if f.step else "plan"
                    print(f"[poe] pre-flight [{f.kind}] {step_str}: {f.message}", file=sys.stderr, flush=True)

        return review

    except Exception as exc:
        log.debug("pre_flight review failed (non-blocking): %s", exc)
        return PlanReview(scope="unknown", scope_note=f"review failed: {exc}")
