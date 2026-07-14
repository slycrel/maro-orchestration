"""Post-step processing for the agent loop (Tier 3 split of agent_loop.py).

Extracted verbatim from agent_loop.py — budget-ceiling continuation/escalation,
the too-broad/march-of-nines health signals, per-iteration artifact writes,
loop-interrupt handling (kill switch, wall-clock timeout, interrupt queue),
post-step observability/security/claim-verification/hooks, the ralph verify
loop, and the Phase F10 completed-step bookkeeping (scratchpad, context,
skill/cost attribution).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ancestry import Origin
from loop_types import LoopContext, StepOutcome, _orch, MAX_RESTART_DEPTH
from loop_artifacts import _write_plan_manifest
from loop_planning import _shape_steps
from loop_report import write_run_report as _write_run_report, write_runs_index as _write_runs_index
from step_exec import verify_step as _verify_step

log = logging.getLogger("maro.loop")

# Max ralph-verified lines per run in the thread brain's Compiled truth —
# verified claims are valuable, but a 20-step run shouldn't drown the brain
# (MILESTONES #3a volume filter).
_RALPH_TRUTH_CAP = 8


def _handle_budget_ceiling(
    ctx: LoopContext,
    step_outcomes: List[StepOutcome],
    remaining_steps: List[str],
    total_tokens_in: int,
    total_tokens_out: int,
    iteration: int,
    max_iterations: int,
    continuation_depth: int,
) -> Optional[str]:
    """Phase F1: Handle max_iterations budget ceiling.

    Returns stuck_reason suffix if continuation/escalation was enqueued, empty string otherwise.
    """
    log.warning("max_iterations reached: %d/%d steps done, %d remaining, tokens=%d",
                len(step_outcomes), len(step_outcomes) + len(remaining_steps),
                len(remaining_steps), total_tokens_in + total_tokens_out)

    _suffix = ""
    if remaining_steps:
        try:
            from task_store import enqueue as _ts_enqueue
            try:
                from runs import current_handle_id as _cur_hid
                _parent_handle = _cur_hid() or ""
            except Exception:
                _parent_handle = ""
            _origin = Origin(
                parent_loop_id=ctx.loop_id,
                parent_handle_id=_parent_handle,
                parent_goal=ctx.goal[:200],
            )
            _done_count = sum(1 for s in step_outcomes if s.status == "done")
            _done_summary = "; ".join(
                s.text[:80] for s in step_outcomes if s.status == "done"
            )
            _remaining_summary = "\n".join(
                f"- {s[:120]}" for s in remaining_steps[:10]
            )
            _next_depth = continuation_depth + 1

            _max_depth = int(os.environ.get(
                "MARO_MAX_CONTINUATION_DEPTH", str(MAX_RESTART_DEPTH)))

            if continuation_depth >= _max_depth:
                _esc_reason = (
                    f"ESCALATION — task has been through {continuation_depth} continuation "
                    f"pass(es) without completing.\n\n"
                    f"Original goal: {ctx.goal}\n\n"
                    f"Accomplished ({_done_count} steps):\n{_done_summary or '(none)'}\n\n"
                    f"Remaining ({len(remaining_steps)} steps):\n{_remaining_summary}\n\n"
                    f"Options: (1) enqueue a new continuation with continuation_depth="
                    f"{_next_depth} to keep going; (2) rewrite the goal to reduce scope; "
                    f"(3) accept the partial result as-is.\n"
                    f"Parent loop: {ctx.loop_id}"
                )
                _esc_task = _ts_enqueue(
                    lane="agenda",
                    source="loop_escalation",
                    reason=_esc_reason,
                    parent_job_id=ctx.loop_id,
                    continuation_depth=continuation_depth,
                    origin=_origin,
                )
                log.warning(
                    "budget_ceiling_escalation: depth=%d >= max=%d, escalated to %s "
                    "(parent=%s, %d steps done, %d remaining)",
                    continuation_depth, _max_depth, _esc_task["job_id"],
                    ctx.loop_id, _done_count, len(remaining_steps),
                )
                _suffix = (
                    f"; escalated (depth {continuation_depth} >= max {_max_depth}) "
                    f"as {_esc_task['job_id']}"
                )
            else:
                _cont_reason = (
                    f"CONTINUATION of: {ctx.goal}\n\n"
                    f"Pass {continuation_depth + 1} of a multi-pass task. "
                    f"Previous pass completed {_done_count}/{len(step_outcomes)} steps "
                    f"before hitting budget ceiling (max_iterations={max_iterations}).\n\n"
                    f"Accomplished so far:\n{_done_summary or '(none)'}\n\n"
                    f"Remaining work ({len(remaining_steps)} steps):\n{_remaining_summary}"
                )
                _cont_task = _ts_enqueue(
                    lane="agenda",
                    source="loop_continuation",
                    reason=_cont_reason,
                    parent_job_id=ctx.loop_id,
                    continuation_depth=_next_depth,
                    origin=_origin,
                )
                log.info(
                    "budget_ceiling_continuation: enqueued %s depth=%d with %d remaining "
                    "steps (parent=%s)",
                    _cont_task["job_id"], _next_depth, len(remaining_steps), ctx.loop_id,
                )
                _suffix = (
                    f"; continuation (depth {_next_depth}) enqueued as "
                    f"{_cont_task['job_id']}"
                )
        except Exception as _ce:
            log.warning("failed to enqueue continuation/escalation task: %s", _ce)

    return _suffix


_MARCH_OF_NINES_WINDOW = 5
_MARCH_OF_NINES_THRESHOLD = 0.5


# Per-step resource cap matching the `decomposition_too_broad` post-mortem
# note: a step that takes >120s AND >200K tokens has burned a step's worth
# of budget on a single sub-task, which implies the plan was decomposed too
# broadly. We emit a structured signal so the warning is visible mid-loop
# (BACKLOG:316 leftover — 8/8-strong loops never block, so the post-mortem
# is the only feedback today, and it only kicks in for the next loop).
_STEP_TOO_BROAD_ELAPSED_MS = 120_000
_STEP_TOO_BROAD_TOKENS = 200_000


def _check_step_too_broad(step_outcome: "StepOutcome") -> Optional[tuple]:
    """Return (elapsed_s, total_tokens, step_index) if the step breached the
    too-broad cap; None otherwise.

    Fires only on `done` steps — blocked/skipped steps are noisy on these
    metrics (a blocked step may have run hot before flagging stuck) and the
    `_handle_blocked_step` path already covers them via the diagnosis taxonomy.
    """
    if getattr(step_outcome, "status", "") != "done":
        return None
    elapsed_ms = getattr(step_outcome, "elapsed_ms", 0) or 0
    tokens = (getattr(step_outcome, "tokens_in", 0) or 0) + (
        getattr(step_outcome, "tokens_out", 0) or 0
    )
    if elapsed_ms > _STEP_TOO_BROAD_ELAPSED_MS and tokens > _STEP_TOO_BROAD_TOKENS:
        return (elapsed_ms // 1000, tokens, getattr(step_outcome, "index", -1))
    return None


def _compute_march_of_nines(step_outcomes: List["StepOutcome"]) -> Optional[tuple]:
    """Return (rate, completed, window_size) if recent-window success rate is
    below threshold; None if no alert should fire.

    Session 20 adversarial review finding 3.10: the previous formula was
       chain_success = (completed/attempted) ** attempted
    which fired false alerts on healthy long runs — a 90% step rate over
    8 steps looked like 0.43 (below the 0.5 threshold) and produced an
    alert despite the run being fine. The pathology: penalizing chain
    LENGTH rather than per-step rate degradation.

    New behavior: look at the last N steps and alert if the success rate
    within the window drops below threshold. Catches actual recent
    degradation without punishing otherwise-healthy long runs.
    """
    if len(step_outcomes) < 3:
        return None
    window = step_outcomes[-_MARCH_OF_NINES_WINDOW:]
    completed = sum(1 for s in window if s.status == "done")
    size = len(window)
    if size == 0:
        return None
    rate = completed / size
    if rate >= _MARCH_OF_NINES_THRESHOLD:
        return None
    return (rate, completed, size)


def _write_iteration_artifacts(
    ctx: LoopContext,
    step_text: str,
    step_status: str,
    outcome: dict,
    step_outcomes: List[StepOutcome],
    steps: List[str],
    manifest_steps: List[str],
    replan_count: int,
    start_ts: str,
    dead_ends_available: bool,
    update_dead_ends_fn=None,
) -> bool:
    """Write checkpoint, manifest, dead ends, march of nines after each step.

    Returns True if march_of_nines_alert was triggered.
    """
    o = _orch()

    # Checkpoint
    try:
        from checkpoint import write_checkpoint as _write_ckpt
        _write_ckpt(ctx.loop_id, ctx.goal, ctx.project or "", steps, step_outcomes)
    except Exception as _exc:
        # Affects loop resumability — silent loss means a crashed loop can't restart.
        log.warning("checkpoint write failed for loop %s: %s", ctx.loop_id, _exc)

    # Update plan manifest
    if ctx.project and manifest_steps:
        try:
            _write_plan_manifest(
                project=ctx.project,
                loop_id=ctx.loop_id,
                goal=ctx.goal,
                planned_steps=manifest_steps,
                start_ts=start_ts,
                step_outcomes=step_outcomes,
                replan_count=replan_count,
            )
        except Exception as _exc:
            log.warning("plan manifest update failed for loop %s: %s", ctx.loop_id, _exc)

        try:
            _write_run_report(
                project=ctx.project,
                loop_id=ctx.loop_id,
                goal=ctx.goal,
                planned_steps=manifest_steps,
                start_ts=start_ts,
                step_outcomes=step_outcomes,
                replan_count=replan_count,
            )
            _write_runs_index()
        except Exception as _exc:
            log.warning("run report update failed for loop %s: %s", ctx.loop_id, _exc)

    # Dead ends
    if step_status == "blocked" and dead_ends_available:
        try:
            _reason = outcome.get("stuck_reason", f"step blocked: {step_text[:80]}")
            _attempted = outcome.get("result", "")[:200]
            _dead_end_text = (
                f"Loop {ctx.loop_id} — Step: {step_text[:80]}\n"
                f"Reason: {_reason}\n"
                f"Attempted: {_attempted}"
            )
            update_dead_ends_fn(ctx.project, [_dead_end_text])
        except Exception as _exc:
            log.warning("dead_ends update failed for loop %s: %s", ctx.loop_id, _exc)

    # March of Nines — sliding-window success rate over recent steps.
    _window_result = _compute_march_of_nines(step_outcomes)
    _alert = False
    if _window_result is not None:
        rate, completed, size = _window_result
        _alert = True
        try:
            o.append_decision(ctx.project, [
                f"[loop:{ctx.loop_id}] March of Nines alert: "
                f"recent_success_rate={rate:.2f} "
                f"({completed}/{size} of last {size} steps done)"
            ])
        except Exception as _exc:
            log.debug("march-of-nines alert append failed for loop %s: %s", ctx.loop_id, _exc)

    # Step-too-broad signal — fires the moment a step breaches the cap, so
    # the warning is visible mid-loop in the per-run captain's-log slice
    # rather than only at post-mortem. The post-mortem path already feeds
    # the next decompose; this closes the visibility gap on the in-flight
    # loop (BACKLOG:316 leftover for the 8/8-strong case).
    if step_outcomes:
        _too_broad = _check_step_too_broad(step_outcomes[-1])
        if _too_broad is not None:
            _elapsed_s, _tokens, _step_idx = _too_broad
            try:
                from captains_log import log_event, STEP_TOO_BROAD
                log_event(
                    STEP_TOO_BROAD,
                    subject=f"loop:{ctx.loop_id}",
                    summary=(
                        f"Step {_step_idx} took {_elapsed_s}s with "
                        f"{_tokens // 1000}K tokens (cap: "
                        f"≤{_STEP_TOO_BROAD_ELAPSED_MS // 1000}s, "
                        f"≤{_STEP_TOO_BROAD_TOKENS // 1000}K)"
                    ),
                    context={
                        "step_index": _step_idx,
                        "elapsed_s": _elapsed_s,
                        "tokens": _tokens,
                        "cap_elapsed_s": _STEP_TOO_BROAD_ELAPSED_MS // 1000,
                        "cap_tokens": _STEP_TOO_BROAD_TOKENS,
                        "project": ctx.project or "",
                    },
                    loop_id=ctx.loop_id,
                )
            except Exception as _exc:
                log.debug("step_too_broad event append failed for loop %s: %s", ctx.loop_id, _exc)
            try:
                o.append_decision(ctx.project, [
                    f"[loop:{ctx.loop_id}] Step too broad: step {_step_idx} "
                    f"took {_elapsed_s}s / {_tokens // 1000}K tok — split if continuing"
                ])
            except Exception as _exc:
                log.debug("step_too_broad decision append failed for loop %s: %s", ctx.loop_id, _exc)

    return _alert


def _check_loop_interrupts(
    ctx: LoopContext,
    *,
    remaining_steps: List[str],
    remaining_indices: List[int],
    interrupt_queue,
    apply_interrupt_fn,
    goal: str,
    interrupts_applied: int,
) -> tuple:
    """Check kill switch, wall-clock timeout, and interrupt queue.

    Returns (loop_status, stuck_reason, goal, interrupts_applied, remaining_steps, remaining_indices).
    loop_status is "" if no interruption, "interrupted" if should break.
    """
    o = _orch()
    loop_status = ""
    stuck_reason = None

    # Kill switch
    try:
        from killswitch import is_active as _ks_active, read_reason as _ks_reason
        if _ks_active():
            _ks_msg = _ks_reason() or "kill switch engaged"
            log.warning("loop %s stopping — kill switch active: %s", ctx.loop_id, _ks_msg)
            loop_status = "interrupted"
            stuck_reason = f"kill switch: {_ks_msg}"
            if ctx.verbose:
                print("[maro] kill switch active — stopping loop", file=sys.stderr, flush=True)
    except Exception as _exc:
        # Safety mechanism — silent failure means a kill switch could be ignored.
        log.error("kill switch check FAILED for loop %s — safety mechanism may be compromised: %s",
                  ctx.loop_id, _exc)

    # Wall-clock timeout
    if not loop_status and ctx.loop_timeout_secs is not None:
        _elapsed_secs = time.monotonic() - ctx.started_at
        if _elapsed_secs >= ctx.loop_timeout_secs:
            log.warning("loop %s wall-clock timeout after %.0fs", ctx.loop_id, _elapsed_secs)
            loop_status = "interrupted"
            stuck_reason = f"wall-clock timeout ({ctx.loop_timeout_secs:.0f}s)"
            if ctx.verbose:
                print(f"[maro] wall-clock timeout after {_elapsed_secs:.0f}s — stopping", file=sys.stderr, flush=True)

    if loop_status:
        return loop_status, stuck_reason, goal, interrupts_applied, remaining_steps, remaining_indices

    # Interrupt polling
    if interrupt_queue is not None:
        try:
            pending = interrupt_queue.poll()
            for intr in pending:
                interrupts_applied += 1
                new_remaining, goal, should_stop = apply_interrupt_fn(
                    intr, remaining_steps, goal
                )
                if should_stop:
                    loop_status = "interrupted"
                    stuck_reason = f"stopped by {intr.source}: {intr.message[:80]}"
                    if ctx.verbose:
                        print(
                            f"[maro] interrupt: stop requested by {intr.source}",
                            file=sys.stderr, flush=True,
                        )
                    remaining_steps = []
                    remaining_indices = []
                    break
                else:
                    new_remaining = _shape_steps(new_remaining, label="interrupt")
                    added = [s for s in new_remaining if s not in remaining_steps]
                    if added:
                        new_idxs = o.append_next_items(ctx.project, added)
                        existing_count = len(remaining_steps)
                        remaining_steps = new_remaining
                        remaining_indices = remaining_indices[:existing_count] + new_idxs
                    else:
                        remaining_steps = new_remaining
                    o.append_decision(ctx.project, [
                        f"[loop:{ctx.loop_id}] interrupt({intr.intent}) from {intr.source}: {intr.message[:60]}",
                    ])
                    if ctx.verbose:
                        print(
                            f"[maro] interrupt({intr.intent}) from {intr.source}: {len(remaining_steps)} steps remaining",
                            file=sys.stderr, flush=True,
                        )
        except Exception as _exc:
            # Safety: silent failure means user-initiated interrupts (stop/pivot)
            # could be silently dropped while the loop keeps running.
            log.error("interrupt queue processing FAILED for loop %s — pending interrupts may be lost: %s",
                      ctx.loop_id, _exc)

    return loop_status, stuck_reason, goal, interrupts_applied, remaining_steps, remaining_indices


def _post_step_checks(
    ctx: LoopContext,
    step_text: str,
    step_idx: int,
    step_status: str,
    step_result: str,
    step_summary: str,
    step_elapsed: int,
    outcome: dict,
    *,
    security_available: bool,
    scan_content_fn=None,
    injection_risk_cls=None,
) -> tuple:
    """Phase F9: Post-step observability, security, claim verification, hooks.

    Returns (step_status, step_result, step_injected_context).
    May mutate outcome dict in place.
    """
    # Emit step event
    try:
        from observe import write_event
        write_event(
            "step_done" if step_status == "done" else "step_stuck",
            goal=ctx.goal,
            project=ctx.project or "",
            loop_id=ctx.loop_id,
            step=step_text,
            step_idx=step_idx,
            status=step_status,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            cache_read_tokens=outcome.get("cache_read_tokens", 0),
            model=getattr(ctx.adapter, "model_key", "") or "",
            elapsed_ms=step_elapsed,
            detail=step_summary[:200] if step_summary else "",
        )
    except Exception as _exc:
        log.debug("write_event(step_done/stuck) failed for step %d: %s", step_idx, _exc)

    # Security scan for prompt injection
    _has_external = "PRE-FETCHED" in step_text or "http" in step_text.lower()
    if security_available and _has_external and step_status == "done" and len(step_result) > 200:
        try:
            _scan = scan_content_fn(
                step_result,
                log_fn=lambda msg: print(f"[maro] {msg}", file=sys.stderr, flush=True),
            )
            if _scan.risk >= injection_risk_cls.HIGH:
                log.warning("step %d injection HIGH in result — redacting before context injection (signals=%s)",
                            step_idx, _scan.signals)
                step_result = _scan.sanitized
                outcome["result"] = step_result
        except Exception as _exc:
            # Security: silent failure means external content passes unscanned
            # into downstream LLM context. Fail loudly so it's visible.
            log.error("security injection scan FAILED for step %d — external content may pass unsanitized: %s",
                      step_idx, _exc)

    # Claim verifier on synthesis steps
    if step_status == "done" and step_result:
        try:
            from claim_verifier import (
                is_synthesis_step as _is_synth,
                verify_file_claims as _verify_files,
                verify_symbol_claims as _verify_symbols,
            )
            if _is_synth(step_text):
                _file_rep = _verify_files(step_result)
                _sym_rep = _verify_symbols(step_result)
                _has_halluc = (_file_rep.has_hallucinations
                               or _sym_rep.has_hallucinations)
                if _has_halluc:
                    # Inline annotation — mirrors claim_verifier.annotate_result
                    # so we don't re-run the checks twice.
                    _parts = []
                    if _file_rep.not_found:
                        _parts.append(
                            f"FILE_CLAIMS_NOT_FOUND: {', '.join(_file_rep.not_found)}")
                    if _sym_rep.not_found:
                        _parts.append(
                            f"SYMBOL_CLAIMS_NOT_FOUND: {', '.join(_sym_rep.not_found)}")
                    if _parts:
                        step_result = (step_result
                                       + "\n\n[claim-verifier] "
                                       + " | ".join(_parts))
                        outcome["result"] = step_result
                    log.warning(
                        "step %d [claim-verifier] hallucinated file/symbol claims detected",
                        step_idx)

                # Structured event so downstream analysis can compute
                # hallucination rate over time — not just a log warning.
                try:
                    from captains_log import log_event, CLAIM_VERIFIER_OUTCOME
                    _outcome_label = "hallucinations_annotated" if _has_halluc else "clean"
                    log_event(
                        CLAIM_VERIFIER_OUTCOME,
                        subject="claim_verifier",
                        summary=(
                            f"Step {step_idx}: {_outcome_label}"
                            + (f" (files={len(_file_rep.not_found)}, "
                               f"symbols={len(_sym_rep.not_found)})"
                               if _has_halluc else "")
                        ),
                        context={
                            "step_idx": step_idx,
                            "outcome": _outcome_label,
                            "action": ("annotated_and_continued"
                                       if _has_halluc else "none"),
                            "file_not_found": list(_file_rep.not_found)[:20],
                            "file_verified_count": len(_file_rep.verified),
                            "symbol_not_found": list(_sym_rep.not_found)[:20],
                            "symbol_verified_count": len(_sym_rep.verified),
                        },
                    )
                except Exception as _log_exc:
                    log.debug("CLAIM_VERIFIER_OUTCOME emit failed for step %d: %s",
                              step_idx, _log_exc)
        except Exception as _exc:
            log.warning("claim verifier failed for step %d (annotations skipped): %s", step_idx, _exc)

    # Step-level hooks
    _step_injected_context = ""
    if ctx.hook_registry is not None:
        try:
            from hooks import run_hooks as _run_hooks, any_blocking as _any_blocking, get_injected_context as _get_injected_ctx, SCOPE_STEP as _SCOPE_STEP
            _step_hook_ctx = {
                "goal": ctx.goal,
                "step": step_text,
                "step_result": step_result,
                "project": ctx.project,
                "step_num": step_idx,
            }
            _step_results = _run_hooks(
                _SCOPE_STEP, _step_hook_ctx,
                registry=ctx.hook_registry, adapter=ctx.adapter,
                dry_run=ctx.dry_run, fire_on="after",
            )
            if _any_blocking(_step_results):
                step_status = "blocked"
                _block_outputs = [r.output for r in _step_results if r.should_block]
                outcome["stuck_reason"] = "blocked by hook reviewer: " + "; ".join(_block_outputs[:2])
            _step_injected_context = _get_injected_ctx(_step_results)
        except Exception as _exc:
            # Correctness: hooks can BLOCK steps. If the hook system errors, a step
            # that should have been blocked proceeds as if approved. Surface loudly.
            log.error("step-level hook execution FAILED for step %d — should-block hooks may not have run: %s",
                      step_idx, _exc)

    return step_status, step_result, _step_injected_context


def _local_auto_ralph_enabled() -> bool:
    """Default the ralph verify loop ON when a usable local validator is
    configured (free verification). Cached + non-fatal."""
    try:
        import local_models as _lm
        return _lm.auto_verify_enabled()
    except Exception:
        return False


def _current_run_dir_safe():
    """current_run_dir() that never raises — returns None if unavailable.
    Used by decide-only instrumentation that must not perturb the loop."""
    try:
        from runs import current_run_dir
        return current_run_dir()
    except Exception:
        return None


def _record_loop_decision(source: str, trigger: str, action: str,
                          reasoning: str = "") -> bool:
    """Append a live mid-loop supervisor decision to the active thread's
    goal-brain Decisions section (Next Up #5 — per-turn maintenance).

    Today the director is the live mid-loop maintainer (Phase 64 adaptive
    execution); when the navigator goes per-turn it becomes the source at
    this same seam. Records only the bounded, high-signal course-corrections
    the director makes on its triggers (stuck / verify_failure /
    step_threshold), not per-iteration churn — so the Decisions trail reads as
    "thread opened → director replanned (why) → ... → closed". Never raises: a
    brain-write failure must not perturb the loop. Returns True if written."""
    try:
        from runs import current_run_dir
        from thread_brain import append_decision
        rd = current_run_dir()
        if rd is None:
            return False
        msg = f"{source} [{trigger}]: {action}"
        r = (reasoning or "").strip()
        if r:
            msg += f" — {r[:160]}"
        return bool(append_decision(rd, msg))
    except Exception:
        return False


def _run_ralph_verify(
    ctx: LoopContext,
    step_text: str,
    step_idx: int,
    step_result: str,
    step_status: str,
    outcome: dict,
    step_adapter,
    *,
    step_tier_overrides: Dict[str, str],
    session_verify_failures: int,
    session_tier_floor: str,
    verify_fail_threshold: int,
) -> tuple:
    """Phase F8: Ralph verify loop — check done step actually addressed its goal.

    Returns (step_status, step_result, session_verify_failures, session_tier_floor).
    May mutate outcome and step_tier_overrides dicts in place.
    """
    from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER

    try:
        _vr = _verify_step(step_text, step_result, step_adapter)
        if not _vr["passed"]:
            log.info("ralph verify FAIL step=%d reason=%r — marking blocked for retry",
                     step_idx, _vr["reason"][:80])
            # Per-step tier escalation on verify failure
            _vf_tier = getattr(step_adapter, "model_key", MODEL_CHEAP)
            if _vf_tier == MODEL_CHEAP:
                step_tier_overrides[step_text] = MODEL_MID
                log.info("step %d verify-fail tier-up: cheap → mid", step_idx)
            elif _vf_tier == MODEL_MID:
                step_tier_overrides[step_text] = MODEL_POWER
                log.info("step %d verify-fail tier-up: mid → power", step_idx)
            # Session-level lagging signal
            session_verify_failures += 1
            if (session_verify_failures >= verify_fail_threshold
                    and not session_tier_floor):
                _current_tier = getattr(ctx.adapter, "model_key", MODEL_CHEAP)
                if _current_tier == MODEL_CHEAP:
                    session_tier_floor = MODEL_MID
                    log.warning("session-level tier-up: %d consecutive verify failures → "
                                "raising floor to mid for remaining steps",
                                session_verify_failures)
                    if ctx.verbose:
                        print(f"[maro] session tier-up: {session_verify_failures} verify "
                              "failures → floor raised to mid",
                              file=sys.stderr, flush=True)
            if ctx.verbose:
                print(f"[maro] ralph verify: step {step_idx} RETRY — {_vr['reason'][:80]}",
                      file=sys.stderr, flush=True)
            outcome["status"] = "blocked"
            outcome["stuck_reason"] = f"[ralph verify] {_vr['reason']}"
            step_status = "blocked"
            step_result = outcome.get("result", "")
        else:
            session_verify_failures = 0
            # Compiled-truth half (MILESTONES #3a): a ralph PASS is a
            # verified claim — record it so mid-run consumers of the thread
            # brain (the navigator at blocked steps) see what is actually
            # done, not just claimed. Volume-conscious by construction: only
            # fires when ralph verify is enabled, one line per step, capped
            # per run.
            try:
                _rd = _current_run_dir_safe()
                if _rd is not None:
                    from thread_brain import append_compiled_truth, brain_path
                    _brain = brain_path(_rd)
                    _n_prior = (
                        _brain.read_text(encoding="utf-8").count("ralph-verified:")
                        if _brain.exists() else 0
                    )
                    if _n_prior < _RALPH_TRUTH_CAP:
                        append_compiled_truth(
                            _rd,
                            f"step {step_idx} ralph-verified: {step_text[:80]}",
                        )
            except Exception:
                pass
    except Exception:
        pass  # verify never blocks loop progress

    return step_status, step_result, session_verify_failures, session_tier_floor


def _process_done_step(
    ctx: LoopContext,
    step_text: str,
    step_idx: int,
    step_result: str,
    step_summary: str,
    step_elapsed: int,
    outcome: dict,
    item_index: int,
    iteration: int,
    *,
    completed_context: List[str],
    remaining_steps: List[str],
    remaining_indices: List[int],
    loop_shared_ctx: Dict[str, Any],
    scratchpad: Dict[str, Any],
    scratchpad_lock,
    step_model: Optional[str] = None,
) -> str:
    """Phase F10: Process a completed step — scratchpad, context, injection, skills.

    Returns the (possibly updated) step_result.
    """
    o = _orch()
    if item_index >= 0:
        try:
            o.mark_item(ctx.project, item_index, o.STATE_DONE)
        except OSError as _mark_exc:  # FileLockTimeout: ledger contended — the run result matters more than the checkbox
            log.warning("mark_item(DONE) failed for %s#%d: %s", ctx.project, item_index, _mark_exc)

    # Write to scratchpad
    if not isinstance(step_result, str):
        step_result = json.dumps(step_result)
        outcome["result"] = step_result
    _result_excerpt = step_result[:2000] if step_result else ""
    _cited_files: List[str] = []
    try:
        import re as _scratchpad_re
        _cited_files = sorted(set(
            _scratchpad_re.findall(r'\b([a-z_]+\.py)\b', step_result or "")
        ))
    except Exception as _exc:
        log.debug("scratchpad file citation extraction failed: %s", _exc)
    with scratchpad_lock:
        scratchpad[f"step_{step_idx}"] = {
            "text": step_text[:200],
            "summary": step_summary[:200],
            "result_excerpt": _result_excerpt,
            "files_cited": _cited_files[:20],
        }
        _all_files = scratchpad.get("shared", {}).get("files_found", [])
        _src_files = set(f.name for f in Path("src").glob("*.py")) if Path("src").exists() else set()
        _real_cited = [f for f in _cited_files if f in _src_files]
        _all_files = sorted(set(_all_files + _real_cited))
        scratchpad.setdefault("shared", {})["files_found"] = _all_files

    # Build context entry
    _ctx_excerpt = step_result[:800] if step_result else ""
    if len(step_result) > 800:
        _ctx_excerpt += f"\n... ({len(step_result)} chars total — full result in scratchpad step_{step_idx})"
    _step_confidence = outcome.get("confidence", "")
    _confidence_tag = f" [confidence:{_step_confidence}]" if _step_confidence else ""
    _ctx_entry = f"Step {step_idx} ({step_text[:80]}){_confidence_tag}:\n{_ctx_excerpt}"
    completed_context.append(_ctx_entry)

    # Environment snapshot
    _snap_key = f"step:{step_idx}:{step_text[:40]}"
    _snap_val = step_summary[:200] if step_summary else (step_result[:200] if step_result else "")
    if _snap_val:
        loop_shared_ctx[_snap_key] = _snap_val

    # Phase 62: Store structured artifacts in shared context
    _artifacts = outcome.get("artifacts")
    if _artifacts and isinstance(_artifacts, dict):
        for _art_name, _art_val in _artifacts.items():
            _art_key = f"artifact:{step_idx}:{_art_name}"
            loop_shared_ctx[_art_key] = _art_val
        log.info("step %d: stored %d artifact(s) in shared context", step_idx, len(_artifacts))

    # Mutable task graph: inject discovered steps
    _injected = outcome.get("inject_steps", [])
    if _injected and isinstance(_injected, list):
        _raw_injected = [str(s).strip() for s in _injected if str(s).strip()][:3]
        _clean_injected = _shape_steps(_raw_injected, label="inject")
        if _clean_injected:
            remaining_steps[:0] = _clean_injected
            remaining_indices[:0] = [-1] * len(_clean_injected)
            log.info("step %d injected %d step(s) into plan: %s",
                     step_idx, len(_clean_injected),
                     [s[:40] for s in _clean_injected])
            if ctx.verbose:
                for _s in _clean_injected:
                    print(f"[maro] injected step: {_s[:80]}", file=sys.stderr, flush=True)

    # Context compression
    _CTX_KEEP_FULL = 3
    _CTX_COMPRESS_AFTER = 5
    if len(completed_context) > _CTX_COMPRESS_AFTER:
        _old_entries = completed_context[:-_CTX_KEEP_FULL]
        _new_entries = completed_context[-_CTX_KEEP_FULL:]
        _compressed = []
        for _e in _old_entries:
            _header = _e.split("\n", 1)[0]
            _body_raw = _e.split("\n", 1)[1] if "\n" in _e else ""
            _body_short = _body_raw[:100].replace("\n", " ")
            if len(_body_raw) > 100:
                _body_short += "..."
            _compressed.append(f"{_header} [summary]: {_body_short}")
        completed_context[:] = _compressed + list(_new_entries)

    if ctx.verbose:
        print(f"[maro] step {step_idx} done: {step_summary[:120]}", file=sys.stderr, flush=True)

    # Phase 32: update skill utility + Phase 59: record skill cost/latency telemetry
    try:
        from skills import find_matching_skills, update_skill_utility, record_variant_outcome, record_skill_outcome
        from metrics import estimate_cost as _est_cost
        _confidence_val = {"strong": 1.0, "weak": 0.5, "inferred": 0.3, "unverified": 0.1}.get(
            outcome.get("confidence", ""), 1.0
        )
        _step_cost = _est_cost(
            int(outcome.get("tokens_in", 0)),
            int(outcome.get("tokens_out", 0)),
            step_model,
            cache_read_tokens=int(outcome.get("cache_read_tokens", 0)),
        )
        for _sk in find_matching_skills(step_text + " " + ctx.goal, use_router=False, project=ctx.project):
            update_skill_utility(_sk.id, success=True)
            if getattr(_sk, "variant_of", None) is not None:
                record_variant_outcome(_sk.id, success=True)
            # Phase 59: record cost/latency per skill invocation
            record_skill_outcome(
                _sk.id,
                success=True,
                cost_usd=_step_cost,
                latency_ms=float(step_elapsed),
                confidence=_confidence_val,
            )
    except Exception as _skill_attr_exc:
        log.debug("skill attribution failed for step %d (non-critical): %s", step_idx, _skill_attr_exc)

    # Phase 33: record per-step cost
    try:
        from metrics import record_step_cost
        record_step_cost(
            step_text=step_text,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            status="done",
            goal=ctx.goal,
            model=getattr(ctx.adapter, "model_key", ""),
            elapsed_ms=step_elapsed,
            cache_read_tokens=outcome.get("cache_read_tokens", 0),
            loop_id=getattr(ctx, "loop_id", "") or "",
        )
    except Exception as _cost_exc:
        log.debug("record_step_cost failed (non-critical): %s", _cost_exc)

    if ctx.step_callback is not None:
        try:
            ctx.step_callback(step_idx, step_text, step_summary, "done")
        except Exception as _cb_exc:
            log.debug("step_callback raised on step %d: %s", step_idx, _cb_exc)

    return step_result
