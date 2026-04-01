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


# ---------------------------------------------------------------------------
# LLM Council — multi-framing critique (sycophancy defense)
# ---------------------------------------------------------------------------

_COUNCIL_FRAMINGS = [
    (
        "devil_advocate",
        textwrap.dedent("""\
            You are the devil's advocate. Assume the output is fundamentally flawed.
            Find what's missing, what assumptions are unjustified, and what conclusions
            the research failed to reach that it should have.

            Be specific. Name gaps. Don't say "could be more thorough" — say exactly
            what was omitted and why it matters for the stated goal.

            Respond with JSON:
            {
              "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
              "concerns": ["specific concern 1", "specific concern 2"],
              "most_critical_gap": "the single biggest missing piece"
            }
        """).strip(),
    ),
    (
        "domain_skeptic",
        textwrap.dedent("""\
            You are a domain skeptic. Challenge the methodology and assumptions.
            Identify where the research draws on weak evidence, misapplies domain
            knowledge, or reaches conclusions a domain expert would dispute.

            Focus on: wrong evidence tiers (animal vs human), confounded variables,
            contested mechanisms, population mismatch, missing context.

            Respond with JSON:
            {
              "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
              "concerns": ["specific concern 1", "specific concern 2"],
              "most_critical_gap": "the single biggest methodological flaw"
            }
        """).strip(),
    ),
    (
        "implementation_critic",
        textwrap.dedent("""\
            You are the implementation critic. Focus on actionability.
            Is this output actually usable? Can someone act on it?
            Are there missing specifics (doses, timelines, tools, steps) that block
            real-world use? Are recommendations internally consistent?

            Respond with JSON:
            {
              "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
              "concerns": ["specific concern 1", "specific concern 2"],
              "most_critical_gap": "what would block someone from actually using this"
            }
        """).strip(),
    ),
]


@dataclass
class CouncilCritique:
    critic: str           # "devil_advocate" | "domain_skeptic" | "implementation_critic"
    verdict: str          # "WEAK" | "ACCEPTABLE" | "STRONG"
    concerns: List[str]
    most_critical_gap: str


@dataclass
class CouncilVerdict:
    critiques: List[CouncilCritique]
    weak_count: int       # how many critics rated WEAK
    escalate: bool        # True if majority (2+) weak


def run_llm_council(
    goal: str,
    step_outcomes: list,
    adapter=None,
) -> CouncilVerdict:
    """Run 3 critics with distinct framings; escalate if 2+ rate WEAK.

    Devil's advocate looks for gaps. Domain skeptic challenges methodology.
    Implementation critic tests actionability. Together they catch failure modes
    that the single adversarial pass misses (sycophancy defense).

    Falls back to empty verdict on any failure — never blocks the caller.
    """
    if adapter is None:
        return CouncilVerdict([], 0, False)

    done_steps = [s for s in step_outcomes if getattr(s, "status", "") == "done"]
    if not done_steps:
        return CouncilVerdict([], 0, False)

    review_steps = done_steps[-3:]
    output_summary = "\n\n".join(
        f"Step {getattr(s, 'index', i+1)}: {getattr(s, 'text', '?')[:80]}\n"
        f"Result: {(getattr(s, 'result', '') or '')[:500]}"
        for i, s in enumerate(review_steps)
    )
    user_msg = f"Goal: {goal[:300]}\n\nOutput to review:\n{output_summary}"

    critiques: List[CouncilCritique] = []

    try:
        from llm import LLMMessage
        import json

        for critic_name, critic_system in _COUNCIL_FRAMINGS:
            try:
                resp = adapter.complete(
                    [
                        LLMMessage("system", critic_system),
                        LLMMessage("user", user_msg),
                    ],
                    max_tokens=512,
                    temperature=0.4,
                )
                content = resp.content.strip()
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(content[start:end])
                    critiques.append(CouncilCritique(
                        critic=critic_name,
                        verdict=data.get("verdict", "ACCEPTABLE").upper(),
                        concerns=data.get("concerns", [])[:4],
                        most_critical_gap=data.get("most_critical_gap", ""),
                    ))
                    log.debug("council critic=%s verdict=%s", critic_name, critiques[-1].verdict)
            except Exception as exc:
                log.debug("council critic=%s failed (non-fatal): %s", critic_name, exc)

    except Exception as exc:
        log.debug("run_llm_council setup failed (non-fatal): %s", exc)

    weak_count = sum(1 for c in critiques if c.verdict == "WEAK")
    escalate = weak_count >= 2
    log.info("council critics=%d weak=%d escalate=%s", len(critiques), weak_count, escalate)

    return CouncilVerdict(critiques=critiques, weak_count=weak_count, escalate=escalate)

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
    council: Optional[CouncilVerdict] = None  # from LLM council (if run_council=True)


def run_quality_gate(
    goal: str,
    step_outcomes: list,
    adapter=None,
    *,
    confidence_threshold: float = 0.75,
    run_adversarial: bool = True,
    run_council: bool = False,
) -> QualityVerdict:
    """Review completed loop output and return a quality verdict.

    Runs up to three passes:
    1. PASS/ESCALATE verdict — should we re-run at a higher tier?
    2. Adversarial claim review — what specific claims are contested/overclaimed?
       Contested claims are returned in verdict.contested_claims regardless of
       PASS/ESCALATE, so callers can append them to the result text.
    3. LLM Council (optional, run_council=True) — 3 critics with distinct framings
       (devil's advocate, domain skeptic, implementation critic). Escalates if 2+
       critics rate WEAK. Catches sycophancy that single-pass adversarial misses.

    Uses the provided adapter (should be cheap tier — gate itself is cheap).
    Returns PASS with low confidence on any failure — gate errors must never
    block or degrade the result.

    Args:
        goal: The original goal text.
        step_outcomes: List of StepOutcome objects from the loop.
        adapter: LLM adapter to use for the review (cheap tier recommended).
        confidence_threshold: Minimum confidence to act on ESCALATE.
        run_adversarial: Whether to run the adversarial claim review pass.
        run_council: Whether to run the LLM council (3 additional critic calls).
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

    # --- Pass 3: LLM Council (optional) ---
    council_verdict: Optional[CouncilVerdict] = None
    if run_council:
        council_verdict = run_llm_council(goal, step_outcomes, adapter)
        if council_verdict.escalate and not escalate:
            escalate = True
            verdict = "ESCALATE"
            reason = (
                f"LLM Council: {council_verdict.weak_count}/3 critics rated WEAK — "
                + (council_verdict.critiques[0].most_critical_gap[:80] if council_verdict.critiques else "")
            )
            log.info("quality_gate council_escalated weak=%d", council_verdict.weak_count)

    return QualityVerdict(verdict, reason, confidence, escalate, contested_claims, council_verdict)


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
