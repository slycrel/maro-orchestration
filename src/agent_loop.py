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
    ContextContribution,
    ContributionLedger,
    render_contributions,
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
    admission_wait_s: Optional[float] = None,  # seconds to poll a busy project slot; None = config (default: refuse immediately)
    defer_learning: bool = False,  # data-r2-01: caller runs closure + finalize_deferred_learning() afterwards — skip verdict-blind lesson extraction/crystallization at finalize
    measurement_class: str = "",  # explicit organic/smoke/control/benchmark provenance; empty = unknown direct caller
    handle_id: str = "",  # top-level request key; continuations reuse it for report dedup
    introspection_access: bool = False,  # introspection-shaped goal: containerized steps get ro run-records + maro source (decree 2026-07-18)
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
        admission_wait_s=admission_wait_s,
        defer_learning=defer_learning,
        measurement_class=measurement_class,
        handle_id=handle_id,
    )
    if _early_return is not None:
        return _early_return

    # BACKLOG #17 sub-item 1: scope the ambient loop_id for the duration
    # of this run so log_event() calls deep in the execution call stack
    # (skills.py, evolver.py, knowledge_lens.py, ...) get attributed
    # without threading loop_id through every signature.
    from captains_log import loop_id_scope
    with loop_id_scope(ctx.loop_id):
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
            from llm import set_default_subprocess_cwd, set_default_container_rw_roots
            import container_exec as _ce
            # Reset run-scoped container state FIRST, before anything can raise —
            # a resumed/sequential run in the same process/context must never
            # inherit a prior run's suppression flag or authorized rw roots
            # (adversarial-review 2026-07-13, finding D).
            _ce.set_container_suppressed(False)
            set_default_container_rw_roots([])
            # Introspection provisioning is opt-in per run and must never be
            # inherited from a prior run in this context (same finding-D
            # reasoning as the two resets above).
            _ce.set_introspection_run(bool(introspection_access))

            if getattr(ctx, "run_worktree", None) is not None:
                # busy_policy=worktree: the whole run works in its isolated
                # worktree; loop_finalize merges back into the project dir.
                _fence_dir = ctx.run_worktree.path
            else:
                _fence_dir = _project_dir_root() / (project or _goal_to_slug(ctx.goal))
            _fence_dir.mkdir(parents=True, exist_ok=True)

            # Self-dev scratch clone (C3, CONTAINER_EXECUTOR_DESIGN §4): when this
            # run is CONFIGURED to containerize (mode on/require — decided on
            # config intent, NOT a live docker probe, so the decision can't race
            # the daemon between here and an executor call) and the fence dir is a
            # git repo, the live repo is never mounted rw — clone it into a
            # throwaway scratch, work the copy, merge back host-side at finalize.
            # If the clone can't be made, FAIL CLOSED: suppress containerization
            # so the live repo is never mounted rw (adversarial-review 2026-07-13,
            # findings A/M3/S2/A1). Off by default (mode off → skip entirely), so
            # a non-container run is byte-identical to before.
            _live_repo = None
            _container_intended = False
            try:
                _container_intended = _ce.container_configured()
                if ctx.container_clone is None and _container_intended:
                    import worktree as _wt
                    if _wt.is_git_repo(_fence_dir):
                        _live_repo = str(_fence_dir)
                        _clone = None
                        try:
                            _clone = _wt.provision_clone(_fence_dir, "container", loop_id=ctx.loop_id)
                        except Exception as _pc_exc:
                            log.warning("container scratch-clone provision error: %s", _pc_exc)
                        if _clone is not None:
                            ctx.container_clone = _clone
                            _fence_dir = _clone.path
                            log.info("container self-dev: working in scratch clone %s (branch %s)",
                                     _clone.path, _clone.branch)
                        else:
                            _ce.set_container_suppressed(True)
                            log.warning(
                                "container self-dev: could not provision a scratch clone of %s — "
                                "executor steps run on the HOST under the fence, NOT containerized "
                                "(the live repo is never mounted rw)", _live_repo)
            except Exception as _cc_exc:
                log.warning("container scratch-clone setup skipped: %s", _cc_exc)
                # Setup itself blew up while intending to containerize a git repo
                # → fail closed rather than risk mounting the live repo rw.
                if _container_intended and ctx.container_clone is None:
                    _ce.set_container_suppressed(True)

            set_default_subprocess_cwd(str(_fence_dir))

            # Container fence rw roots (C3): the goal's declared roots +
            # validate.write_fence_allow join the container's writable mount set
            # (the cwd is always rw). Host /tmp and the workspace root are
            # hard-excluded by build_mount_map (design §4); the live repo of a
            # self-dev run is dropped here too so a goal that names its own repo
            # path can't re-introduce it as a rw mount (adversarial-review
            # 2026-07-13, finding B). Read only in the container branch of
            # _run_subprocess_safe.
            try:
                from artifact_check import goal_declared_roots as _gdr
                from config import get as _cfg_get
                _rw_roots = list(_gdr(ctx.goal))
                for _r in (_cfg_get("validate.write_fence_allow", []) or []):
                    if _r:
                        _rw_roots.append(os.path.expanduser(str(_r)))
                if _live_repo:
                    _lr = os.path.realpath(_live_repo)
                    _rw_roots = [r for r in _rw_roots
                                 if os.path.realpath(str(r)) != _lr]
            except Exception as _rw_exc:
                # Root discovery is optional enrichment. An empty list leaves
                # only the already-bound cwd writable, which is the safe fallback.
                log.warning("container rw-roots discovery failed; using cwd only: %s", _rw_exc)
                _rw_roots = []
            # Binding the safe fallback is mandatory: swallowing a setter
            # failure here could retain a previous run's writable-root policy.
            set_default_container_rw_roots(_rw_roots)
        except Exception as _fence_exc:
            _fence_msg = (
                f"execution fence setup failed: "
                f"{type(_fence_exc).__name__}: {_fence_exc}"
            )
            log.error("loop refused before decomposition — %s", _fence_msg)

            # Nothing agentic has run yet, so any clone/worktree created during
            # admission/fence setup is safe to remove without a merge-back.
            if getattr(ctx, "container_clone", None) is not None:
                try:
                    import worktree as _wtmod
                    _wtmod.cleanup_clone(ctx.container_clone)
                    ctx.container_clone = None
                except Exception as _cleanup_exc:
                    log.warning("execution fence scratch-clone cleanup failed: %s", _cleanup_exc)
            if getattr(ctx, "run_worktree", None) is not None:
                try:
                    import worktree as _wtmod
                    _wtmod.cleanup(ctx.run_worktree)
                    _wtmod.prune(ctx.run_worktree.repo_dir)
                    ctx.run_worktree = None
                except Exception as _cleanup_exc:
                    log.warning("execution fence worktree cleanup failed: %s", _cleanup_exc)

            # Best-effort neutralization for later non-loop calls in the same
            # process; the refusal itself does not depend on these succeeding.
            for _neutralize, _label in (
                (lambda: set_default_subprocess_cwd(None), "subprocess cwd"),
                (lambda: set_default_container_rw_roots([]), "rw-root policy"),
                (lambda: _ce.set_container_suppressed(True), "container suppression"),
                (lambda: _ce.set_introspection_run(False), "introspection access"),
            ):
                try:
                    _neutralize()
                except Exception as _neutralize_exc:
                    log.warning(
                        "execution fence refusal could not neutralize %s: %s",
                        _label, _neutralize_exc,
                    )
            try:
                if getattr(ctx, "project_slot", None) is not None:
                    ctx.project_slot.release()
                    ctx.project_slot = None
            except Exception as _release_exc:
                log.warning("execution fence refusal project-slot release failed: %s", _release_exc)
            try:
                if getattr(ctx, "run_lease", None) is not None:
                    ctx.run_lease.release()
                    ctx.run_lease = None
            except Exception as _release_exc:
                log.warning("execution fence refusal run-lease release failed: %s", _release_exc)
            try:
                from interrupt import clear_loop_running
                clear_loop_running()
            except Exception as _release_exc:
                log.warning("execution fence refusal running-state clear failed: %s", _release_exc)
            try:
                from observe import write_event as _write_event
                _write_event(
                    "loop_done", goal=ctx.goal, project=ctx.project or "",
                    loop_id=ctx.loop_id, status="stuck", detail=_fence_msg,
                )
            except Exception:
                pass
            return LoopResult(
                loop_id=ctx.loop_id,
                goal=ctx.goal,
                project=ctx.project or "",
                steps=[],
                status="stuck",
                stuck_reason=_fence_msg,
                elapsed_ms=int((time.monotonic() - ctx.started_at) * 1000),
            )

        # In-fence scratch space is an inspectability convenience, not part of
        # the cwd/policy safety boundary. Keep it best-effort and visible.
        try:
            from config import workspace_root as _ws_root
            (_ws_root() / "tmp").mkdir(parents=True, exist_ok=True)
        except Exception as _scratch_exc:
            log.warning("workspace scratch directory setup skipped: %s", _scratch_exc)

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
        _resume_executor_session = _pf["resume_executor_session"]
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
                # 2026-07-08 adversarial review (finding #1): this early return
                # bypasses _build_result_and_finalize() entirely — true for every
                # finalize side effect (telegram notify, introspection, Reflexion
                # memory), not just the run-visibility report; fixing that whole
                # gap is a separate, larger effort out of scope here. This
                # narrowly ensures the report/index still reach a terminal state
                # for parallel/DAG runs instead of being silently stuck "running".
                try:
                    from loop_report import write_run_report as _write_run_report, write_runs_index as _write_runs_index
                    # 2026-07-08 review, round 2 (unanimous, all 5 reviewers):
                    # the round-1 fix froze the report and forced the index but
                    # never wrote build/loop-*-log.json — the ONLY source
                    # write_runs_index() reads token/step totals from. Without
                    # it, a parallel run's index row shows a report link but "-"
                    # tokens/status forever. _write_loop_log is the same writer
                    # the sequential finalize path already calls; parallel just
                    # never had it, independent of this feature.
                    _write_loop_log(
                        project=ctx.project,
                        loop_id=ctx.loop_id,
                        goal=ctx.goal,
                        status=_parallel_result.status,
                        steps=_parallel_result.steps,
                        start_ts=ctx.start_ts,
                        elapsed_ms=_parallel_result.elapsed_ms,
                        stuck_reason=_parallel_result.stuck_reason,
                    )
                    if ctx.project and _manifest_steps:
                        _write_run_report(
                            project=ctx.project,
                            loop_id=ctx.loop_id,
                            goal=ctx.goal,
                            planned_steps=_manifest_steps,
                            start_ts=ctx.start_ts,
                            step_outcomes=_parallel_result.steps,
                            status=_parallel_result.status,
                            elapsed_ms=_parallel_result.elapsed_ms,
                            replan_count=_replan_count,
                        )
                    _write_runs_index(force=True)
                except Exception as _rep_exc:
                    log.warning("run report write failed for parallel loop %s: %s", ctx.loop_id, _rep_exc)
                return _parallel_result

        # Phase E: Shape steps and write to NEXT.md
        ctx.set_phase(LoopPhase.PREPARE)
        steps, step_indices, _manifest_steps = _prepare_execution(ctx, steps, _manifest_steps)

        # Phase F: Main execute loop
        ctx.set_phase(LoopPhase.EXECUTE)
        # Runaway cost circuit (BACKLOG #23e): armed for the execute phase
        # ONLY — finalize/closure/quality-gate must never be refused a call
        # (budget-breaker demotion lesson, 8f8344a). Runaway-only by design:
        # ceiling = multiplier x cost_budget, ABOVE the between-step hard
        # stop, so legit long work under budget never sees it.
        _disarm_runaway = None
        if ctx.cost_budget:
            try:
                from config import get as _rc_get
                _rc_mult = _rc_get("budget.runaway_multiplier", 1.5)
                _rc_mult = float(_rc_mult) if _rc_mult is not None else 0.0
                if _rc_mult > 0:
                    from llm import arm_cost_meter
                    _disarm_runaway = arm_cost_meter(ctx.cost_budget * _rc_mult)
            except Exception as _rc_exc:
                log.warning("runaway cost circuit not armed: %s", _rc_exc)
        try:
            _ex = _execute_main_loop(
                ctx, steps, step_indices,
                resume_completed=_resume_completed,
                resume_executor_session=_resume_executor_session,
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
        finally:
            if _disarm_runaway is not None:
                _disarm_runaway()
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
                        measurement_class=ctx.measurement_class,
                        handle_id=ctx.handle_id,
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

    # No copy_context() wrap here, deliberately: each pool thread gets its own
    # root context, and run_agent_loop sets its own run-scoped ContextVars
    # (subprocess cwd; run-dir stays with the spawning handle). Wrapping would
    # leak the *caller's* run context into every goal.
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
