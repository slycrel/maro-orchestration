"""Meta-Evolver — §19 Self-Leveling / Meta-Evolution

Periodically reviews recent run outcomes to identify failure patterns,
propose prompt improvements, and generate new guardrails.

This is the "Maro gets better over time" component. It:
  1. Loads the last N outcomes from memory/outcomes.jsonl
  2. Asks an LLM to identify failure patterns and suggest improvements
  3. Writes structured suggestions to memory/suggestions.jsonl
  4. Optionally sends a summary via Telegram

Design follows the Reflexion pattern (per §19): reflect on failures,
store lessons, inject lessons into future prompts (handled by memory.py).
The meta-evolver is the *aggregate* level — looking across many runs, not
just one.

Usage:
    python3 evolver.py                  # run once
    python3 evolver.py --dry-run        # analyze without writing
    python3 evolver.py --min-outcomes 5 # only run if >= 5 new outcomes
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, List
from llm_parse import extract_json, safe_float, safe_str, safe_list, content_or_empty

log = logging.getLogger("maro.evolver")

# Module-level imports for clean test patching
try:
    from memory import load_outcomes, load_lessons, Outcome, Lesson
except ImportError:  # pragma: no cover
    load_outcomes = None  # type: ignore[assignment]
    load_lessons = None  # type: ignore[assignment]

try:
    from llm import build_adapter, MODEL_MID, LLMMessage
except ImportError:  # pragma: no cover
    build_adapter = None  # type: ignore[assignment]

# Suggestion storage + apply/revert engine — extracted to evolver_store.py.
# Re-exported here so external callers and test patches keep working.
from evolver_store import (
    Suggestion, EvolverReport,
    _suggestions_path, _dynamic_constraints_path,
    load_suggestions, _save_suggestions, list_pending_suggestions,
    suggestion_is_applied,
    _apply_suggestion_action, apply_suggestion, revert_suggestion,
    stamp_verification,
    _run_skill_test_gate, validate_skill_mutation, record_tiered_lesson, MemoryTier,
)

# Statistical scanners + business-signal scan + impact analysis — extracted
# to evolver_scans.py. Re-exported here so run_evolver() and external callers
# and test patches keep working.
from evolver_scans import (
    BusinessSignal, scan_outcomes_for_signals, _load_user_signals, _SIGNAL_SYSTEM,
    CalibrationFinding, scan_calibration_log,
    scan_step_costs,
    QualityDriftFinding, scan_quality_drift, _baselines_path, _load_baselines, _save_baseline,
    scan_canon_candidates,
    _record_suggestion_outcomes, scan_suggestion_outcomes,
    EvolverImpactRecord, scan_evolver_impact, format_impact_summary,
    verify_applied_suggestions,
)

# Skill rewrite/synthesis/maintenance — extracted to skill_lifecycle.py.
# Re-exported here so run_evolver() and external callers (heartbeat.py)
# keep working.
from skill_lifecycle import (
    _compactness_adjusted_score, _top_peer_skills, rewrite_skill,
    _SYNTHESIZE_SYSTEM, _OFF_TARGET_CORPUS, _MIN_EDGE_CASES,
    _gate_trigger_precision, _gate_output_schema, _gate_edge_case_coverage,
    synthesize_skill, run_skill_maintenance, get_friction_summary,
)


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

_EVOLVER_SYSTEM = """\
You are a meta-evolution agent. You analyze patterns across many completed and failed runs
to identify systemic improvements.

You will receive a summary of recent run outcomes. Identify:
1. Failure patterns (repeated reasons for "stuck" outcomes)
2. Success patterns (what made "done" outcomes succeed)
3. Prompt improvements (changes to agent instructions that would reduce failures)
4. New guardrails (checks or constraints to prevent common failure modes)

Respond ONLY with a JSON object in this format:
{
  "failure_patterns": ["pattern 1", "pattern 2"],
  "suggestions": [
    {
      "category": "prompt_tweak|new_guardrail|skill_pattern|observation",
      "target": "all|research|build|ops|agenda|now",
      "suggestion": "specific improvement text",
      "failure_pattern": "what pattern motivated this",
      "confidence": 0.0-1.0,
      "expected_signal": [
        {"metric": "failure_class_rate|stuck_rate|cost_per_run|<other observable>",
         "class": "the failure class this should reduce, if applicable",
         "direction": "down|up"}
      ]
    }
  ]
}

expected_signal declares which observable this specific change should move and
which direction, so it can be checked later against what actually happened.
Omit it (or leave the list empty) if you can't name a concrete observable.

