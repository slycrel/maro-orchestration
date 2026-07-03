"""Skill rewrite, synthesis, and maintenance — Phase 32 + FunSearch rewrite.

Extracted from evolver.py (Tier 3 refactor split). Owns the circuit-breaker
rewrite pipeline (rewrite_skill + its compactness/peer-ranking helpers), the
3-gate pre-promotion quality check for LLM-synthesized skills, skill
synthesis from successful outcomes, and run_skill_maintenance (the
promote/demote/rewrite/frontier/A-B/rule-refight sweep called every N
heartbeats).

All external dependencies here are imported lazily inside each function
(matching the pre-extraction style) — this module carries no top-level
try/except import bindings and needs none, since nothing here is patched
at module level by tests.

evolver.py (facade) imports and re-exports everything here so run_evolver()
and external callers (heartbeat.py) continue to work unchanged.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING
from llm_parse import extract_json, content_or_empty

if TYPE_CHECKING:  # annotation-only; runtime imports stay inside functions
    from skill_types import Skill

log = logging.getLogger("maro.evolver")


def _compactness_adjusted_score(skill: "Skill") -> float:
    """Brevity-penalized utility score (FunSearch-inspired).

    Favors compact skills over verbose ones with the same utility. Uses a
    log-penalty so very short skills aren't unfairly favored over medium ones.

    char_count = len(description) + sum of step lengths
    adjusted = utility_score / log(1 + char_count / 200)

    A skill with utility_score=0.9 and 400 chars scores ~0.66.
    A skill with utility_score=0.9 and 100 chars scores ~0.86.
    """
    import math
    char_count = len(skill.description) + sum(len(s) for s in skill.steps_template)
    penalty = math.log(1.0 + char_count / 200.0)
    return skill.utility_score / max(penalty, 1.0)


def _top_peer_skills(failing_skill: "Skill", k: int = 2) -> List["Skill"]:
    """Return up to k healthy peer skills with the highest compactness-adjusted score.

    Used to build ranked-candidate context for rewrite_skill (FunSearch pattern:
    LLM sees "here is v0 (score=X), v1 (score=Y) — generate v2").
    """
    try:
        from skills import load_skills
    except ImportError:
        return []

    all_skills = load_skills()
    # Exclude the failing skill and any with open circuit
    candidates = [
        s for s in all_skills
        if s.id != failing_skill.id and s.circuit_state != "open" and s.utility_score > 0.5
    ]
    if not candidates:
        return []

    # Score by compactness-adjusted utility
    scored = sorted(candidates, key=_compactness_adjusted_score, reverse=True)
    return scored[:k]


def rewrite_skill(skill: "Skill", adapter, *, verbose: bool = False) -> Optional["Skill"]:
    """LLM-rewrite a skill whose circuit breaker is OPEN.

    Analyses the skill's failure_notes and current body, produces a revised
    description + steps_template. Resets consecutive_failures and sets
    circuit_state to "half_open" (probationary — not yet trusted).

    Returns the updated Skill on success, None if rewrite fails or adapter unavailable.

    The skill is saved to disk whether or not the caller uses the return value.
    """
    try:
        from skill_types import compute_skill_hash, skill_to_dict as _skill_to_dict
        from skills import _save_skills, load_skills
        from llm import LLMMessage
    except ImportError:
        return None

    if adapter is None:
        return None

    failure_summary = (
        "\n".join(f"- {n}" for n in skill.failure_notes)
        if skill.failure_notes
        else "(no specific failure reasons recorded)"
    )

    # Build ranked-candidate context (FunSearch pattern: show top performers so LLM
    # can recombine their approaches rather than starting from scratch)
    peer_skills = _top_peer_skills(skill)
    peer_context = ""
    if peer_skills:
        lines = ["Top-performing peer skills for reference (compactness-adjusted):"]
        for i, peer in enumerate(peer_skills):
            steps_preview = "; ".join(peer.steps_template[:3])
            lines.append(
                f"  v{i} (score={peer.utility_score:.2f}): {peer.name} — {peer.description[:100]}"
                f"\n    Steps: {steps_preview}"
            )
        peer_context = "\n" + "\n".join(lines) + "\n"

    prompt = f"""You are improving a skill definition for an autonomous agent system.

