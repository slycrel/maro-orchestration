"""Phase 47: VerificationAgent — first-class verification agent.

Promotes verification from scattered function calls to a named agent with
its own identity, system prompts, and tool set. Centralizes:
  - Step-level verification (ralph verify loop) from step_exec.verify_step
  - Adversarial claim review from quality_gate._ADVERSARIAL_SYSTEM
  - Mission output quality review from quality_gate._GATE_SYSTEM

Usage:
    from verification_agent import VerificationAgent
    va = VerificationAgent(adapter)
    result = va.verify_step(step_text, result)         # passes or retries
    claims = va.adversarial_pass(goal, result_text)    # list of contested claims
    verdict = va.quality_review(goal, step_outcomes)   # pass/escalate verdict

CLI:
    poe-verify --step "Fetch market data" --result "Got 42 records" [--adversarial]
"""

from __future__ import annotations

import json
import logging
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from llm_parse import extract_json, safe_float, content_or_empty

log = logging.getLogger("poe.verification")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_VERIFY_STEP_SYSTEM = textwrap.dedent("""\
    You are Poe's verification agent. A step in an autonomous task just completed.
    Your job: did the result actually accomplish what the step asked for?

    PASS: the result directly addresses the step goal with specific content.
    RETRY: the result is vague, off-topic, incomplete, or mostly a plan for doing
           the work rather than the work itself.

    Respond with JSON only:
    {"verdict": "PASS" or "RETRY", "reason": "one sentence", "confidence": 0.0-1.0}

    Be strict but fair. RETRY only if the result genuinely failed the step goal.
    Do not retry steps that are complete but imperfect.
""").strip()

_ADVERSARIAL_SYSTEM = textwrap.dedent("""\
    You are Poe's adversarial reviewer. A research or analysis task just completed.
    Challenge the claims before they reach the user.

    For each significant claim in the output:
    - Is the evidence actually what it claims to be? (RCT vs observational vs animal?)
    - Is the mechanism sound, or is it extrapolation?
    - Are there competing studies, frameworks, or interpretations not mentioned?
    - Is the dose, population, or context applicable to the goal?

    Grade each finding: CONFIRMED / DOWNGRADED / CONTESTED / OVERCLAIMED.
    Be specific — cite what's wrong, not just that something is uncertain.
    Skip claims that are clearly solid. Focus on what would change a decision.

    Produce a concise list of contested claims with verdict and one-sentence reason.
    If everything checks out, respond with an empty list: []
    Format: JSON array of {"claim": "...", "verdict": "...", "reason": "..."}
""").strip()

_QUALITY_REVIEW_SYSTEM = textwrap.dedent("""\
    You are Poe's quality reviewer. A research or analysis task just completed.
    Decide if the output meets the bar for the stated goal.

    PASS criteria (all must hold):
    - The output directly addresses the goal — not tangential or generic
    - Key claims are specific, not vague ("evidence is mixed" without detail is vague)
    - If the goal asked for risks/interactions/alternatives, they were covered
    - The output would be useful to act on or bring to a domain expert

    ESCALATE criteria (any one is enough):
    - Output is shallow, generic, or clearly incomplete for the goal
    - Important sub-questions in the goal were skipped
    - Claims are unverified or obviously wrong
    - The result looks like a Wikipedia summary, not targeted research

    Respond with JSON only:
    {"verdict": "PASS" or "ESCALATE", "reason": "one sentence", "confidence": 0.0-1.0}

    Be direct. Only escalate if the output would genuinely mislead or disappoint.
""").strip()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class StepVerdict:
    passed: bool
    reason: str
    confidence: float


@dataclass
class ClaimContest:
    claim: str
    verdict: str  # CONFIRMED / DOWNGRADED / CONTESTED / OVERCLAIMED
    reason: str


