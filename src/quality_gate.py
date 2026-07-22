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
from finding_codes import FINDING_CODES, parse_finding_codes, parse_unknown_codes

log = logging.getLogger("maro.quality_gate")

# Back-compat aliases: the prober + timeout moved to claim_probe.py (shared with
# verification_agent so the adversarial prompts can't diverge again). Existing
# call sites and tests import these names from quality_gate.
_probe_contested_claims = probe_contested_claims
_PROBE_TIMEOUT_SEC = PROBE_TIMEOUT_SEC


def _hosted_free_adapter_or_none():
    """Hosted-free adapter when the tier is configured + opted-in, else None.

    Consent boundary: validate.hosted_free.enabled owns data egress; callers
    that get None fall back to their paid adapter (or skip) unchanged.
    """
    try:
        import hosted_free as _hf
        if _hf.available():
            return _hf.build_hosted_free_adapter()
    except Exception as exc:
        log.debug("hosted-free adapter unavailable: %s", exc)
    return None


# ---------------------------------------------------------------------------
# LLM Council — evidence-path lenses (chunk 5b)
#
# The 2026-04-era council ran three prompt costumes (devil's advocate /
# domain skeptic / implementation critic) over the SAME evidence — the last
# 3 step results. Same context in, correlated verdicts out. The chunk-5b
# repoint makes the seats differ in EVIDENCE PATH instead:
#   transcript_aware — sees the step-by-step run transcript (process vs claims)
#   artifact_only    — sees ONLY the final deliverable (the context-blind seat)
#   probe_armed      — must name settled_by_command probes; probes actually run
# The old costume framings live on in lens_ablation.py as the control arm of
# the era-04 triad ablation (do N seats diverge, or agree at Nx cost?).
#
# Seats stamp findings with FINDING[CODE] from the shared finding_codes
# vocabulary — the taxonomy is vocabulary WITHIN lenses, not extra seats.
# ---------------------------------------------------------------------------

_LENS_CODE_VOCAB = "\n".join(
    f"- FINDING[{code}]: {defn}" for code, (defn, _hint) in sorted(FINDING_CODES.items())
)

_LENS_JSON_CONTRACT = textwrap.dedent("""\
    Respond with JSON:
    {
      "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
      "concerns": ["specific concern 1", "specific concern 2"],
      "most_critical_gap": "the single biggest problem"
    }
""").strip()

_LENS_CODE_INSTRUCTION = (
    "When a concern fits one of these typed finding codes, start it with the "
    "stamp (e.g. \"FINDING[PHANTOM_SYMBOL] cites a file that doesn't exist\"). "
    "A concern that fits no code goes unstamped — never force a classification.\n"
    + _LENS_CODE_VOCAB
)


def _lens_evidence_transcript(goal: str, done_steps: list) -> str:
    """Transcript-aware seat: the step-by-step trail, not just final output."""
    steps = done_steps[-8:]
    lines = [
        f"Step {getattr(s, 'index', i + 1)}: {getattr(s, 'text', '?')[:120]}\n"
        f"Result: {(getattr(s, 'result', '') or '')[:400]}"
        for i, s in enumerate(steps)
    ]
    return (
        f"Goal: {goal[:300]}\n\n"
        f"Run transcript ({len(done_steps)} completed steps, showing last {len(steps)}):\n\n"
        + "\n\n".join(lines)
    )


def _lens_evidence_artifact(goal: str, done_steps: list) -> str:
    """Artifact-only seat: final deliverable, deliberately no process context."""
    final = done_steps[-1]
    return (
        f"Goal: {goal[:300]}\n\n"
        f"Final deliverable (you see only this):\n"
        f"{(getattr(final, 'result', '') or '')[:2400]}"
    )


def _lens_evidence_probe(goal: str, done_steps: list) -> str:
    """Probe-armed seat: same last-3 summary the gate itself reviews."""
    steps = done_steps[-3:]
    lines = [
        f"Step {getattr(s, 'index', i + 1)}: {getattr(s, 'text', '?')[:80]}\n"
        f"Result: {(getattr(s, 'result', '') or '')[:500]}"
        for i, s in enumerate(steps)
    ]
    return f"Goal: {goal[:300]}\n\nOutput to review:\n" + "\n\n".join(lines)