The skill "{skill.name}" has a tripped circuit breaker (consecutive_failures={skill.consecutive_failures},
utility_score={skill.utility_score:.2f}). Here are the recorded failure reasons:

{failure_summary}

Current skill description:
{skill.description}

Current steps template:
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(skill.steps_template))}
{peer_context}
Based on the failure pattern, rewrite the skill. Output ONLY valid JSON with these keys:
{{
  "description": "<revised one-sentence description of what this skill does>",
  "steps_template": ["<step 1>", "<step 2>", "..."],
  "trigger_patterns": ["<keyword or phrase that should trigger this skill>", "..."]
}}

Rules:
- Keep steps concrete and actionable (not vague)
- Address the failure reasons directly
- Do not add steps that require external network access if failures were network-related
- 2-5 steps maximum
- trigger_patterns: 3-6 short keyword phrases"""

    try:
        resp = adapter.complete(
            [LLMMessage("user", prompt)],
            max_tokens=600,
        )
        raw = resp.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]
        parsed = json.loads(raw)
    except Exception as e:
        if verbose:
            print(f"[evolver] rewrite_skill parse error for {skill.id}: {e}", file=sys.stderr)
        return None

    new_desc = str(parsed.get("description", skill.description)).strip()
    new_steps = [str(s).strip() for s in parsed.get("steps_template", skill.steps_template) if str(s).strip()]
    new_triggers = [str(t).strip() for t in parsed.get("trigger_patterns", skill.trigger_patterns) if str(t).strip()]

    # Pre-save sanity gate (FunSearch pattern: discard invalid candidates before storing)
    # Silently discard if the rewrite fails basic structural requirements.
    if not new_steps or not new_desc:
        log.debug("rewrite_skill discard: empty steps or description for skill %s", skill.id)
        return None
    if len(new_desc) > 400:
        log.debug("rewrite_skill discard: description too long (%d chars) for skill %s",
                  len(new_desc), skill.id)
        return None
    if len(new_steps) > 10:
        log.debug("rewrite_skill discard: too many steps (%d) for skill %s",
                  len(new_steps), skill.id)
        return None
    if not new_triggers:
        # Inherit existing triggers rather than discarding
        new_triggers = skill.trigger_patterns

    # Apply rewrite — set to half_open (probationary) not closed
    skills = load_skills()
    target = next((s for s in skills if s.id == skill.id), None)
    if target is None:
        return None

    target.description = new_desc
    target.steps_template = new_steps
    target.trigger_patterns = new_triggers
    target.consecutive_failures = 0
    target.consecutive_successes = 0
    target.circuit_state = "half_open"  # on probation — not trusted yet
    target.content_hash = compute_skill_hash(target)
    target.failure_notes = target.failure_notes[-2:]  # keep last 2 for history

    _save_skills(skills)

    if verbose:
        print(
            f"[evolver] rewrote skill {skill.id} ({skill.name}) → half_open",
            file=sys.stderr,
        )

    return target


# ---------------------------------------------------------------------------
# Phase 32: Skill synthesis — create a new skill from a successful outcome
# ---------------------------------------------------------------------------

_SYNTHESIZE_SYSTEM = """\
You are an agent that distills successful task executions into reusable skill templates.
Given a completed goal and its outcome summary, synthesize ONE reusable skill definition.
Output ONLY valid JSON with these keys:
{
  "name": "<short snake_case skill name, e.g. web_research_summarise>",
  "description": "<one sentence describing what this skill does>",
  "trigger_patterns": ["<2-5 short keyword phrases that should trigger this skill>"],
  "steps_template": ["<step 1>", "<step 2>", "<step 3>"],
  "expected_outputs": ["<artifact or result 1>", "<artifact or result 2>"],
  "edge_cases": ["<adversarial case 1>", "<adversarial case 2>", "<adversarial case 3>"]
}
Rules:
- 2-5 steps, each concrete and actionable
- trigger_patterns should be SPECIFIC — distinct phrases found in this goal type,
  NOT generic words that would match unrelated goals (e.g. "and", "do", "task")
