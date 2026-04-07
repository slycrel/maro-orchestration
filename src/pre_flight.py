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

try:
    from llm import build_adapter
except Exception:
    build_adapter = None  # type: ignore[assignment]

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
        from llm import LLMMessage, MODEL_CHEAP
        # Always use cheap model — this is a fast pattern-match, not deep reasoning
        try:
            _reviewer = build_adapter(model=MODEL_CHEAP)
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


# ---------------------------------------------------------------------------
# Multi-lens review (Phase 58: philosopher perspectives)
# ---------------------------------------------------------------------------
# Three focused Haiku passes, each with a single-focus prompt. More signal
# than the combined pass, but 3x the cost. Use for high-stakes goals or
# when the single-pass review returns scope=wide.
# ---------------------------------------------------------------------------

_LENS_SCOPE_SYSTEM = """\
You are a SCOPE DETECTOR reviewing a step plan before execution.
One job only: is the step count honest for the true size of this work?
Count how many atomic file-reads or tool-calls are hidden inside single steps.
Respond ONLY with JSON: {"scope": "narrow"|"medium"|"wide", "note": "<one sentence why>",
"compressed_steps": [<step numbers that hide multiple operations>]}
""".strip()

_LENS_DEPS_SYSTEM = """\
You are a DEPENDENCY SPIDER reviewing a step plan before execution.
One job only: find hidden dependencies — steps that cannot proceed without
information that must be discovered in another step or that isn't in the plan.
Respond ONLY with JSON:
{"dependency_risks": [{"step": <1-based int>, "missing": "<what's needed>"}]}
""".strip()

_LENS_ASSUMPTIONS_SYSTEM = """\
You are an ASSUMPTION AUDITOR reviewing a step plan before execution.
One job only: list the silent assumptions this plan makes that could break it.
Focus on: access/credentials assumed available, outputs of prior steps assumed
to have specific formats, goal scope assumed smaller than it might be.
Respond ONLY with JSON:
{"critical_assumptions": [{"step": <int or 0 for whole plan>, "assumption": "<string>"}]}
""".strip()


def multi_lens_review(
    goal: str,
    steps: List[str],
    adapter,
    *,
    verbose: bool = False,
) -> PlanReview:
    """Run three focused lens reviews and merge into a single PlanReview.

    Uses 3 cheap Haiku calls, each with a single-focus prompt:
      1. Scope detector — is the step count honest?
      2. Dependency spider — hidden inter-step dependencies?
      3. Assumption auditor — what could silently break the plan?

    Returns a merged PlanReview. Never raises. Falls back to single-pass
    review if any lens fails.
    """
    if not steps:
        return PlanReview(scope="unknown", scope_note="no steps to review")

    from llm import LLMMessage, MODEL_CHEAP
    steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
    user_msg = f"Goal: {goal}\n\nProposed plan:\n{steps_text}"

    flags: List[PlanFlag] = []
    scope = "medium"
    scope_note = ""
    milestone_indices: List[int] = []

    try:
        _reviewer = build_adapter(model=MODEL_CHEAP)
    except Exception:
        _reviewer = adapter

    # --- Lens 1: Scope detector ---
    try:
        resp1 = _reviewer.complete(
            [LLMMessage("system", _LENS_SCOPE_SYSTEM), LLMMessage("user", user_msg)],
            max_tokens=256, temperature=0.1, timeout=20,
        )
        raw1 = resp1.content.strip()
        if raw1.startswith("```"):
            raw1 = "\n".join(raw1.splitlines()[1:]).rstrip("`").strip()
        d1 = json.loads(raw1)
        scope = d1.get("scope", "medium")
        scope_note = d1.get("note", "")
        for s in d1.get("compressed_steps", []):
            flags.append(PlanFlag(kind="milestone", step=int(s), message="step may hide multiple operations", severity="warn"))
            milestone_indices.append(int(s))
    except Exception as e:
        log.debug("multi_lens scope lens failed: %s", e)

    # --- Lens 2: Dependency spider ---
    try:
        resp2 = _reviewer.complete(
            [LLMMessage("system", _LENS_DEPS_SYSTEM), LLMMessage("user", user_msg)],
            max_tokens=256, temperature=0.1, timeout=20,
        )
        raw2 = resp2.content.strip()
        if raw2.startswith("```"):
            raw2 = "\n".join(raw2.splitlines()[1:]).rstrip("`").strip()
        d2 = json.loads(raw2)
        for r in d2.get("dependency_risks", []):
            flags.append(PlanFlag(kind="assumption", step=int(r.get("step", 0)),
                                  message=f"hidden dep: {r.get('missing', '')}", severity="warn"))
    except Exception as e:
        log.debug("multi_lens dep lens failed: %s", e)

    # --- Lens 3: Assumption auditor ---
    try:
        resp3 = _reviewer.complete(
            [LLMMessage("system", _LENS_ASSUMPTIONS_SYSTEM), LLMMessage("user", user_msg)],
            max_tokens=256, temperature=0.1, timeout=20,
        )
        raw3 = resp3.content.strip()
        if raw3.startswith("```"):
            raw3 = "\n".join(raw3.splitlines()[1:]).rstrip("`").strip()
        d3 = json.loads(raw3)
        for a in d3.get("critical_assumptions", []):
            flags.append(PlanFlag(kind="assumption", step=int(a.get("step", 0)),
                                  message=a.get("assumption", ""), severity="warn"))
    except Exception as e:
        log.debug("multi_lens assumption lens failed: %s", e)

    review = PlanReview(
        scope=scope,
        scope_note=scope_note or f"multi-lens: {len(flags)} flags",
        flags=flags,
        milestone_step_indices=milestone_indices,
    )

    _log_level = logging.WARNING if review.has_concerns else logging.INFO
    log.log(_log_level, "multi-lens: %s", review.summary())
    if verbose:
        import sys
        print(f"[poe] pre-flight multi-lens: {review.summary()}", file=sys.stderr, flush=True)

    return review