_EVIDENCE_LENSES = [
    (
        "transcript_aware",
        textwrap.dedent("""\
            You are the transcript auditor. You see the run's step-by-step
            transcript. Judge whether the PROCESS supports the final claims:
            did the steps actually produce the evidence the output relies on?
            Look for steps whose results don't support the conclusions drawn
            from them, failures narrated as successes, and evidence-free leaps
            between steps. The polish of the final prose is not your concern —
            the trail is.

            {code_instruction}

            {json_contract}
        """).strip(),
        _lens_evidence_transcript,
    ),
    (
        "artifact_only",
        textwrap.dedent("""\
            You are a context-blind reviewer. You see ONLY the goal and the
            final deliverable — deliberately no transcript, no process story,
            no narration of effort. Judge the artifact as a stranger would:
            does it stand on its own, answer the goal, and carry its evidence
            inside itself? Anything the deliverable claims but does not show
            is a gap.

            {code_instruction}

            {json_contract}
        """).strip(),
        _lens_evidence_artifact,
    ),
    (
        "probe_armed",
        textwrap.dedent("""\
            You are the probe-armed reviewer. Challenge the output's checkable
            claims about files, commands, tools, and system state. For EVERY
            concern that asserts something a shell command could settle, you
            MUST supply `settled_by_command`: a single-line, safe, read-only
            command that decisively settles whether your concern is correct
            (exit 0 = your concern was wrong). Set it to null only for
            genuinely un-probe-able concerns. Your probes will actually run —
            a concern your own probe dismisses is dropped.

            {code_instruction}

            Respond with JSON:
            {{
              "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
              "concerns": [
                {{"claim": "specific concern", "settled_by_command": "cmd" or null}}
              ],
              "most_critical_gap": "the single biggest problem"
            }}
        """).strip(),
        _lens_evidence_probe,
    ),
]

# Render the shared code-vocabulary + JSON contract into each seat's system
# text once, at import time (probe_armed carries its own contract with {{ }}
# escapes, so the unused json_contract kwarg is harmless there).
_EVIDENCE_LENSES = [
    (name, system.format(code_instruction=_LENS_CODE_INSTRUCTION,
                         json_contract=_LENS_JSON_CONTRACT), builder)
    for name, system, builder in _EVIDENCE_LENSES
]


@dataclass
class CouncilCritique:
    critic: str           # "transcript_aware" | "artifact_only" | "probe_armed"
    verdict: str          # "WEAK" | "ACCEPTABLE" | "STRONG"
    concerns: List[str]
    most_critical_gap: str
    source: str = ""      # model attribution for this seat
    finding_codes: List[str] = field(default_factory=list)  # typed codes stamped in concerns
    probe_dismissed: int = 0  # probe_armed only: concerns the seat's own probe refuted


@dataclass
class CouncilVerdict:
    critiques: List[CouncilCritique]
    weak_count: int       # how many critics rated WEAK (acting round)
    escalate: bool        # True if majority (2+) weak on the ACTING round
    source: str = ""      # attribution of the acting round ("" = never ran)


