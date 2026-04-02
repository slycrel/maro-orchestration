"""Passes — Unified multi-pass review pipeline.

Chains quality review passes in a single call:
  Pass 1 — quality_gate (skeptic pass: is the output thorough?)
  Pass 2 — adversarial  (contested claims: what's overclaimed?)
  Pass 3 — council      (LLM council: devil's advocate, domain skeptic, implementer)
  Pass 4 — debate       (bull vs bear vs risk manager)
  Pass 5 — thinkback    (hindsight replay of step decisions)

All passes are optional and composable. The final PassReport aggregates verdicts
from all enabled passes and produces a single escalation decision.

Philosophy: vs quality_gate.py's run_quality_gate() which runs sub-passes internally,
this module is the external-facing unified API. It's what you call when you want
a full review, not just one lens.

Usage:
    from passes import run_passes, PassConfig, PassReport
    config = PassConfig(council=True, debate=True)
    report = run_passes(goal, step_outcomes, config=config, adapter=adapter)
    if report.escalate:
        print(f"Escalate: {report.escalation_reason}")

CLI:
    poe-passes --goal "..." --passes council,debate
    poe-passes --passes all    # all five passes
    poe-passes --passes quick  # quality_gate only
"""

from __future__ import annotations

import logging
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.passes")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PASS_NAMES = ["quality_gate", "adversarial", "council", "debate", "thinkback"]

_PASS_PRESETS: Dict[str, List[str]] = {
    "quick":    ["quality_gate"],
    "standard": ["quality_gate", "adversarial"],
    "thorough": ["quality_gate", "adversarial", "council"],
    "full":     ["quality_gate", "adversarial", "council", "debate"],
    "all":      ["quality_gate", "adversarial", "council", "debate", "thinkback"],
}


@dataclass
class PassConfig:
    quality_gate: bool = True
    adversarial: bool = False   # already part of quality_gate Pass 2 when True
    council: bool = False
    debate: bool = False
    thinkback: bool = False

    @classmethod
    def from_names(cls, names: List[str]) -> "PassConfig":
        """Build config from a list of pass names."""
        # Expand presets
        expanded: List[str] = []
        for name in names:
            if name in _PASS_PRESETS:
                expanded.extend(_PASS_PRESETS[name])
            else:
                expanded.append(name)
        return cls(
            quality_gate="quality_gate" in expanded,
            adversarial="adversarial" in expanded,
            council="council" in expanded,
            debate="debate" in expanded,
            thinkback="thinkback" in expanded,
        )

    @classmethod
    def from_preset(cls, preset: str) -> "PassConfig":
        """Build from a named preset (quick/standard/thorough/full/all)."""
        return cls.from_names([preset])

    def active_passes(self) -> List[str]:
        active = []
        if self.quality_gate:
            active.append("quality_gate")
        if self.adversarial:
            active.append("adversarial")
        if self.council:
            active.append("council")
        if self.debate:
            active.append("debate")
        if self.thinkback:
            active.append("thinkback")
        return active


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PassResult:
    name: str
    verdict: str                # "PASS" | "FAIL" | "ESCALATE" | "CAUTION" | "REJECT" | "WEAK"
    reason: str
    escalate: bool
    elapsed_ms: int
    raw: Any = None             # The raw verdict object from the pass


