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
from typing import Any, List, Optional
from llm_parse import extract_json, safe_float, safe_str, safe_list, content_or_empty

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
                data = extract_json(content_or_empty(resp), dict, log_tag="quality_gate.council")
                if data:
                    critiques.append(CouncilCritique(
                        critic=critic_name,
                        verdict=safe_str(data.get("verdict", "ACCEPTABLE")).upper(),
                        concerns=safe_list(data.get("concerns", []), element_type=str, max_items=4),
                        most_critical_gap=safe_str(data.get("most_critical_gap")),
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
    Format: JSON array of {"claim": "...", "verdict": "...", "reason": "...", "population_match": true|false}
    Set population_match=false when the cited study population doesn't match the goal population
    (e.g. study was in MCI patients but goal targets healthy adults; study used vegetarians
    but recommendation applies to omnivores). This is the most commonly missed downgrade.
""").strip()


@dataclass
class QualityVerdict:
    verdict: str        # "PASS" | "ESCALATE"
    reason: str
    confidence: float
    escalate: bool      # True if verdict == "ESCALATE" and confidence is high enough
    contested_claims: List[dict] = field(default_factory=list)  # from adversarial pass
    council: Optional[CouncilVerdict] = None  # from LLM council (if run_council=True)
    debate: Optional["DebateVerdict"] = None  # from bull/bear debate (if run_debate=True)
    cross_ref: Optional[Any] = None  # from cross-reference check (if run_cross_ref=True)


def run_quality_gate(
    goal: str,
    step_outcomes: list,
    adapter=None,
    *,
    confidence_threshold: float = 0.75,
    run_adversarial: bool = True,
    run_council: bool = False,
    with_debate: bool = False,
    run_cross_ref: bool = False,
) -> QualityVerdict:
    """Review completed loop output and return a quality verdict.

    Runs up to five passes:
    1. PASS/ESCALATE verdict — should we re-run at a higher tier?
    2. Adversarial claim review — what specific claims are contested/overclaimed?
    2.5. Cross-reference check (optional, run_cross_ref=True) — second-source fact check.
       Contested claims are returned in verdict.contested_claims regardless of
       PASS/ESCALATE, so callers can append them to the result text.
    3. LLM Council (optional, run_council=True) — 3 critics with distinct framings
       (devil's advocate, domain skeptic, implementation critic). Escalates if 2+
       critics rate WEAK. Catches sycophancy that single-pass adversarial misses.
    4. Multi-agent debate (optional, with_debate=True) — bull/bear debaters argue
       for/against the output; risk manager gives PROCEED/CAUTION/REJECT verdict.

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
        # Inject inspector friction summary if available — friction signals (stuck steps,
        # escalation tone, backtracking) should bias the gate toward ESCALATE.
        _friction_note = ""
        try:
            from inspector import get_friction_summary as _get_friction_summary
            _fs = _get_friction_summary()
            if _fs:
                _friction_note = f"\nInspector friction signals (from recent runs): {_fs[:300]}\n"
        except Exception:
            pass  # friction context is optional — never block the gate

        user_msg = (
            f"Goal: {goal[:300]}\n\n"
            f"Output from final steps:\n{output_summary}\n"
            f"{_friction_note}\n"
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

        data = extract_json(content_or_empty(resp), dict, log_tag="quality_gate.pass1")
        if data:
            verdict = safe_str(data.get("verdict", "PASS")).upper()
            reason = safe_str(data.get("reason"))
            confidence = safe_float(data.get("confidence"), default=0.5, min_val=0.0, max_val=1.0)
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

            parsed = extract_json(content_or_empty(adv_resp), list, log_tag="quality_gate.adversarial")
            if parsed:
                contested_claims = safe_list(
                    [c for c in parsed if isinstance(c, dict) and c.get("claim")],
                    element_type=dict,
                )
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

    # --- Pass 4: Multi-agent debate (optional) ---
    debate_verdict: Optional[DebateVerdict] = None
    if with_debate:
        debate_verdict = run_debate(goal, step_outcomes, adapter=adapter)
        if debate_verdict.escalate and not escalate:
            escalate = True
            verdict = "ESCALATE"
            reason = (
                f"Debate: Risk Manager={debate_verdict.risk_manager_verdict} "
                f"(dominant={debate_verdict.dominant_position}) — "
                f"{debate_verdict.risk_manager_reasoning[:80]}"
            )
            log.info("quality_gate debate_escalated verdict=%s", debate_verdict.risk_manager_verdict)

    # --- Pass 2.5: Cross-reference check (optional) ---
    cross_ref_result = None
    if run_cross_ref:
        try:
            from cross_ref import run_cross_ref as _run_cross_ref
            # Build cross-ref text from step outputs
            _cr_text = "\n\n".join(
                (getattr(s, "result", "") or s.get("result", "") if isinstance(s, dict) else getattr(s, "result", ""))
                for s in done_steps[-3:]
            )
            cross_ref_result = _run_cross_ref(_cr_text, adapter=adapter)
            if cross_ref_result.has_disputes and not escalate:
                escalate = True
                verdict = "ESCALATE"
                reason = (
                    f"Cross-ref: {len(cross_ref_result.disputes)} disputed claim(s) — "
                    + cross_ref_result.disputes[0].claim[:60]
                )
                log.info("quality_gate cross_ref_escalated disputes=%d", len(cross_ref_result.disputes))
        except Exception as exc:
            log.warning("quality_gate cross_ref pass failed: %s", exc)

    return QualityVerdict(verdict, reason, confidence, escalate, contested_claims, council_verdict, debate_verdict, cross_ref_result)


# ---------------------------------------------------------------------------
# Multi-agent debate: Bull / Bear / Risk Manager
# ---------------------------------------------------------------------------

_BULL_SYSTEM = textwrap.dedent("""\
    You are the BULL analyst. Your job: argue the strongest possible case FOR the output
    being correct, useful, and sufficient. Commit to a position — don't hedge.

    Make the most compelling argument you can for why:
    - The key claims are well-supported
    - The methodology is sound for the stated goal
    - The output is actionable and ready to use

    Respond with JSON:
    {
      "position": "brief summary of your bull thesis",
      "key_points": ["point 1", "point 2", "point 3"],
      "confidence": 0.0–1.0,
      "strongest_evidence": "the single most compelling piece of evidence for this output"
    }
""").strip()

_BEAR_SYSTEM = textwrap.dedent("""\
    You are the BEAR analyst. Your job: argue the strongest possible case AGAINST the output
    — that it is flawed, incomplete, or dangerous to act on. Commit to a position — don't hedge.

    Make the most compelling argument you can for why:
    - Key claims are unsupported or wrong
    - The methodology has critical gaps
    - Acting on this output would be a mistake

    Respond with JSON:
    {
      "position": "brief summary of your bear thesis",
      "key_points": ["point 1", "point 2", "point 3"],
      "confidence": 0.0–1.0,
      "fatal_flaw": "the single most damaging problem with this output"
    }
""").strip()

_RISK_MANAGER_SYSTEM = textwrap.dedent("""\
    You are the RISK MANAGER. A bull analyst and a bear analyst have debated an output.
    Your job: read both positions and give a final verdict.

    You are NOT looking for who argued better rhetorically. You are deciding:
    - Can the user safely act on this output? (PROCEED)
    - Should the user proceed cautiously with caveats? (CAUTION)
    - Is the output too flawed or incomplete to act on? (REJECT)

    PROCEED: bull's case is substantially stronger; output is actionable.
    CAUTION: bear raises valid concerns but the output is still useful with caveats.
    REJECT: bear identifies fatal flaws that make the output unreliable.

    Respond with JSON:
    {
      "verdict": "PROCEED" | "CAUTION" | "REJECT",
      "reasoning": "one sentence — why this verdict",
      "dominant_position": "bull" | "bear" | "neutral",
      "key_risk": "the most important caveat the user should know"
    }
""").strip()


@dataclass
class DebatePosition:
    role: str               # "bull" | "bear"
    position: str           # the argued thesis
    key_points: List[str]
    confidence: float
    highlight: str          # strongest_evidence (bull) or fatal_flaw (bear)


@dataclass
class DebateVerdict:
    bull: Optional[DebatePosition]
    bear: Optional[DebatePosition]
    risk_manager_verdict: str       # "PROCEED" | "CAUTION" | "REJECT"
    risk_manager_reasoning: str
    dominant_position: str          # "bull" | "bear" | "neutral"
    key_risk: str
    escalate: bool                  # True if REJECT or CAUTION


def run_debate(
    goal: str,
    step_outcomes: list,
    *,
    adapter=None,
) -> DebateVerdict:
    """Run the bull/bear debate with risk manager verdict.

    Three-stage structured debate:
    1. Bull argues FOR the output being correct and actionable
    2. Bear argues AGAINST — fatal flaws, unsupported claims, gaps
    3. Risk Manager reads both and gives PROCEED / CAUTION / REJECT

    Returns a DebateVerdict. Escalates if risk manager says REJECT or CAUTION.
    Never raises — returns a neutral default on any failure.
    """
    _null = DebateVerdict(
        bull=None, bear=None,
        risk_manager_verdict="PROCEED", risk_manager_reasoning="(debate skipped)",
        dominant_position="neutral", key_risk="", escalate=False,
    )

    if adapter is None:
        return _null

    done_steps = [s for s in step_outcomes if getattr(s, "status", "") == "done"]
    if not done_steps:
        return _null

    review_steps = done_steps[-4:]
    output_summary = "\n\n".join(
        f"Step {getattr(s, 'index', i+1)}: {getattr(s, 'text', '?')[:80]}\n"
        f"Result: {(getattr(s, 'result', '') or '')[:600]}"
        for i, s in enumerate(review_steps)
    )
    context_msg = f"Goal: {goal[:300]}\n\nOutput to debate:\n{output_summary}"

    bull_pos: Optional[DebatePosition] = None
    bear_pos: Optional[DebatePosition] = None

    try:
        import json
        from llm import LLMMessage

        # Stage 1: Bull
        try:
            bull_resp = adapter.complete(
                [LLMMessage("system", _BULL_SYSTEM), LLMMessage("user", context_msg)],
                max_tokens=512, temperature=0.5,
            )
            d = json.loads(_extract_json(bull_resp.content, "{", "}"))
            bull_pos = DebatePosition(
                role="bull",
                position=d.get("position", ""),
                key_points=d.get("key_points", [])[:4],
                confidence=float(d.get("confidence", 0.5)),
                highlight=d.get("strongest_evidence", ""),
            )
            log.debug("debate bull confidence=%.2f", bull_pos.confidence)
        except Exception as exc:
            log.debug("debate bull failed (non-fatal): %s", exc)

        # Stage 2: Bear
        try:
            bear_resp = adapter.complete(
                [LLMMessage("system", _BEAR_SYSTEM), LLMMessage("user", context_msg)],
                max_tokens=512, temperature=0.5,
            )
            d = json.loads(_extract_json(bear_resp.content, "{", "}"))
            bear_pos = DebatePosition(
                role="bear",
                position=d.get("position", ""),
                key_points=d.get("key_points", [])[:4],
                confidence=float(d.get("confidence", 0.5)),
                highlight=d.get("fatal_flaw", ""),
            )
            log.debug("debate bear confidence=%.2f", bear_pos.confidence)
        except Exception as exc:
            log.debug("debate bear failed (non-fatal): %s", exc)

        # Stage 3: Risk Manager — reads both positions
        debate_summary = f"Goal: {goal[:200]}\n\n"
        if bull_pos:
            debate_summary += (
                f"BULL POSITION:\n{bull_pos.position}\n"
                f"Key points: {'; '.join(bull_pos.key_points)}\n"
                f"Strongest evidence: {bull_pos.highlight}\n\n"
            )
        if bear_pos:
            debate_summary += (
                f"BEAR POSITION:\n{bear_pos.position}\n"
                f"Key points: {'; '.join(bear_pos.key_points)}\n"
                f"Fatal flaw: {bear_pos.highlight}\n\n"
            )

        rm_verdict = "PROCEED"
        rm_reasoning = "(risk manager unavailable)"
        dominant = "neutral"
        key_risk = ""

        try:
            rm_resp = adapter.complete(
                [LLMMessage("system", _RISK_MANAGER_SYSTEM), LLMMessage("user", debate_summary)],
                max_tokens=256, temperature=0.2,
            )
            d = json.loads(_extract_json(rm_resp.content, "{", "}"))
            rm_verdict = d.get("verdict", "PROCEED").upper()
            rm_reasoning = d.get("reasoning", "")
            dominant = d.get("dominant_position", "neutral")
            key_risk = d.get("key_risk", "")
            log.info("debate risk_manager verdict=%s dominant=%s", rm_verdict, dominant)
        except Exception as exc:
            log.debug("debate risk_manager failed (non-fatal): %s", exc)

        escalate = rm_verdict in {"REJECT", "CAUTION"}
        return DebateVerdict(
            bull=bull_pos,
            bear=bear_pos,
            risk_manager_verdict=rm_verdict,
            risk_manager_reasoning=rm_reasoning,
            dominant_position=dominant,
            key_risk=key_risk,
            escalate=escalate,
        )

    except Exception as exc:
        log.debug("run_debate setup failed (non-fatal): %s", exc)
        return _null


def _extract_json(text: str, open_char: str, close_char: str) -> str:
    """Extract the first JSON object/array from text."""
    start = text.find(open_char)
    end = text.rfind(close_char if open_char == "{" else "]") + 1
    if start >= 0 and end > start:
        return text[start:end]
    return text


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