def _parse_lens_concerns(critic_name: str, data: dict) -> tuple:
    """Normalize a seat's concerns; run probes for the probe-armed seat.

    Returns (concern_strings, finding_codes, probe_dismissed, verdict_override).
    verdict_override is "ACCEPTABLE" when a WEAK verdict rested entirely on
    concerns the seat's own probes dismissed, else "".
    """
    raw = data.get("concerns", [])
    if not isinstance(raw, list):
        raw = []
    probe_dismissed = 0
    concerns: List[str] = []

    if critic_name == "probe_armed":
        claim_dicts = [
            {"claim": safe_str(c.get("claim")),
             "settled_by_command": c.get("settled_by_command")}
            for c in raw if isinstance(c, dict) and c.get("claim")
        ]
        # Tolerate seats that answered with plain strings despite the contract.
        plain = [safe_str(c) for c in raw if isinstance(c, str) and c.strip()]
        if claim_dicts:
            probed = _probe_contested_claims(claim_dicts)
            for c in probed:
                status = c.get("probe_status", "unprobed")
                if status == "dismissed":
                    probe_dismissed += 1
                    continue  # the seat's own probe refuted it — dropped
                concerns.append(f"{c['claim']} [probe:{status}]")
        concerns.extend(plain)
    else:
        concerns = safe_list(raw, element_type=str, max_items=4)

    all_text = "\n".join(concerns)
    codes = parse_finding_codes(all_text, strict=False)
    unknown = parse_unknown_codes(all_text)
    if unknown:
        log.info("council seat=%s stamped unknown finding codes: %s",
                 critic_name, sorted(set(unknown)))

    verdict_override = ""
    if (critic_name == "probe_armed" and probe_dismissed > 0
            and not concerns
            and safe_str(data.get("verdict")).upper() == "WEAK"):
        # Every concern behind the WEAK verdict was mechanically refuted.
        verdict_override = "ACCEPTABLE"
    return concerns[:6], codes, probe_dismissed, verdict_override


def _run_council_round(goal: str, done_steps: list, adapter) -> List[CouncilCritique]:
    """One council round: each evidence-path seat dispatched once."""
    from persona_dispatch import dispatch_prompt

    critiques: List[CouncilCritique] = []
    for lens_name, lens_system, evidence_builder in _EVIDENCE_LENSES:
        try:
            evidence = evidence_builder(goal, done_steps)
            result = dispatch_prompt(
                evidence,
                system=lens_system,
                adapter=adapter,
                expect="json",
                max_tokens=700,
                temperature=0.4,
                purpose=f"council lens {lens_name}",
            )
            data = result.data
            if not data:
                log.debug("council seat=%s no verdict (%s)", lens_name,
                          result.error or "unparsable")
                continue
            concerns, codes, dismissed, override = _parse_lens_concerns(lens_name, data)
            verdict = override or safe_str(data.get("verdict", "ACCEPTABLE")).upper()
            critiques.append(CouncilCritique(
                critic=lens_name,
                verdict=verdict,
                concerns=concerns,
                most_critical_gap=safe_str(data.get("most_critical_gap")),
                source=result.source,
                finding_codes=codes,
                probe_dismissed=dismissed,
            ))
            log.debug("council seat=%s verdict=%s codes=%s", lens_name, verdict, codes)
        except Exception as exc:
            log.debug("council seat=%s failed (non-fatal): %s", lens_name, exc)
    return critiques