@dataclass
class QualityVerdict:
    verdict: str          # "PASS" | "ESCALATE"
    reason: str
    confidence: float
    escalate: bool
    contested_claims: List[ClaimContest] = field(default_factory=list)

    def contested_summary(self) -> str:
        """Format contested claims as a readable addendum for appending to results."""
        if not self.contested_claims:
            return ""
        lines = ["\n\n---\n**Verification notes:**"]
        for c in self.contested_claims:
            lines.append(f"- [{c.verdict}] {c.claim} — {c.reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# VerificationAgent
# ---------------------------------------------------------------------------

class VerificationAgent:
    """Named verification agent — centralizes step, adversarial, and quality verification.

    Designed as a first-class agent (peer to planAgent / exploreAgent in Claude Code
    architecture) rather than a scattered function call. Supports TeamCreateTool-style
    composition: callers can address this agent by name and configure its behavior.

    All methods are non-fatal — verification errors return permissive defaults so they
    never block execution.
    """

    name = "verification_agent"
    role = "verifier"

    def __init__(self, adapter, *, confidence_threshold: float = 0.75):
        self._adapter = adapter
        self._confidence_threshold = confidence_threshold

    # ------------------------------------------------------------------
    # verify_step — ralph verify loop (step-level)
    # ------------------------------------------------------------------

    def verify_step(self, step_text: str, result: str) -> StepVerdict:
        """Verify a completed step result. Returns StepVerdict(passed, reason, confidence).

        PASS → accept the result. RETRY → step should be retried.
        Returns passed=True on any error so verification never blocks execution.
        """
        if not isinstance(result, str):
            result = str(result) if result else ""
        if not result.strip():
            return StepVerdict(passed=False, reason="empty result", confidence=1.0)

        try:
            from llm import LLMMessage
            resp = self._adapter.complete(
                [
                    LLMMessage("system", _VERIFY_STEP_SYSTEM),
                    LLMMessage("user",
                        f"Step goal: {step_text}\n\n"
                        f"Step result (first 1200 chars):\n{result[:1200]}"
                    ),
                ],
                max_tokens=128,
                temperature=0.1,
            )
            data = extract_json(content_or_empty(resp), dict, log_tag="verification_agent.verify_step")
            if data:
                verdict = data.get("verdict", "PASS").upper()
                reason = data.get("reason", "")
                confidence = safe_float(data.get("confidence"), default=0.5, min_val=0.0, max_val=1.0)
                passed = verdict == "PASS" or confidence < self._confidence_threshold
                log.debug("verify_step verdict=%s confidence=%.2f passed=%s reason=%r",
                          verdict, confidence, passed, reason[:80])
                return StepVerdict(passed=passed, reason=reason, confidence=confidence)
        except Exception as exc:
            log.debug("verify_step failed (non-fatal): %s", exc)

        return StepVerdict(passed=True, reason="verify skipped (error)", confidence=0.0)

    # ------------------------------------------------------------------
    # adversarial_pass — claim-level adversarial review
    # ------------------------------------------------------------------

    def adversarial_pass(self, goal: str, result_text: str) -> List[ClaimContest]:
        """Run adversarial claim review. Returns list of contested/overclaimed findings.

        Empty list means everything checked out. Never raises.
        """
        if not result_text.strip():
            return []

        try:
            from llm import LLMMessage
            resp = self._adapter.complete(
                [
                    LLMMessage("system", _ADVERSARIAL_SYSTEM),
                    LLMMessage("user",
                        f"Goal: {goal[:300]}\n\n"
                        f"Output to review:\n{result_text[:2000]}"
                    ),
                ],
                max_tokens=1024,
                temperature=0.2,
            )
            raw = extract_json(content_or_empty(resp), list, log_tag="verification_agent.adversarial_pass")
            if raw is not None:
                claims = []
                for item in raw:
                    if isinstance(item, dict):
                        claims.append(ClaimContest(
                            claim=str(item.get("claim", ""))[:200],
                            verdict=str(item.get("verdict", "CONTESTED")).upper(),
                            reason=str(item.get("reason", ""))[:200],
                        ))
                log.info("adversarial_pass: %d contested claims for goal=%r",
                         len(claims), goal[:60])
                return claims
        except Exception as exc:
            log.debug("adversarial_pass failed (non-fatal): %s", exc)

        return []

    # ------------------------------------------------------------------
    # quality_review — mission-level pass/escalate verdict
    # ------------------------------------------------------------------

    def quality_review(
        self,
        goal: str,
        step_outcomes: list,
        *,
        run_adversarial: bool = True,
    ) -> QualityVerdict:
        """Review completed loop output. Returns QualityVerdict(verdict, reason, escalate).

        Runs two passes:
        1. PASS/ESCALATE verdict — should we re-run at a higher tier?
        2. Optional adversarial claim review — appended to verdict.contested_claims.

        Returns PASS with low confidence on any failure — never blocks execution.
        """
        done_steps = [s for s in step_outcomes if getattr(s, "status", "") == "done"]
        if not done_steps:
            return QualityVerdict("PASS", "no completed steps to review", 0.5, False)

        review_steps = done_steps[-3:]
        output_summary = "\n\n".join(
            f"Step {getattr(s, 'index', i+1)}: {getattr(s, 'text', '?')[:80]}\n"
            f"Result: {(getattr(s, 'result', '') or '')[:600]}"
            for i, s in enumerate(review_steps)
        )

        verdict = "PASS"
        reason = ""
        confidence = 0.0
        escalate = False

        try:
            from llm import LLMMessage
            user_msg = (
                f"Goal: {goal[:300]}\n\n"
                f"Output from final steps:\n{output_summary}\n\n"
                "Does this output meet the bar for the stated goal?"
            )
            resp = self._adapter.complete(
                [
                    LLMMessage("system", _QUALITY_REVIEW_SYSTEM),
                    LLMMessage("user", user_msg),
                ],
                max_tokens=256,
                temperature=0.1,
            )
            data = extract_json(content_or_empty(resp), dict, log_tag="verification_agent.quality_review")
            if data:
                verdict = data.get("verdict", "PASS").upper()
                reason = data.get("reason", "")
                confidence = safe_float(data.get("confidence"), default=0.5, min_val=0.0, max_val=1.0)
                escalate = verdict == "ESCALATE" and confidence >= self._confidence_threshold
                log.info("quality_review verdict=%s confidence=%.2f escalate=%s reason=%r",
                         verdict, confidence, escalate, reason[:80])
        except Exception as exc:
            log.debug("quality_review pass1 failed (non-fatal): %s", exc)
            return QualityVerdict("PASS", "gate parse error — defaulting to pass", 0.0, False)

        contested: List[ClaimContest] = []
        if run_adversarial:
            full_result = "\n\n".join(
                (getattr(s, "result", "") or "")[:600] for s in review_steps
            )
            contested = self.adversarial_pass(goal, full_result)

        return QualityVerdict(
            verdict=verdict,
            reason=reason,
            confidence=confidence,
            escalate=escalate,
            contested_claims=contested,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list = None) -> int:
    """poe-verify CLI — run verification agent against a step or result text."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="poe-verify",
        description="Run VerificationAgent against a step result or completed output.",
    )
    parser.add_argument("--step", help="Step goal text (for step-level verify)")
    parser.add_argument("--result", help="Result text to verify")
    parser.add_argument("--goal", help="High-level goal (for adversarial/quality pass)")
    parser.add_argument("--adversarial", action="store_true",
                        help="Run adversarial claim review on --result")
    parser.add_argument("--model", default="cheap", help="Model tier (cheap/mid/power)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        from llm import build_adapter
    except ImportError:
        print("ERROR: llm module not available", file=sys.stderr)
        return 1

    adapter = build_adapter(model=args.model)
    va = VerificationAgent(adapter)

    if args.step and args.result:
        verdict = va.verify_step(args.step, args.result)
        print(f"Step verify: {'PASS' if verdict.passed else 'RETRY'}")
        print(f"  reason: {verdict.reason}")
        print(f"  confidence: {verdict.confidence:.2f}")
        return 0

    if args.adversarial and args.result:
        goal = args.goal or "(unspecified)"
        claims = va.adversarial_pass(goal, args.result)
        if not claims:
            print("Adversarial pass: no contested claims")
        else:
            print(f"Adversarial pass: {len(claims)} claim(s) flagged")
            for c in claims:
                print(f"  [{c.verdict}] {c.claim[:80]} — {c.reason[:80]}")
        return 0

    print("Usage: poe-verify --step TEXT --result TEXT")
    print("       poe-verify --adversarial --result TEXT [--goal GOAL]")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