Be specific and actionable. Suggest at most 5 improvements total. If there are no clear patterns
(e.g., too few outcomes), return {"failure_patterns": [], "suggestions": []}.
"""


def _build_outcomes_summary(outcomes: List[Any]) -> str:
    """Summarize outcomes for LLM analysis.

    Meta-Harness steal: enriches stuck outcomes with full step-level execution
    traces so the proposer sees actual failure paths, not just aggregate summaries.
    """
    if not outcomes:
        return "(no outcomes to analyze)"

    stuck = [o for o in outcomes if o.status == "stuck"]
    done = [o for o in outcomes if o.status == "done"]
    # Verdict tri-state (SF-2): "done" only says the loop finished. Prefer the
    # judged goal verdict when present; absent = unjudged (neither success nor
    # failure). A done-but-goal-NOT-achieved run is failure signal for the
    # proposer, not success.
    achieved = [o for o in done if getattr(o, "goal_achieved", None) is True]
    goal_failed = [o for o in done if getattr(o, "goal_achieved", None) is False]
    unjudged_done = len(done) - len(achieved) - len(goal_failed)

    lines = [
        f"Total outcomes: {len(outcomes)} ({len(done)} done "
        f"[{len(achieved)} verified achieved, {len(goal_failed)} goal-NOT-achieved, "
        f"{unjudged_done} unjudged], {len(stuck)} stuck)",
        "",
        "Recent outcomes:",
    ]
    for o in outcomes[:20]:
        _ga = getattr(o, "goal_achieved", None)
        _verdict_tag = "" if _ga is None else (" [goal achieved]" if _ga else " [goal NOT achieved]")
        lines.append(
            f"  [{o.status}]{_verdict_tag} [{o.task_type}] {o.goal[:60]}"
            + (f" — {o.summary[:80]}" if o.summary else "")
        )

    if goal_failed:
        lines.append("\nCompleted-but-goal-NOT-achieved summaries (treat as failures):")
        for o in goal_failed[:10]:
            lines.append(f"  - {o.summary[:120]}")

    if stuck:
        lines.append("\nStuck outcome summaries:")
        for o in stuck[:10]:
            lines.append(f"  - {o.summary[:120]}")

        # Meta-Harness: include full step traces for stuck outcomes so the
        # proposer can identify exactly where runs failed and why
        stuck_ids = [getattr(o, "outcome_id", "") for o in stuck[:5] if getattr(o, "outcome_id", "")]
        if stuck_ids:
            try:
                from memory import load_step_traces
                traces = load_step_traces(stuck_ids)
                if traces:
                    lines.append("\nFull step traces for stuck runs:")
                    for oid, trace in traces.items():
                        lines.append(f"\n  [trace:{oid}] goal: {trace.get('goal', '')[:80]}")
                        for step in trace.get("steps", [])[:8]:
                            s_status = step.get("status", "?")
                            s_text = step.get("step", "")[:60]
                            s_reason = step.get("stuck_reason", "")
                            lines.append(f"    [{s_status}] {s_text}"
                                         + (f" — stuck: {s_reason[:80]}" if s_reason else ""))
            except Exception:
                pass

    return "\n".join(lines)


def _llm_analyze(outcomes: List[Any], *, dry_run: bool = False, adapter=None) -> tuple[List[str], List[dict]]:
    """Ask LLM to identify patterns and suggest improvements. Returns (patterns, raw_suggestions).

    adapter: reuse the caller's LLMAdapter (e.g. the run's adapter when the
    meta-cycle fires from run finalization); falls back to building one.
    """
    if dry_run or not outcomes:
        return [], []

    try:
        if adapter is None:
            adapter = build_adapter(model=MODEL_MID)
        summary = _build_outcomes_summary(outcomes)

        # Captain's log context: recent learning-system actions for the evolver
        # to account for (e.g., "skill X was just demoted — don't re-suggest it").
        # Shared read bridge with the loop slice; the evolver keeps its own
        # event set (EVOLVER_SKIPPED matters here, DIAGNOSIS noise doesn't).
        _log_ctx = ""
        try:
            from recall import recent_learning_activity
            _log_ctx = recent_learning_activity(
                event_types=(
                    "SKILL_PROMOTED", "SKILL_DEMOTED", "SKILL_CIRCUIT_OPEN",
                    "SKILL_REWRITE", "EVOLVER_APPLIED", "EVOLVER_SKIPPED",
                    "STANDING_RULE_CONTRADICTED", "RULE_GRADUATED",
                ),
                scan_limit=20,
                header="\n\nRecent learning system activity:",
            )
        except Exception:
            pass

        resp = adapter.complete(
            [
                LLMMessage("system", _EVOLVER_SYSTEM),
                LLMMessage("user", f"Analyze these outcomes:\n\n{summary}{_log_ctx}"),
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="evolver._analyze")
        if data:
            patterns = safe_list(data.get("failure_patterns", []), element_type=str)
            raw_suggestions = safe_list(data.get("suggestions", []), element_type=dict)
            return patterns, raw_suggestions
    except Exception as e:
        if __debug__:
            print(f"[evolver] LLM analysis failed: {e}", file=sys.stderr)
    return [], []


def run_statistical_scans(
    outcomes: "List[Any] | None" = None,
    *,
    outcomes_window: int = 50,
    verbose: bool = False,
    scan_calibration: bool = True,
    scan_costs: bool = True,
    scan_canon: bool = True,
    scan_suggestion_calibration: bool = True,
    scan_drift: bool = True,
) -> List[Suggestion]:
    """Run the 5 non-LLM statistical scanners and return wrapped Suggestions.

    No LLM calls, so no cost/latency concern running this far more often than
    the evolver heartbeat cadence (BACKLOG #13, 2026-07-03) — e.g. once per
    goal run instead of once per N heartbeat ticks. Callers decide whether to
    persist (``_save_suggestions``) and never auto-apply from here; these are
    observational, same as ``scan_canon_candidates``'s own "not automatic"
    contract.
    """
    if outcomes is None:
        try:
            outcomes = load_outcomes(limit=outcomes_window)
        except Exception:
            outcomes = []

    suggestions: List[Suggestion] = []

    # Calibration review — detect systematic over/under-confidence in escalation decisions
    if scan_calibration:
        try:
            cal_findings = scan_calibration_log()
            for cf in cal_findings:
                import uuid as _cal_uuid
                suggestions.append(Suggestion(
                    suggestion_id=f"cal-{_cal_uuid.uuid4().hex[:8]}",
                    category="prompt_tweak",
                    target="escalation",
                    suggestion=cf.suggestion,
                    failure_pattern=(
                        f"calibration: class={cf.decision_class!r} "
                        f"override_rate={cf.override_rate:.0%} "
                        f"mean_confidence={cf.mean_confidence:.1f}/10 "
                        f"n={cf.entry_count}"
                    ),
                    confidence=0.75,
                    outcomes_analyzed=cf.entry_count,
                ))
            if verbose and cal_findings:
                print(f"[evolver] calibration_scan: {len(cal_findings)} finding(s)", file=sys.stderr)
            log.info("evolver calibration_scan findings=%d", len(cal_findings))
        except Exception as _cal_exc:
            log.debug("calibration scan failed (non-fatal): %s", _cal_exc)

    # Step cost scan — detect high-burn step patterns, propose Haiku routing
    if scan_costs:
        try:
            cost_suggestions = scan_step_costs()
            suggestions.extend(cost_suggestions)
            if verbose and cost_suggestions:
                print(f"[evolver] cost_scan: {len(cost_suggestions)} high-burn suggestion(s)", file=sys.stderr)
            log.info("evolver cost_scan suggestions=%d", len(cost_suggestions))
        except Exception as _cost_exc:
            log.debug("cost scan failed (non-fatal): %s", _cost_exc)

    # Canon candidate scan — Stage 2→3 promotion surface (human-gated, no auto-apply)
    if scan_canon:
        try:
            canon_suggestions = scan_canon_candidates()
            suggestions.extend(canon_suggestions)
            if verbose and canon_suggestions:
                print(
                    f"[evolver] canon_scan: {len(canon_suggestions)} identity promotion candidate(s)",
                    file=sys.stderr,
                )
            log.info("evolver canon_scan candidates=%d", len(canon_suggestions))
        except Exception as _canon_exc:
            log.debug("canon scan failed (non-fatal): %s", _canon_exc)

    # Suggestion confidence calibration — empirical pass rate vs self-reported confidence
    if scan_suggestion_calibration:
        try:
            calibration_suggestions = scan_suggestion_outcomes()
            suggestions.extend(calibration_suggestions)
            if verbose and calibration_suggestions:
                print(
                    f"[evolver] suggestion_calibration: {len(calibration_suggestions)} miscalibration finding(s)",
                    file=sys.stderr,
                )
            log.info("evolver suggestion_calibration findings=%d", len(calibration_suggestions))
        except Exception as _sco_exc:
            log.debug("suggestion calibration scan failed (non-fatal): %s", _sco_exc)

    # Quality drift detection — compare this cycle to rolling baseline
    if scan_drift:
        try:
            # Convert outcomes to dicts for scan_quality_drift
            _outcome_dicts = [o if isinstance(o, dict) else (o.__dict__ if hasattr(o, "__dict__") else {}) for o in outcomes]
            drift_findings = scan_quality_drift(_outcome_dicts)
            for df in drift_findings:
                import uuid as _drift_uuid
                suggestions.append(Suggestion(
                    suggestion_id=f"drift-{_drift_uuid.uuid4().hex[:8]}",
                    category="observation",
                    target=df.metric,
                    suggestion=df.suggestion,
                    failure_pattern=f"quality_drift: {df.metric} delta={df.delta_pct:.1f}% consecutive={df.consecutive_drops}",
                    confidence=min(0.9, 0.6 + df.consecutive_drops * 0.1),
                    outcomes_analyzed=len(outcomes),
                ))
            if verbose and drift_findings:
                print(f"[evolver] drift_scan: {len(drift_findings)} quality drift finding(s)", file=sys.stderr)
            log.info("evolver drift_scan findings=%d", len(drift_findings))
        except Exception as _drift_exc:
            log.debug("quality drift scan failed (non-fatal): %s", _drift_exc)

    return suggestions


def promote_skill_candidates(*, adapter=None, dry_run: bool = False,
                              limit: int = 5, verbose: bool = False) -> int:
    """Catch-up sweep for run_curation.flag_skill_candidate's advisory flag.

    Adversarial-review R1 batch-1 finding #4: `card["skill_candidate"]` had
    no consumer outside tests. WIRED, not removed — arch-quality-selfimprove
    names this exact gap ("skills-lite covers only runs that deliberately
    author a skill .md"; outcome-driven extract_skills discovery is rare),
    and flag_skill_candidate's own docstring already names extract_skills as
    the intended consumer. It can't be same-run (loop_finalize calls
    extract_skills at goal-end, BEFORE run_curation.curate_run runs — see
    run_curation.py's "skill_candidate consumer" section for the full
    ordering), so this is a periodic catch-up pass instead: it feeds
    unconsumed flags through the SAME extract_skills() call loop_finalize
    uses, so there's one skill-crystallization code path with two triggers
    into it, not a second promotion mechanism.

    Returns the number of new skills saved this call (0 covers: no
    candidates, dry_run, or extract_skills itself declining a low-signal
    batch — all non-error outcomes for a heuristic pass).
    """
    if dry_run:
        return _promote_skill_candidates_impl(
            adapter=adapter, dry_run=True, limit=limit, verbose=verbose
        )

    # The read-unconsumed → paid extraction → mark-consumed transaction must
    # have one process owner. A second manual/heartbeat/run-cadence evolver
    # skips non-blockingly; the winner consumes successes, while extraction
    # failures remain available for a later cycle. Lock infrastructure fails
    # closed because duplicate LLM spend is the risk this boundary prevents.
    try:
        from proc_lock import hold_pidfile
    except Exception as exc:
        log.warning("skill_candidate sweep lock failed closed: %s", exc)
        return 0
    with hold_pidfile("skill-candidate-sweep", fail_open=False) as acquired:
        if not acquired:
            log.info("skill_candidate sweep skipped: another process owns it")
            return 0
        return _promote_skill_candidates_impl(
            adapter=adapter, dry_run=False, limit=limit, verbose=verbose
        )


def _promote_skill_candidates_impl(*, adapter=None, dry_run: bool = False,
                                   limit: int = 5, verbose: bool = False) -> int:
    """Implementation; production caller owns the sweep lock; dry-run does not."""
    try:
        from run_curation import find_unconsumed_skill_candidates, mark_skill_candidate_consumed
        from skills import extract_skills
    except Exception:
        return 0

    try:
        candidates = find_unconsumed_skill_candidates(limit=limit)
    except Exception:
        candidates = []
    if not candidates:
        return 0

    if verbose:
        print(f"[evolver] skill_candidate sweep: {len(candidates)} unconsumed", file=sys.stderr)

    # Preserve the curated classification. extract_skills shares the same
    # learnability predicate and accepts this card-derived shape directly;
    # do not synthesize a fake raw-outcome status.
    outcomes = []
    from outcome_policy import is_learnable_outcome
    for card in candidates:
        if not is_learnable_outcome(card):
            continue
        reasons = (card.get("skill_candidate") or {}).get("reasons") or []
        outcomes.append({
            "outcome_id": card.get("handle_id"),
            "goal": card.get("goal", ""),
            "task_type": card.get("lane", ""),
            "success_class": card.get("success_class"),
            "goal_achieved": card.get("goal_achieved"),
            "summary": card.get("result_excerpt") or "; ".join(reasons),
        })

    n_saved = 0
    extraction_failed = False
    if outcomes and not dry_run:
        try:
            if adapter is None:
                adapter = build_adapter(model=MODEL_MID)
            saved = extract_skills(outcomes, adapter)
            n_saved = len(saved or [])
            if verbose and n_saved:
                print(f"[evolver] skill_candidate sweep: {n_saved} skill(s) promoted", file=sys.stderr)
            log.info("evolver skill_candidate_sweep candidates=%d saved=%d", len(outcomes), n_saved)
        except Exception as exc:
            log.debug("promote_skill_candidates: extract_skills failed (non-fatal): %s", exc)
            extraction_failed = True

    # Mark every CONSIDERED candidate consumed regardless of whether
    # extract_skills chose to promote it — consumed means "looked at", not
    # "produced a skill" (see mark_skill_candidate_consumed's docstring);
    # extract_skills declining a small/low-signal batch isn't a reason to
    # re-scan the same runs forever. But a candidate that made it into
    # `outcomes` was never actually looked at if extract_skills raised
    # before returning — consuming it anyway would burn its only retry on
    # a transient failure (bad adapter, timeout, malformed response), not
    # a real decision. Only candidates extract_skills genuinely evaluated
    # (or that the local success_class filter already rejected) get marked.
    outcome_ids = {o["outcome_id"] for o in outcomes} if extraction_failed else set()
    if not dry_run:
        for card in candidates:
            hid = card.get("handle_id")
            if not hid or hid in outcome_ids:
                continue
            try:
                mark_skill_candidate_consumed(hid)
            except Exception:
                pass

    return n_saved


def run_evolver(
    *,
    outcomes_window: int = 50,
    min_outcomes: int = 3,
    dry_run: bool = False,
    verbose: bool = True,
    notify: bool = False,
    adapter=None,
    scan_signals: bool = True,
    scan_calibration: bool = True,
    scan_costs: bool = True,
    scan_drift: bool = True,
    scan_canon: bool = True,
    scan_suggestion_calibration: bool = True,
    scan_persona_gaps: bool = True,
    scan_harness_friction: bool = True,
    scan_skill_candidates: bool = True,
) -> EvolverReport:
    """Run one meta-evolution cycle.

    Args:
        outcomes_window: How many recent outcomes to analyze.
        min_outcomes: Skip if fewer than this many outcomes exist.
        dry_run: Analyze without writing suggestions.
        verbose: Print progress to stderr.
        notify: Send Telegram summary if suggestions were generated.
        adapter: Optional LLMAdapter to reuse for the analysis call (threaded
            through from run finalization's cadence trigger so the meta-cycle
            rides the run's adapter instead of constructing a fresh one).

    Returns:
        EvolverReport with suggestions and failure patterns.
    """
    import uuid as _uuid

    run_id = _uuid.uuid4().hex[:8]
    started = time.monotonic()

    log.info("evolver_start run_id=%s outcomes_window=%d min=%d dry_run=%s",
             run_id, outcomes_window, min_outcomes, dry_run)
    if verbose:
        print(f"[evolver] run_id={run_id} starting...", file=sys.stderr)

    # VERIFY_LEARN_ARC V3 precursor: cheap structural checks for graduation
    # suggestions that are actually applied. Observe/notify only — no revert
    # or demotion until V1 expectations and V2 authority provenance/consumer
    # exist. Run before the outcome-count early return so a quiet interval does
    # not suppress verification on the cadence pass.
    if not dry_run:
        try:
            from graduation import run_graduation_verification
            run_graduation_verification(notify=notify)
        except Exception as _grad_verify_exc:
            log.warning(
                "graduation structural verification failed non-fatally: %s",
                _grad_verify_exc,
            )

    # Load recent outcomes
    try:
        outcomes = load_outcomes(limit=outcomes_window)
    except Exception as e:
        return EvolverReport(run_id=run_id, outcomes_reviewed=0, skipped=True, skip_reason=str(e))

    if len(outcomes) < min_outcomes:
        return EvolverReport(
            run_id=run_id,
            outcomes_reviewed=len(outcomes),
            skipped=True,
            skip_reason=f"only {len(outcomes)} outcomes (need {min_outcomes})",
        )

    if verbose:
        print(f"[evolver] analyzing {len(outcomes)} outcomes...", file=sys.stderr)

    # LLM analysis
    patterns, raw_suggestions = _llm_analyze(outcomes, dry_run=dry_run, adapter=adapter)

    # Build Suggestion objects
    suggestions: List[Suggestion] = []
    for i, raw in enumerate(raw_suggestions):
        try:
            suggestions.append(Suggestion(
                suggestion_id=f"{run_id}-{i:02d}",
                category=raw.get("category", "observation"),
                target=raw.get("target", "all"),
                suggestion=raw.get("suggestion", ""),
                failure_pattern=raw.get("failure_pattern", ""),
                confidence=safe_float(raw.get("confidence"), default=0.5, min_val=0.0, max_val=1.0),
                outcomes_analyzed=len(outcomes),
                expected_signal=safe_list(raw.get("expected_signal", []), element_type=dict),
            ))
        except Exception:
            pass

    # Business signal scan — convert actionable findings to sub_mission suggestions
    if scan_signals:
        try:
            signals = scan_outcomes_for_signals(outcomes, dry_run=dry_run)
            for sig in signals:
                import uuid as _sig_uuid
                suggestions.append(Suggestion(
                    suggestion_id=f"sig-{_sig_uuid.uuid4().hex[:8]}",
                    category="sub_mission",
                    target=sig.signal_type,
                    suggestion=sig.suggested_goal,
                    failure_pattern=f"signal from: {sig.source_outcome[:80]}",
                    confidence=sig.confidence,
                    outcomes_analyzed=len(outcomes),
                ))
            if verbose and signals:
                print(f"[evolver] signal_scan: {len(signals)} sub_mission suggestion(s)", file=sys.stderr)
            log.info("evolver signal_scan signals=%d", len(signals))
        except Exception as _sig_exc:
            log.debug("signal scan failed (non-fatal): %s", _sig_exc)

    # 5 non-LLM statistical scanners (calibration, step-cost, canon, suggestion-
    # calibration, quality-drift) — shared with the per-run hook in
    # loop_finalize.py's _finalize_loop() (BACKLOG #13, 2026-07-03).
    suggestions.extend(run_statistical_scans(
        outcomes,
        verbose=verbose,
        scan_calibration=scan_calibration,
        scan_costs=scan_costs,
        scan_canon=scan_canon,
        scan_suggestion_calibration=scan_suggestion_calibration,
        scan_drift=scan_drift,
    ))

    # Harness friction scan — "Harness Is the Problem" (@sebgoddijn / Ramp Glass)
    # Models are fine; friction in code paths = harness quality signal.
    if scan_harness_friction:
        try:
            from harness_optimizer import scan_harness_friction as _scan_friction
            from harness_optimizer import _save_friction_suggestions
            friction_report = _scan_friction()
            if friction_report.friction_points:
                n_saved = _save_friction_suggestions(
                    friction_report.friction_points,
                    run_id=f"{run_id}-friction",
                    dry_run=dry_run,
                )
                if verbose and friction_report.friction_points:
                    print(
                        f"[evolver] harness_friction: {len(friction_report.friction_points)} "
                        f"friction point(s), {n_saved} suggestion(s) saved",
                        file=sys.stderr,
                    )
                log.info(
                    "evolver harness_friction friction=%d saved=%d",
                    len(friction_report.friction_points), n_saved,
                )
        except Exception as _hf_exc:
            log.debug("harness friction scan failed (non-fatal): %s", _hf_exc)

    # Persona gap scan — detect recurring fallback dispatches → author new personas
    if scan_persona_gaps:
        try:
            from persona import scan_persona_gaps as _scan_pg
            import uuid as _pg_uuid
            gaps = _scan_pg()
            for gap in gaps:
                role = gap["role_hint"]
                slug = gap["suggested_slug"]
                count = gap["fallback_count"]
                sample = "; ".join(gap["sample_goals"][:2])
                suggestions.append(Suggestion(
                    suggestion_id=f"pg-{_pg_uuid.uuid4().hex[:8]}",
                    category="persona_authoring",
                    target=slug,
                    suggestion=(
                        f"Author a new persona 'personas/{slug}.md' for the recurring '{role}' role. "
                        f"{count} dispatches fell back to default persona (no confident match). "
                        f"Sample goals: {sample}"
                    ),
                    failure_pattern=f"no_persona_match: role={role} count={count}",
                    confidence=0.75,  # human-review recommended before auto-apply
                    outcomes_analyzed=count,
                ))
            if verbose and gaps:
                print(f"[evolver] persona_gap_scan: {len(gaps)} unmatched role(s)", file=sys.stderr)
            log.info("evolver persona_gap_scan gaps=%d", len(gaps))
        except Exception as _pg_exc:
            log.debug("persona gap scan failed (non-fatal): %s", _pg_exc)

    report = EvolverReport(
        run_id=run_id,
        outcomes_reviewed=len(outcomes),
        suggestions=suggestions,
        failure_patterns=patterns,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )

    if verbose:
        print(f"[evolver] found {len(patterns)} patterns, {len(suggestions)} suggestions", file=sys.stderr)

    # Persist suggestions
    if not dry_run and suggestions:
        try:
            _save_suggestions(suggestions)
        except Exception as e:
            if verbose:
                print(f"[evolver] failed to save suggestions: {e}", file=sys.stderr)

    # Auto-apply high-confidence suggestions (closes the feedback loop)
    # Advisor Pattern: for medium-confidence suggestions (0.6–0.79), consult
    # Opus before applying. High-confidence (≥0.8) still auto-apply directly.
    auto_applied = 0
    applied_ids: List[str] = []  # parallel to counter; lets _verify_post_apply revert on failure
    advisor_promoted = 0
    if not dry_run and suggestions:
        for s in suggestions:
            if s.applied:
                continue
            if s.confidence >= 0.8:
                if (
                    apply_suggestion(s.suggestion_id)
                    and suggestion_is_applied(s.suggestion_id)
                ):
                    auto_applied += 1
                    applied_ids.append(s.suggestion_id)
            elif 0.6 <= s.confidence < 0.8:
                # Advisor gate: let Opus decide on medium-confidence suggestions
                try:
                    from llm import advisor_call as _adv_call
                    _adv_context = (
                        f"Category: {s.category}\n"
                        f"Suggestion: {s.suggestion[:300]}\n"
                        f"Confidence: {s.confidence:.2f}\n"
                        f"Target: {getattr(s, 'target', 'all')}\n"
                        f"Based on {len(outcomes)} recent outcomes."
                    )
                    _advice = _adv_call(
                        goal="meta-improvement: should this suggestion be auto-applied?",
                        context=_adv_context,
                        question=(
                            "This suggestion has medium confidence (0.6-0.79). "
                            "Should we auto-apply it? Consider: (a) could it degrade existing behavior, "
                            "(b) is the evidence strong enough, (c) is it reversible? "
                            "Answer YES to apply, NO to defer for human review."
                        ),
                    )
                    if _advice and "yes" in _advice.lower().split()[:5]:
                        if (
                            apply_suggestion(s.suggestion_id)
                            and suggestion_is_applied(s.suggestion_id)
                        ):
                            auto_applied += 1
                            applied_ids.append(s.suggestion_id)
                            advisor_promoted += 1
                            log.info("evolver advisor: promoted suggestion %s (confidence %.2f)",
                                     s.suggestion_id, s.confidence)
                    else:
                        log.info("evolver advisor: deferred suggestion %s (confidence %.2f): %s",
                                 s.suggestion_id, s.confidence, (_advice or "no response")[:100])
                except Exception:
                    pass  # advisor is optional — never block evolver
        if verbose and auto_applied:
            print(f"[evolver] auto-applied {auto_applied} suggestions ({advisor_promoted} via advisor)", file=sys.stderr)

    # Verify→learn: after mutations, check test suite health and record outcome
    if auto_applied and not dry_run:
        _verify_post_apply(applied_ids, run_id, verbose=verbose)

    # Telegram notification
    if notify and suggestions and not dry_run:
        _notify_telegram(report)

    report.elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info("evolver_done run_id=%s patterns=%d suggestions=%d auto_applied=%d elapsed=%dms",
             run_id, len(patterns), len(suggestions), auto_applied, report.elapsed_ms)

    # Captain's log: evolver cycle summary
    try:
        from captains_log import log_event, EVOLVER_GENERATED, EVOLVER_APPLIED, EVOLVER_SKIPPED
        if suggestions:
            log_event(
                event_type=EVOLVER_GENERATED,
                subject=f"run-{run_id}",
                summary=f"Generated {len(suggestions)} suggestions from {len(outcomes)} outcomes. {auto_applied} auto-applied.",
                context={
                    "run_id": run_id,
                    "outcomes_reviewed": len(outcomes),
                    "suggestions": len(suggestions),
                    "auto_applied": auto_applied,
                    "patterns": len(patterns),
                },
            )
        elif not report.skipped:
            log_event(
                event_type=EVOLVER_SKIPPED,
                subject=f"run-{run_id}",
                summary=f"No suggestions from {len(outcomes)} outcomes.",
                context={"run_id": run_id, "outcomes_reviewed": len(outcomes)},
            )
    except Exception:
        pass

    # Phase 17: check if router retraining is needed
    try:
        from router import maybe_retrain
        maybe_retrain()
    except Exception:
        pass

    # Phase 46: intervention graduation — propose permanent rules for repeated patterns
    if not dry_run:
        try:
            from graduation import run_graduation
            _grad_count = run_graduation(verbose=verbose)
            if _grad_count and verbose:
                print(f"[evolver] graduation: {_grad_count} new permanent rule suggestion(s)", file=sys.stderr)
            log.debug("evolver graduation_pass: new_suggestions=%d", _grad_count)
        except Exception as _grad_exc:
            log.debug("graduation pass failed (non-fatal): %s", _grad_exc)

    # skill_candidate catch-up sweep (adversarial-review R1 batch-1 finding
    # #4) — flag_skill_candidate flags runs at curate_run time; this feeds
    # any still-unconsumed flags through extract_skills so they don't sit
    # unread forever. Passes through this cycle's `adapter` param when the
    # caller supplied one (e.g. loop_finalize.py's run-cadence trigger); the
    # heartbeat-driven autonomous cycle never does, so promote_skill_candidates
    # builds its own second MODEL_MID adapter there — not a correctness issue,
    # just not the single-adapter-per-cycle guarantee the old comment implied
    # (adversarial-review batch-1, Architect finding #6, 2026-07-13).
    if scan_skill_candidates:
        try:
            _sc_saved = promote_skill_candidates(adapter=adapter, dry_run=dry_run, verbose=verbose)
            if _sc_saved and verbose:
                print(f"[evolver] skill_candidate_sweep: {_sc_saved} skill(s) promoted", file=sys.stderr)
        except Exception as _sc_exc:
            log.debug("skill_candidate sweep failed (non-fatal): %s", _sc_exc)

    # FunSearch island model — anti-monoculture selection pressure on skill pool
    try:
        from skills import run_island_cycle
        _island_result = run_island_cycle(dry_run=dry_run, verbose=verbose)
        if _island_result.get("total_culled") and verbose:
            print(f"[evolver] island_cycle: culled {_island_result['total_culled']} underperforming skills",
                  file=sys.stderr)
        log.debug("evolver island_cycle: assigned=%d total_culled=%d",
                  _island_result.get("assigned", 0), _island_result.get("total_culled", 0))
    except Exception as _island_exc:
        log.debug("island cycle failed (non-fatal): %s", _island_exc)

    # VERIFY_LEARN_ARC V2: cadence verdicts + authority-aware auto-revert.
    # The longitudinal impact check is no longer warn-only — applied changes
    # now get the same lifecycle discipline lessons already have: an expectation
    # at birth (V1), a verdict at cadence, and demotion (revert) when
    # contradicted. Rides this cadence hook; no daemon. Confirmed changes feed
    # confidence calibration; degraded self-applied changes are reverted; a
    # human-applied change that degrades is surfaced, never overridden.
    if not dry_run:
        try:
            verify_applied_suggestions(run_id, verbose=verbose)
        except Exception as _verify_exc:
            log.debug("evolver cadence verdict pass failed (non-fatal): %s", _verify_exc)

    return report


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

def _verify_post_apply(applied_ids, run_id: str, *, verbose: bool = False) -> None:
    """Verify→learn: run test suite after auto-applying suggestions; auto-revert on failure.

    Closes the verify→learn loop by checking whether mutations broke anything.
    On test failure, iterates applied_ids and calls revert_suggestion on each —
    the self-improvement loop must not be able to make itself worse and stay there.

    Accepts either a list of suggestion IDs (preferred) or an int count
    (legacy; no revert possible).
    """
    import subprocess
    from pathlib import Path

    # Backward-compat: some callers/tests pass an int count.
    if isinstance(applied_ids, int):
        auto_applied = applied_ids
        id_list: List[str] = []
    else:
        id_list = list(applied_ids or [])
        auto_applied = len(id_list)

    if auto_applied <= 0:
        return

    repo_root = Path(__file__).parent.parent
    test_dir = repo_root / "tests"
    if not test_dir.is_dir():
        return

    log.info("verify_post_apply: running test suite after %d auto-applied mutations (run_id=%s)",
             auto_applied, run_id)

    # Throttled like scripts/test-safe.sh (BACKLOG batch-02): the evolver
    # fires at run finalize, i.e. while the box may be doing other work — an
    # unthrottled full suite pins all cores (and re-ran the ops-r2-05 leak
    # every cadence firing). nice + a core cap keep the verify pass a
    # background citizen; the wider timeout buys back the throttling.
    import shutil as _shutil
    _cmd: List[str] = []
    if _shutil.which("nice"):
        _cmd += ["nice", "-n", "15"]
    if _shutil.which("taskset"):
        _cmd += ["taskset", "-c", os.environ.get("TEST_CORES", "0,1")]
    _cmd += [sys.executable, "-m", "pytest", str(test_dir), "-q", "--tb=no", "-x"]
    _timeout_s = 900
    try:
        result = subprocess.run(
            _cmd,
            capture_output=True,
            text=True,
            timeout=_timeout_s,
            cwd=str(repo_root),
        )
        passed = result.returncode == 0
        # Extract pass count from output (e.g. "3553 passed, 5 skipped")
        summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    except subprocess.TimeoutExpired:
        passed = False
        summary = f"test suite timed out ({_timeout_s}s)"
    except Exception as exc:
        log.debug("verify_post_apply: test run failed: %s", exc)
        return

    reverted: List[dict] = []
    if passed:
        log.info("verify_post_apply: tests PASSED after %d mutations — %s", auto_applied, summary)
    else:
        log.warning("verify_post_apply: tests FAILED after %d mutations — %s", auto_applied, summary)
        # Auto-revert every auto-applied mutation. Leaving broken state in place means
        # self-improvement can make itself worse and stay that way.
        for sid in id_list:
            try:
                rv = revert_suggestion(sid)
                reverted.append({"suggestion_id": sid, **rv})
                if rv.get("reverted"):
                    log.info("verify_post_apply: reverted %s (%s): %s",
                             sid, rv.get("category"), rv.get("detail"))
                else:
                    log.warning("verify_post_apply: revert FAILED for %s: %s",
                                sid, rv.get("detail"))
            except Exception as exc:
                log.warning("verify_post_apply: revert raised for %s: %s", sid, exc)
                reverted.append({"suggestion_id": sid, "reverted": False, "detail": str(exc)})

    if verbose:
        _icon = "✓" if passed else "✗"
        _revert_note = f", reverted {sum(1 for r in reverted if r.get('reverted'))}/{len(reverted)}" if reverted else ""
        print(f"[evolver] verify→learn: tests {_icon} after {auto_applied} mutations{_revert_note}",
              file=sys.stderr, flush=True)

    # Record per-suggestion verification outcomes for confidence calibration
    _record_suggestion_outcomes(id_list, passed, run_id)

    # Record outcome as a lesson for the learning pipeline
    try:
        from memory_ledger import record_outcome
        _revert_summary = ""
        if reverted:
            _n_ok = sum(1 for r in reverted if r.get("reverted"))
            _revert_summary = f" Auto-reverted {_n_ok}/{len(reverted)} mutations."
        record_outcome(
            goal=f"evolver auto-apply ({auto_applied} suggestions, run {run_id})",
            status="done" if passed else "stuck",
            summary=f"Post-mutation test suite: {'PASSED' if passed else 'FAILED'}. {summary}{_revert_summary}",
            task_type="evolver_verify",
        )
    except Exception:
        pass

    # Captain's log
    try:
        from captains_log import log_event, EVOLVER_VERIFY
        log_event(
            event_type=EVOLVER_VERIFY,
            subject=f"run-{run_id}",
            summary=f"Post-mutation tests {'PASSED' if passed else 'FAILED'} after {auto_applied} auto-applied suggestions. {summary}",
            context={"run_id": run_id, "auto_applied": auto_applied, "passed": passed,
                     "summary": summary, "reverted": reverted},
        )
    except Exception:
        pass


def _notify_telegram(report: EvolverReport) -> None:
    try:
        from telegram_listener import telegram_notify
        lines = [f"🧠 *Maro Meta-Evolver* — {len(report.suggestions)} suggestions"]
        for fp in report.failure_patterns[:3]:
            lines.append(f"• Pattern: {fp}")
        for s in report.suggestions[:3]:
            lines.append(f"  [{s.category}] {s.suggestion[:100]}")
        msg = "\n".join(lines)
        telegram_notify(msg)
    except Exception as e:
        print(f"[evolver] telegram notify failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entry point (maro-evolver)
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI entry point for maro-evolver."""
    import argparse

    parser = argparse.ArgumentParser(description="Maro meta-evolver — analyze outcomes, manage suggestions")
    subparsers = parser.add_subparsers(dest="cmd")

    # Default: run evolver analysis
    run_p = subparsers.add_parser("run", help="Run evolver analysis on recent outcomes")
    run_p.add_argument("--dry-run", action="store_true", help="Analyze without writing suggestions")
    run_p.add_argument("--min-outcomes", type=int, default=3)
    run_p.add_argument("--window", type=int, default=50)
    run_p.add_argument("--notify", action="store_true")
    run_p.add_argument("--format", choices=["text", "json"], default="text")

    # List pending suggestions
    subparsers.add_parser("list", help="List pending (unapplied) suggestions")

    # Apply pending suggestions
    apply_p = subparsers.add_parser("apply", help="Apply pending suggestions (human-in-loop)")
    apply_p.add_argument("--all", action="store_true", help="Apply all pending (no confirmation)")
    apply_p.add_argument("--dry-run", action="store_true", help="Show what would be applied without doing it")
    apply_p.add_argument("id", nargs="?", help="Suggestion ID to apply (omit for interactive mode)")

    # Longitudinal impact analysis
    impact_p = subparsers.add_parser("impact", help="Show longitudinal impact of applied evolver suggestions")
    impact_p.add_argument("--lookback", type=int, default=24, help="Hours before apply event to sample (default 24)")
    impact_p.add_argument("--lookahead", type=int, default=24, help="Hours after apply event to sample (default 24)")
    impact_p.add_argument("--limit", type=int, default=10, help="Max apply events to analyze (default 10)")

    # VERIFY_LEARN_ARC V2 cadence verdicts — inspect (dry-run) or run for real.
    verify_p = subparsers.add_parser(
        "verify",
        help="Render cadence verdicts on applied changes (VERIFY_LEARN_ARC V2)")
    verify_p.add_argument(
        "--apply", action="store_true",
        help="Actually stamp/revert/park (default is dry-run: report only)")

    args = parser.parse_args()

    if args.cmd == "impact":
        records = scan_evolver_impact(
            lookback_hours=args.lookback,
            lookahead_hours=args.lookahead,
            limit=args.limit,
        )
        print(format_impact_summary(records))
        return 0

    if args.cmd == "verify":
        summary = verify_applied_suggestions(
            "cli", dry_run=not args.apply, verbose=True)
        if not summary.get("enabled", True):
            print("Cadence verdicts disabled (evolver.verify_cadence_verdicts=false).")
            return 0
        mode = "APPLIED" if args.apply else "dry-run (use --apply to act)"
        print(
            f"Cadence verdicts [{mode}]: "
            f"{summary.get('confirmed', 0)} confirmed, "
            f"{summary.get('reverted', 0)} reverted, "
            f"{summary.get('review_queued', 0)} review-queued, "
            f"{summary.get('unverifiable', 0)} unverifiable, "
            f"{summary.get('pending', 0)} pending "
            f"(of {summary.get('candidates', 0)} applied-unverified)."
        )
        return 0

    if args.cmd == "list" or args.cmd is None:
        # List pending suggestions (also default when no subcommand)
        pending = list_pending_suggestions(limit=50)
        if not pending:
            print("No pending suggestions.")
            return 0
        print(f"\nPending suggestions ({len(pending)}):\n")
        for s in pending:
            print(f"  [{s.suggestion_id}] {s.category:15s} conf={s.confidence:.0%}  {s.suggestion[:80]}")
        return 0

    if args.cmd == "apply":
        pending = list_pending_suggestions(limit=50)
        if not pending:
            print("No pending suggestions to apply.")
            return 0

        to_apply = pending
        if hasattr(args, "id") and args.id:
            to_apply = [s for s in pending if s.suggestion_id == args.id]
            if not to_apply:
                print(f"Suggestion {args.id!r} not found in pending list.")
                return 1

        if args.dry_run:
            print(f"dry_run: would apply {len(to_apply)} suggestion(s):")
            for s in to_apply:
                print(f"  [{s.suggestion_id}] {s.category}: {s.suggestion[:100]}")
            return 0

        if not getattr(args, "all", False):
            # Interactive review
            applied = 0
            for s in to_apply:
                print(f"\n[{s.suggestion_id}] {s.category} (conf={s.confidence:.0%})")
                print(f"  {s.suggestion}")
                resp = input("Apply? [y/N/q]: ").strip().lower()
                if resp == "q":
                    break
                if resp == "y":
                    processed = apply_suggestion(s.suggestion_id, manual=True)
                    if processed and suggestion_is_applied(s.suggestion_id):
                        print(f"  Applied.")
                        applied += 1
                    else:
                        print("  Not applied (blocked, held, action failed, or not found).")
            print(f"\nApplied {applied} suggestion(s).")
        else:
            applied = 0
            for s in to_apply:
                processed = apply_suggestion(s.suggestion_id, manual=True)
                if processed and suggestion_is_applied(s.suggestion_id):
                    applied += 1
            print(f"Applied {applied}/{len(to_apply)} suggestions.")
        return 0

    # run subcommand
    report = run_evolver(
        outcomes_window=getattr(args, "window", 50),
        min_outcomes=getattr(args, "min_outcomes", 3),
        dry_run=getattr(args, "dry_run", False),
        notify=getattr(args, "notify", False),
    )
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
