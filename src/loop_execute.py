"""Main step-execution loop for the agent loop (Tier 3 split of agent_loop.py).

Extracted verbatim from agent_loop.py — per-step model tier selection
(_select_step_adapter), the local-validator lifecycle decorator
(_run_scoped_validator), and _execute_main_loop itself: the Phase F
step-iteration engine (parallel-peer batching, ralph-verify, post-step
checks, stuck detection, adaptive-execution triggers, recovery and
interrupt handling) that runs until no steps remain or a terminal status
is reached.
"""

from __future__ import annotations

import functools
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loop_types import (
    LoopContext,
    StepOutcome,
    step_from_decompose,
    _orch,
    _project_dir_root,
    _configure_logging,
)
from loop_artifacts import _write_step_artifact
from loop_planning import _is_combined_exec_analyze, _split_exec_analyze, _shape_steps
from loop_blocked import BlockedStepContext, _process_blocked_step
from loop_parallel import _run_parallel_batch
from loop_post_step import (
    _handle_budget_ceiling,
    _local_auto_ralph_enabled,
    _run_ralph_verify,
    _post_step_checks,
    _record_loop_decision,
    _process_done_step,
    _write_iteration_artifacts,
    _check_loop_interrupts,
)
from loop_finalize import _build_result_and_finalize
from step_exec import execute_step as _execute_step

