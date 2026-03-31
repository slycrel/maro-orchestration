"""Post-loop quality gate — skeptic review of completed run output.

After a loop finishes, the quality gate reviews the output and decides whether
it meets the bar for the goal. If not, it can recommend or trigger a re-run at
a higher model tier.

The gate also runs an adversarial pass that produces specific contested claims —
these are appended to the result text even on PASS, so the output flags its own
weak spots rather than silently emitting potentially overclaimed findings.

The gate uses a cheap model (Haiku) regardless of what ran the loop — fast,
low-cost, and good enough at pattern-matching for "was this thorough?".

Usage:
    from quality_gate import run_quality_gate, QualityVerdict
    verdict = run_quality_gate(goal, step_outcomes, adapter)
    if verdict.escalate:
        print(f"Re-run needed: {verdict.reason}")
    if verdict.contested_claims:
        print(f"Contested: {verdict.contested_claims}")
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("poe.quality_gate")

_GATE_SYSTEM = textwrap.dedent("""\
    You are a quality reviewer. A research/analysis task just completed.
    Your job: decide if the output meets the bar for the stated goal.

    PASS criteria (all must hold):
    - The output directly addresses the goal — not tangential or generic
    - Key claims are specific, not vague ("evidence is mixed" without detail is vague)
    - If the goal asked for risks/interactions/alternatives, they were covered
    - The output would be useful to act on or bring to a domain expert

    ESCALATE criteria (any one is enough):
    - Output is shallow, generic, or clearly incomplete for the goal
    - Important sub-questions in the goal were skipped
    - Claims are unverified or obviously wrong (e.g. wrong drug class, wrong mechanism)
    - The result looks like a Wikipedia summary, not targeted research

    Respond with a JSON object:
    {
      "verdict": "PASS" or "ESCALATE",
      "reason": "one sentence — if ESCALATE, what specifically is missing or wrong",
      "confidence": 0.0–1.0
    }

    Be direct. Do not hedge. If it's good enough, say PASS. Only escalate if
    the output would genuinely mislead or disappoint the user.
""").strip()

_ADVERSARIAL_SYSTEM = textwrap.dedent("""\
    You are an adversarial reviewer. A research task just completed. Your job:
    challenge the claims before they reach the user.

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


@dataclass
class QualityVerdict:
    verdict: str        # "PASS" | "ESCALATE"
    reason: str
    confidence: float
    escalate: bool      # True if verdict == "ESCALATE" and confidence is high enough
    contested_claims: List[dict] = field(default_factory=list)  # from adversarial pass


def run_quality_gate(
    goal: str,
    step_outcomes: list,
    adapter=None,
    *,
    confidence_threshold: float = 0.75,
    run_adversarial: bool = True,
) -> QualityVerdict:
    """Review completed loop output and return a quality verdict.

    Runs two passes:
    1. PASS/ESCALATE verdict — should we re-run at a higher tier?
    2. Adversarial claim review — what specific claims are contested/overclaimed?
       Contested claims are returned in verdict.contested_claims regardless of
       PASS/ESCALATE, so callers can append them to the result text.

    Uses the provided adapter (should be cheap tier — gate itself is cheap).
    Returns PASS with low confidence on any failure — gate errors must never
    block or degrade the result.

    Args:
        goal: The original goal text.
        step_outcomes: List of StepOutcome objects from the loop.
        adapter: LLM adapter to use for the review (cheap tier recommended).
        confidence_threshold: Minimum confidence to act on ESCALATE.
        run_adversarial: Whether to run the adversarial claim review pass.
    """
    if adapter is None:
        return QualityVerdict("PASS", "no adapter — gate skipped", 0.0, False)

    # Build a compact summary of what the loop produced
    done_steps = [s for s in step_outcomes if getattr(s, "status", "") == "done"]
    if not done_steps:
        return QualityVerdict("PASS", "no completed steps to review", 0.5, False)

    # Use the last 3 step results as the review payload — synthesis/summary steps
    # are most representative of final quality
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
    contested_claims: List[dict] = []

    try:
        from llm import LLMMessage
        import json

        # --- Pass 1: PASS/ESCALATE verdict ---
        user_msg = (
            f"Goal: {goal[:300]}\n\n"
            f"Output from final steps:\n{output_summary}\n\n"
            f"Does this output meet the bar for the stated goal?"
        )

        resp = adapter.complete(
            [
                LLMMessage("system", _GATE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=256,
            temperature=0.1,
        )

        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            verdict = data.get("verdict", "PASS").upper()
            reason = data.get("reason", "")
            confidence = float(data.get("confidence", 0.5))
            escalate = verdict == "ESCALATE" and confidence >= confidence_threshold
            log.info("quality_gate verdict=%s confidence=%.2f escalate=%s reason=%r",
                     verdict, confidence, escalate, reason[:80])

    except Exception as exc:
        log.debug("quality_gate pass1 failed (non-fatal): %s", exc)
        return QualityVerdict("PASS", "gate parse error — defaulting to pass", 0.0, False)

    # --- Pass 2: Adversarial claim review ---
    if run_adversarial:
        try:
            from llm import LLMMessage
            import json

            adv_resp = adapter.complete(
                [
                    LLMMessage("system", _ADVERSARIAL_SYSTEM),
                    LLMMessage("user",
                        f"Goal: {goal[:300]}\n\n"
                        f"Output to challenge:\n{output_summary}"
                    ),
                ],
                max_tokens=1024,
                temperature=0.3,
            )

            adv_content = adv_resp.content.strip()
            start = adv_content.find("[")
            end = adv_content.rfind("]") + 1
            if start >= 0 and end > start:
                parsed = json.loads(adv_content[start:end])
                if isinstance(parsed, list):
                    contested_claims = [
                        c for c in parsed
                        if isinstance(c, dict) and c.get("claim")
                    ]
                    log.info("quality_gate adversarial found %d contested claims",
                             len(contested_claims))

        except Exception as exc:
            log.debug("quality_gate adversarial pass failed (non-fatal): %s", exc)

    return QualityVerdict(verdict, reason, confidence, escalate, contested_claims)


def next_model_tier(current_model: str) -> Optional[str]:
    """Return the next tier up from the current model, or None if already at top."""
    _TIER_ORDER = ["cheap", "mid", "power"]
    # Normalize raw model strings to tier names
    _MODEL_TO_TIER = {
        "claude-haiku-4-5-20251001": "cheap",
        "claude-haiku-4-5": "cheap",
        "haiku": "cheap",
        "cheap": "cheap",
        "claude-sonnet-4-6": "mid",
        "sonnet": "mid",
        "mid": "mid",
        "claude-opus-4-6": "power",
        "opus": "power",
        "power": "power",
    }
    tier = _MODEL_TO_TIER.get(current_model, "")
    if not tier:
        return None  # unknown model — don't escalate
    idx = _TIER_ORDER.index(tier)
    if idx >= len(_TIER_ORDER) - 1:
        return None  # already at power
    return _TIER_ORDER[idx + 1]