def run_llm_council(
    goal: str,
    step_outcomes: list,
    adapter=None,
    *,
    loop_id: Optional[str] = None,
) -> CouncilVerdict:
    """Run the 3 evidence-path lenses; escalate if 2+ rate WEAK.

    Seats differ in what evidence they see (transcript / artifact-only /
    probe-armed), not in prompt costume — decorrelation by evidence path
    (chunk 5b). Each seat dispatches through persona_dispatch on the
    hosted-free tier when available ($0, second model family), else on the
    passed adapter.

    Escalation authority follows the validator-ladder rule — a weaker
    family never overrules a stronger one: when the free round flags
    (2+ WEAK) and a paid adapter is available, the seats re-run on the paid
    adapter and THAT round's vote acts. A free flag with no paid adapter to
    confirm is recorded but never acts.

    Falls back to empty verdict on any failure — never blocks the caller.
    """
    done_steps = [s for s in step_outcomes if getattr(s, "status", "") == "done"]
    if not done_steps:
        return CouncilVerdict([], 0, False)

    free_adapter = _hosted_free_adapter_or_none()
    round1_adapter = free_adapter if free_adapter is not None else adapter
    if round1_adapter is None:
        return CouncilVerdict([], 0, False)

    _t0 = time.monotonic()
    critiques = _run_council_round(goal, done_steps, round1_adapter)
    ran_free_first = free_adapter is not None
    if not critiques and ran_free_first and adapter is not None:
        # Every free seat failed/unparsable — a degraded free tier must not
        # silently neuter an opted-in (strict:) council; fall back to paid.
        free_adapter = None
        critiques = _run_council_round(goal, done_steps, adapter)
    weak_count = sum(1 for c in critiques if c.verdict == "WEAK")
    round1_source = critiques[0].source if critiques else ""
    ran_free = free_adapter is not None

    acting = critiques
    acting_weak = weak_count
    escalate = False
    confirmation_ran = False
    free_flag_unconfirmed = False

    if weak_count >= 2:
        if not ran_free:
            escalate = True  # round already ran on the run's own (paid) adapter
        elif adapter is not None:
            # Weaker family flagged — the paid family re-judges and acts.
            confirmation_ran = True
            confirmed = _run_council_round(goal, done_steps, adapter)
            confirmed_weak = sum(1 for c in confirmed if c.verdict == "WEAK")
            if confirmed:
                acting = confirmed
                acting_weak = confirmed_weak
            escalate = confirmed_weak >= 2
        else:
            # Free seats flagged but no paid adapter exists to confirm —
            # flag-only (weaker-never-acts), recorded for the readout.
            free_flag_unconfirmed = True

    elapsed_ms = int((time.monotonic() - _t0) * 1000)
    acting_source = acting[0].source if acting else round1_source
    log.info("council seats=%d weak=%d escalate=%s source=%s confirmed=%s",
             len(acting), acting_weak, escalate, acting_source or "?", confirmation_ran)

    if critiques:
        try:
            from captains_log import log_event, QUALITY_GATE_COUNCIL
            log_event(
                QUALITY_GATE_COUNCIL,
                subject=goal[:120],
                summary=(
                    f"weak={acting_weak}/{len(acting)} escalate={escalate}"
                    + (" confirmed_by_paid" if confirmation_ran else "")
                    + (" free_flag_unconfirmed" if free_flag_unconfirmed else "")
                ),
                context={
                    "seats": [
                        {"lens": c.critic, "verdict": c.verdict, "source": c.source,
                         "finding_codes": c.finding_codes,
                         "probe_dismissed": c.probe_dismissed,
                         "most_critical_gap": c.most_critical_gap[:200]}
                        for c in acting
                    ],
                    "weak_count": acting_weak,
                    "escalate": escalate,
                    "free_round_weak": weak_count if confirmation_ran else None,
                    "confirmation_ran": confirmation_ran,
                    "free_flag_unconfirmed": free_flag_unconfirmed,
                    "source": acting_source,
                    "elapsed_ms": elapsed_ms,
                    "step_count": len(done_steps),
                },
                loop_id=loop_id,
            )
        except Exception as _ev_exc:
            log.debug("captains_log council emit failed: %s", _ev_exc)

    return CouncilVerdict(critiques=acting, weak_count=acting_weak,
                          escalate=escalate, source=acting_source)

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
    2.5. Cross-reference check (optional) — second-source fact check.
       run_cross_ref=True (strict: lane) runs on the gate adapter and disputes
       may flip the verdict. run_cross_ref="hosted_free" (research-shaped
       goals, chunk 5b) runs on the hosted-free tier flag-only: disputes are
       recorded (QUALITY_GATE_CROSS_REF event) but never act — the weaker
       family doesn't overrule the paid verdict. Killswitch:
       quality_gate.cross_ref_research (hosted lane only).
       Contested claims are returned in verdict.contested_claims regardless of
       PASS/ESCALATE, so callers can append them to the result text.
    3. LLM Council (optional, run_council=True) — 3 evidence-path lenses
       (transcript-aware / artifact-only / probe-armed) dispatched via
       persona_dispatch, hosted-free first with paid confirmation before any
       escalation acts (chunk 5b). Escalates if 2+ seats rate WEAK on the
       acting round. Catches sycophancy that single-pass adversarial misses.

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
            # config.get returns raw YAML nodes — a quoted "false" is a
            # truthy string, so normalize the same way hosted_free_enabled()
            # does (chunk-5a review F1) or the killswitch can't kill.
            _sf_enabled = _cfg_get("quality_gate.second_family_check", True)
            if isinstance(_sf_enabled, str):
                _sf_enabled = _sf_enabled.strip().lower() not in ("false", "0", "no", "off")
            if _sf_enabled and _hf.available():
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
        council_verdict = run_llm_council(goal, step_outcomes, adapter, loop_id=loop_id)
        if council_verdict.escalate and not escalate:
            escalate = True
            verdict = "ESCALATE"
            reason = (
                f"LLM Council: {council_verdict.weak_count}/3 critics rated WEAK — "
                + (council_verdict.critiques[0].most_critical_gap[:80] if council_verdict.critiques else "")
            )
            log.info("quality_gate council_escalated weak=%d", council_verdict.weak_count)

    # --- Pass 2.5: Cross-reference check (optional) ---
    # Two lanes: True (strict:) = gate adapter, disputes may act.
    # "hosted_free" (research-shaped goals) = hosted-free adapter, flag-only —
    # the second family's disputes are readout fodder, never actions, matching
    # the Pass 1.5 A/B posture. Inert without hosted-free consent.
    cross_ref_result = None
    _cr_lane = "paid" if run_cross_ref is True else (
        run_cross_ref if isinstance(run_cross_ref, str) else "")
    if _cr_lane == "hosted_free":
        try:
            from config import get as _cfg_get
            _cr_enabled = _cfg_get("quality_gate.cross_ref_research", True)
            if isinstance(_cr_enabled, str):
                _cr_enabled = _cr_enabled.strip().lower() not in ("false", "0", "no", "off")
            if not _cr_enabled:
                _cr_lane = ""
        except Exception:
            _cr_lane = ""
    if _cr_lane:
        try:
            from cross_ref import run_cross_ref as _run_cross_ref
            _cr_adapter = adapter
            if _cr_lane == "hosted_free":
                _cr_adapter = _hosted_free_adapter_or_none()
            if _cr_adapter is not None:
                # Build cross-ref text from step outputs
                _cr_text = "\n\n".join(
                    (getattr(s, "result", "") or s.get("result", "") if isinstance(s, dict) else getattr(s, "result", ""))
                    for s in done_steps[-3:]
                )
                _cr_t0 = time.monotonic()
                cross_ref_result = _run_cross_ref(_cr_text, adapter=_cr_adapter)
                _cr_acted = False
                if (_cr_lane == "paid" and cross_ref_result.has_disputes
                        and not escalate):
                    escalate = True
                    verdict = "ESCALATE"
                    reason = (
                        f"Cross-ref: {len(cross_ref_result.disputes)} disputed claim(s) — "
                        + cross_ref_result.disputes[0].claim[:60]
                    )
                    _cr_acted = True
                    log.info("quality_gate cross_ref_escalated disputes=%d",
                             len(cross_ref_result.disputes))
                if cross_ref_result.claims_checked > 0 or cross_ref_result.claims_extracted > 0:
                    try:
                        from captains_log import log_event, QUALITY_GATE_CROSS_REF
                        log_event(
                            QUALITY_GATE_CROSS_REF,
                            subject=goal[:120],
                            summary=(
                                f"lane={_cr_lane} checked={cross_ref_result.claims_checked} "
                                f"disputed={len(cross_ref_result.disputes)} acted={_cr_acted}"
                            ),
                            context={
                                "lane": _cr_lane,
                                "claims_extracted": cross_ref_result.claims_extracted,
                                "claims_checked": cross_ref_result.claims_checked,
                                "disputes": len(cross_ref_result.disputes),
                                "disputed_claims": [
                                    d.claim[:120] for d in cross_ref_result.disputes[:3]
                                ],
                                "acted": _cr_acted,
                                "source": (
                                    f"hosted_free:{getattr(_cr_adapter, '_active_provider', '') or '?'}"
                                    f":{getattr(_cr_adapter, 'model_key', '')}"
                                    if _cr_lane == "hosted_free"
                                    else getattr(_cr_adapter, "model_key", "") or "unknown"
                                ),
                                "paid_verdict": verdict,
                                "elapsed_ms": int((time.monotonic() - _cr_t0) * 1000),
                            },
                            loop_id=loop_id,
                        )
                    except Exception as _cr_ev_exc:
                        log.debug("captains_log cross_ref emit failed: %s", _cr_ev_exc)
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
