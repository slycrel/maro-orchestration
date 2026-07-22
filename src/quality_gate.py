"""Post-loop quality gate — skeptic review of completed run output.

After a loop finishes, the quality gate reviews the output and decides whether
it meets the bar for the goal. If not, it can recommend or trigger a re-run at
a higher model tier.

The gate also runs an adversarial pass that produces specific contested claims —
these are appended to the result text even on PASS, so the output flags its own
weak spots rather than silently emitting potentially overclaimed findings.

The gate runs on the run's execution adapter (MID by default since the
2026-07-21 unification). Because that means the gate reviews output its own
model family produced, a hosted-free second-family check (Groq/Gemini) can
stack a decorrelated opinion on top — see run_quality_gate Pass 1.5.

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
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional
from llm_parse import extract_json, safe_float, safe_str, safe_list, content_or_empty
from claim_probe import probe_contested_claims, SETTLED_BY_COMMAND_CLAUSE, PROBE_TIMEOUT_SEC

log = logging.getLogger("maro.quality_gate")

# Back-compat aliases: the prober + timeout moved to claim_probe.py (shared with
# verification_agent so the adversarial prompts can't diverge again). Existing
# call sites and tests import these names from quality_gate.
_probe_contested_claims = probe_contested_claims
_PROBE_TIMEOUT_SEC = PROBE_TIMEOUT_SEC


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
                    no_tools=True,
                    purpose="council critique",
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

    {settled_clause}

    Produce a concise list of contested claims with verdict, reason, and probe.
    If everything checks out, respond with an empty list: []
    Format: JSON array of {{"claim": "...", "verdict": "...", "reason": "...",
                            "population_match": true|false,
                            "settled_by_command": "..." or null}}
    Set population_match=false when the cited study population doesn't match the goal population
    (e.g. study was in MCI patients but goal targets healthy adults; study used vegetarians
    but recommendation applies to omnivores). This is the most commonly missed downgrade.
""").strip().format(settled_clause=SETTLED_BY_COMMAND_CLAUSE)


@dataclass
class QualityVerdict:
    verdict: str        # "PASS" | "ESCALATE"
    reason: str
    confidence: float
    escalate: bool      # True if verdict == "ESCALATE" and confidence is high enough
    contested_claims: List[dict] = field(default_factory=list)  # from adversarial pass
    council: Optional[CouncilVerdict] = None  # from LLM council (if run_council=True)
    cross_ref: Optional[Any] = None  # from cross-reference check (if run_cross_ref=True)
    second_family: Optional[dict] = None  # hosted-free second-family check (Pass 1.5); flag-only


