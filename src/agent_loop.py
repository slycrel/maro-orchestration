#!/usr/bin/env python3
# @lat: [[core-loop]]
"""Phase 1: Autonomous loop runner for Maro orchestration.

The critical unlock: give Maro a goal, watch it work until done or stuck.

Loop model:
    goal → decompose → for each step: [act → observe → decide] → done | stuck

Usage:
    from agent_loop import run_agent_loop
    result = run_agent_loop("research winning polymarket strategies", project="polymarket-research")
    print(result.summary())

CLI:
    python -m agent_loop "your goal here" [--project SLUG] [--model MODEL] [--dry-run]
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import time
import re as _re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

log = logging.getLogger("maro.loop")

# Core types, state machine, and the _orch/_project_dir_root/_configure_logging
# helpers moved to loop_types.py (Tier 3 split) — every other loop_*.py module
# needs those helpers, so they live in the one module everything else already
# needs first, avoiding a load-order cycle back through this file.
from loop_types import (
    _configure_logging,
    _orch,
    _project_dir_root,
    StepOutcome,
    step_from_decompose,
    LoopResult,
    LoopPhase,
    InvalidTransitionError,
    LoopContext,
    LoopStateMachine,
)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

# Prompts and tools — extracted to planner.py and step_exec.py for readability.
# Re-exported here for backward compatibility with existing imports.
from planner import DECOMPOSE_SYSTEM
from step_exec import (
    EXECUTE_SYSTEM, EXECUTE_TOOLS,
    execute_step as _execute_step,
    generate_refinement_hint as _generate_refinement_hint,
    verify_step as _verify_step,
    get_tools_for_role as _get_tools_for_role,
    _classify_step,
)
try:
    from tool_registry import PermissionContext as _PermissionContext, ROLE_WORKER as _ROLE_WORKER
except ImportError:
    _PermissionContext = None  # type: ignore[assignment,misc]
    _ROLE_WORKER = "worker"

_DECOMPOSE_SYSTEM = DECOMPOSE_SYSTEM
_EXECUTE_SYSTEM = EXECUTE_SYSTEM
_EXECUTE_TOOLS = EXECUTE_TOOLS


# ---------------------------------------------------------------------------
# Parallel fan-out helpers (Phase 35 P1)
# ---------------------------------------------------------------------------

# Dependency-pattern detection (_DEPENDENCY_PATTERNS, _DEP_RE,
# _steps_are_independent) moved to loop_planning.py (Tier 3 split).


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
            _origin = {
                "parent_loop_id": ctx.loop_id,
                "parent_handle_id": _parent_handle,
                "parent_goal": ctx.goal[:200],
            }
            _done_count = sum(1 for s in step_outcomes if s.status == "done")
            _done_summary = "; ".join(
                s.text[:80] for s in step_outcomes if s.status == "done"
            )
            _remaining_summary = "\n".join(
                f"- {s[:120]}" for s in remaining_steps[:10]
            )
            _next_depth = continuation_depth + 1

            _max_depth = int(os.environ.get("MARO_MAX_CONTINUATION_DEPTH", "4"))

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


# BlockedStepContext, _process_blocked_step moved to loop_blocked.py (Tier 3 split).


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
    except Exception:
        pass  # verify never blocks loop progress

    return step_status, step_result, session_verify_failures, session_tier_floor


# _run_parallel_batch, _run_parallel_path, _run_steps_parallel, and
# _run_steps_dag moved to loop_parallel.py (Tier 3 split).


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
        o.mark_item(ctx.project, item_index, o.STATE_DONE)

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


def _select_step_adapter(
    ctx: LoopContext,
    step_text: str,
    step_idx: int,
    *,
    step_tier_overrides: Dict[str, str],
    session_tier_floor: str,
    tier_order: dict,
):
    """Phase F5: Per-step model selection.

    Returns the adapter to use for this step (may be different from ctx.adapter).
    """
    from llm import build_adapter, MODEL_CHEAP, LLMAdapter as _LLMAdapterBase

    # Only re-tier adapters we know how to rebuild (build_adapter products all
    # subclass LLMAdapter). _DryRunAdapter and injected test doubles are plain
    # classes without model_key — they'd slip past the explicit-model check
    # below and get swapped for a live adapter (real LLM calls in dry runs).
    if ctx.dry_run or not isinstance(ctx.adapter, _LLMAdapterBase):
        return ctx.adapter

    adapter = ctx.adapter
    _step_adapter = adapter
    _explicit_model = getattr(adapter, "model_key", "") not in ("cheap", "mid", "power", "")
    if not _explicit_model:
        _tier_override = step_tier_overrides.get(step_text)
        if _tier_override:
            try:
                _step_adapter = build_adapter(model=_tier_override)
                if ctx.verbose:
                    _tier_name = {"cheap": "haiku", "mid": "sonnet", "power": "opus"}.get(_tier_override, _tier_override)
                    print(f"[maro] step {step_idx}: escalated to {_tier_name} (retry tier-up)", file=sys.stderr, flush=True)
            except Exception as _ta_exc:
                log.debug("tier-override adapter build failed for step %d, using default: %s", step_idx, _ta_exc)
        else:
            try:
                from conductor import classify_step_model
                _step_model = classify_step_model(step_text)
                if session_tier_floor and tier_order.get(_step_model, 0) < tier_order.get(session_tier_floor, 0):
                    _step_model = session_tier_floor
                if _step_model != adapter.model_key:
                    _step_adapter = build_adapter(model=_step_model)
                    if ctx.verbose:
                        _tier = "haiku" if _step_model == MODEL_CHEAP else "sonnet"
                        print(f"[maro] step {step_idx}: routing to {_tier} (classify_step_model)", file=sys.stderr, flush=True)
            except Exception as _cm_exc:
                log.debug("classify_step_model failed for step %d, using default: %s", step_idx, _cm_exc)
    return _step_adapter


def _build_result_and_finalize(
    ctx: LoopContext,
    *,
    step_outcomes: List[StepOutcome],
    loop_status: str,
    stuck_reason: Optional[str],
    total_tokens_in: int,
    total_tokens_out: int,
    interrupts_applied: int,
    march_of_nines_alert: bool,
    pf_review,
    manifest_steps: List[str],
    replan_count: int,
    start_ts: str,
    milestone_expanded: set,
    had_no_matching_skill: bool,
    failure_chain: List[str],
    recovery_step_count: int,
    scratchpad: Dict[str, Any],
    scratchpad_lock,
) -> LoopResult:
    """Phase G: Build final LoopResult, write artifacts, run finalize side-effects."""
    elapsed_total = int((time.monotonic() - ctx.started_at) * 1000)
    o = _orch()

    # Write final plan manifest with terminal status and elapsed time
    if ctx.project and manifest_steps:
        try:
            _write_plan_manifest(
                project=ctx.project,
                loop_id=ctx.loop_id,
                goal=ctx.goal,
                planned_steps=manifest_steps,
                start_ts=start_ts,
                step_outcomes=step_outcomes,
                status=loop_status,
                elapsed_ms=elapsed_total,
                replan_count=replan_count,
            )
        except Exception as _mf_exc:
            log.warning("plan manifest write failed (affects replay/debugging): %s", _mf_exc)

    log_path = _write_loop_log(
        project=ctx.project,
        loop_id=ctx.loop_id,
        goal=ctx.goal,
        status=loop_status,
        steps=step_outcomes,
        start_ts=start_ts,
        elapsed_ms=elapsed_total,
        stuck_reason=stuck_reason,
    )

    o.append_decision(ctx.project, [
        f"[loop:{ctx.loop_id}] finished status={loop_status} steps={len(step_outcomes)} tokens={total_tokens_in}+{total_tokens_out}",
    ])
    o.write_operator_status()

    # Phase 58: Pre-flight calibration feedback
    if pf_review is not None and not ctx.dry_run:
        try:
            from orch_items import memory_dir as _fb_memory_dir
            _pf_predicted_wide = pf_review.scope in ("wide", "deep")
            _actual_stuck = loop_status == "stuck"
            _steps_done = sum(1 for s in step_outcomes if s.status == "done")
            _fb_entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "loop_id": ctx.loop_id,
                "scope_predicted": pf_review.scope,
                "milestone_candidates": len(pf_review.milestone_step_indices),
                "milestones_expanded": len(milestone_expanded),
                "flag_count": len(pf_review.flags),
                "actual_status": loop_status,
                "steps_done": _steps_done,
                "steps_total": len(step_outcomes),
                "true_positive": _pf_predicted_wide and _actual_stuck,
                "false_positive": _pf_predicted_wide and not _actual_stuck,
                "false_negative": not _pf_predicted_wide and _actual_stuck,
                "true_negative": not _pf_predicted_wide and not _actual_stuck,
            }
            _fb_path = _fb_memory_dir() / "preflight_calibration.jsonl"
            with open(_fb_path, "a") as _fb_f:
                _fb_f.write(json.dumps(_fb_entry) + "\n")
            log.info("pre-flight calibration: scope=%s actual=%s tp=%s fp=%s fn=%s",
                     pf_review.scope, loop_status,
                     _fb_entry["true_positive"], _fb_entry["false_positive"],
                     _fb_entry["false_negative"])
        except Exception as _pf_exc:
            log.debug("pre-flight calibration feedback write failed: %s", _pf_exc)

    # Phase 36: emit loop_done event
    try:
        from observe import write_event as _write_event_done
        _write_event_done(
            "loop_done",
            goal=ctx.goal,
            project=ctx.project or "",
            loop_id=ctx.loop_id,
            status=loop_status,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            elapsed_ms=elapsed_total,
            detail=stuck_reason or "",
        )
    except Exception as _obs_exc:
        log.debug("loop_done observe event failed: %s", _obs_exc)

    result = LoopResult(
        loop_id=ctx.loop_id,
        project=ctx.project,
        goal=ctx.goal,
        status=loop_status,
        steps=step_outcomes,
        interrupts_applied=interrupts_applied,
        stuck_reason=stuck_reason,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        elapsed_ms=elapsed_total,
        log_path=log_path,
        march_of_nines_alert=march_of_nines_alert,
        pre_flight_review=pf_review,
    )

    # Write the loop transcript artifact: RESULT.md for a completed loop,
    # PARTIAL.md otherwise (the old unconditional -PARTIAL name made done
    # runs open with "Partial result ... Status: done" — BACKLOG 2026-06-11).
    _done_steps = [s for s in step_outcomes if s.status == "done"]
    if _done_steps:
        try:
            _transcript_kind = "RESULT" if loop_status == "done" else "PARTIAL"
            _partial_lines = [
                f"# {'Result' if loop_status == 'done' else 'Partial result'}: "
                f"{ctx.goal}\n"
            ]
            _partial_lines.append(f"Status: {loop_status} | "
                                  f"{len(_done_steps)}/{len(step_outcomes)} steps done | "
                                  f"tokens: {total_tokens_in+total_tokens_out} | "
                                  f"elapsed: {elapsed_total}ms\n")
            if stuck_reason:
                _partial_lines.append(f"Stuck reason: {stuck_reason}\n")
            _partial_lines.append("---\n")
            for _pos, s in enumerate(step_outcomes, start=1):
                _icon = "Done" if s.status == "done" else "BLOCKED"
                # s.index is the NEXT.md ledger line, not plan position — it
                # starts wherever the project ledger left off, so rendering it
                # as the step number read as "Step 11 of a 4-step plan".
                _partial_lines.append(f"\n## Step {_pos}/{len(step_outcomes)}"
                                      f" (ledger #{s.index}): {s.text[:100]}")
                _partial_lines.append(f"*[{_icon}]*\n")
                if s.result:
                    _partial_lines.append(s.result[:2000])
                    if len(s.result) > 2000:
                        _partial_lines.append(f"\n... (truncated, {len(s.result)} chars total)")
                _partial_lines.append("")
            try:
                from runs import artifact_dir as _runs_artifact_dir
                _art_dir = _runs_artifact_dir(ctx.project, project_root_fn=_project_dir_root)
            except Exception:
                _art_dir = _project_dir_root() / ctx.project / "artifacts"
                _art_dir.mkdir(parents=True, exist_ok=True)
            (_art_dir / f"loop-{ctx.loop_id}-{_transcript_kind}.md").write_text(
                "\n".join(_partial_lines), encoding="utf-8")
            log.info("wrote loop transcript: %s (%d steps)",
                     f"loop-{ctx.loop_id}-{_transcript_kind}.md", len(_done_steps))
            # Persist scratchpad
            _scratch_dir = _art_dir / f"loop-{ctx.loop_id}-scratchpad"
            _scratch_dir.mkdir(exist_ok=True)
            with scratchpad_lock:
                for _sk, _sv in scratchpad.items():
                    (_scratch_dir / f"{_sk}.json").write_text(
                        json.dumps(_sv, indent=2, default=str), encoding="utf-8")
                (_scratch_dir / "index.json").write_text(
                    json.dumps({"keys": list(scratchpad.keys())}, indent=2), encoding="utf-8")
        except Exception as exc:
            log.debug("partial result write failed: %s", exc)

    if ctx.verbose:
        print(f"[maro] {result.summary()}", file=sys.stderr, flush=True)

    _finalize_loop(
        loop_id=ctx.loop_id,
        goal=ctx.goal,
        project=ctx.project,
        loop_status=loop_status,
        step_outcomes=step_outcomes,
        adapter=ctx.adapter,
        dry_run=ctx.dry_run,
        verbose=ctx.verbose,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        elapsed_ms=elapsed_total,
        had_no_matching_skill=had_no_matching_skill,
        failure_chain=failure_chain,
        recovery_steps=recovery_step_count,
    )

    # Delete checkpoint on successful completion
    if result.status == "done":
        try:
            from checkpoint import delete_checkpoint as _del_ckpt
            _del_ckpt(ctx.loop_id)
        except Exception as _ckpt_exc:
            log.debug("checkpoint delete failed: %s", _ckpt_exc)

    # Artifact cleanup: per-step artifacts are temp by default.
    # Only keep them if config `keep_artifacts: true` is set.
    # Plan manifests, RESULT/PARTIAL.md, loop logs, and scratchpad are always kept.
    if not ctx.dry_run and ctx.project:
        try:
            from config import get as _cfg_get
            _keep = bool(_cfg_get("keep_artifacts", False))
        except Exception:
            _keep = False
        if not _keep:
            try:
                try:
                    from runs import artifact_dir as _runs_artifact_dir
                    _art_dir = _runs_artifact_dir(ctx.project, project_root_fn=_project_dir_root)
                except Exception:
                    _art_dir = _project_dir_root() / ctx.project / "artifacts"
                _deleted = 0
                for _f in _art_dir.glob(f"loop-{ctx.loop_id}-step-*.md"):
                    try:
                        _f.unlink()
                        _deleted += 1
                    except OSError:
                        pass
                if _deleted:
                    log.debug("artifact cleanup: deleted %d per-step artifact(s) "
                              "(set keep_artifacts: true to retain)", _deleted)
            except Exception as _art_exc:
                log.debug("artifact cleanup failed: %s", _art_exc)

    # Release loop lock
    try:
        from interrupt import clear_loop_running
        clear_loop_running()
    except Exception as _lock_exc:
        log.debug("clear_loop_running failed: %s", _lock_exc)

    # Signal heartbeat to wake immediately — pick up next queued task without
    # waiting for the full interval tick.  Reduces task-to-task latency from
    # up to interval seconds to near-zero.
    try:
        from heartbeat import post_heartbeat_event as _phb_event
        _phb_event(event_type="loop_done", payload=(ctx.project or ""))
    except Exception as _phb_exc:
        log.debug("post_heartbeat_event(loop_done) failed: %s", _phb_exc)

    return result


# _prepare_execution moved to loop_planning.py (Tier 3 split).


# _preflight_checks and _decompose_goal moved to loop_planning.py (Tier 3 split).


def _budget_gate(ctx, *, goal: str, project: Optional[str], dry_run: bool):
    """Budget gates (substrate-trial hardening, 2026-07-01). Two layers:

    - per-run: callers rarely pass cost_budget, so an unattended run was
      uncapped — config ``budget.per_run_usd`` supplies the default (an
      explicit caller arg still wins). Enforced mid-loop by the existing
      cost hard-stop.
    - daily: per-run caps don't stop a substrate burning through runs one
      under-cap loop at a time — ``budget.daily_usd`` gates on the cross-run
      spend ledger (metrics.spend_today) before any tokens are spent.

    Both unset by default = old behavior. dry_run skips (burns nothing).
    Returns a stuck LoopResult to refuse the run, or None to proceed.
    Never raises.
    """
    if dry_run:
        return None
    try:
        from config import get as _budget_get
        if ctx.cost_budget is None:
            _per_run = _budget_get("budget.per_run_usd", None)
            if _per_run is not None:
                ctx.cost_budget = float(_per_run)
                log.info("cost_budget defaulted from config: $%.2f", ctx.cost_budget)
        _daily_cap = _budget_get("budget.daily_usd", None)
        if _daily_cap is not None:
            import metrics as _metrics
            _spent = _metrics.spend_today()
            if _spent >= float(_daily_cap):
                _msg = (f"daily budget exhausted: ${_spent:.2f} spent today >= "
                        f"budget.daily_usd ${float(_daily_cap):.2f} — refusing to start; "
                        f"resets at UTC midnight")
                log.warning("loop refused to start — %s", _msg)
                try:
                    from notify import emit as _budget_notify
                    _budget_notify("escalation", {
                        "handle_id": "", "goal": goal[:200], "status": "stuck",
                        "summary": _msg, "reason": "daily budget gate",
                        "point": "budget_gate",
                    })
                except Exception:
                    pass
                return LoopResult(
                    loop_id=ctx.loop_id,
                    goal=goal,
                    project=project or "",
                    steps=[],
                    status="stuck",
                    stuck_reason=_msg,
                    total_tokens_in=0,
                    total_tokens_out=0,
                    elapsed_ms=0,
                    log_path=None,
                )
    except Exception as _budget_exc:
        log.debug("budget gate check failed (non-blocking): %s", _budget_exc)
    return None


def _initialize_loop(
    goal: str,
    *,
    project: Optional[str],
    repo_path: str = "",
    model: Optional[str],
    backend: Optional[str],
    adapter,
    dry_run: bool,
    verbose: bool,
    interrupt_queue,
    hook_registry,
    ancestry_context_extra: str,
    permission_context,
    continuation_depth: int,
    cost_budget: Optional[float],
    token_budget: Optional[int],
    ralph_verify: bool,
    max_steps: int,
    max_iterations: int,
    step_callback,
    loop_reason: str = "initial",
    parent_loop_id: Optional[str] = None,
) -> tuple:
    """Phase A: Initialize loop — setup adapter, project, ancestry, hooks.

    Returns (ctx: LoopContext, early_return: Optional[LoopResult]).
    If early_return is not None, caller should return it immediately.
    """
    from llm import build_adapter
    from interrupt import InterruptQueue, set_loop_running
    from conductor import assign_model_by_role

    ctx = LoopStateMachine()
    ctx.goal = goal
    ctx.verbose = verbose
    ctx.dry_run = dry_run
    ctx.max_iterations = max_iterations
    ctx.continuation_depth = continuation_depth
    ctx.ralph_verify = ralph_verify
    ctx.step_callback = step_callback
    ctx.cost_budget = cost_budget
    ctx.token_budget = token_budget
    ctx.repo_path = repo_path or ""

    ctx.loop_id = str(uuid.uuid4())[:8]
    ctx.started_at = time.monotonic()
    ctx.start_ts = datetime.now(timezone.utc).isoformat()

    _configure_logging(verbose)

    log.info("loop_start loop_id=%s goal=%r project=%s max_steps=%d reason=%s parent=%s",
             ctx.loop_id, goal[:80], project or "(auto)", max_steps,
             loop_reason, parent_loop_id or "-")

    try:
        from captains_log import log_event, LOOP_CREATED
        log_event(
            LOOP_CREATED,
            subject=goal[:120],
            summary=f"reason={loop_reason} project={project or '(auto)'} max_steps={max_steps}",
            context={
                "reason": loop_reason,
                "parent_loop_id": parent_loop_id,
                "project": project or "",
                "max_steps": max_steps,
                "continuation_depth": continuation_depth,
                "dry_run": dry_run,
            },
            loop_id=ctx.loop_id,
            related_ids=[parent_loop_id] if parent_loop_id else None,
        )
    except Exception as _ev_exc:
        log.debug("captains_log LOOP_CREATED emit failed: %s", _ev_exc)

    # Kill switch check — refuse to start if sentinel is present
    try:
        from killswitch import is_active as _ks_active, read_reason as _ks_reason
        if _ks_active():
            _ks_msg = _ks_reason() or "kill switch engaged"
            log.warning("loop refused to start — kill switch active: %s", _ks_msg)
            return ctx, LoopResult(
                loop_id=ctx.loop_id,
                goal=goal,
                project=project or "",
                steps=[],
                status="interrupted",
                stuck_reason=f"kill switch active: {_ks_msg}",
                total_tokens_in=0,
                total_tokens_out=0,
                elapsed_ms=0,
                log_path=None,
            )
    except Exception as _ks_exc:
        log.debug("killswitch check failed (non-blocking): %s", _ks_exc)

    _budget_refusal = _budget_gate(ctx, goal=goal, project=project, dry_run=dry_run)
    if _budget_refusal is not None:
        return ctx, _budget_refusal

    # Wall-clock timeout — default 2 hours, override via MARO_LOOP_TIMEOUT_SECS
    try:
        ctx.loop_timeout_secs = float(os.environ.get("MARO_LOOP_TIMEOUT_SECS", "7200"))
    except (ValueError, TypeError):
        ctx.loop_timeout_secs = 7200.0

    if verbose:
        print(f"[maro] loop_id={ctx.loop_id} goal={goal!r}", file=sys.stderr, flush=True)

    # Resolve tool set from PermissionContext (Phase 41 — prompt-composition-time gating)
    ctx.perm_ctx = permission_context
    if ctx.perm_ctx is None and _PermissionContext is not None:
        ctx.perm_ctx = _PermissionContext(role=_ROLE_WORKER)

    # Build adapter — worker role uses MODEL_MID by default (role-semantic selection)
    if adapter is None and not dry_run:
        _build_kw: dict = {"model": model or assign_model_by_role("worker")}
        if backend:
            _build_kw["backend"] = backend
        ctx.adapter = build_adapter(**_build_kw)
    elif dry_run:
        ctx.adapter = _DryRunAdapter()
    else:
        ctx.adapter = adapter

    # Set up interrupt queue — auto-create if not provided
    if interrupt_queue is None:
        try:
            ctx.interrupt_queue = InterruptQueue()
        except Exception as _iq_exc:
            log.debug("InterruptQueue init failed, running without interrupt support: %s", _iq_exc)
            ctx.interrupt_queue = None
    else:
        ctx.interrupt_queue = interrupt_queue

    # Resolve or create project
    # Always call ensure_project (idempotent) — guards against partially-initialized
    # projects where the dir exists but NEXT.md was never written.
    o = _orch()
    if project:
        _proj_existed = o.project_dir(project).exists()
        o.ensure_project(project, goal[:80])
        if verbose and not _proj_existed:
            print(f"[maro] created project={project}", file=sys.stderr, flush=True)
    else:
        project = _goal_to_slug(goal)
        _proj_existed = o.project_dir(project).exists()
        o.ensure_project(project, goal[:80])
        if verbose and not _proj_existed:
            print(f"[maro] created project={project}", file=sys.stderr, flush=True)
    ctx.project = project

    # Advertise this loop as running so other interfaces can route interrupts
    # Must be after ctx.project is set so the per-project lockfile is written correctly
    try:
        set_loop_running(ctx.loop_id, goal, project=ctx.project)
    except Exception as _slr_exc:
        log.debug("set_loop_running failed: %s", _slr_exc)

    # Load goal ancestry for prompt injection
    try:
        from ancestry import get_project_ancestry, build_ancestry_prompt
        _proj_dir = o.project_dir(project)
        _ancestry = get_project_ancestry(_proj_dir)
        ctx.ancestry_context = build_ancestry_prompt(_ancestry, current_task=goal)
    except Exception as _anc_exc:
        log.debug("ancestry context load failed: %s", _anc_exc)
        ctx.ancestry_context = ""

    # Continuation depth awareness: let the planner know this is pass N of a large task.
    if continuation_depth > 0:
        _depth_note = (
            f"CONTINUATION PASS {continuation_depth}: This loop is a continuation of a larger "
            f"task that exceeded budget in a prior pass. Decompose narrowly — focus on the "
            f"remaining work described in the goal, not the full original scope."
        )
        ctx.ancestry_context = (
            (ctx.ancestry_context + "\n\n" + _depth_note) if ctx.ancestry_context else _depth_note
        )

    # Merge injected context from mission-level notification hooks (Phase 11)
    if ancestry_context_extra:
        ctx.ancestry_context = (
            (ctx.ancestry_context + "\n\n" + ancestry_context_extra)
            if ctx.ancestry_context
            else ancestry_context_extra
        )

    # Load hook registry for step-level hooks (Phase 11)
    ctx.hook_registry = hook_registry
    if ctx.hook_registry is None:
        try:
            from hooks import load_registry as _load_registry
            ctx.hook_registry = _load_registry()
        except Exception as _hr_exc:
            log.debug("hook registry load failed: %s", _hr_exc)
            ctx.hook_registry = None

    return ctx, None


# Convergence tracking, missing-input checks, _BlockDecision, timeout-split
# generation, diagnosis consult, and _handle_blocked_step moved to
# loop_blocked.py (Tier 3 split).


def _finalize_loop(
    loop_id: str,
    goal: str,
    project: str,
    loop_status: str,
    step_outcomes: List["StepOutcome"],
    adapter,
    *,
    dry_run: bool,
    verbose: bool,
    total_tokens_in: int,
    total_tokens_out: int,
    elapsed_ms: int,
    had_no_matching_skill: bool,
    failure_chain: Optional[List[str]] = None,
    recovery_steps: int = 0,
) -> None:
    """Run all post-loop side effects after the main execution loop ends.

    Handles: Reflexion/memory recording, skill crystallisation, skill synthesis.
    All failures are swallowed — post-loop side effects must never raise.
    """
    _done = sum(1 for s in step_outcomes if s.status == "done")
    _blocked = sum(1 for s in step_outcomes if s.status == "blocked")
    log.info("loop_end loop_id=%s status=%s steps=%d/%d(done/blocked) tokens=%d elapsed=%dms",
             loop_id, loop_status, _done, _blocked,
             total_tokens_in + total_tokens_out, elapsed_ms)

    # Phase 44-45: Self-reflection — auto-diagnose + lenses + recovery plan
    try:
        from introspect import diagnose_loop as _diagnose, save_diagnosis as _save_diag
        from introspect import run_lenses as _run_lenses, aggregate_lenses as _aggregate
        from introspect import plan_recovery as _plan_recovery
        from introspect import _build_step_profiles, _load_loop_events
        # NOTE: `project` is the local param — a `ctx.project` here was a
        # NameError that silently killed this whole block for six weeks
        # (2026-04-26 → session 40); the outer except swallowed it every run.
        _diag = _diagnose(loop_id, project=project or "")
        _save_diag(_diag)
        if _diag.failure_class != "healthy":
            log.warning("introspect: %s", _diag.summary())
            # Run heuristic lenses on non-healthy loops
            _events = _load_loop_events(loop_id)
            _profiles = _build_step_profiles(_events)
            _lens_results = _run_lenses(_diag, _profiles)
            for _lr in _lens_results:
                if _lr.action:
                    log.warning("lens[%s]: %s", _lr.lens_name, _lr.action)
            # Aggregated synthesis
            if _lens_results:
                _agg = _aggregate(_diag, _lens_results)
                log.info("synthesis: confidence=%.0f%% agreement=%d action=%s",
                         _agg.confidence * 100, _agg.lens_agreement, _agg.primary_action)
            # Recovery plan
            _recovery = _plan_recovery(_diag, use_advisor=True)
            if _recovery:
                _tag = "AUTO-RECOVERABLE" if _recovery.auto_apply else "NEEDS-REVIEW"
                log.warning("recovery[%s] risk=%s: %s", _tag, _recovery.risk, _recovery.action)
                # M3 (session 40): the plan itself is a recovery insight —
                # record it typed so the next similar run gets it injected at
                # decompose time instead of re-deriving it from a fresh
                # failure. Stable text (failure_class + table action) means
                # recurring plans reinforce via near-duplicate dedup rather
                # than duplicating, feeding the standing-rule pipeline.
                if not dry_run:
                    try:
                        from memory import record_tiered_lesson as _record_lesson
                        _record_lesson(
                            lesson_text=f"[recovery-plan] {_diag.failure_class}: {_recovery.action}",
                            task_type="agenda",
                            outcome=loop_status,
                            source_goal=goal[:120],
                            confidence=0.5,  # suggested, not yet verified by a completed run
                            lesson_type="recovery",
                        )
                    except Exception as _rp_exc:
                        log.debug("recovery-plan lesson record failed: %s", _rp_exc)
        # Inject diagnosis-derived lessons directly into memory
        # so the planner sees them via inject_lessons_for_task on the next run
        if _diag.failure_class != "healthy":
            try:
                from memory import _store_lesson
                _diag_lesson = (
                    f"[auto-diagnosis] {_diag.failure_class}: {_diag.recommendation}"
                )
                _store_lesson(
                    task_type="agenda",
                    outcome=_diag.failure_class,
                    lesson=_diag_lesson,
                    source_goal=goal[:120],
                    confidence=0.8,
                )
                log.info("injected diagnosis lesson: %s", _diag.failure_class)
            except Exception as _store_exc:
                log.warning("failed to persist diagnosis lesson (learning data lost): %s", _store_exc)
    except Exception as exc:
        log.debug("introspect failed: %s", exc)

    # M3 (session 40): a completed run that needed recovery actions is a
    # *verified* recovery — the failure_chain says what went wrong and which
    # metacognitive action fixed it. Record it typed ("recovery") at higher
    # confidence than LLM-extracted lessons: the run finishing IS the
    # verification. Recurring identical recoveries reinforce via dedup.
    if not dry_run and loop_status == "done" and recovery_steps > 0 and failure_chain:
        try:
            from memory import record_tiered_lesson as _record_lesson
            _kind_markers = (
                ("re-decomposing", "re-decompose"),
                ("split", "step-split"),
                ("retry", "retry-with-hint"),
            )
            _kinds = sorted({k for e in failure_chain for m, k in _kind_markers if m in e})
            _record_lesson(
                lesson_text=(
                    f"[recovery-verified] {', '.join(_kinds) or 'recovery'} unblocked a run: "
                    f"{failure_chain[0][:100]}"
                ),
                task_type="agenda",
                outcome="done",
                source_goal=goal[:120],
                confidence=0.7,  # verified — the run completed after the recovery
                lesson_type="recovery",
            )
            log.info("recorded verified-recovery lesson (%d recovery steps)", recovery_steps)
        except Exception as _vr_exc:
            log.debug("verified-recovery lesson record failed: %s", _vr_exc)

    # Phase 5: Reflexion — record outcome + extract lessons
    try:
        from memory import reflect_and_record, record_step_trace
        done_steps = [s for s in step_outcomes if s.status == "done"]
        summary = (
            f"Completed {len(done_steps)}/{len(step_outcomes)} steps. "
            + (step_outcomes[-1].result[:80] if step_outcomes and loop_status == "done" else "")
        )
        _outcome_rec = reflect_and_record(
            goal=goal,
            status=loop_status,
            result_summary=summary,
            task_type="agenda",
            project=project,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            elapsed_ms=elapsed_ms,
            model=getattr(adapter, "model_key", ""),
            adapter=adapter if not dry_run else None,
            dry_run=dry_run,
            failure_chain=failure_chain or [],
            recovery_steps=recovery_steps,
        )
        # Meta-Harness steal: persist step-level traces so the evolver proposer
        # sees full execution context, not just aggregate summaries.
        if not dry_run and step_outcomes and _outcome_rec is not None:
            try:
                record_step_trace(
                    _outcome_rec.outcome_id,
                    goal,
                    step_outcomes,
                    task_type="agenda",
                )
            except Exception as _trace_exc:
                log.debug("record_step_trace failed (non-critical): %s", _trace_exc)
    except Exception as _reflect_exc:
        log.warning("reflect_and_record failed — run %s produced no learning data: %s", loop_id, _reflect_exc)

    # Auto-extract skills from successful loops (crystallise patterns)
    if loop_status == "done" and not dry_run and step_outcomes:
        try:
            from skills import extract_skills, save_skill, load_skills
            done_summaries = [s.result[:200] for s in step_outcomes if s.status == "done" and s.result]
            outcome_for_extraction = {
                "goal": goal,
                "status": loop_status,
                "task_type": "agenda",
                "summary": ". ".join(done_summaries[:4]),
                "steps": [
                    {"step": s.text, "status": s.status, "result": s.result[:200]}
                    for s in step_outcomes
                ],
                "project": project,
            }
            existing_skills = {s.name for s in load_skills()}
            extracted = extract_skills([outcome_for_extraction], adapter if adapter else None)
            for skill in extracted:
                if skill.name not in existing_skills:
                    save_skill(skill)
                    if verbose:
                        print(f"[maro] skill crystallised: {skill.name}", file=sys.stderr, flush=True)
        except Exception as _skill_exc:
            log.warning("skill extraction failed — loop %s may not contribute to skill library: %s", loop_id, _skill_exc)

    # Phase 32: skill synthesis — when no skill matched at start, synthesize from this run
    if loop_status == "done" and had_no_matching_skill and not dry_run and step_outcomes:
        try:
            from evolver import synthesize_skill
            done_steps = [s for s in step_outcomes if s.status == "done" and s.result]
            _synth_summary = ". ".join(s.result[:120] for s in done_steps[:3])
            synthesize_skill(
                goal=goal,
                outcome_summary=_synth_summary or "completed successfully",
                source_loop_id=loop_id,
                adapter=adapter,
                verbose=verbose,
            )
        except Exception as _synth_exc:
            log.warning("skill synthesis failed — loop %s: %s", loop_id, _synth_exc)

    # Phase 32: auto-promote skills that meet threshold (don't wait for evolver heartbeat)
    if not dry_run:
        try:
            from evolver import run_skill_maintenance
            run_skill_maintenance()
        except ImportError:
            pass
        except Exception as _maint_exc:
            log.debug("skill maintenance failed (non-critical): %s", _maint_exc)

    # Post-mission Telegram notification
    if not dry_run:
        try:
            from telegram_listener import telegram_notify
            _done_count = sum(1 for s in step_outcomes if s.status == "done")
            _total_tokens = total_tokens_in + total_tokens_out
            _status_icon = "✅" if loop_status == "done" else ("⚠️" if loop_status == "partial" else "❌")
            _msg = (
                f"{_status_icon} *Mission complete* — `{project or goal[:40]}`\n"
                f"Status: {loop_status} | Steps: {_done_count}/{len(step_outcomes)} done\n"
                f"Tokens: {_total_tokens:,} | Time: {elapsed_ms // 1000}s"
            )
            telegram_notify(_msg)
        except Exception as _tg_exc:
            log.debug("post-mission Telegram notification failed (non-critical): %s", _tg_exc)


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def _run_scoped_validator(fn):
    """Own the local validator's lifecycle for the whole run: spin it up at the
    start (if it'll be used) and tear down what this run started at the end —
    on completion or failure. Reused/external/parent-run servers are left alone.
    Non-fatal: any lifecycle hiccup just falls through to lazy per-step spin-up.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Configure logging up front (idempotent) so the run-start validator
        # spin-up is visible — otherwise it fires before the in-body setup.
        _configure_logging(kwargs.get("verbose", False))
        goal = args[0] if args else kwargs.get("goal", "")
        ralph = kwargs.get("ralph_verify", False)
        try:
            import local_models as _lm
            cm = _lm.managed_for_run(goal, ralph)
        except Exception:
            return fn(*args, **kwargs)
        with cm:
            return fn(*args, **kwargs)
    return wrapper


@_run_scoped_validator
def _execute_main_loop(
    ctx: LoopContext,
    steps: List[str],
    step_indices: List[int],
    *,
    resume_completed: List[StepOutcome],
    prereq_context: Dict[int, str],
    pf_review,
    levels,
    manifest_steps: List[str],
    replan_count: int,
    loop_shared_ctx: Dict[str, Any],
    resolve_tools_fn,
    tier_order: Dict[str, int],
    parallel_fan_out: int,
) -> dict:
    """Phase F: the main execute loop.

    Iterates remaining steps — executing each (with parallel-peer batching,
    ralph-verify, post-step checks, stuck detection, adaptive-execution
    triggers, recovery and interrupt handling) — until no steps remain or a
    terminal status is reached. Returns a dict of the terminal loop state
    consumed by Phase G (_build_result_and_finalize) and the auto-recovery
    path. ctx.goal / ctx.max_iterations are intentionally left untouched; the
    (possibly interrupt-mutated) goal and the (possibly bumped) max_iterations
    are returned for the auto-recovery re-run, matching the pre-extraction
    inline behavior.
    """
    from llm import LLMTool, MODEL_CHEAP, MODEL_MID
    from interrupt import apply_interrupt_to_steps

    o = _orch()

    # Aliases from ctx / phase inputs — keep the loop body verbatim against the
    # pre-extraction inline version.
    goal = ctx.goal
    max_iterations = ctx.max_iterations
    continuation_depth = ctx.continuation_depth
    token_budget = ctx.token_budget
    cost_budget = ctx.cost_budget
    ralph_verify = ctx.ralph_verify
    dry_run = ctx.dry_run
    verbose = ctx.verbose
    loop_id = ctx.loop_id
    start_ts = ctx.start_ts
    project = ctx.project
    adapter = ctx.adapter
    interrupt_queue = ctx.interrupt_queue
    _ancestry_context = ctx.ancestry_context
    _resolve_tools = resolve_tools_fn
    _TIER_ORDER = tier_order
    _resume_completed = resume_completed
    _prereq_context = prereq_context
    _pf_review = pf_review
    _levels = levels
    _loop_shared_ctx = loop_shared_ctx
    _manifest_steps = manifest_steps
    _replan_count = replan_count

    # Step 2: Execute each step in order (dynamic — interrupts may add/replace steps)
    # Pre-populate with any completed steps from a checkpoint resume
    step_outcomes: List[StepOutcome] = list(_resume_completed)
    total_tokens_in = 0
    total_tokens_out = 0
    total_cache_read = 0
    total_cost_usd = 0.0   # accumulated per-step (correct across model switches)
    stuck_streak = 0
    last_action: Optional[str] = None
    _consecutive_max_timeouts = 0  # ceiling-hit timeouts across different steps — adapter health signal
    _MAX_CONSECUTIVE_TIMEOUTS = 3  # bail out if adapter appears hung, not just steps being too large
    iteration = 0
    loop_status = "done"
    stuck_reason = None
    completed_context: List[str] = []
    # Loop scratchpad: structured data store for step-to-step data sharing.
    # Each step writes its findings here; subsequent steps can reference them.
    # Persisted to artifacts at loop end for debugging and replay.
    _scratchpad: Dict[str, Any] = {"steps": {}, "shared": {}}
    import threading as _threading
    _scratchpad_lock = _threading.Lock()
    interrupts_applied = 0

    # Use mutable lists so interrupt handlers can modify remaining work
    remaining_steps: List[str] = list(steps)
    remaining_indices: List[int] = list(step_indices)
    step_idx = 0  # global step counter (for numbering, includes injected steps)
    _next_step_injected_context: str = ""  # Phase 11: injected context from previous step's hooks
    _march_of_nines_alert = False  # Phase 19: cumulative step success rate alert
    _step_retries: Dict[str, int] = {}  # roadblock resilience: retries per step text
    _error_fingerprints: Dict[str, List[str]] = {}  # Phase 62: error fingerprints per step text
    _step_tier_overrides: Dict[str, str] = {}  # step_text → escalated tier on retry
    # Phase 57: session-level lagging signal — if verify failures cluster, raise the global tier.
    # Tracks consecutive verify failures; at threshold, adapter baseline escalates.
    _session_verify_failures: int = 0
    _SESSION_VERIFY_FAIL_THRESHOLD = 3  # 3 consecutive verify failures → global tier-up
    _session_tier_floor: str = ""       # non-empty when global tier has been raised
    # Agent0 steal: failure-chain recording — every retry/recovery is a training signal
    _failure_chain: List[str] = []
    _recovery_step_count: int = 0
    # Phase 58: milestone-aware expansion — track which milestone steps have been expanded
    # so we don't re-expand sub-steps that happen to share the same 1-based index.
    _milestone_expanded: set = set()

    # Lazy import for injection scanning (security.py)
    try:
        from security import scan_external_content as _scan_content, InjectionRisk as _InjectionRisk
        _security_available = True
    except ImportError:
        _security_available = False

    # Phase 19: lazy import for dead_ends and boot_protocol
    try:
        from boot_protocol import update_dead_ends as _update_dead_ends
        _dead_ends_available = True
    except ImportError:
        _dead_ends_available = False

    # Pre-compute project artifact dir (used by both parallel batch and single-step paths)
    _proj_artifact_dir = ""
    if project:
        try:
            _proj_artifact_dir = str(_project_dir_root() / project)
        except Exception as _pad_exc:
            log.debug("project artifact dir resolution failed: %s", _pad_exc)

    # Phase F: Main execute loop
    _budget_bumped = False  # guard: mid-loop budget bump fires at most once
    while remaining_steps:
        if iteration >= max_iterations:
            loop_status = "stuck"
            stuck_reason = f"hit max_iterations={max_iterations} before completing all steps"
            _ceiling_suffix = _handle_budget_ceiling(
                ctx, step_outcomes, remaining_steps,
                total_tokens_in, total_tokens_out,
                iteration, max_iterations, continuation_depth,
            )
            if _ceiling_suffix:
                stuck_reason += _ceiling_suffix
            break

        # Mid-loop budget bump: when 75%+ of budget is consumed, there are still
        # steps remaining, and good progress has been made, bump max_iterations
        # by 50% (once only) so the loop can complete rather than hard-landing.
        _remaining_budget = max_iterations - iteration
        _BUDGET_WARN_THRESHOLD = 0.75
        if (
            not _budget_bumped
            and len(remaining_steps) > 2
            and iteration >= int(max_iterations * _BUDGET_WARN_THRESHOLD)
        ):
            _steps_done = sum(1 for s in step_outcomes if s.status == "done")
            _completion_rate = _steps_done / max(len(step_outcomes), 1)
            if _completion_rate >= 0.5:
                _bump_amount = max(10, max_iterations // 2)
                max_iterations += _bump_amount
                _remaining_budget = max_iterations - iteration
                _budget_bumped = True
                log.info(
                    "mid-loop budget bump: max_iterations bumped by %d to %d "
                    "(%.0f%% done, %d steps remain)",
                    _bump_amount, max_iterations, _completion_rate * 100, len(remaining_steps),
                )
                try:
                    from captains_log import log_event
                    log_event(
                        event_type="METACOGNITIVE_DECISION",
                        subject=goal[:80],
                        summary=(
                            f"Budget running low at {iteration}/{max_iterations - _bump_amount} "
                            f"iterations — bumped max_iterations by {_bump_amount} to {max_iterations}. "
                            f"{_steps_done}/{len(step_outcomes)} steps done, {len(remaining_steps)} remain."
                        ),
                        context={
                            "action": "budget_bump",
                            "bump_amount": _bump_amount,
                            "new_max_iterations": max_iterations,
                            "completion_rate": round(_completion_rate, 2),
                            "steps_remaining": len(remaining_steps),
                        },
                    )
                except Exception as _bmp_clog_exc:
                    log.debug("budget bump captain's log write failed: %s", _bmp_clog_exc)

        # Budget-aware landing: when only 2 iterations remain and there are
        # still multiple steps, replace the remaining steps with a single
        # "synthesize what we have" step so the loop lands gracefully.
        if _remaining_budget <= 2 and len(remaining_steps) > 1 and len(step_outcomes) >= 3:
            _done_count = sum(1 for s in step_outcomes if s.status == "done")
            if _done_count >= 2:
                _synth_step = (
                    f"Synthesize the findings from the {_done_count} completed steps into "
                    f"a structured summary. Include: key findings, gaps or open questions, "
                    f"and concrete recommendations. This is a partial result — "
                    f"{len(remaining_steps)} steps were not completed."
                )
                remaining_steps.clear()
                remaining_steps.append(_synth_step)
                remaining_indices.clear()
                remaining_indices.append(-1)
                # Track replan in manifest so it's visible
                _manifest_steps.append(f"[REPLAN — budget pressure] {_synth_step[:80]}")
                _replan_count += 1
                log.info("budget-aware landing: replaced %d remaining steps with synthesis step "
                         "(budget=%d iterations left, %d steps done)",
                         len(remaining_steps), _remaining_budget, _done_count)
                # back-pressure: inject budget reminder so synthesis step knows context
                _budget_reminder = (
                    f"BUDGET PRESSURE — {_remaining_budget} iteration(s) remaining.\n"
                    f"Original goal: {goal}\n"
                    "Deliver the best synthesis possible from what you have. "
                    "Do NOT attempt new research — consolidate only."
                )
                _next_step_injected_context = (
                    (_next_step_injected_context + "\n\n" + _budget_reminder).strip()
                    if _next_step_injected_context
                    else _budget_reminder
                )

        step_text = remaining_steps.pop(0)
        item_index = remaining_indices.pop(0) if remaining_indices else -1

        # Phase 58: Milestone-aware expansion — if pre-flight flagged this step as a
        # milestone candidate, pre-decompose it into sub-steps before executing.
        # Only at depth 0 to prevent recursive explosion. Skip if already expanded.
        _would_be_step_idx = step_idx + 1  # 1-based index this step will get
        if (_pf_review is not None
                and continuation_depth == 0
                and _would_be_step_idx in _pf_review.milestone_step_indices
                and _would_be_step_idx not in _milestone_expanded):
            _milestone_expanded.add(_would_be_step_idx)
            try:
                from planner import decompose as _ms_decompose
                _ms_sub = _ms_decompose(step_text, adapter, max_steps=5)
                if _ms_sub and len(_ms_sub) >= 2:
                    _ms_sub = _shape_steps(_ms_sub, label="milestone-expand")
                    remaining_steps[:0] = _ms_sub
                    remaining_indices[:0] = [-1] * len(_ms_sub)
                    log.info("milestone-aware: step %d %r → %d sub-steps",
                             _would_be_step_idx, step_text[:60], len(_ms_sub))
                    if verbose:
                        import sys as _ms_sys
                        print(f"[maro] milestone step {_would_be_step_idx} expanded → "
                              f"{len(_ms_sub)} sub-steps", file=_ms_sys.stderr, flush=True)
                    continue  # Run sub-steps instead of this milestone step directly
            except Exception as _ms_exc:
                log.debug("milestone-aware: expand failed for step %d: %s",
                          _would_be_step_idx, _ms_exc)
                # Advisor Pattern: consult Opus when a milestone can't be decomposed
                try:
                    from llm import advisor_call as _ms_advisor
                    _ms_advice = _ms_advisor(
                        goal=ctx.goal,
                        context=(
                            f"Milestone step {_would_be_step_idx}: {step_text}\n"
                            f"Decomposition failed: {_ms_exc}\n"
                            f"Completed {len(step_outcomes)} of ~{len(remaining_steps) + len(step_outcomes) + 1} steps."
                        ),
                        question=(
                            "This step was flagged as a complex milestone but can't be decomposed into sub-steps. "
                            "Should we: (a) execute it as-is (may be too broad), (b) skip it and continue with "
                            "remaining steps, or (c) rephrase it to be more concrete? If (c), suggest the rephrased text."
                        ),
                    )
                    if _ms_advice:
                        if "(b)" in _ms_advice.lower():
                            log.info("milestone advisor: skip step %d on advice", _would_be_step_idx)
                            continue  # skip this step
                        elif "(c)" in _ms_advice.lower():
                            # Try to extract rephrased text — advisor should lead with it
                            _rephrase_lines = [l.strip() for l in _ms_advice.split("\n") if l.strip() and not l.strip().startswith("(")]
                            if _rephrase_lines:
                                step_text = _rephrase_lines[-1][:200]
                                log.info("milestone advisor: rephrased step %d → %s", _would_be_step_idx, step_text[:80])
                except Exception as _ms_adv_exc:
                    log.debug("milestone advisor call failed: %s", _ms_adv_exc)
            # Fall through to execute normally if decompose fails or returns 1 step

        # Check for parallel peers: if this step has siblings at the same
        # dependency level, batch them for parallel execution
        _parallel_peers: List[str] = []
        _peer_item_indices: List[int] = []
        if _levels and parallel_fan_out > 0 and remaining_steps:
            _current_step_num = step_idx + 1  # 1-based
            # Find which level this step belongs to
            for _lvl in _levels:
                if _current_step_num in _lvl and len(_lvl) > 1:
                    # Pop remaining peers from the front of remaining_steps,
                    # keeping their NEXT.md item indices so batch outcomes can
                    # mark items done and record a real index (BACKLOG #2:
                    # discarding these was the source of phantom "Step -1").
                    _peer_count = 0
                    for _peer_idx in _lvl:
                        if _peer_idx != _current_step_num and remaining_steps:
                            _parallel_peers.append(remaining_steps.pop(0))
                            _peer_item_indices.append(
                                remaining_indices.pop(0) if remaining_indices else -1)
                            _peer_count += 1
                    if _parallel_peers:
                        log.info("parallel batch: step %d + %d peers at same level",
                                 _current_step_num, len(_parallel_peers))
                    break

        if _parallel_peers:
            iteration, step_idx, _tin, _tout = _run_parallel_batch(
                ctx, step_text, _parallel_peers,
                step_outcomes=step_outcomes,
                completed_context=completed_context,
                remaining_steps=remaining_steps,
                remaining_indices=remaining_indices,
                loop_shared_ctx=_loop_shared_ctx,
                resolve_tools_fn=_resolve_tools,
                parallel_fan_out=parallel_fan_out,
                proj_artifact_dir=_proj_artifact_dir,
                iteration=iteration,
                step_idx=step_idx,
                batch_item_indices=[item_index] + _peer_item_indices,
            )
            total_tokens_in += _tin
            total_tokens_out += _tout
            # Keep total_cost_usd honest for the budget breaker. The batch helper
            # doesn't surface cache_read, so price at full rate — the safe (slight
            # over-estimate) direction for a circuit breaker.
            try:
                from metrics import estimate_cost as _batch_est
                total_cost_usd += _batch_est(_tin, _tout, model=getattr(ctx.adapter, "model_key", "") or None)
            except ImportError:
                pass
            continue  # Skip the single-step execution below

        iteration += 1
        step_idx += 1
        if verbose:
            print(f"[maro] step {step_idx}: {step_text!r}", file=sys.stderr, flush=True)

        # vtrivedy10/systematicls: re-inject goal + key constraints at step 5+ (every 5 steps)
        # Counteracts instruction fade-out as context grows.
        _REMINDER_EVERY = 5
        if step_idx > 0 and step_idx % _REMINDER_EVERY == 0:
            _reorient = (
                f"GOAL REORIENTATION (step {step_idx}):\n"
                f"Original goal: {goal}\n"
                "You are still working on this goal. Stay on task.\n"
                "Key constraints: target <500 tokens per step result; "
                "never dump raw API output; use prior step data already in context."
            )
            _next_step_injected_context = (
                (_next_step_injected_context + "\n\n" + _reorient).strip()
                if _next_step_injected_context
                else _reorient
            )

        # Context snowball observation — log size so degradation is visible, not silent.
        # Guideline: warn above 50K chars (rough proxy for ~12K tokens of accumulated context).
        _ctx_chars = sum(len(c) for c in completed_context)
        if _ctx_chars > 0:
            _ctx_level = "warn" if _ctx_chars > 50_000 else "info"
            getattr(log, _ctx_level)(
                "step %d context: %d chars across %d prior steps%s",
                step_idx, _ctx_chars, len(completed_context),
                " [large — synthesis quality may degrade]" if _ctx_chars > 50_000 else "",
            )
            if verbose and _ctx_chars > 50_000:
                import sys as _sys
                print(f"[maro] step {step_idx}: accumulated context {_ctx_chars:,} chars "
                      f"({len(completed_context)} entries) — synthesis quality may degrade",
                      file=_sys.stderr, flush=True)

        step_start = time.monotonic()
        # Per-step model selection (Phase F5)
        _step_adapter = _select_step_adapter(
            ctx, step_text, step_idx,
            step_tier_overrides=_step_tier_overrides,
            session_tier_floor=_session_tier_floor,
            tier_order=_TIER_ORDER,
        )

        # _next_step_injected is set by the previous iteration's hook run
        # Phase 27: merge per-step prereq context (graveyard / sub-loop acquired)
        _prereq_for_step = _prereq_context.get(step_idx, "")
        if _prereq_for_step:
            _next_step_injected_context = (
                (_next_step_injected_context + "\n\n" + _prereq_for_step).strip()
                if _next_step_injected_context
                else _prereq_for_step
            )
        _step_ancestry = (
            (_ancestry_context + "\n\n" + _next_step_injected_context)
            if _next_step_injected_context
            else _ancestry_context
        )
        # Invariant guard: if a compound step still reaches execution, recover
        # immediately instead of merely logging. This closes shaper gaps across
        # weird plan-mutation paths by converting the leak into replacement steps
        # before any executor/tool burn happens.
        if _is_combined_exec_analyze(step_text):
            _parts = _split_exec_analyze(step_text)
            # Convergence guard: if a replacement would itself re-trip the
            # detector (analysis-first steps with an incidental exec keyword,
            # e.g. "Analyze findings from build X"), splitting would loop
            # forever — execute the step as-is instead.
            if any(_is_combined_exec_analyze(p) for p in _parts):
                log.warning(
                    "step-shape-LEAK step=%d: split non-convergent (likely a "
                    "false-positive compound match); executing as-is: %r",
                    step_idx, step_text[:120],
                )
            else:
                log.warning(
                    "step-shape-LEAK step=%d: compound exec+analyze step reached executor; "
                    "auto-splitting into %d replacement steps: %r",
                    step_idx, len(_parts), step_text[:120],
                )
                remaining_steps[:0] = _parts
                remaining_indices[:0] = [-1] * len(_parts)
                if verbose:
                    print(
                        f"[maro] step {step_idx}: recovered compound step by splitting into {len(_parts)} steps",
                        file=sys.stderr,
                        flush=True,
                    )
                continue
        # Fabrication ground-truth (done≠achieved): snapshot the workspace before
        # the step so a write-claim with an empty diff + no on-disk file can be
        # caught after. The cwd fix (#1) makes project_dir a reliable diff target.
        _artifact_check_on = True
        try:
            from config import get as _ac_cfg_get
            _artifact_check_on = bool(_ac_cfg_get("validate.artifact_check", True))
        except Exception:
            pass
        _artifact_snapshot = {}
        if _artifact_check_on:
            try:
                from artifact_check import snapshot_dir as _ac_snapshot
                _artifact_snapshot = _ac_snapshot(_proj_artifact_dir)
            except Exception:
                _artifact_check_on = False

        outcome = _execute_step(
            goal=goal,
            step_text=step_text,
            step_num=step_idx,
            total_steps=step_idx + len(remaining_steps),
            completed_context=completed_context,
            adapter=_step_adapter,
            tools=[LLMTool(**t) for t in _resolve_tools()],
            verbose=verbose,
            ancestry_context=_step_ancestry,
            project_dir=_proj_artifact_dir,
            shared_ctx=_loop_shared_ctx,
        )
        step_elapsed = int((time.monotonic() - step_start) * 1000)

        total_tokens_in += outcome.get("tokens_in", 0)
        total_tokens_out += outcome.get("tokens_out", 0)
        total_cache_read += outcome.get("cache_read_tokens", 0)
        # Per-step cost estimate (cache-aware: cache reads priced at ~0.1x)
        _step_model = getattr(_step_adapter, "model_key", "")
        try:
            from metrics import estimate_cost
            _step_cost = estimate_cost(outcome.get("tokens_in", 0), outcome.get("tokens_out", 0), model=_step_model,
                                       cache_read_tokens=outcome.get("cache_read_tokens", 0))
            # Accumulate per step: repricing the running total at the latest step's
            # model swings the figure when steps switch cheap<->mid<->power.
            total_cost_usd += _step_cost
            _total_cost = total_cost_usd
        except ImportError:
            _step_cost = 0.0
            _total_cost = total_cost_usd
        log.info("step %d %s tokens_step=%d tokens_total=%d cost_step=$%.4f cost_total=$%.4f model=%s elapsed=%dms iter=%d/%d",
                 step_idx, outcome.get("status", "?"),
                 outcome.get("tokens_in", 0) + outcome.get("tokens_out", 0),
                 total_tokens_in + total_tokens_out,
                 _step_cost, _total_cost,
                 _step_model or "unknown",
                 step_elapsed, iteration, max_iterations)

        # Phase 33: token budget — abort gracefully if exceeded
        if token_budget is not None and (total_tokens_in + total_tokens_out) >= token_budget:
            loop_status = "stuck"
            stuck_reason = (
                f"token_budget={token_budget} exceeded "
                f"({total_tokens_in + total_tokens_out} total tokens after step {step_idx})"
            )
            if verbose:
                print(f"[maro] {stuck_reason}", file=sys.stderr, flush=True)
            break

        # Cost budget — warn at 80%, hard stop at budget + 20% slush
        if cost_budget is not None and _total_cost > 0:
            _cost_pct = _total_cost / cost_budget * 100
            _slush = cost_budget * 0.2
            if _total_cost >= cost_budget + _slush:
                loop_status = "stuck"
                stuck_reason = (
                    f"cost_budget=${cost_budget:.2f} + slush=${_slush:.2f} exceeded "
                    f"(${_total_cost:.4f} total after step {step_idx})"
                )
                log.warning("cost hard stop: %s", stuck_reason)
                if verbose:
                    print(f"[maro] {stuck_reason}", file=sys.stderr, flush=True)
                break
            elif _cost_pct >= 80 and not getattr(run_agent_loop, "_cost_warned", False):
                log.warning("cost approaching budget: $%.4f / $%.2f (%.0f%%)",
                            _total_cost, cost_budget, _cost_pct)
                run_agent_loop._cost_warned = True  # type: ignore[attr-defined]

        step_status = outcome["status"]
        _raw_result = outcome.get("result", "")
        # Guard: LLM can return a JSON schema object instead of a string value for
        # result/summary fields. If non-string, convert to empty string (result) or step_text (summary).
        step_result = _raw_result if isinstance(_raw_result, str) else str(_raw_result) if _raw_result else ""
        _raw_summary = outcome.get("summary", step_text)
        step_summary = _raw_summary if isinstance(_raw_summary, str) else step_text

        # Fabrication ground-truth check (done≠achieved). Runs before ralph verify
        # so a fabricated "done" is demoted to "blocked" and never reaches the
        # text-only verifier (which can't see the filesystem). Evidence-based, zero
        # code execution: catches a write-claim with no artifact (missing-artifact)
        # and a concrete-output claim against a provably-inert .py (inert-output).
        if _artifact_check_on and step_status == "done" and step_result:
            try:
                from artifact_check import check_fabrication as _ac_check
                _ac_verdict = _ac_check(step_result, _proj_artifact_dir, _artifact_snapshot)
                # Exec-claim contradiction: agent claimed success but every
                # command it actually ran failed (real tool transcript). Only
                # runs when the FS/AST layers found nothing.
                if not _ac_verdict.fabricated:
                    try:
                        from artifact_check import check_execution_claim as _ec_check
                        _ec_verdict = _ec_check(step_result, outcome.get("tool_events"))
                        if _ec_verdict.fabricated:
                            _ac_verdict = _ec_verdict
                    except Exception:
                        pass
                if _ac_verdict.fabricated:
                    log.warning(
                        "FABRICATION step=%d kind=%s: %s",
                        step_idx, _ac_verdict.kind, _ac_verdict.reason,
                    )
                    step_status = "blocked"
                    outcome["status"] = "blocked"
                    outcome["stuck_reason"] = f"artifact-fabrication[{_ac_verdict.kind}]: {_ac_verdict.reason}"
                    step_result = (
                        f"{step_result}\n\n[fabrication-guard] {_ac_verdict.reason}. "
                        f"Marked blocked — re-run and actually produce the claimed "
                        f"artifact/output before reporting done."
                    )
                    try:
                        from captains_log import log_event as _ac_log_event, FABRICATION_DETECTED
                        _ac_log_event(
                            FABRICATION_DETECTED,
                            subject=f"step {step_idx}",
                            summary=_ac_verdict.reason,
                            context={
                                "step_text": step_text[:200],
                                "kind": _ac_verdict.kind,
                                "claims": _ac_verdict.claims,
                                "missing": _ac_verdict.missing,
                                "changed_count": _ac_verdict.changed_count,
                            },
                        )
                    except Exception:
                        pass
                    if verbose:
                        print(
                            f"[maro] step {step_idx}: fabrication guard blocked — {_ac_verdict.reason}",
                            file=sys.stderr, flush=True,
                        )
            except Exception:
                pass

        # Ralph verify loop (Phase F8). Defaults ON when a usable local validator
        # is configured — verification is then free (opt out: validate.auto_verify).
        _ralph_active = (ralph_verify
                         or goal.lower().startswith(("ralph:", "verify:"))
                         or _local_auto_ralph_enabled())
        if step_status == "done" and _ralph_active and step_result:
            step_status, step_result, _session_verify_failures, _session_tier_floor = _run_ralph_verify(
                ctx, step_text, step_idx, step_result, step_status, outcome, _step_adapter,
                step_tier_overrides=_step_tier_overrides,
                session_verify_failures=_session_verify_failures,
                session_tier_floor=_session_tier_floor,
                verify_fail_threshold=_SESSION_VERIFY_FAIL_THRESHOLD,
            )

        # Post-step checks: observability, security, claim verifier, hooks (Phase F9)
        step_status, step_result, _step_injected_context = _post_step_checks(
            ctx, step_text, step_idx, step_status, step_result, step_summary, step_elapsed, outcome,
            security_available=_security_available,
            scan_content_fn=_scan_content if _security_available else None,
            injection_risk_cls=_InjectionRisk if _security_available else None,
        )

        # Stuck detection: same action repeated 3x
        action_key = f"{step_text}:{step_status}"
        if action_key == last_action:
            stuck_streak += 1
        else:
            stuck_streak = 0
            last_action = action_key

        if stuck_streak >= 2:  # 3rd repeat
            # Phase 64 Phase A: adaptive execution — director evaluates before stuck advisor
            try:
                from config import get as _ae_cfg_get
                _ae_on = bool(_ae_cfg_get("adaptive_execution", False))
            except Exception:
                _ae_on = False
            if _ae_on:
                try:
                    from director import (
                        director_evaluate as _dir_evaluate,
                        EvaluationContext as _EvalCtx,
                    )
                    _ae_done = [o for o in step_outcomes if o.status == "done"]
                    _ae_results = "\n---\n".join(
                        (o.result or "")[:600] for o in _ae_done[-3:]
                    )
                    _ae_ctx = _EvalCtx(
                        goal=goal,
                        current_pass_scope=goal,
                        steps_completed=[o.text for o in step_outcomes],
                        # include current stuck step so director knows what failed
                        steps_remaining=[step_text] + list(remaining_steps),
                        step_results_summary=f"[Stuck on: {step_text}]\n\n{_ae_results}",
                        verify_failure_count=_session_verify_failures,
                        total_steps_taken=len(step_outcomes),
                        max_steps=max_iterations,
                        convergence_budget_remaining=(
                            ctx.director_budget_ceiling - ctx.director_replan_count
                        ),
                    )
                    _ae_decision = _dir_evaluate(goal, _ae_ctx, "stuck", adapter, dry_run=dry_run)
                    ctx.steps_since_last_check = 0
                    # Budget enforcement: replan disallowed when ceiling reached
                    if (_ae_decision.action == "replan"
                            and ctx.director_replan_count >= ctx.director_budget_ceiling):
                        log.info("adaptive [stuck]: replan requested but budget exhausted "
                                 "(%d/%d) — forcing continue",
                                 ctx.director_replan_count, ctx.director_budget_ceiling)
                        _ae_decision = type(_ae_decision)(
                            action="continue",
                            reasoning="replan budget exhausted",
                            next_check_in=_ae_decision.next_check_in,
                        )
                    _record_loop_decision(
                        "director", "stuck",
                        _ae_decision.action, _ae_decision.reasoning,
                    )
                    if _ae_decision.action == "continue":
                        stuck_streak = 0
                        log.info("adaptive [stuck/continue]: resetting streak — %s",
                                 _ae_decision.reasoning[:100])
                        continue
                    elif _ae_decision.action == "adjust" and _ae_decision.revised_steps:
                        _ae_new = _ae_decision.revised_steps
                        remaining_steps[:] = _ae_new
                        remaining_indices[:] = [-1] * len(_ae_new)
                        stuck_streak = 0
                        log.info("adaptive [stuck/adjust]: replaced %d steps — %s",
                                 len(_ae_new), _ae_decision.reasoning[:100])
                        if verbose:
                            print(f"[maro] adaptive adjust (stuck): {len(_ae_new)} steps — "
                                  f"{_ae_decision.reasoning[:60]}", file=sys.stderr, flush=True)
                        continue
                    elif _ae_decision.action == "replan":
                        try:
                            from planner import decompose as _planner_decompose
                            _ae_completed_ctx = "\n".join(
                                f"- {o.text}: {(o.result or '')[:200]}"
                                for o in step_outcomes[-5:]
                            )
                            _ae_ancestry = (
                                f"Director replan: {_ae_decision.new_approach or 'fresh approach'}\n\n"
                                f"Already completed:\n{_ae_completed_ctx}"
                            )
                            _ae_replan_steps = _planner_decompose(
                                goal, adapter,
                                max_steps=max(3, max_iterations - len(step_outcomes)),
                                ancestry_context=_ae_ancestry,
                            )
                            if _ae_replan_steps:
                                remaining_steps[:] = _ae_replan_steps
                                remaining_indices[:] = [-1] * len(_ae_replan_steps)
                                ctx.director_replan_count += 1
                                stuck_streak = 0
                                log.info(
                                    "adaptive [stuck/replan]: fresh %d steps "
                                    "(replan %d/%d) — %s",
                                    len(_ae_replan_steps),
                                    ctx.director_replan_count, ctx.director_budget_ceiling,
                                    _ae_decision.reasoning[:80],
                                )
                                if verbose:
                                    print(
                                        f"[maro] adaptive replan (stuck): "
                                        f"{len(_ae_replan_steps)} steps — "
                                        f"{_ae_decision.reasoning[:60]}",
                                        file=sys.stderr, flush=True,
                                    )
                                continue
                        except Exception as _ae_replan_exc:
                            log.debug("adaptive replan (stuck) planner call failed: %s",
                                      _ae_replan_exc)
                    elif _ae_decision.action == "restart":
                        # Break with restart status — handle.py detects and re-runs
                        _ae_restart_ctx = (
                            _ae_decision.restart_context or _ae_decision.reasoning
                        )
                        ctx.director_replan_count += 1  # restart counts toward budget
                        loop_status = "restart"
                        stuck_reason = _ae_restart_ctx
                        log.info("adaptive [stuck/restart]: breaking loop "
                                 "(replan %d/%d) — %s",
                                 ctx.director_replan_count, ctx.director_budget_ceiling,
                                 _ae_restart_ctx[:100])
                        if verbose:
                            print(f"[maro] adaptive restart (stuck) — "
                                  f"{_ae_restart_ctx[:80]}", file=sys.stderr, flush=True)
                        break
                    elif _ae_decision.action == "escalate":
                        _ae_question = _ae_decision.user_question or _ae_decision.reasoning
                        if ctx.channel is not None:
                            try:
                                _ae_reply = ctx.channel.ask(_ae_question)
                                if _ae_reply:
                                    _next_step_injected_context = (
                                        f"Director asked: {_ae_question}\n"
                                        f"User replied: {_ae_reply}"
                                    )
                                    log.info("adaptive [stuck/escalate]: got user reply "
                                             "(%d chars)", len(_ae_reply))
                            except Exception as _esc_exc:
                                log.debug("adaptive escalate channel.ask failed: %s",
                                          _esc_exc)
                        else:
                            log.info("adaptive [stuck/escalate]: no channel — "
                                     "logging question: %s", _ae_question[:150])
                        stuck_streak = 0
                        continue
                except Exception as _ae_exc:
                    log.debug("adaptive execution (stuck trigger) error: %s", _ae_exc)

            # Advisor Pattern: before giving up, ask Opus for strategic guidance
            try:
                from llm import advisor_call as _advisor_call
                _ctx_summary = "\n".join(
                    f"  step {i+1}: {o_s.get('status','?')} — {o_s.get('summary','')[:60]}"
                    for i, o_s in enumerate(step_outcomes[-5:])
                )
                _advice = _advisor_call(
                    goal=goal,
                    context=f"Completed {len(step_outcomes)} steps.\nRecent:\n{_ctx_summary}\n\nCurrent stuck step: {step_text}",
                    question=f"Step '{step_text}' has failed 3 times with status '{step_status}'. Should we: (a) skip this step and continue, (b) rephrase the step and retry, or (c) abort the mission? If (b), suggest the rephrased step.",
                )
                if _advice and "(b)" in _advice.lower():
                    # Advisor says rephrase — extract suggestion and retry once
                    log.info("advisor: suggests rephrasing stuck step %d — trying once more", step_idx)
                    if verbose:
                        print(f"[maro] advisor (Opus): rephrase step {step_idx}", file=sys.stderr)
                    stuck_streak = 0  # reset streak to give one more attempt
                    # Don't break — let the loop continue with the same step
                    # The advisor's advice is logged but the step text stays the same
                    # (rephrasing would require plan mutation which is a bigger change)
                    continue
                elif _advice:
                    log.info("advisor on stuck step %d: %s", step_idx, _advice[:120])
            except Exception as _adv_exc:
                log.debug("stuck-step advisor call failed: %s", _adv_exc)

            loop_status = "stuck"
            stuck_reason = f"same outcome '{step_status}' on '{step_text}' repeated 3 times"
            step_outcomes.append(step_from_decompose(
                step_text, item_index,
                status="blocked",
                result=step_result,
                iteration=iteration,
                tokens_in=outcome.get("tokens_in", 0),
                tokens_out=outcome.get("tokens_out", 0),
                cache_read_tokens=outcome.get("cache_read_tokens", 0),
                elapsed_ms=step_elapsed,
            ))
            if item_index >= 0:
                o.mark_item(project, item_index, o.STATE_BLOCKED)
            o.append_decision(project, [f"[loop:{loop_id}] stuck on step {step_idx}: {stuck_reason}"])
            break

        # Write artifact
        _write_step_artifact(project, loop_id, step_idx, step_text, step_result)

        # Phase 59 (Feynman steal): Task ledger entry — per-step audit trail
        try:
            from memory import append_task_ledger as _atl, TaskLedgerEntry as _TLE
            _atl(_TLE(
                task_id=f"step_{step_idx}",
                owner="agent_loop",
                task=step_text[:200],
                status=step_status,
                loop_id=loop_id,
                result_summary=(step_result or "")[:200],
            ))
        except Exception:
            pass  # ledger must never block loop progress

        # Lightweight verification: check if .py filenames cited in the result
        # actually exist. Append a correction note if hallucinated files found.
        if step_status == "done" and step_result and ".py" in step_result:
            try:
                import re as _verify_re
                _cited_files = set(_verify_re.findall(r'\b([a-z_]+\.py)\b', step_result))
                _src_files = set(f.name for f in Path("src").glob("*.py")) if Path("src").exists() else set()
                _test_files = set(f.name for f in Path("tests").glob("*.py")) if Path("tests").exists() else set()
                # Also scan sibling dirs in ~/claude/ (one level deep) to avoid false positives
                # when the agent references files from an external repo under ~/claude/
                _sibling_py: set = set()
                _home_claude = Path.home() / "claude"
                if _home_claude.exists():
                    for _sibling in _home_claude.iterdir():
                        if _sibling.is_dir() and not _sibling.name.startswith("."):
                            for _sub in ("src", "tests", ""):
                                _d = _sibling / _sub if _sub else _sibling
                                if _d.is_dir():
                                    _sibling_py.update(f.name for f in _d.glob("*.py"))
                _all_real = _src_files | _test_files | _sibling_py
                _hallucinated = _cited_files - _all_real - {"__init__.py", "setup.py", "conftest.py"}
                if _hallucinated and len(_hallucinated) <= len(_cited_files):
                    _note = f"\n[VERIFICATION: {len(_hallucinated)} file(s) cited but not found: {', '.join(sorted(_hallucinated)[:5])}]"
                    step_result = step_result + _note
                    outcome["result"] = step_result
                    log.warning("step %d verification: %d hallucinated files: %s",
                                step_idx, len(_hallucinated), ", ".join(sorted(_hallucinated)[:5]))
            except Exception as _vfy_exc:
                log.debug("file-citation verification failed for step %d: %s", step_idx, _vfy_exc)

        if step_status == "done":
            step_result = _process_done_step(
                ctx, step_text, step_idx, step_result, step_summary, step_elapsed,
                outcome, item_index, iteration,
                completed_context=completed_context,
                remaining_steps=remaining_steps,
                remaining_indices=remaining_indices,
                loop_shared_ctx=_loop_shared_ctx,
                scratchpad=_scratchpad,
                scratchpad_lock=_scratchpad_lock,
                step_model=getattr(_step_adapter, "model_key", None),
            )
            _consecutive_max_timeouts = 0  # successful step — adapter is healthy
        else:
            _blk = BlockedStepContext(
                step_text=step_text,
                step_idx=step_idx,
                step_result=step_result,
                step_elapsed=step_elapsed,
                outcome=outcome,
                item_index=item_index,
                iteration=iteration,
                step_adapter=_step_adapter,
                step_retries=_step_retries,
                step_tier_overrides=_step_tier_overrides,
                failure_chain=_failure_chain,
                step_outcomes=step_outcomes,
                remaining_steps=remaining_steps,
                remaining_indices=remaining_indices,
                manifest_steps=_manifest_steps,
                error_fingerprints=_error_fingerprints,
                next_step_injected_context=_next_step_injected_context,
                consecutive_max_timeouts=_consecutive_max_timeouts,
                max_consecutive_timeouts=_MAX_CONSECUTIVE_TIMEOUTS,
                replan_count=_replan_count,
            )
            (_blk_flow, step_idx, _blk_status, _blk_reason,
             _next_step_injected_context, _consecutive_max_timeouts,
             _blk_recovery_delta, _replan_count) = _process_blocked_step(ctx, _blk)
            _recovery_step_count += _blk_recovery_delta
            if _blk_flow == "continue":
                continue
            elif _blk_flow == "break":
                loop_status = _blk_status
                stuck_reason = _blk_reason
                break
            else:  # "normal" — terminal failure, fall through
                loop_status = _blk_status
                stuck_reason = _blk_reason

        step_outcomes.append(step_from_decompose(
            step_text, item_index,
            status=step_status,
            result=step_result,
            iteration=iteration,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            cache_read_tokens=outcome.get("cache_read_tokens", 0),
            elapsed_ms=step_elapsed,
            confidence=outcome.get("confidence", ""),
            injected_steps=outcome.get("inject_steps", []),
        ))

        # End-of-iteration artifacts: checkpoint, manifest, dead ends, march of nines
        _mon_alert = _write_iteration_artifacts(
            ctx, step_text, step_status, outcome,
            step_outcomes, steps, _manifest_steps, _replan_count, start_ts,
            dead_ends_available=_dead_ends_available,
            update_dead_ends_fn=_update_dead_ends if _dead_ends_available else None,
        )
        if _mon_alert:
            _march_of_nines_alert = True

        # Trajectory-based tier escalation: if early steps show low success rate,
        # raise the session floor so remaining steps use a stronger model.
        # Fires once after 3+ steps if done-rate < 50% and floor not already raised.
        _TRAJECTORY_CHECK_AFTER = 3
        _TRAJECTORY_DONE_THRESHOLD = 0.5
        if (len(step_outcomes) >= _TRAJECTORY_CHECK_AFTER
                and not _session_tier_floor
                and getattr(adapter, "model_key", "") in (MODEL_CHEAP, "")):
            _traj_done = sum(1 for s in step_outcomes if s.status == "done")
            _traj_rate = _traj_done / len(step_outcomes)
            if _traj_rate < _TRAJECTORY_DONE_THRESHOLD:
                _session_tier_floor = MODEL_MID
                log.warning("trajectory check: done-rate %.0f%% (%d/%d) after %d steps → "
                            "raising session floor to mid for remaining steps",
                            _traj_rate * 100, _traj_done, len(step_outcomes),
                            len(step_outcomes))
                if verbose:
                    print(f"[maro] trajectory check: {_traj_done}/{len(step_outcomes)} steps done "
                          f"({_traj_rate:.0%}) → floor raised to mid",
                          file=sys.stderr, flush=True)

        if loop_status == "stuck":
            break

        # Phase 64 Phase A: adaptive execution — verify_failure and step_threshold triggers.
        # Sync session-level counters to ctx so triggers can read them.
        ctx.session_verify_failures = _session_verify_failures
        ctx.stuck_streak = stuck_streak
        ctx.steps_since_last_check += 1
        try:
            from config import get as _ae2_cfg_get
            _ae2_on = bool(_ae2_cfg_get("adaptive_execution", False))
        except Exception:
            _ae2_on = False
        if _ae2_on:
            _AE_K = 5  # step threshold between mandatory checks
            _ae2_fire = (
                ctx.session_verify_failures >= 2
                or ctx.steps_since_last_check >= _AE_K
            )
            if _ae2_fire:
                try:
                    from director import (
                        director_evaluate as _dir_evaluate2,
                        EvaluationContext as _EvalCtx2,
                    )
                    _ae2_done = [o for o in step_outcomes if o.status == "done"]
                    _ae2_results = "\n---\n".join(
                        (o.result or "")[:600] for o in _ae2_done[-3:]
                    )
                    _ae2_ctx = _EvalCtx2(
                        goal=goal,
                        current_pass_scope=goal,
                        steps_completed=[o.text for o in step_outcomes],
                        steps_remaining=list(remaining_steps),
                        step_results_summary=_ae2_results,
                        verify_failure_count=ctx.session_verify_failures,
                        total_steps_taken=len(step_outcomes),
                        max_steps=max_iterations,
                        convergence_budget_remaining=(
                            ctx.director_budget_ceiling - ctx.director_replan_count
                        ),
                    )
                    _ae2_trigger = (
                        "verify_failure" if ctx.session_verify_failures >= 2
                        else "step_threshold"
                    )
                    _ae2_decision = _dir_evaluate2(
                        goal, _ae2_ctx, _ae2_trigger, adapter, dry_run=dry_run
                    )
                    ctx.steps_since_last_check = 0  # reset regardless of action
                    # Budget enforcement: replan disallowed when ceiling reached
                    if (_ae2_decision.action == "replan"
                            and ctx.director_replan_count >= ctx.director_budget_ceiling):
                        log.info(
                            "adaptive [%s]: replan requested but budget exhausted "
                            "(%d/%d) — forcing continue",
                            _ae2_trigger,
                            ctx.director_replan_count, ctx.director_budget_ceiling,
                        )
                        _ae2_decision = type(_ae2_decision)(
                            action="continue",
                            reasoning="replan budget exhausted",
                            next_check_in=_ae2_decision.next_check_in,
                        )
                    _record_loop_decision(
                        "director", _ae2_trigger,
                        _ae2_decision.action, _ae2_decision.reasoning,
                    )
                    if _ae2_decision.action == "adjust" and _ae2_decision.revised_steps:
                        _ae2_new = _ae2_decision.revised_steps
                        remaining_steps[:] = _ae2_new
                        remaining_indices[:] = [-1] * len(_ae2_new)
                        log.info("adaptive [%s/adjust]: replaced %d steps — %s",
                                 _ae2_trigger, len(_ae2_new), _ae2_decision.reasoning[:100])
                        if verbose:
                            print(
                                f"[maro] adaptive adjust ({_ae2_trigger}): {len(_ae2_new)} steps — "
                                f"{_ae2_decision.reasoning[:60]}", file=sys.stderr, flush=True,
                            )
                    elif _ae2_decision.action == "replan":
                        try:
                            from planner import decompose as _planner_decompose2
                            _ae2_completed_ctx = "\n".join(
                                f"- {o.text}: {(o.result or '')[:200]}"
                                for o in step_outcomes[-5:]
                            )
                            _ae2_ancestry = (
                                f"Director replan: "
                                f"{_ae2_decision.new_approach or 'fresh approach'}\n\n"
                                f"Already completed:\n{_ae2_completed_ctx}"
                            )
                            _ae2_replan_steps = _planner_decompose2(
                                goal, adapter,
                                max_steps=max(3, max_iterations - len(step_outcomes)),
                                ancestry_context=_ae2_ancestry,
                            )
                            if _ae2_replan_steps:
                                remaining_steps[:] = _ae2_replan_steps
                                remaining_indices[:] = [-1] * len(_ae2_replan_steps)
                                ctx.director_replan_count += 1
                                log.info(
                                    "adaptive [%s/replan]: fresh %d steps "
                                    "(replan %d/%d) — %s",
                                    _ae2_trigger, len(_ae2_replan_steps),
                                    ctx.director_replan_count, ctx.director_budget_ceiling,
                                    _ae2_decision.reasoning[:80],
                                )
                                if verbose:
                                    print(
                                        f"[maro] adaptive replan ({_ae2_trigger}): "
                                        f"{len(_ae2_replan_steps)} steps — "
                                        f"{_ae2_decision.reasoning[:60]}",
                                        file=sys.stderr, flush=True,
                                    )
                        except Exception as _ae2_replan_exc:
                            log.debug("adaptive replan (%s) planner call failed: %s",
                                      _ae2_trigger, _ae2_replan_exc)
                    elif _ae2_decision.action == "restart":
                        _ae2_restart_ctx = (
                            _ae2_decision.restart_context or _ae2_decision.reasoning
                        )
                        ctx.director_replan_count += 1
                        loop_status = "restart"
                        stuck_reason = _ae2_restart_ctx
                        log.info("adaptive [%s/restart]: breaking loop "
                                 "(replan %d/%d) — %s",
                                 _ae2_trigger,
                                 ctx.director_replan_count, ctx.director_budget_ceiling,
                                 _ae2_restart_ctx[:100])
                        if verbose:
                            print(f"[maro] adaptive restart ({_ae2_trigger}) — "
                                  f"{_ae2_restart_ctx[:80]}", file=sys.stderr, flush=True)
                    elif _ae2_decision.action == "escalate":
                        _ae2_question = (
                            _ae2_decision.user_question or _ae2_decision.reasoning
                        )
                        if ctx.channel is not None:
                            try:
                                _ae2_reply = ctx.channel.ask(_ae2_question)
                                if _ae2_reply:
                                    _next_step_injected_context = (
                                        f"Director asked: {_ae2_question}\n"
                                        f"User replied: {_ae2_reply}"
                                    )
                                    log.info("adaptive [%s/escalate]: got user reply "
                                             "(%d chars)", _ae2_trigger, len(_ae2_reply))
                            except Exception as _esc2_exc:
                                log.debug("adaptive escalate channel.ask failed: %s",
                                          _esc2_exc)
                        else:
                            log.info("adaptive [%s/escalate]: no channel — "
                                     "logging question: %s", _ae2_trigger,
                                     _ae2_question[:150])
                    else:
                        log.info("adaptive [%s/continue]: %s",
                                 _ae2_trigger, _ae2_decision.reasoning[:100])
                except Exception as _ae2_exc:
                    log.debug("adaptive execution error: %s", _ae2_exc)

        # Restart break — must be outside the adaptive try/except block
        if loop_status == "restart":
            break

        # Carry injected context forward to next step
        _next_step_injected_context = _step_injected_context

        # Kill switch, timeout, interrupt polling
        _intr_status, _intr_reason, goal, interrupts_applied, remaining_steps, remaining_indices = _check_loop_interrupts(
            ctx,
            remaining_steps=remaining_steps,
            remaining_indices=remaining_indices,
            interrupt_queue=interrupt_queue,
            apply_interrupt_fn=apply_interrupt_to_steps,
            goal=goal,
            interrupts_applied=interrupts_applied,
        )
        if _intr_status:
            loop_status = _intr_status
            stuck_reason = _intr_reason
            break

    return {
        "step_outcomes": step_outcomes,
        "loop_status": loop_status,
        "stuck_reason": stuck_reason,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "interrupts_applied": interrupts_applied,
        "march_of_nines_alert": _march_of_nines_alert,
        "manifest_steps": _manifest_steps,
        "replan_count": _replan_count,
        "milestone_expanded": _milestone_expanded,
        "failure_chain": _failure_chain,
        "recovery_step_count": _recovery_step_count,
        "scratchpad": _scratchpad,
        "scratchpad_lock": _scratchpad_lock,
        "goal": goal,
        "max_iterations": max_iterations,
    }


def run_agent_loop(
    goal: str,
    *,
    project: Optional[str] = None,
    repo_path: str = "",
    model: Optional[str] = None,
    backend: Optional[str] = None,
    adapter=None,
    knowledge_sub_goals: bool = False,
    max_steps: int = 8,
    max_iterations: int = 40,
    dry_run: bool = False,
    verbose: bool = False,
    interrupt_queue=None,
    hook_registry=None,
    ancestry_context_extra: str = "",
    step_callback=None,
    parallel_fan_out: int = 0,
    token_budget: Optional[int] = None,
    cost_budget: Optional[float] = None,
    ralph_verify: bool = False,
    resume_from_loop_id: Optional[str] = None,
    permission_context=None,
    continuation_depth: int = 0,
    preset_steps: Optional[List[str]] = None,
    channel=None,  # Optional ConversationChannel for mid-loop escalation (Phase 64C)
    loop_reason: str = "initial",  # why this loop was spawned — for run-transparency captain's log
    parent_loop_id: Optional[str] = None,
) -> LoopResult:
    """Run the autonomous loop for a goal.

    Args:
        goal: Natural language goal description.
        project: Existing project slug to attach to, or None to auto-create.
        model: LLM model string (defaults to MODEL_CHEAP).
        step_callback: Optional callable(step_num, step_text, summary, status) called
            after each step completes. Useful for live progress updates (e.g. Telegram).
        parallel_fan_out: If > 0 and all decomposed steps are independent (no inter-step
            references), run up to this many steps concurrently via ThreadPoolExecutor.
            Falls back to sequential if steps have dependencies. Default 0 (sequential).
        adapter: Pre-built LLMAdapter instance (skips build_adapter()).
        max_steps: Maximum steps to decompose the goal into.
        max_iterations: Hard cap on total LLM calls.
        dry_run: Simulate without LLM calls (uses stub responses).
        verbose: Print progress to stdout.
        interrupt_queue: InterruptQueue instance (or None). If None, a default
            queue is created automatically so any interface can post interrupts.
        resume_from_loop_id: If set, load the checkpoint for this loop_id and
            skip already-completed steps. The original goal and steps are
            replayed from checkpoint; new steps start from where it left off.

    Returns:
        LoopResult with full outcome.
    """
    # Reset per-run state (cost-warn flag persists across calls otherwise)
    run_agent_loop._cost_warned = False  # type: ignore[attr-defined]

    # Phase A: Initialize loop state
    ctx, _early_return = _initialize_loop(
        goal,
        project=project,
        repo_path=repo_path,
        model=model,
        backend=backend,
        adapter=adapter,
        dry_run=dry_run,
        verbose=verbose,
        interrupt_queue=interrupt_queue,
        hook_registry=hook_registry,
        ancestry_context_extra=ancestry_context_extra,
        permission_context=permission_context,
        continuation_depth=continuation_depth,
        cost_budget=cost_budget,
        token_budget=token_budget,
        ralph_verify=ralph_verify,
        max_steps=max_steps,
        max_iterations=max_iterations,
        step_callback=step_callback,
        loop_reason=loop_reason,
        parent_loop_id=parent_loop_id,
    )
    if _early_return is not None:
        return _early_return

    ctx.channel = channel  # Phase 64C: mid-loop escalation channel

    # Model constants for the session-level tier floor ordering (Phase 57).
    from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER
    _TIER_ORDER = {MODEL_CHEAP: 0, MODEL_MID: 1, MODEL_POWER: 2}

    # Unpack ctx into the locals the orchestrator still threads into phase
    # calls and the auto-recovery re-run.
    loop_id = ctx.loop_id
    start_ts = ctx.start_ts
    project = ctx.project
    adapter = ctx.adapter
    interrupt_queue = ctx.interrupt_queue
    _perm_ctx = ctx.perm_ctx

    # Bind the run-scoped default cwd to this loop's project dir so EVERY
    # agentic subprocess (verify/quality_gate/pre_flight/refinement/claim_probe)
    # writes in-workspace instead of inheriting Maro's launch cwd. The executor
    # still binds cwd per-call (same value); recursive/fan-out sub-loops re-set
    # this on their own entry. Not reset on exit by design — quality_gate runs
    # after the loop returns and should inherit the same project dir (handle.py
    # also scopes it explicitly). Tests reset it via an autouse fixture.
    try:
        from llm import set_default_subprocess_cwd
        if project:
            set_default_subprocess_cwd(str(_project_dir_root() / project))
    except Exception:
        pass

    def _resolve_tools() -> list:
        """Re-query tool registry on each call to pick up runtime-registered tools."""
        return (
            _get_tools_for_role(_perm_ctx.role, _perm_ctx.deny_patterns)
            if _perm_ctx is not None else list(_EXECUTE_TOOLS)
        )

    # Phase B: Decompose goal into steps
    ctx.set_phase(LoopPhase.DECOMPOSE)
    steps, _prereq_context, _lessons_context, _skills_context, _cost_context, _had_no_matching_skill = _decompose_goal(
        ctx,
        preset_steps=preset_steps,
        max_steps=max_steps,
        knowledge_sub_goals=knowledge_sub_goals,
        permission_context=permission_context,
    )

    # Phase C: Pre-flight checks
    ctx.set_phase(LoopPhase.PRE_FLIGHT)
    steps, _pf, _pf_early_return = _preflight_checks(
        ctx, steps,
        resume_from_loop_id=resume_from_loop_id,
        parallel_fan_out=parallel_fan_out,
    )
    if _pf_early_return is not None:
        return _pf_early_return

    # Unpack pre-flight results into locals used by subsequent phases
    _resume_completed = _pf["resume_completed"]
    _pf_review = _pf["pf_review"]
    _clean_steps = _pf["clean_steps"]
    _deps = _pf["deps"]
    _levels = _pf["levels"]
    _parallel_levels = _pf["parallel_levels"]
    _manifest_steps = _pf["manifest_steps"]
    _replan_count = _pf["replan_count"]
    _loop_shared_ctx = _pf["loop_shared_ctx"]
    _proj_fanout_dir = _pf["proj_fanout_dir"]
    _use_dag = _pf["use_dag"]
    _use_fanout = _pf["use_fanout"]

    # Phase D: Parallel fan-out (early return if applicable)
    if _use_dag or _use_fanout:
        ctx.set_phase(LoopPhase.PARALLEL)
        _parallel_result = _run_parallel_path(
            ctx, steps,
            clean_steps=_clean_steps,
            deps=_deps,
            levels=_levels,
            parallel_levels=_parallel_levels,
            parallel_fan_out=parallel_fan_out,
            proj_fanout_dir=_proj_fanout_dir,
            loop_shared_ctx=_loop_shared_ctx,
            use_dag=_use_dag,
            resolve_tools_fn=_resolve_tools,
        )
        if _parallel_result is not None:
            return _parallel_result

    # Phase E: Shape steps and write to NEXT.md
    ctx.set_phase(LoopPhase.PREPARE)
    steps, step_indices, _manifest_steps = _prepare_execution(ctx, steps, _manifest_steps)

    # Phase F: Main execute loop
    ctx.set_phase(LoopPhase.EXECUTE)
    _ex = _execute_main_loop(
        ctx, steps, step_indices,
        resume_completed=_resume_completed,
        prereq_context=_prereq_context,
        pf_review=_pf_review,
        levels=_levels,
        manifest_steps=_manifest_steps,
        replan_count=_replan_count,
        loop_shared_ctx=_loop_shared_ctx,
        resolve_tools_fn=_resolve_tools,
        tier_order=_TIER_ORDER,
        parallel_fan_out=parallel_fan_out,
    )
    step_outcomes = _ex["step_outcomes"]
    loop_status = _ex["loop_status"]
    stuck_reason = _ex["stuck_reason"]
    total_tokens_in = _ex["total_tokens_in"]
    total_tokens_out = _ex["total_tokens_out"]
    interrupts_applied = _ex["interrupts_applied"]
    _march_of_nines_alert = _ex["march_of_nines_alert"]
    _manifest_steps = _ex["manifest_steps"]
    _replan_count = _ex["replan_count"]
    _milestone_expanded = _ex["milestone_expanded"]
    _failure_chain = _ex["failure_chain"]
    _recovery_step_count = _ex["recovery_step_count"]
    _scratchpad = _ex["scratchpad"]
    _scratchpad_lock = _ex["scratchpad_lock"]
    goal = _ex["goal"]
    max_iterations = _ex["max_iterations"]

    # Phase G: Build result, write artifacts, run finalize side-effects
    ctx.set_phase(LoopPhase.FINALIZE)
    result = _build_result_and_finalize(
        ctx,
        step_outcomes=step_outcomes,
        loop_status=loop_status,
        stuck_reason=stuck_reason,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        interrupts_applied=interrupts_applied,
        march_of_nines_alert=_march_of_nines_alert,
        pf_review=_pf_review,
        manifest_steps=_manifest_steps,
        replan_count=_replan_count,
        start_ts=start_ts,
        milestone_expanded=_milestone_expanded,
        had_no_matching_skill=_had_no_matching_skill,
        failure_chain=_failure_chain,
        recovery_step_count=_recovery_step_count,
        scratchpad=_scratchpad,
        scratchpad_lock=_scratchpad_lock,
    )

    # Phase 45: Auto-recovery — if loop stuck with a low-risk auto-apply recovery,
    # retry once with adjusted parameters. Only fires on first attempt (no recursion).
    _auto_recovery_attempted = getattr(run_agent_loop, "_recovery_in_progress", False)
    if (result.status == "stuck" and not dry_run and not _auto_recovery_attempted):
        try:
            from introspect import diagnose_loop as _diag_fn, plan_recovery as _plan_fn
            _diag = _diag_fn(loop_id)
            _recovery = _plan_fn(_diag)
            if _recovery and _recovery.auto_apply and _recovery.risk == "low":
                log.info("auto-recovery: %s (class=%s)", _recovery.action, _diag.failure_class)
                # Captain's log
                try:
                    from captains_log import log_event, AUTO_RECOVERY
                    log_event(
                        event_type=AUTO_RECOVERY,
                        subject=_diag.failure_class,
                        summary=f"Auto-recovery triggered: {_recovery.action}. Class: {_diag.failure_class}.",
                        context={"action": _recovery.action, "risk": _recovery.risk, "params": dict(_recovery.params)},
                        loop_id=loop_id,
                    )
                except Exception as _clog_exc:
                    log.debug("auto-recovery captain's log write failed: %s", _clog_exc)
                _new_params = dict(_recovery.params)
                _new_max_steps = _new_params.pop("max_steps", max_steps)
                _new_max_iter = _new_params.pop("max_iterations", max_iterations)
                # Guard against infinite recursion
                run_agent_loop._recovery_in_progress = True  # type: ignore[attr-defined]
                try:
                    result = run_agent_loop(
                        goal=goal,
                        project=project,
                        model=model,
                        adapter=adapter,
                        max_steps=_new_max_steps,
                        max_iterations=_new_max_iter,
                        dry_run=dry_run,
                        verbose=verbose,
                        interrupt_queue=interrupt_queue,
                        hook_registry=hook_registry,
                        ancestry_context_extra=ancestry_context_extra,
                        step_callback=step_callback,
                        parallel_fan_out=parallel_fan_out,
                        token_budget=token_budget,
                    )
                    log.info("auto-recovery result: status=%s", result.status)
                finally:
                    run_agent_loop._recovery_in_progress = False  # type: ignore[attr-defined]
        except ImportError:
            pass
        except Exception as exc:
            log.debug("auto-recovery failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Decompose + Execute — thin wrappers around planner.py and step_exec.py.
# The full implementations live in those modules. These wrappers exist so
# internal callers and tests that import from agent_loop still work.
# ---------------------------------------------------------------------------

# _execute_step and _generate_refinement_hint are imported from step_exec at module top.
# _decompose (delegates to planner.decompose) moved to loop_planning.py (Tier 3 split).

# Artifact/manifest/log writers + _goal_to_slug moved to loop_artifacts.py
# (Tier 3 split).
from loop_artifacts import (
    _write_step_artifact,
    _plan_manifest_path,
    _write_plan_manifest,
    _write_loop_log,
    _goal_to_slug,
)

# Dependency-pattern detection, exec+analyze step shaping, loop-context
# assembly, goal decomposition, and pre-flight/prepare-execution phases
# moved to loop_planning.py (Tier 3 split).
from loop_planning import (
    _DEPENDENCY_PATTERNS,
    _DEP_RE,
    _steps_are_independent,
    _is_combined_exec_analyze,
    _split_exec_analyze,
    _shape_steps,
    _build_loop_context,
    _decompose_goal,
    _preflight_checks,
    _prepare_execution,
    _decompose,
)

# BlockedStepContext, convergence tracking, missing-input checks,
# _BlockDecision, timeout-split generation, diagnosis consult, and the
# blocked-step handlers moved to loop_blocked.py (Tier 3 split).
from loop_blocked import (
    BlockedStepContext,
    _process_blocked_step,
    _error_fingerprint,
    _is_converging,
    _sibling_failure_rate,
    _looks_like_missing_input,
    _is_input_consuming_step,
    _BlockDecision,
    _generate_timeout_split,
    _consult_diagnosis,
    _handle_blocked_step,
)

# _run_parallel_batch, _run_parallel_path, _run_steps_parallel, and
# _run_steps_dag moved to loop_parallel.py (Tier 3 split).
from loop_parallel import (
    _run_parallel_batch,
    _run_parallel_path,
    _run_steps_parallel,
    _run_steps_dag,
)

# ---------------------------------------------------------------------------
# Dry-run adapter (for testing without API credits)
# ---------------------------------------------------------------------------

class _DryRunAdapter:
    """Simulates LLM responses for testing."""

    def complete(self, messages, *, tools=None, tool_choice="auto", max_tokens=4096, temperature=0.3, **kwargs):
        from llm import LLMResponse, ToolCall

        # Extract user message content for context
        user_content = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )

        # Decompose request → return fake steps
        if "decompose" in user_content.lower() or "concrete steps" in user_content.lower():
            goal_line = next((l for l in user_content.split("\n") if l.startswith("Goal:")), "Goal: test")
            goal = goal_line.replace("Goal:", "").strip()
            words = goal.split()[:6]
            steps = [
                f"Research {' '.join(words[:3])}",
                f"Analyze findings from {' '.join(words[:3])}",
                f"Produce summary of {goal[:40]}",
            ]
            return LLMResponse(
                content=json.dumps(steps),
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=30,
            )

        # Execute step → call complete_step
        if tools and tool_choice == "required":
            step_line = next(
                (l for l in user_content.split("\n") if "Current step" in l), "Current step: do work"
            )
            step_text = step_line.split(":", 1)[-1].strip() if ":" in step_line else step_line
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(
                    name="complete_step",
                    arguments={
                        "result": f"[dry-run] Completed: {step_text}",
                        "summary": f"[dry-run] {step_text[:60]}",
                    },
                )],
                stop_reason="tool_use",
                input_tokens=80,
                output_tokens=40,
            )

        return LLMResponse(
            content="[dry-run] OK",
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=5,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="maro-run", description="Run Maro's autonomous loop on a goal")
    parser.add_argument("goal", nargs="+", help="Goal description")
    parser.add_argument("--project", "-p", help="Project slug (auto-created if not exists)")
    parser.add_argument("--model", "-m", help="LLM model string (e.g. anthropic/claude-haiku-4-5)")
    parser.add_argument("--max-steps", type=int, default=6, help="Max decomposition steps (default: 6)")
    parser.add_argument("--max-iterations", type=int, default=20, help="Hard cap on LLM calls (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without LLM API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    parser.add_argument(
        "--backend", "-b",
        choices=["auto", "anthropic", "openrouter", "openai", "subprocess", "codex"],
        default=None,
        help="LLM backend (default: auto-detect; MARO_BACKEND env var also accepted)",
    )

    args = parser.parse_args(argv)
    goal = " ".join(args.goal)

    result = run_agent_loop(
        goal,
        project=args.project,
        model=args.model,
        backend=args.backend,
        max_steps=args.max_steps,
        max_iterations=args.max_iterations,
        dry_run=args.dry_run,
        verbose=args.verbose or True,
    )

    print(result.summary())
    return 0 if result.status == "done" else 1


# ---------------------------------------------------------------------------
# Concurrent project support (Phase 8)
# ---------------------------------------------------------------------------

def run_parallel_loops(
    goals: List[str],
    *,
    max_workers: int = 3,
    **kwargs,
) -> List[LoopResult]:
    """Run multiple goals concurrently via ThreadPoolExecutor.

    Args:
        goals: List of goal strings to execute in parallel.
        max_workers: Maximum concurrent threads (default: 3).
        **kwargs: Passed through to run_agent_loop() for each goal.

    Returns:
        List of LoopResult in same order as input goals.
    """
    import concurrent.futures

    if not goals:
        return []

    effective_workers = min(max_workers, len(goals))

    with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = [
            executor.submit(run_agent_loop, goal, **kwargs)
            for goal in goals
        ]
        results = [f.result() for f in futures]

    return results


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