@dataclass
class PassReport:
    goal: str
    passes_run: List[str]
    results: List[PassResult]
    escalate: bool              # True if ANY pass escalated
    escalation_reason: str      # First escalation reason, or "PASS" if none
    elapsed_ms: int

    def summary(self) -> str:
        lines = [f"PassReport goal={self.goal!r}"]
        for r in self.results:
            flag = " [!]" if r.escalate else ""
            lines.append(f"  [{r.name}]{flag} {r.verdict}: {r.reason[:80]}")
        lines.append(
            f"  overall={'ESCALATE' if self.escalate else 'PASS'} "
            f"({self.elapsed_ms}ms)"
        )
        if self.escalate:
            lines.append(f"  reason: {self.escalation_reason}")
        return "\n".join(lines)

    def to_text(self) -> str:
        """Full human-readable report."""
        lines = [
            f"# Multi-Pass Review",
            f"Goal: {self.goal}",
            f"Passes: {', '.join(self.passes_run)}",
            f"Overall: {'ESCALATE' if self.escalate else 'PASS'}",
            "",
        ]
        for r in self.results:
            flag = " ⚠" if r.escalate else " ✓"
            lines.append(f"## {r.name.upper()}{flag}")
            lines.append(f"Verdict: {r.verdict}")
            lines.append(f"Reason: {r.reason}")
            lines.append(f"Time: {r.elapsed_ms}ms")
            lines.append("")
        if self.escalate:
            lines.append(f"**Escalation reason:** {self.escalation_reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass runners
# ---------------------------------------------------------------------------

def _run_quality_gate_pass(
    goal: str,
    step_outcomes: list,
    adapter,
    *,
    run_adversarial: bool = True,
    run_council: bool = False,
    run_debate: bool = False,
) -> PassResult:
    t0 = time.monotonic()
    try:
        from quality_gate import run_quality_gate
        verdict = run_quality_gate(
            goal,
            step_outcomes,
            adapter=adapter,
            run_council=run_council,
            with_debate=run_debate,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        return PassResult(
            name="quality_gate",
            verdict=verdict.verdict,
            reason=verdict.reason,
            escalate=verdict.escalate,
            elapsed_ms=elapsed,
            raw=verdict,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log.warning("passes: quality_gate pass failed: %s", exc)
        return PassResult(
            name="quality_gate",
            verdict="PASS",
            reason=f"(pass failed: {exc})",
            escalate=False,
            elapsed_ms=elapsed,
        )


def _run_council_pass(goal: str, step_outcomes: list, adapter) -> PassResult:
    t0 = time.monotonic()
    try:
        from quality_gate import run_llm_council
        council = run_llm_council(goal, step_outcomes, adapter=adapter)
        elapsed = int((time.monotonic() - t0) * 1000)
        weak_critics = [c.critic for c in council.critiques if c.verdict == "WEAK"]
        verdict = "WEAK" if council.escalate else "ACCEPTABLE"
        reason = (
            f"{council.weak_count}/3 critics rated WEAK"
            + (f": {', '.join(weak_critics)}" if weak_critics else "")
        )
        return PassResult(
            name="council",
            verdict=verdict,
            reason=reason,
            escalate=council.escalate,
            elapsed_ms=elapsed,
            raw=council,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log.warning("passes: council pass failed: %s", exc)
        return PassResult(
            name="council",
            verdict="ACCEPTABLE",
            reason=f"(pass failed: {exc})",
            escalate=False,
            elapsed_ms=elapsed,
        )


def _run_debate_pass(goal: str, step_outcomes: list, adapter) -> PassResult:
    t0 = time.monotonic()
    try:
        from quality_gate import run_debate
        debate = run_debate(goal, step_outcomes, adapter=adapter)
        elapsed = int((time.monotonic() - t0) * 1000)
        verdict = debate.risk_manager_verdict  # "PROCEED" | "CAUTION" | "REJECT"
        reason = (
            f"Risk Manager: {verdict} (dominant={debate.dominant_position}) "
            f"— {debate.risk_manager_reasoning[:80]}"
        )
        return PassResult(
            name="debate",
            verdict=verdict,
            reason=reason,
            escalate=debate.escalate,
            elapsed_ms=elapsed,
            raw=debate,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log.warning("passes: debate pass failed: %s", exc)
        return PassResult(
            name="debate",
            verdict="PROCEED",
            reason=f"(pass failed: {exc})",
            escalate=False,
            elapsed_ms=elapsed,
        )


def _run_thinkback_pass(goal: str, step_outcomes: list, adapter, loop_result=None) -> PassResult:
    """Thinkback pass — works best with a real LoopResult, falls back to outcome synthesis."""
    t0 = time.monotonic()
    try:
        from thinkback import run_thinkback, run_thinkback_from_outcome

        if loop_result is not None:
            report = run_thinkback(loop_result, adapter=adapter)
        else:
            # Synthesize a fake outcome from step_outcomes
            class _FakeStep:
                def __init__(self, i, text, result):
                    self.index = i
                    self.text = text
                    self.status = "done"
                    self.result = result
                    self.confidence = ""

            class _FakeLoop:
                pass

            obj = _FakeLoop()
            obj.loop_id = "passes"
            obj.goal = goal
            obj.status = "done"
            obj.steps = [
                _FakeStep(i, s.get("text", "") if isinstance(s, dict) else getattr(s, "text", ""),
                         s.get("result", "") if isinstance(s, dict) else getattr(s, "result", ""))
                for i, s in enumerate(step_outcomes)
            ]
            obj.total_tokens_in = 0
            obj.total_tokens_out = 0
            report = run_thinkback(obj, adapter=adapter)

        elapsed = int((time.monotonic() - t0) * 1000)
        poor_count = sum(1 for r in report.step_reviews if r.decision_quality == "poor")
        verdict = "WEAK" if poor_count >= 2 or report.overall_assessment == "weak" else report.overall_assessment.upper()
        escalate = poor_count >= 2 or report.overall_assessment == "weak"
        reason = (
            f"Assessment={report.overall_assessment} efficiency={report.mission_efficiency:.0%} "
            f"poor_decisions={poor_count}/{len(report.step_reviews)}"
        )
        return PassResult(
            name="thinkback",
            verdict=verdict,
            reason=reason,
            escalate=escalate,
            elapsed_ms=elapsed,
            raw=report,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log.warning("passes: thinkback pass failed: %s", exc)
        return PassResult(
            name="thinkback",
            verdict="ACCEPTABLE",
            reason=f"(pass failed: {exc})",
            escalate=False,
            elapsed_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_passes(
    goal: str,
    step_outcomes: list,
    *,
    config: Optional[PassConfig] = None,
    adapter=None,
    loop_result=None,
    preset: Optional[str] = None,
) -> PassReport:
    """Run configured review passes on completed step outcomes.

    Args:
        goal: The original mission goal
        step_outcomes: List of step outcome dicts or StepOutcome objects
        config: PassConfig specifying which passes to run (default: quality_gate only)
        adapter: LLM adapter (builds cheap adapter if None)
        loop_result: Optional LoopResult for thinkback pass
        preset: Named preset ("quick"/"standard"/"thorough"/"full"/"all")

    Returns:
        PassReport with per-pass results and overall escalation verdict
    """
    if preset is not None:
        config = PassConfig.from_preset(preset)
    if config is None:
        config = PassConfig()  # quality_gate only

    # Build adapter if not provided
    if adapter is None:
        try:
            from llm import build_adapter
            adapter = build_adapter("cheap")
        except Exception as exc:
            log.warning("passes: could not build adapter: %s", exc)

    t0 = time.monotonic()
    results: List[PassResult] = []
    active = config.active_passes()

    # quality_gate (absorbs adversarial, council, debate if co-enabled)
    if config.quality_gate:
        r = _run_quality_gate_pass(
            goal, step_outcomes, adapter,
            run_adversarial=config.adversarial,
            run_council=config.council and not config.debate,
            run_debate=config.debate,
        )
        results.append(r)
        # If quality_gate ran council/debate internally, don't run them again separately
        if config.council and not config.debate:
            config = PassConfig(
                quality_gate=False,
                adversarial=False,
                council=False,
                debate=False,
                thinkback=config.thinkback,
            )
        elif config.debate:
            config = PassConfig(
                quality_gate=False,
                adversarial=False,
                council=False,
                debate=False,
                thinkback=config.thinkback,
            )

    # Council (standalone — if quality_gate not used or council not absorbed)
    if config.council:
        results.append(_run_council_pass(goal, step_outcomes, adapter))

    # Debate (standalone)
    if config.debate:
        results.append(_run_debate_pass(goal, step_outcomes, adapter))

    # Thinkback (standalone — always separate)
    if config.thinkback:
        results.append(_run_thinkback_pass(goal, step_outcomes, adapter, loop_result=loop_result))

    elapsed = int((time.monotonic() - t0) * 1000)

    # Aggregate escalation
    escalating = [r for r in results if r.escalate]
    escalate = bool(escalating)
    escalation_reason = escalating[0].reason if escalating else "PASS"

    return PassReport(
        goal=goal,
        passes_run=active,
        results=results,
        escalate=escalate,
        escalation_reason=escalation_reason,
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="poe-passes",
        description="Run multi-pass review on a goal + step outcomes.",
    )
    p.add_argument("--goal", required=True, help="The mission goal to review")
    p.add_argument(
        "--passes",
        default="quick",
        help=(
            "Comma-separated pass names or preset. "
            f"Presets: {', '.join(_PASS_PRESETS)}. "
            f"Passes: {', '.join(PASS_NAMES)}."
        ),
    )
    p.add_argument("--latest-outcome", action="store_true",
                   help="Load latest outcome from memory and use its summary as input")
    p.add_argument("--model", default="cheap", help="Model tier (default: cheap)")
    p.add_argument("--output", metavar="FILE", help="Write report to file")
    args = p.parse_args(argv)

    # Build config
    pass_list = [p.strip() for p in args.passes.split(",")]
    config = PassConfig.from_names(pass_list)

    # Build step outcomes
    step_outcomes = []
    if args.latest_outcome:
        try:
            from thinkback import load_latest_outcome
            outcome = load_latest_outcome()
            if outcome:
                # Synthesize step outcomes from summary + lessons
                step_outcomes = [
                    {"text": "Mission execution", "result": outcome.get("summary", ""), "status": "done"},
                ]
                for i, lesson in enumerate(outcome.get("lessons", []), 1):
                    step_outcomes.append({"text": f"Lesson {i}", "result": lesson, "status": "done"})
                print(f"Loaded outcome: {outcome.get('outcome_id')} / {outcome.get('goal', '')[:60]!r}")
            else:
                print("WARNING: no outcomes found, running with empty context")
        except Exception as exc:
            print(f"WARNING: could not load outcome: {exc}")
    else:
        # Empty step outcomes — passes will analyze goal text only
        step_outcomes = [{"text": args.goal, "result": args.goal, "status": "done"}]

    # Build adapter
    adapter = None
    try:
        from llm import build_adapter
        adapter = build_adapter(args.model)
    except Exception as exc:
        print(f"WARNING: could not build adapter ({exc}), some passes may fail")

    # Run passes
    print(f"Running passes: {', '.join(config.active_passes())} on goal: {args.goal[:60]!r}")
    report = run_passes(args.goal, step_outcomes, config=config, adapter=adapter)

    # Output
    print()
    print(report.summary())

    if args.output:
        from pathlib import Path
        Path(args.output).write_text(report.to_text(), encoding="utf-8")
        print(f"\nFull report written to {args.output}")

    return 1 if report.escalate else 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())
