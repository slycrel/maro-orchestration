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
from loop_init import _budget_gate, _initialize_loop, _DryRunAdapter
from loop_post_step import (
    _handle_budget_ceiling,
    _check_step_too_broad,
    _compute_march_of_nines,
    _write_iteration_artifacts,
    _check_loop_interrupts,
    _post_step_checks,
    _local_auto_ralph_enabled,
    _current_run_dir_safe,
    _record_loop_decision,
    _run_ralph_verify,
    _process_done_step,
)
from loop_finalize import _build_result_and_finalize, _finalize_loop
from loop_execute import _select_step_adapter, _run_scoped_validator, _execute_main_loop

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


# _handle_budget_ceiling, _check_step_too_broad, _compute_march_of_nines,
# _write_iteration_artifacts, _check_loop_interrupts, _post_step_checks,
# _local_auto_ralph_enabled, _current_run_dir_safe, _record_loop_decision,
# _run_ralph_verify, and _process_done_step moved to loop_post_step.py
# (Tier 3 split).



# _select_step_adapter, _run_scoped_validator, and _execute_main_loop
# moved to loop_execute.py (Tier 3 split).


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
    _recovery_in_progress: bool = False,  # internal: set by the Phase 45 auto-recovery re-run to prevent recursion
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
    # A project-less run is NOT exempt (BACKLOG #1, 3rd repro: dispatched
    # goals arrived with project=None and the whole run executed with the
    # inherited launch cwd — relative writes leaked into the repo root).
    # Fall back to the goal-slug project dir — the same identity the scope
    # pass derives — and create it, since Popen raises on a missing cwd.
    try:
        from llm import set_default_subprocess_cwd
        _fence_dir = _project_dir_root() / (project or _goal_to_slug(ctx.goal))
        _fence_dir.mkdir(parents=True, exist_ok=True)
        set_default_subprocess_cwd(str(_fence_dir))
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
    if (result.status == "stuck" and not dry_run and not _recovery_in_progress):
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
                # _recovery_in_progress=True guards against infinite recursion —
                # passed as a call-stack-local arg (not shared mutable state) so
                # concurrent run_agent_loop calls (run_parallel_loops) can't race.
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
                    _recovery_in_progress=True,
                )
                log.info("auto-recovery result: status=%s", result.status)
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

# _DryRunAdapter moved to loop_init.py (Tier 3 split).


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