log = logging.getLogger("maro.loop")


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

    # Fence intent-widening (2026-07-04): paths the goal text explicitly names
    # join the write fence for this run, auditable via FENCE_EXTENDED. Computed
    # once here; consumed by the per-step scavenge/write-fence blocks below.
    _goal_fence_roots: list = []
    try:
        from artifact_check import goal_declared_roots as _gdr
        _goal_fence_roots = _gdr(goal)
        if _goal_fence_roots:
            from captains_log import log_event as _fe_log, FENCE_EXTENDED
            _fe_log(
                FENCE_EXTENDED,
                subject=goal[:80],
                summary=f"fence widened to {len(_goal_fence_roots)} goal-declared root(s)",
                context={"roots": _goal_fence_roots},
                loop_id=getattr(ctx, "loop_id", "") or None,
            )
    except Exception:
        _goal_fence_roots = []
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
    # Cuts-first planning: boundary expansions this loop. Capped at 2 so a
    # replan that re-draws cuts gets one more boundary, but a pathological
    # plan can't loop expand-forever.
    _boundary_expansions: int = 0

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

        # Cuts-first boundary expansion (Qix-cuts decree, 2026-07-10): a
        # [boundary] step is a plan-here-later marker from planner cuts-first
        # mode. Expand it into real steps WITH the probe findings in context —
        # this is the whole point of cuts: the plan for the bounded remainder
        # is drawn after the probes have collapsed the space, not before.
        # On failure the step runs as-is (tag stripped): the goal degrades to
        # one broad step instead of dying.
        from planner import is_boundary_step as _is_boundary_step, strip_boundary_tag as _strip_boundary
        if _is_boundary_step(step_text) and _boundary_expansions < 2:
            _boundary_expansions += 1
            _remainder_goal = _strip_boundary(step_text)
            step_text = _remainder_goal
            try:
                from planner import decompose as _bd_decompose
                _evidence_parts: List[str] = []
                for _po in step_outcomes[-4:]:
                    _po_res = (getattr(_po, "result", "") or "")[:1500]
                    _evidence_parts.append(
                        f"- Probe: {_po.text}\n  Outcome ({_po.status}): {_po_res}")
                _evidence = ""
                if _evidence_parts:
                    _evidence = (
                        "PROBE FINDINGS (evidence gathered before this plan — plan "
                        "INSIDE what these findings establish; do not re-do the probes):\n"
                        + "\n".join(_evidence_parts))
                # #23c: the remainder text may drop the goal's stated priority
                # phrasing, so decompose's own detector can't see it — carry
                # the binding directive across the boundary explicitly.
                from planner import (goal_states_priority_order as _gspo,
                                     _PRIORITY_DIRECTIVE as _prio_directive)
                if _gspo(goal) and not _gspo(_remainder_goal):
                    _evidence = (_evidence + "\n\n" if _evidence else "") + (
                        f"{_prio_directive}\nOriginal goal (source of the "
                        f"priority order): {goal}")
                _bd_sub = _bd_decompose(
                    _remainder_goal, adapter, max_steps=5,
                    ancestry_context=_evidence, allow_cuts=False,
                )
                if _bd_sub and _bd_sub != [_remainder_goal]:
                    _bd_sub = _shape_steps(_bd_sub, label="boundary-expand")
                    remaining_steps[:0] = _bd_sub
                    remaining_indices[:0] = [-1] * len(_bd_sub)
                    log.info("boundary-expand: %r → %d step(s), %d finding(s) in context",
                             _remainder_goal[:60], len(_bd_sub), len(_evidence_parts))
                    try:
                        from captains_log import log_event as _bd_log_event, BOUNDARY_EXPANDED as _BD_EVT
                        _bd_log_event(
                            _BD_EVT,
                            subject="boundary_expanded",
                            summary=f"Boundary step expanded into {len(_bd_sub)} step(s) "
                                    f"with {len(_evidence_parts)} probe finding(s) in context.",
                            context={
                                "loop_id": loop_id,
                                "remainder": _remainder_goal[:200],
                                "sub_steps": [s[:120] for s in _bd_sub],
                            },
                        )
                    except Exception:
                        pass
                    if verbose:
                        import sys as _bd_sys
                        print(f"[maro] boundary step expanded → {len(_bd_sub)} step(s) "
                              f"(probe evidence in context)", file=_bd_sys.stderr, flush=True)
                    continue  # Run the drawn plan instead of the marker step
                log.warning("boundary-expand: decompose returned nothing usable; "
                            "executing boundary step as one broad step")
            except Exception as _bd_exc:
                log.warning("boundary-expand failed (%s); executing boundary step as-is", _bd_exc)

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
                _ms_sub = _ms_decompose(step_text, adapter, max_steps=5, allow_cuts=False)
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

        # In-flight marker, written BEFORE the step: a mid-step crash leaves
        # {index, started_at, pid} in the checkpoint so resume knows this step
        # may have partial side effects (vs. never started — the hermes goal-2
        # wound). The post-step checkpoint write clears it.
        try:
            from checkpoint import write_checkpoint as _inflight_ckpt
            _inflight_ckpt(ctx.loop_id, ctx.goal, ctx.project or "",
                           steps, step_outcomes, in_flight_index=step_idx)
        except Exception as _if_exc:
            log.debug("in-flight checkpoint write failed (non-fatal): %s", _if_exc)

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

        # Scavenging diagnostic (BACKLOG #1): flag out-of-fence file access from
        # the real tool transcript. Detection never changes step status; the
        # tier-a write fence below (config-gated) consumes the report's writes.
        _sc_report = None
        try:
            from config import get as _sc_cfg_get
            if bool(_sc_cfg_get("validate.scavenge_detect", True)) and outcome.get("tool_events"):
                from artifact_check import (
                    detect_out_of_fence_access as _sc_detect,
                    fence_allow_roots as _sc_allow_roots,
                )
                from config import workspace_root as _sc_ws_root
                _sc_report = _sc_detect(
                    outcome.get("tool_events"),
                    [_proj_artifact_dir, str(_sc_ws_root())]
                    + _sc_allow_roots() + _goal_fence_roots,
                )
                if _sc_report.flagged:
                    log.warning(
                        "SCAVENGE step=%d reads=%d writes=%d%s",
                        step_idx, len(_sc_report.reads), len(_sc_report.writes),
                        " (truncated)" if _sc_report.truncated else "",
                    )
                    from captains_log import log_event as _sc_log_event, SCAVENGE_DETECTED
                    _sc_log_event(
                        SCAVENGE_DETECTED,
                        subject=f"step {step_idx}",
                        summary=(
                            f"worker touched {len(_sc_report.reads)} path(s) outside the fence"
                            + (f", wrote {len(_sc_report.writes)}" if _sc_report.writes else "")
                        ),
                        context={
                            "step_text": step_text[:200],
                            "reads": _sc_report.reads,
                            "writes": _sc_report.writes,
                            "truncated": _sc_report.truncated,
                            "fence_project_dir": _proj_artifact_dir,
                        },
                        loop_id=getattr(ctx, "loop_id", "") or None,
                    )
        except Exception:
            pass

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

        # Phase 33: token budget — abort gracefully if exceeded.
        # Only a run with work LEFT gets demoted: the breaker exists to stop
        # FURTHER spend, and when the plan is fully consumed there is none —
        # run 692bd96f (2026-07-11) finished all steps + passed closure, then
        # the cost stop after the final step stamped it stuck/failed.
        if token_budget is not None and (total_tokens_in + total_tokens_out) >= token_budget:
            _budget_note = (
                f"token_budget={token_budget} exceeded "
                f"({total_tokens_in + total_tokens_out} total tokens after step {step_idx})"
            )
            if remaining_steps:
                loop_status = "stuck"
                stuck_reason = _budget_note
            else:
                log.warning("budget exceeded on final step (run kept done): %s",
                            _budget_note)
            if verbose:
                print(f"[maro] {_budget_note}", file=sys.stderr, flush=True)
            break

        # Cost budget — warn at 80%, hard stop at budget + 20% slush.
        # Truthiness (not `is not None`): 0 means uncapped, same as the
        # budget.per_run_usd convention — and 0.0 must never reach the division.
        if cost_budget and _total_cost > 0:
            _cost_pct = _total_cost / cost_budget * 100
            _slush = cost_budget * 0.2
            if _total_cost >= cost_budget + _slush:
                _budget_note = (
                    f"cost_budget=${cost_budget:.2f} + slush=${_slush:.2f} exceeded "
                    f"(${_total_cost:.4f} total after step {step_idx})"
                )
                # Same finished-plan carve-out as the token breaker above.
                if remaining_steps:
                    loop_status = "stuck"
                    stuck_reason = _budget_note
                    log.warning("cost hard stop: %s", stuck_reason)
                else:
                    log.warning("cost budget exceeded on final step "
                                "(run kept done): %s", _budget_note)
                if verbose:
                    print(f"[maro] {_budget_note}", file=sys.stderr, flush=True)
                break
            elif _cost_pct >= 80 and not ctx.cost_warned:
                log.warning("cost approaching budget: $%.4f / $%.2f (%.0f%%)",
                            _total_cost, cost_budget, _cost_pct)
                ctx.cost_warned = True

        # Runaway cost circuit tripped MID-step (BACKLOG #23e): the adapter
        # seam refused a call because run spend crossed multiplier x
        # cost_budget. Stop here — retrying or continuing to the next step
        # would hit the same refusal (that retry churn is exactly what the
        # circuit exists to prevent). No finished-plan carve-out: firing
        # mid-step means this step never completed, so work remains.
        if outcome.get("error_class") == "budget_runaway":
            loop_status = "stuck"
            stuck_reason = (outcome.get("stuck_reason")
                            or "runaway cost circuit tripped mid-step")
            log.warning("runaway cost circuit stop: %s", stuck_reason)
            if verbose:
                print(f"[maro] {stuck_reason}", file=sys.stderr, flush=True)
            break

        step_status = outcome["status"]
        _raw_result = outcome.get("result", "")
        # Guard: LLM can return a JSON schema object instead of a string value for
        # result/summary fields. If non-string, convert to empty string (result) or step_text (summary).
        step_result = _raw_result if isinstance(_raw_result, str) else str(_raw_result) if _raw_result else ""
        _raw_summary = outcome.get("summary", step_text)
        step_summary = _raw_summary if isinstance(_raw_summary, str) else step_text

        # Tier-a write fence (BACKLOG #1): an out-of-fence WRITE in the real tool
        # transcript demotes done→blocked. Positive evidence only (a Write/Edit
        # with an absolute out-of-fence path, or a shell write resolved against a
        # drifted cwd). Default ON since 2026-07-09 (1.0 posture: enforce on
        # fresh installs; this box ran it enabled since 2026-07-04 with /tmp +
        # goal-declared-path widening in place). Opt out via
        # validate.write_fence: false. Detection above stays always-on.
        if _sc_report is not None and _sc_report.writes and step_status == "done":
            try:
                from config import get as _wf_cfg_get
                if bool(_wf_cfg_get("validate.write_fence", True)):
                    _wf_paths = [w.get("path", "?") for w in _sc_report.writes]
                    log.warning("WRITE FENCE step=%d blocked: %s", step_idx, _wf_paths)
                    step_status = "blocked"
                    outcome["status"] = "blocked"
                    outcome["stuck_reason"] = (
                        f"write-fence: wrote {len(_wf_paths)} path(s) outside the "
                        f"project fence: {_wf_paths[:5]}"
                    )
                    step_result = (
                        f"{step_result}\n\n[write-fence] This step wrote outside the "
                        f"project workspace: {_wf_paths[:5]}. Marked blocked — re-run "
                        f"and keep deliverables inside the project directory "
                        f"({_proj_artifact_dir}); /tmp is fine for scratch."
                    )
                    try:
                        from captains_log import log_event as _wf_log_event, FENCE_WRITE_BLOCKED
                        _wf_log_event(
                            FENCE_WRITE_BLOCKED,
                            subject=f"step {step_idx}",
                            summary=f"write fence blocked {len(_wf_paths)} out-of-fence write(s)",
                            context={
                                "step_text": step_text[:200],
                                "writes": _sc_report.writes,
                                "fence_project_dir": _proj_artifact_dir,
                            },
                            loop_id=getattr(ctx, "loop_id", "") or None,
                        )
                    except Exception:
                        pass
                    if verbose:
                        print(
                            f"[maro] step {step_idx}: write fence blocked — {_wf_paths[:5]}",
                            file=sys.stderr, flush=True,
                        )
            except Exception:
                pass

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
                            loop_id=getattr(ctx, "loop_id", "") or None,
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
                call_record=outcome.get("call_record", ""),
            ))
            if item_index >= 0:
                try:
                    o.mark_item(project, item_index, o.STATE_BLOCKED)
                except OSError as _mark_exc:  # FileLockTimeout: ledger contended — the run result matters more than the checkbox
                    log.warning("mark_item(BLOCKED) failed for %s#%d: %s", project, item_index, _mark_exc)
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
            call_record=outcome.get("call_record", ""),
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