def run_quality_gate(
    goal: str,
    step_outcomes: list,
    adapter=None,
    *,
    confidence_threshold: float = 0.75,
    run_adversarial: bool = True,
    run_council: bool = False,
    run_cross_ref: bool = False,
    loop_id: Optional[str] = None,
    _ladder: bool = True,
) -> QualityVerdict:
    """Review completed loop output and return a quality verdict.

    Runs up to five passes:
    1. PASS/ESCALATE verdict — should we re-run at a higher tier?
    1.5. Hosted-free second-family check (`_ladder`, chunk 5a) — on a Pass-1
       PASS, one Groq/Gemini call re-judges the same payload. Stack, don't
       substitute: dissent is recorded in verdict.second_family and the
       captain's log, never acted on. Inert when hosted-free is
       unconfigured/not-opted-in.
    2. Adversarial claim review — what specific claims are contested/overclaimed?
    2.5. Cross-reference check (optional, run_cross_ref=True) — second-source fact check.
       Contested claims are returned in verdict.contested_claims regardless of
       PASS/ESCALATE, so callers can append them to the result text.
    3. LLM Council (optional, run_council=True) — 3 critics with distinct framings
       (devil's advocate, domain skeptic, implementation critic). Escalates if 2+
       critics rate WEAK. Catches sycophancy that single-pass adversarial misses.

    Uses the provided adapter — callers pass the run's execution adapter
    (MID by default since the 2026-07-21 unification).
    Returns PASS with low confidence on any failure — gate errors must never
    block or degrade the result.

    Args:
        goal: The original goal text.
        step_outcomes: List of StepOutcome objects from the loop.
        adapter: LLM adapter to use for the review (the run's execution adapter).
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

    # Free gate rung: the local-model Tier 0 was REMOVED 2026-07-21 by decree
    # ("local LLMs are in the way for now"). Its replacement is Pass 1.5 below
    # (chunk 5a): a hosted-free second-family check that STACKS on the paid
    # verdict instead of substituting for it. `_ladder` gates that pass.

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

        _t0 = time.monotonic()
        resp = adapter.complete(
            [
                LLMMessage("system", _GATE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=256,
            temperature=0.1,
            no_tools=True,
            purpose="quality gate verdict",
        )
        _gate_elapsed_ms = int((time.monotonic() - _t0) * 1000)

        data = extract_json(content_or_empty(resp), dict, log_tag="quality_gate.pass1")
        if data:
            verdict = safe_str(data.get("verdict", "PASS")).upper()
            reason = safe_str(data.get("reason"))
            confidence = safe_float(data.get("confidence"), default=0.5, min_val=0.0, max_val=1.0)
            escalate = verdict == "ESCALATE" and confidence >= confidence_threshold
            # Decision = what we're actually going to do, so the log line never reads
            # `verdict=ESCALATE escalate=False` (a 2026-04-26 audit finding: the LLM
            # said ESCALATE but confidence under threshold meant we wouldn't act, yet
            # the printed verdict still claimed ESCALATE). Recommendation stays in
            # `verdict`; the *action* is `decision` and matches `escalate`.
            if escalate:
                decision = "ESCALATE"
            elif verdict == "ESCALATE":
                decision = "WEAK_ESCALATE"  # LLM wanted ESCALATE but confidence too low
            else:
                decision = "PASS"
            log.info("quality_gate decision=%s verdict=%s confidence=%.2f threshold=%.2f reason=%r",
                     decision, verdict, confidence, confidence_threshold, reason[:80])
            try:
                from captains_log import log_event, QUALITY_GATE_VERDICT
                log_event(
                    QUALITY_GATE_VERDICT,
                    subject=goal[:120],
                    summary=(
                        f"decision={decision} verdict={verdict} "
                        f"confidence={confidence:.2f} threshold={confidence_threshold:.2f}"
                    ),
                    context={
                        "decision": decision,
                        "verdict": verdict,
                        "confidence": confidence,
                        "confidence_threshold": confidence_threshold,
                        "escalate": escalate,
                        # Which adapter produced this verdict — on a local→paid
                        # escalation the gate logs one row per tier.
                        "source": getattr(adapter, "model_key", "") or "unknown",
                        "reason": reason[:400],
                        "step_count": len(done_steps),
                        # verdict-call latency (BACKLOG #9 ROI measurement)
                        "elapsed_ms": _gate_elapsed_ms,
                        "input_chars": len(user_msg),
                    },
                    loop_id=loop_id,
                )
            except Exception as _ev_exc:
                log.debug("captains_log quality_gate emit failed: %s", _ev_exc)

    except Exception as exc:
        log.debug("quality_gate pass1 failed (non-fatal): %s", exc)
        return QualityVerdict("PASS", "gate parse error — defaulting to pass", 0.0, False)

    # --- Pass 1.5: hosted-free second-family check (chunk 5a) ---
    # The paid gate reviews output its own model family produced —
    # family-correlated sycophancy is the failure mode the removed local rung
    # targeted by SUBSTITUTING a free verdict for the paid one. This stacks
    # instead: on a paid PASS, one second-family call (Groq llama / Gemini
    # flash-lite via hosted_free) judges the SAME payload, and dissent is
    # recorded as a flag for the agreement readout, never an action (the
    # WEAK_ESCALATE stance above: recommendation ≠ action). Authority for the
    # second family comes from A/B agreement data, or not at all.
    # Expectation on record: modest lift — all 4 measured gate false-passes
    # were narration-vs-evidence, which the deterministic provenance probes
    # (claim_probe, Pass 2) already catch.
    second_family: Optional[dict] = None
    if _ladder and data and verdict == "PASS":
        try:
            from config import get as _cfg_get
            import hosted_free as _hf
            if _cfg_get("quality_gate.second_family_check", True) and _hf.available():
                _hosted = _hf.build_hosted_free_adapter()
                if _hosted is not None:
                    _t0 = time.monotonic()
                    _sf_resp = _hosted.complete(
                        [
                            LLMMessage("system", _GATE_SYSTEM),
                            LLMMessage("user", user_msg),
                        ],
                        max_tokens=256,
                        temperature=0.1,
                        no_tools=True,
                        purpose="quality gate second-family check",
                    )
                    _sf_elapsed_ms = int((time.monotonic() - _t0) * 1000)
                    _sf_source = (
                        f"hosted_free:{getattr(_hosted, '_active_provider', '') or '?'}"
                        f":{getattr(_hosted, 'model_key', '')}"
                    )
                    _sf_data = extract_json(content_or_empty(_sf_resp), dict,
                                            log_tag="quality_gate.second_family")
                    _sf_verdict = safe_str((_sf_data or {}).get("verdict", "")).upper()
                    _sf_conf = safe_float((_sf_data or {}).get("confidence"),
                                          default=0.0, min_val=0.0, max_val=1.0)
                    _sf_reason = safe_str((_sf_data or {}).get("reason"))
                    if _sf_verdict == "PASS":
                        _sf_decision = "SECOND_FAMILY_AGREE"
                    elif _sf_verdict == "ESCALATE" and _sf_conf >= _hf.min_certainty():
                        _sf_decision = "SECOND_FAMILY_DISSENT"
                    elif _sf_verdict == "ESCALATE":
                        # Wanted ESCALATE without conviction — UNDECIDED, not
                        # dissent (same min_certainty semantics as the
                        # validator ladder: a weak judge cannot flag).
                        _sf_decision = "SECOND_FAMILY_UNDECIDED"
                    else:
                        # Response received but no usable verdict — recorded
                        # so the agreement readout sees the true denominator.
                        _sf_decision = "SECOND_FAMILY_NO_VERDICT"
                    second_family = {
                        "decision": _sf_decision,
                        "verdict": _sf_verdict,
                        "confidence": _sf_conf,
                        "reason": _sf_reason[:400],
                        "source": _sf_source,
                        "elapsed_ms": _sf_elapsed_ms,
                    }
                    log.info(
                        "quality_gate second_family decision=%s verdict=%s conf=%.2f via %s",
                        _sf_decision, _sf_verdict or "?", _sf_conf, _sf_source)
                    try:
                        from captains_log import log_event, QUALITY_GATE_SECOND_FAMILY
                        log_event(
                            QUALITY_GATE_SECOND_FAMILY,
                            subject=goal[:120],
                            summary=(
                                f"decision={_sf_decision} paid=PASS "
                                f"second={_sf_verdict or '?'} conf={_sf_conf:.2f}"
                            ),
                            context={
                                "decision": _sf_decision,
                                "verdict": _sf_verdict,
                                "confidence": _sf_conf,
                                "reason": _sf_reason[:400],
                                "source": _sf_source,
                                "paid_verdict": verdict,
                                "paid_confidence": confidence,
                                "paid_source": getattr(adapter, "model_key", "") or "unknown",
                                "elapsed_ms": _sf_elapsed_ms,
                                "input_chars": len(user_msg),
                            },
                            loop_id=loop_id,
                        )
                    except Exception as _sf_ev_exc:
                        log.debug("captains_log second_family emit failed: %s", _sf_ev_exc)
        except Exception as exc:
            # Transport failure / every provider tripped: the tier failed
            # rather than judged. hosted_free's own breakers already recorded
            # the failure; the gate result is untouched.
            log.debug("quality_gate second-family check skipped (non-fatal): %s", exc)

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
                no_tools=True,
                purpose="adversarial claim review",
            )

            parsed = extract_json(content_or_empty(adv_resp), list, log_tag="quality_gate.adversarial")
            if parsed:
                contested_claims = safe_list(
                    [c for c in parsed if isinstance(c, dict) and c.get("claim")],
                    element_type=dict,
                )
                log.info("quality_gate adversarial found %d contested claims",
                         len(contested_claims))

                # Inversion-at-verification: for each contested claim that
                # named a concrete probe, run the probe and reclassify based
                # on what actually happens on disk. Catches reviewer
                # hallucinations (e.g. 2026-04-17 slycrel-go: "Go not
                # installed" — one `command -v go` settles it in <50ms).
                if contested_claims:
                    contested_claims = _probe_contested_claims(contested_claims)

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

    return QualityVerdict(verdict, reason, confidence, escalate, contested_claims,
                          council_verdict, cross_ref_result, second_family)


# ---------------------------------------------------------------------------
# Probe contested claims — moved to claim_probe.py and shared with
# verification_agent (imported + aliased as _probe_contested_claims at the top
# of this module for back-compat). See claim_probe.probe_contested_claims.
# ---------------------------------------------------------------------------



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
