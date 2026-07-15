"""Loop initialization: budget gate, context setup, and the dry-run test adapter.

Extracted from agent_loop.py (Tier 3 split, step 6).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from loop_types import _configure_logging, _orch, LoopResult, LoopStateMachine
from loop_artifacts import _goal_to_slug

try:
    from tool_registry import PermissionContext as _PermissionContext, ROLE_WORKER as _ROLE_WORKER
except ImportError:
    _PermissionContext = None  # type: ignore[assignment,misc]
    _ROLE_WORKER = "worker"

log = logging.getLogger("maro.loop")

# Safe-by-default spend caps (2026-07-09, 1.0 posture). A fresh install must
# never run uncapped; config overrides, 0/null disables. See docs/DEFAULTS.md.
DEFAULT_PER_RUN_USD = 5.0
DEFAULT_DAILY_USD = 25.0


def _budget_gate(ctx, *, goal: str, project: Optional[str], dry_run: bool):
    """Budget gates (substrate-trial hardening, 2026-07-01). Two layers:

    - per-run: callers rarely pass cost_budget, so an unattended run was
      uncapped — config ``budget.per_run_usd`` supplies the default (an
      explicit caller arg still wins). Enforced mid-loop by the existing
      cost hard-stop.
    - daily: per-run caps don't stop a substrate burning through runs one
      under-cap loop at a time — ``budget.daily_usd`` gates on the cross-run
      spend ledger (metrics.spend_today) before any tokens are spent.

    Capped by default since 2026-07-09 (1.0 posture: a fresh install must
    never be uncapped spend) — $5/run, $25/day. Opt out explicitly with
    ``budget.per_run_usd: 0`` / ``budget.daily_usd: 0`` (or null). dry_run
    skips (burns nothing). Returns a stuck LoopResult to refuse the run, or
    None to proceed. Never raises.
    """
    if dry_run:
        return None

    def _coerce_cap(key: str, default: float) -> float:
        """Config value → float cap. 0/null = explicit uncapped opt-out (0.0).

        A malformed value fails CLOSED to the default cap (with a warning) —
        a typo in budget config must never silently disable the caps.
        """
        from config import get as _budget_get
        raw = _budget_get(key, default)
        if raw is None:
            return 0.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            log.warning("budget gate: %s=%r is not a number — using default $%.2f",
                        key, raw, default)
            return default

    try:
        if ctx.cost_budget is None:
            _per_run = _coerce_cap("budget.per_run_usd", DEFAULT_PER_RUN_USD)
            if _per_run > 0:
                ctx.cost_budget = _per_run
                log.info("cost_budget defaulted from config: $%.2f", ctx.cost_budget)
    except Exception as _budget_exc:
        log.warning("budget gate: per-run cap check failed: %s", _budget_exc)
    try:
        _daily_cap = _coerce_cap("budget.daily_usd", DEFAULT_DAILY_USD)
        if _daily_cap > 0:
            import metrics as _metrics
            _spent = _metrics.spend_today()
            if _spent >= _daily_cap:
                _msg = (f"daily budget exhausted: ${_spent:.2f} spent today >= "
                        f"budget.daily_usd ${_daily_cap:.2f} — refusing to start; "
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
        log.warning("budget gate: daily cap check failed (non-blocking): %s", _budget_exc)
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
    admission_wait_s: Optional[float] = None,
    defer_learning: bool = False,
    measurement_class: str = "",
    handle_id: str = "",
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
    ctx.defer_learning = defer_learning
    ctx.measurement_class = measurement_class
    ctx.handle_id = handle_id

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

    # Admission gate: atomically claim the per-project slot (flock, held for
    # the process's lifetime). Two runs on one project stomp each other's
    # NEXT.md flow and git state — refuse by default; the heartbeat retries
    # next tick and `--wait N` / loop.admission_wait_s opts into polling.
    try:
        from interrupt import acquire_project_slot, LoopBusy
        try:
            ctx.project_slot = acquire_project_slot(
                ctx.project, loop_id=ctx.loop_id, goal=goal,
                wait_s=admission_wait_s,
            )
        except LoopBusy as _busy:
            _holder = _busy.holder
            # busy_policy=worktree (opt-in, phase 3b): instead of refusing,
            # run in an isolated worktree of the project dir and merge at
            # finalize. Only possible for git-repo projects — non-git falls
            # through to refuse (mutual exclusion is the complete fix there).
            _policy = ""
            try:
                from config import get as _cfg_get
                _policy = str(_cfg_get("loop.busy_policy", "refuse")).strip().lower()
            except Exception:
                _policy = "refuse"
            if _policy == "worktree":
                try:
                    import worktree as _wtmod
                    from orch_items import project_dir as _proj_dir
                    _wt = _wtmod.provision(
                        _proj_dir(ctx.project), "run", loop_id=ctx.loop_id,
                    )
                except Exception as _wt_exc:
                    log.warning("busy_policy=worktree provision error: %s", _wt_exc)
                    _wt = None
                if _wt is not None:
                    ctx.run_worktree = _wt
                    log.info(
                        "project '%s' busy — proceeding in worktree %s (branch %s)",
                        ctx.project, _wt.path, _wt.branch,
                    )
                    if verbose:
                        print(
                            f"[maro] project busy — isolated worktree on {_wt.branch}",
                            file=sys.stderr, flush=True,
                        )
            if ctx.run_worktree is None:
                log.warning("loop refused: %s", _busy)
                if verbose:
                    print(f"[maro] {_busy}", file=sys.stderr, flush=True)
                return ctx, LoopResult(
                    loop_id=ctx.loop_id,
                    goal=goal,
                    project=ctx.project,
                    steps=[],
                    status="refused_busy",
                    stuck_reason=str(_busy),
                    total_tokens_in=0,
                    total_tokens_out=0,
                    elapsed_ms=0,
                    log_path=None,
                )
    except ImportError as _gate_exc:
        log.debug("admission gate unavailable: %s", _gate_exc)

    # Run-lifetime lease: a per-loop flock held from here until process
    # death. Checkpoints only carry an in_flight pid while a step executes,
    # so a healthy loop BETWEEN steps is invisible to pid heuristics — the
    # held lease is the liveness evidence `maro resume` and the heartbeat
    # stranded sweep trust instead. Unconditional (projectless runs too);
    # fs problems degrade ungated inside acquire_run_lease, never refusing
    # work (see run_lease module docstring).
    try:
        from run_lease import acquire_run_lease
        ctx.run_lease = acquire_run_lease(
            ctx.loop_id, handle_id=ctx.handle_id, goal=goal)
    except ImportError as _lease_exc:
        log.debug("run lease unavailable: %s", _lease_exc)

    # Advertise this loop as running so other interfaces can route interrupts.
    # The slot above owns the per-project lockfile; this writes only the
    # global informational one (project="" keeps set_loop_running off it).
    try:
        set_loop_running(ctx.loop_id, goal, project="" if ctx.project_slot else ctx.project)
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
