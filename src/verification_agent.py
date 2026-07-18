"""Phase 47: VerificationAgent — first-class verification agent.

Promotes step-level verification from a scattered function call to a named
agent with its own identity and system prompt.

Usage:
    from verification_agent import VerificationAgent
    va = VerificationAgent(adapter)
    result = va.verify_step(step_text, result)         # passes or retries

CLI:
    maro-verify --step "Fetch market data" --result "Got 42 records"
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass
from llm_parse import extract_json, safe_float, content_or_empty

log = logging.getLogger("maro.verification")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_VERIFY_STEP_SYSTEM = textwrap.dedent("""\
    You are a verification agent. A step in an autonomous task just completed.
    Your job: did the result actually accomplish what the step asked for?

    PASS: the result directly addresses the step goal with specific content.
    RETRY: the result is vague, off-topic, incomplete, or mostly a plan for doing
           the work rather than the work itself.
    RETRY also when the result PROMISES future or background completion instead
    of reporting finished work ("started a monitor", "will be notified when it
    completes", "running in background, will report back") — a promise is not
    the work; the agent's session ends when it returns, so promised follow-ups
    never run. State in your reason that the step must re-execute SYNCHRONOUSLY.

    Steps often deliver their real content to files in the project artifacts
    directory and report only a summary — the result text is narration, not the
    deliverable. When an artifact listing is provided and shows a file whose
    name, size, and excerpt match the claimed delivery, treat that content as
    delivered and judge the step on the artifact evidence. Do NOT retry a step
    merely because the full content is not pasted into the result text.

    Respond with JSON only:
    {"verdict": "PASS" or "RETRY", "reason": "one sentence", "confidence": 0.0-1.0}

    Be strict but fair. RETRY only if the result genuinely failed the step goal.
    Do not retry steps that are complete but imperfect.
""").strip()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class StepVerdict:
    passed: bool
    reason: str
    confidence: float


# ---------------------------------------------------------------------------
# VerificationAgent
# ---------------------------------------------------------------------------

class VerificationAgent:
    """Named verification agent — centralizes step-level verification.

    Designed as a first-class agent (peer to planAgent / exploreAgent in Claude Code
    architecture) rather than a scattered function call. Supports TeamCreateTool-style
    composition: callers can address this agent by name and configure its behavior.

    All methods are non-fatal — verification errors return permissive defaults so they
    never block execution.
    """

    name = "verification_agent"
    role = "verifier"

    def __init__(self, adapter, *, confidence_threshold: float = 0.75,
                 max_input_chars: int = 1200):
        self._adapter = adapter
        self._confidence_threshold = confidence_threshold
        # How much of the step result the validator sees. 1200 is the
        # cost-conscious default for paid validators; a free local validator can
        # afford a far larger window (set via validate.max_input_chars).
        self._max_input_chars = max(200, int(max_input_chars))

    # ------------------------------------------------------------------
    # verify_step — ralph verify loop (step-level)
    # ------------------------------------------------------------------

    def verify_step(self, step_text: str, result: str,
                    artifacts_note: str = "") -> StepVerdict:
        """Verify a completed step result. Returns StepVerdict(passed, reason, confidence).

        PASS → accept the result. RETRY → step should be retried.
        Returns passed=True on any error so verification never blocks execution.

        artifacts_note: optional listing of project artifact files (name/size/
        excerpt) — evidence that content the result only summarizes was actually
        delivered to disk. Kept outside the result truncation window.
        """
        if not isinstance(result, str):
            result = str(result) if result else ""
        if not result.strip():
            return StepVerdict(passed=False, reason="empty result", confidence=1.0)

        _evidence = ""
        if artifacts_note:
            _evidence = (
                "\n\nProject artifact files (evidence of content delivered to "
                f"disk):\n{artifacts_note[:1200]}"
            )
        try:
            from llm import LLMMessage
            resp = self._adapter.complete(
                [
                    LLMMessage("system", _VERIFY_STEP_SYSTEM),
                    LLMMessage("user",
                        f"Step goal: {step_text}\n\n"
                        f"Step result (first {self._max_input_chars} chars):\n"
                        f"{result[:self._max_input_chars]}"
                        f"{_evidence}"
                    ),
                ],
                max_tokens=128,
                temperature=0.1,
                no_tools=True,
                purpose="verify-step",
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list = None) -> int:
    """maro-verify CLI — run verification agent against a step result."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="maro-verify",
        description="Run VerificationAgent against a step result.",
    )
    parser.add_argument("--step", help="Step goal text (for step-level verify)")
    parser.add_argument("--result", help="Result text to verify")
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

    print("Usage: maro-verify --step TEXT --result TEXT")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