# ---------------------------------------------------------------------------
# Calibration stats CLI (poe-preflight-stats)
# ---------------------------------------------------------------------------

def preflight_calibration_stats(cal_path=None) -> dict:
    """Read memory/preflight_calibration.jsonl and return accuracy metrics.

    Returns dict with: total, true_positive, false_positive, false_negative,
    true_negative, precision, recall, scope_breakdown.
    """
    import json
    from pathlib import Path

    if cal_path is None:
        try:
            from orch_items import memory_dir
            cal_path = memory_dir() / "preflight_calibration.jsonl"
        except Exception:
            return {"error": "cannot locate memory_dir"}

    cal_path = Path(cal_path)
    if not cal_path.exists():
        return {"total": 0, "note": "no calibration data yet"}

    entries = []
    for line in cal_path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass

    if not entries:
        return {"total": 0, "note": "file exists but no valid entries"}

    tp = sum(1 for e in entries if e.get("true_positive"))
    fp = sum(1 for e in entries if e.get("false_positive"))
    fn = sum(1 for e in entries if e.get("false_negative"))
    tn = sum(1 for e in entries if e.get("true_negative"))

    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None

    scope_breakdown: dict = {}
    for e in entries:
        sc = e.get("scope_predicted", "unknown")
        scope_breakdown.setdefault(sc, {"count": 0, "stuck": 0, "done": 0})
        scope_breakdown[sc]["count"] += 1
        if e.get("actual_status") == "stuck":
            scope_breakdown[sc]["stuck"] += 1
        else:
            scope_breakdown[sc]["done"] += 1

    return {
        "total": len(entries),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "scope_breakdown": scope_breakdown,
    }


def _preflight_stats_main():
    """CLI entry point for poe-preflight-stats."""
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        description="Show pre-flight scope prediction accuracy vs actual loop outcomes."
    )
    parser.add_argument("--cal-path", default=None, help="Path to preflight_calibration.jsonl")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--scope-check", metavar="GOAL",
                        help="Classify a goal's scope without running a loop (debug)")
    args = parser.parse_args()

    if args.scope_check:
        # Quick scope classification without any LLM call
        try:
            from planner import estimate_goal_scope
            scope = estimate_goal_scope(args.scope_check)
        except Exception as e:
            print(f"Error: {e}")
            return 1
        print(f"scope: {scope}")
        print(f"goal:  {args.scope_check!r}")
        _hints = {
            "narrow": "→ single-shot decompose (skips multi-plan comparison)",
            "medium": "→ standard multi-plan (3 candidates, best selected)",
            "wide":   "→ staged-pass (multi-lens pre-flight review triggered)",
            "deep":   "→ staged-pass (milestone-aware execution likely)",
        }
        print(f"effect: {_hints.get(scope, '')}")
        return 0

    stats = preflight_calibration_stats(cal_path=args.cal_path)

    if args.json:
        print(_json.dumps(stats, indent=2))
        return 0

    total = stats.get("total", 0)
    if total == 0:
        print(f"No calibration data yet. Run some AGENDA loops to accumulate data.")
        print(f"Data stored at: memory/preflight_calibration.jsonl")
        return 0

    print(f"\nPre-flight calibration stats ({total} loops)\n")
    print(f"  True positives  (wide → stuck):  {stats['true_positive']}")
    print(f"  False positives (wide → done):   {stats['false_positive']}")
    print(f"  False negatives (narrow → stuck): {stats['false_negative']}")
    print(f"  True negatives  (narrow → done):  {stats['true_negative']}")
    print()
    prec = stats.get("precision")
    rec = stats.get("recall")
    print(f"  Precision: {prec:.0%}" if prec is not None else "  Precision: n/a")
    print(f"  Recall:    {rec:.0%}" if rec is not None else "  Recall:    n/a")
    print()
    print("  Scope breakdown:")
    for scope, data in sorted(stats.get("scope_breakdown", {}).items()):
        stuck_pct = data["stuck"] / data["count"] * 100 if data["count"] > 0 else 0
        print(f"    {scope:8s}: {data['count']:3d} loops  "
              f"{data['stuck']:2d} stuck / {data['done']:2d} done  "
              f"({stuck_pct:.0f}% stuck rate)")
    print()
    return 0