- description must be one sentence
- name must be unique and descriptive (snake_case)
- expected_outputs: 1-3 concrete artifacts or results the skill produces
- edge_cases: at least 3 adversarial or boundary cases the skill should handle
  (e.g. "empty input", "timeout mid-way", "ambiguous goal wording")
"""


# -----------------------------------------------------------------------------
# 3-gate pre-promotion quality check (BACKLOG item, 2026-04-14)
# Source: Anthropic engineers' Claude Skills quality bar. Three failure modes:
#   (1) trigger precision — must not fire on off-target inputs
#   (2) output schema — must declare what it produces
#   (3) edge case coverage — must articulate adversarial cases
# Run in synthesize_skill() before persistence. A skill that fails any gate
# is discarded with a logged reason; the alternative is polluting the skill
# library with generic-trigger skills that steal matches from better ones.
# -----------------------------------------------------------------------------

# Fixed corpus of generic goals spanning the solution space. Any trigger
# pattern that matches too many of these is too generic to be useful.
_OFF_TARGET_CORPUS = (
    "write a blog post about AI safety",
    "fix the failing CI pipeline",
    "deploy the new database migration",
    "review yesterday's grafana dashboards",
    "update the README with new install instructions",
    "send a status email to the team",
    "schedule a follow-up meeting",
    "create a quarterly OKR report",
    "investigate the auth bug in staging",
    "post a tweet about the release",
)

# If any single trigger matches this many off-target goals, precision fails.
_TRIGGER_PRECISION_MAX_HITS = 3
# Triggers shorter than this are almost always too generic.
_TRIGGER_MIN_LEN = 4
# Minimum edge cases the LLM must articulate.
_MIN_EDGE_CASES = 3


def _gate_trigger_precision(
    trigger_patterns: List[str],
    off_target: tuple = _OFF_TARGET_CORPUS,
) -> tuple:
    """Reject skills whose triggers fire on generic off-target goals.

    Uses the same substring-match logic as skills.find_matching_skills, so
    this gate models real-world match behavior rather than approximating it.
    Returns (passed, reason).
    """
    if not trigger_patterns:
        return False, "no trigger_patterns"
    for pattern in trigger_patterns:
        p = (pattern or "").strip().lower()
        if len(p) < _TRIGGER_MIN_LEN:
            return False, f"trigger {pattern!r} too short (<{_TRIGGER_MIN_LEN} chars)"
        hits = sum(
            1 for goal in off_target
            if p in goal.lower() or goal.lower() in p
        )
        if hits >= _TRIGGER_PRECISION_MAX_HITS:
            return False, (
                f"trigger {pattern!r} matched {hits}/{len(off_target)} off-target goals "
                f"(threshold {_TRIGGER_PRECISION_MAX_HITS})"
            )
    return True, ""


def _gate_output_schema(parsed: dict) -> tuple:
    """Reject skills that don't declare what they produce.

    Requires `expected_outputs` as a non-empty list of non-empty strings.
    Returns (passed, reason).
    """
    raw = parsed.get("expected_outputs")
    if not isinstance(raw, list) or not raw:
        return False, "expected_outputs missing or not a list"
    outputs = [str(o).strip() for o in raw if str(o).strip()]
    if not outputs:
        return False, "expected_outputs empty after filtering blanks"
    return True, ""


def _gate_edge_case_coverage(parsed: dict) -> tuple:
    """Reject skills that don't articulate enough adversarial cases.

    Requires `edge_cases` to contain at least _MIN_EDGE_CASES distinct
    non-empty strings. Returns (passed, reason).
    """
    raw = parsed.get("edge_cases")
    if not isinstance(raw, list):
        return False, "edge_cases missing or not a list"
    cases = {str(c).strip().lower() for c in raw if str(c).strip()}
    if len(cases) < _MIN_EDGE_CASES:
        return False, (
            f"edge_cases has {len(cases)} distinct entries (need {_MIN_EDGE_CASES})"
        )
    return True, ""


def synthesize_skill(
    goal: str,
    outcome_summary: str,
    source_loop_id: str = "",
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
) -> "Optional[Skill]":
    """Synthesize a new provisional skill from a completed goal + outcome.

    Called when a successful loop had no matching skill at start — this fills
    the gap so similar goals benefit from the pattern next time.

    Args:
        goal:            The completed goal string.
        outcome_summary: Brief description of what was accomplished.
        source_loop_id:  Loop ID to tag as the source of this skill.
        adapter:         LLMAdapter to use for synthesis.
        dry_run:         If True, synthesize but do not persist.
        verbose:         Print progress to stderr.

    Returns:
        New Skill on success, None if synthesis fails or adapter unavailable.
    """
    log.info("synthesize_skill goal=%r source_loop=%s", goal[:60], source_loop_id)
    try:
        from skill_types import Skill, compute_skill_hash
        from skills import save_skill, load_skills
        from llm import LLMMessage
    except ImportError:
        return None

    if adapter is None:
        log.debug("synthesize_skill skipped — no adapter")
        return None

    prompt = (
        f"Completed goal: {goal}\n\n"
        f"Outcome: {outcome_summary[:400]}"
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _SYNTHESIZE_SYSTEM),
                LLMMessage("user", prompt),
            ],
            max_tokens=512,
            temperature=0.3,
        )
        parsed = extract_json(content_or_empty(resp), dict, log_tag="evolver.synthesize_skill")
    except Exception as e:
        if verbose:
            print(f"[evolver] synthesize_skill parse error: {e}", file=sys.stderr)
        return None

    if not parsed:
        return None

    name = str(parsed.get("name", "")).strip()
    description = str(parsed.get("description", "")).strip()
    trigger_patterns = [str(t) for t in parsed.get("trigger_patterns", [])]
    steps_template = [str(s) for s in parsed.get("steps_template", [])]

    if not name or not description or not steps_template:
        return None

    # 3-gate pre-promotion quality check (see _gate_* helpers above).
    # Run before the injection guard so we don't spend guard cycles on
    # structurally-bad skills that would fail anyway.
    _gates = (
        ("trigger_precision", _gate_trigger_precision(trigger_patterns)),
        ("output_schema", _gate_output_schema(parsed)),
        ("edge_case_coverage", _gate_edge_case_coverage(parsed)),
    )
    for _gate_name, (_passed, _reason) in _gates:
        if not _passed:
            log.info(
                "synthesize_skill: gate %s rejected skill %r: %s",
                _gate_name, name, _reason,
            )
            if verbose:
                print(
                    f"[evolver] synthesize_skill: {_gate_name} gate failed for "
                    f"'{name}' — {_reason}",
                    file=sys.stderr,
                )
            # Observability: emit a captain's log event so dev CLI + evolver
            # can count how often each gate fires and for which skill shapes.
            try:
                from captains_log import log_event, SKILL_SYNTHESIS_REJECTED
                log_event(
                    event_type=SKILL_SYNTHESIS_REJECTED,
                    subject=name,
                    summary=f"{_gate_name} gate: {_reason}",
                    context={
                        "gate": _gate_name,
                        "reason": _reason,
                        "goal": goal[:200],
                        "trigger_patterns": trigger_patterns[:5],
                    },
                    loop_id=source_loop_id or None,
                )
            except Exception:
                pass
            return None

    # Injection guard: scan LLM-generated skill content before persisting
    try:
        from injection_guard import scan_content as _scan_content
        _skill_text = "\n".join([description] + steps_template)
        _ig = _scan_content(_skill_text, source="internal")
        if not _ig.safe_to_auto_apply:
            if verbose:
                print(
                    f"[evolver] synthesize_skill: injection risk detected ({_ig.risk_level}) "
                    f"in LLM-generated skill '{name}' — discarding",
                    file=sys.stderr,
                )
            return None
    except Exception as _ig_exc:
        # Fail-closed: if the guard scan throws, discard rather than silently persist
        # content that bypassed injection checking.
        log.warning(
            "synthesize_skill: injection_guard scan FAILED — discarding skill '%s': %s",
            name, _ig_exc,
        )
        return None

    # Deduplicate — don't create if a skill with this name already exists
    if not dry_run:
        existing_names = {s.name for s in load_skills()}
        if name in existing_names:
            if verbose:
                print(f"[evolver] synthesize_skill: skill '{name}' already exists, skipping", file=sys.stderr)
            return None

    now = datetime.now(timezone.utc).isoformat()
    new_skill = Skill(
        id=__import__("uuid").uuid4().hex[:8],
        name=name,
        description=description,
        trigger_patterns=trigger_patterns or [goal[:60]],
        steps_template=steps_template,
        source_loop_ids=[source_loop_id] if source_loop_id else [],
        created_at=now,
        tier="provisional",
        utility_score=1.0,
        circuit_state="closed",
    )
    new_skill.content_hash = compute_skill_hash(new_skill)

    if not dry_run:
        try:
            save_skill(new_skill)
        except Exception as e:
            if verbose:
                print(f"[evolver] synthesize_skill: save failed: {e}", file=sys.stderr)
            return None

    if verbose:
        print(f"[evolver] synthesized new skill: {new_skill.name} ({new_skill.id})", file=sys.stderr)

    # Captain's log
    try:
        from captains_log import log_event, SKILL_SYNTHESIZED
        log_event(
            event_type=SKILL_SYNTHESIZED,
            subject=new_skill.name,
            summary=f"New skill synthesized from goal: {goal[:80]}.",
            context={"skill_id": new_skill.id, "goal": goal[:200], "outcome": outcome_summary[:200]},
            loop_id=source_loop_id or None,
            related_ids=[f"skill:{new_skill.id}"],
        )
    except Exception:
        pass

    return new_skill


def run_skill_maintenance(
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Phase 32: auto-promotion, demotion, circuit-breaker-gated rewriting.

    Called from run_evolver() and from heartbeat every N ticks.

    Rewrite policy:
      - Only skills with circuit_state == "open" are eligible
      - A single failure never triggers a rewrite (blip tolerance)
      - CIRCUIT_OPEN_THRESHOLD (3) consecutive failures trips the breaker
      - After rewrite, skill is set to "half_open" (probationary)
      - CIRCUIT_HALFOPEN_RECOVERY (2) consecutive successes closes the breaker

    Also re-fights contested standing rules (decay-by-invalidation v0) when
    an adapter is available — same collision→repair shape, rule layer.

    Returns dict with keys: promoted, demoted, rewritten, rewrite_candidates,
    rules_refought.
    """
    from skills import (
        maybe_auto_promote_skills,
        maybe_demote_skills,
        skills_needing_rewrite,
        frontier_skills,
        retire_losing_variants,
        create_skill_variant,
    )

    promoted: list = []
    demoted: list = []
    rewritten: list = []
    rewrite_candidates: list = []

    if not dry_run:
        try:
            promoted = maybe_auto_promote_skills()
            if promoted and verbose:
                print(f"[evolver] auto-promoted skills: {promoted}", file=sys.stderr)
            # K4: record skill promotions in knowledge layer (non-blocking)
            for sk in promoted:
                try:
                    from knowledge_bridge import record_skill_evolution
                    record_skill_evolution(sk, event="promoted")
                except Exception:
                    pass
        except Exception as e:
            if verbose:
                print(f"[evolver] auto-promote failed: {e}", file=sys.stderr)

        try:
            demoted = maybe_demote_skills()
            if demoted and verbose:
                print(f"[evolver] demoted skills: {demoted}", file=sys.stderr)
            # K4: record skill demotions in knowledge layer (non-blocking)
            for sk in demoted:
                try:
                    from knowledge_bridge import record_skill_evolution
                    record_skill_evolution(sk, event="demoted")
                except Exception:
                    pass
        except Exception as e:
            if verbose:
                print(f"[evolver] demotion failed: {e}", file=sys.stderr)

    try:
        candidates = skills_needing_rewrite()
        rewrite_candidates = [s.id for s in candidates]
        if rewrite_candidates and verbose:
            print(f"[evolver] skills with open circuit (rewrite candidates): {rewrite_candidates}", file=sys.stderr)

        if not dry_run and adapter is not None:
            for skill in candidates:
                updated = rewrite_skill(skill, adapter=adapter, verbose=verbose)
                if updated is not None:
                    rewritten.append(skill.id)
    except Exception as e:
        if verbose:
            print(f"[evolver] rewrite scan failed: {e}", file=sys.stderr)

    # Agent0 steal: frontier task targeting — also rewrite skills in the 40-70% zone.
    # These are neither trivially successful nor circuit-broken; they're the hardest
    # to diagnose without trying an improved version. Cap at 2 per cycle to avoid
    # over-spending LLM budget on exploratory rewrites.
    try:
        _frontier = frontier_skills()
        if _frontier and verbose:
            print(f"[evolver] frontier skills (40-70% utility): {[s.id for s in _frontier[:2]]}", file=sys.stderr)
        if not dry_run and adapter is not None:
            for skill in _frontier[:2]:  # max 2 frontier rewrites per cycle
                if skill.id not in rewrite_candidates:  # don't double-rewrite
                    # Pre-score candidate with replay-based fitness oracle before rewriting
                    try:
                        from strategy_evaluator import evaluate_skill as _eval_skill
                        _fitness = _eval_skill(skill)
                        log.info(
                            "evolver frontier_prescore: skill %s fitness=%.2f confidence=%.2f verdict=%s",
                            skill.id, _fitness.fitness_score, _fitness.confidence, _fitness.verdict,
                        )
                        if _fitness.verdict == "PASS" and _fitness.confidence >= 0.3:
                            if verbose:
                                print(
                                    f"[evolver] frontier skill {skill.id} scores PASS — skipping rewrite",
                                    file=sys.stderr,
                                )
                            continue
                    except Exception as _pe:
                        log.debug("strategy pre-score failed (non-fatal): %s", _pe)
                    _updated = rewrite_skill(skill, adapter=adapter, verbose=verbose)
                    if _updated is not None:
                        # A/B variant: frontier rewrites become challengers, not replacements
                        try:
                            _challenger = create_skill_variant(skill, _updated)
                            from skills import save_skill as _save_skill
                            _save_skill(_challenger)
                            rewritten.append(skill.id)
                            log.info(
                                "evolver frontier_ab: created challenger %s for parent %s (utility=%.2f)",
                                _challenger.id, skill.id, skill.utility_score,
                            )
                        except Exception as _ve:
                            log.debug("ab variant save failed (non-fatal): %s", _ve)
    except Exception as _fe:
        log.debug("frontier rewrite scan failed (non-fatal): %s", _fe)

    # A/B retirement: check existing variants for sufficient evidence and retire losers
    try:
        _ab_result = retire_losing_variants(dry_run=dry_run)
        if _ab_result.get("retired") and verbose:
            print(
                f"[evolver] ab_variants: promoted={_ab_result['promoted']} retired={_ab_result['retired']}",
                file=sys.stderr,
            )
    except Exception as _ab_e:
        log.debug("ab variant retirement failed (non-fatal): %s", _ab_e)

    # Decay-by-invalidation v0 (BACKLOG 2026-06-11): re-fight contested
    # standing rules — the rewrite_skill collision→repair pattern applied to
    # the rule layer. A contradicted rule stops being injected "apply
    # unconditionally" immediately (knowledge_lens.inject_standing_rules);
    # this is the repair half. Capped per cycle to bound spend.
    refought: list = []
    try:
        from knowledge_lens import contested_rules, refight_rule
        _contested = contested_rules()
        if _contested and verbose:
            print(
                f"[evolver] contested standing rules (re-fight candidates): "
                f"{[r.rule_id for r in _contested[:3]]}",
                file=sys.stderr,
            )
        if not dry_run and adapter is not None:
            for _rule in _contested[:3]:  # max 3 re-fights per cycle
                _action = refight_rule(_rule, adapter, verbose=verbose)
                if _action:
                    refought.append(f"{_rule.rule_id}:{_action}")
    except Exception as _rf_e:
        log.debug("rule re-fight scan failed (non-fatal): %s", _rf_e)

    return {
        "promoted": promoted,
        "demoted": demoted,
        "rewritten": rewritten,
        "rewrite_candidates": rewrite_candidates,
        "rules_refought": refought,
    }


def get_friction_summary() -> str:
    """Return a brief human-readable friction summary from the latest inspector run.

    Used by heartbeat tier-2 LLM diagnosis. Delegates to
    inspector.get_friction_summary() to avoid duplication.
    """
    try:
        from inspector import get_friction_summary as _inspector_summary
        return _inspector_summary()
    except Exception:
        return ""
