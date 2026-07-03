"""Task-store queue consumer for Maro's Handle.

Extracted from handle.py (pure move, Tier 3 of docs/REFACTOR_PLAN.md): routes
queued task_store tasks (escalations, continuations, ad-hoc user goals) to the
appropriate handler, and the user-facing goal-enqueue API that feeds the queue.

This module reaches back into `handle` for a few names (`handle`, `HandleResult`,
`_context_firewall`, `_parse_continuation_reason`, `_navigator_act_dispatch`) via
a deferred `import handle` inside each function body — never at module level.
That's not just cycle-avoidance (handle.py imports this module at load time, so
a module-level `import handle` here would deadlock on the partially-initialized
module); it also preserves `mock.patch("handle.X", ...)` semantics in existing
tests, since a deferred attribute lookup at call time picks up patches applied
to the `handle` module, while a name bound once at import time would not.
"""

from __future__ import annotations

import logging

from typing import List, Optional

log = logging.getLogger("maro.handle")


def handle_task(
    task: dict,
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
):
    """Route a task_store task to the appropriate handler based on its source.

    - loop_escalation → director.handle_escalation() (judgment call: continue/narrow/close/surface)
    - loop_continuation → run_agent_loop() directly with continuation_depth (already classified AGENDA)
    - all others → handle(reason) (standard text-based routing)

    This is the closure mechanism: escalation tasks don't sit silently in the queue,
    they route to the director for a reasoned decision.
    """
    import handle as _handle_mod

    source = task.get("source", "")
    reason = task.get("reason", "")
    try:
        depth = int(task.get("continuation_depth", 0))
    except (TypeError, ValueError):
        depth = 0
    job_id = task.get("job_id", "unknown")

    if source == "loop_escalation":
        from director import handle_escalation
        log.info("handle_task routing escalation job_id=%s depth=%d", job_id, depth)
        _esc = handle_escalation(task, adapter=adapter, dry_run=dry_run, verbose=verbose)
        # "surface" means "for operator review" — that review only happens if
        # the operator is told. continue/narrow/close are internal dispositions.
        if getattr(_esc, "action", "") == "surface" and not dry_run:
            try:
                from notify import emit as _notify_emit
                _notify_emit("escalation", {
                    "handle_id": "",
                    "goal": reason[:500],
                    "status": "surfaced",
                    "summary": getattr(_esc, "summary_for_user", ""),
                    "reason": getattr(_esc, "reasoning", ""),
                    "job_id": job_id,
                    "source": source,
                    "point": "director_escalation",
                })
            except Exception:
                pass
        return _esc

    elif source == "loop_continuation":
        # Continuations are already classified AGENDA — skip intent classification overhead.
        # Extract the original goal cleanly; pass accomplished/remaining context as ancestry
        # so the planner gets focused decomposition ("this is pass N, remaining work is X")
        # rather than treating the full blob as a new goal to plan from scratch.
        log.info("handle_task routing continuation job_id=%s depth=%d", job_id, depth)
        _cont_goal, _cont_ctx = _handle_mod._parse_continuation_reason(reason)
        from agent_loop import run_agent_loop
        if adapter is None and not dry_run:
            from llm import build_adapter, MODEL_CHEAP
            adapter = build_adapter(model=MODEL_CHEAP)
        _filtered_ctx = _handle_mod._context_firewall(_cont_ctx, depth=depth) if _cont_ctx else ""
        return run_agent_loop(
            _cont_goal,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            continuation_depth=depth,
            ancestry_context_extra=_filtered_ctx,
        )

    else:
        log.info("handle_task routing %s job_id=%s via handle()", source or "unknown", job_id)
        # Carry ancestry across the requeue boundary: the task's origin (if its
        # creator recorded one) plus queue-level identity. Without this, a
        # requeued goal arrives at handle() indistinguishable from fresh user
        # input (goal-brain pressure test, 2026-06-10, finding 1).
        _origin = dict(task.get("origin") or {})
        _origin.setdefault("source", source or "task_store")
        _origin.setdefault("job_id", job_id)
        if task.get("parent_job_id"):
            _origin.setdefault("parent_job_id", task["parent_job_id"])
        # Dispatch guard (goal-brain step 3, docs/RECALL_DESIGN.md): refuse to
        # re-run a goal whose recent attempts ALL failed. Applies only to this
        # autonomous requeue path — a human calling handle() directly is never
        # blocked. Basis: 2026-05-17, the same goal ran ~25x in 35 minutes
        # with nothing consulting prior outcomes. Skipped on dry_run (preview
        # burns nothing, so there is no waste to guard against).
        if not dry_run:
            try:
                from config import get as _cfg_get
                _guard_on = bool(_cfg_get("recall.dispatch_guard", True))
                _guard_attempts = int(_cfg_get("recall.guard_attempts", 3))
                _guard_window = float(_cfg_get("recall.guard_window_minutes", 60))
            except Exception:
                _guard_on, _guard_attempts, _guard_window = True, 3, 60.0
            # One recall serves both the guard and the live navigator shadow.
            _rr = None
            if _guard_on:
                try:
                    from recall import recall as _recall_fn
                    _rr = _recall_fn(reason, slice="dispatch", origin=_origin)
                except Exception as _guard_exc:
                    log.debug("handle_task recall guard skipped: %s", _guard_exc)
            _sig = None
            if _rr is not None:
                try:
                    _sig = _rr.dispatch_signals(window_minutes=_guard_window)
                except Exception:
                    _sig = None
            _guard_tripped = bool(
                _sig and _sig["repeat_count"] >= _guard_attempts and _sig["all_failing"]
            )
            # Live navigator shadow (goal-brain step 5, docs/NAVIGATOR_SCHEMA.md):
            # decide-only beside the pipeline; NAVIGATOR_DECIDED records the
            # navigator's move next to what dispatch actually did. Config-gated
            # (navigator.shadow_dispatch, default off) and failure-isolated —
            # it can never change dispatch behavior.
            _nav_decision = None
            try:
                from navigator_shadow import shadow_dispatch_live
                _nav_decision = shadow_dispatch_live(
                    reason,
                    origin=_origin,
                    recall_result=_rr,
                    pipeline_move="guard_refused" if _guard_tripped else "execute",
                    extra={"job_id": job_id, "source": source or "task_store"},
                )
            except Exception as _shadow_exc:
                log.debug("handle_task navigator shadow skipped: %s", _shadow_exc)
            if _guard_tripped:
                _msg = (
                    f"recall guard: {_sig['repeat_count']} attempts at this goal "
                    f"in the last {int(_guard_window)}m, all failed — refusing to "
                    f"re-run without a change of approach (docs/RECALL_DESIGN.md)"
                )
                log.warning("handle_task %s job_id=%s", _msg, job_id)
                try:
                    from captains_log import log_event, RECALL_GUARD_TRIPPED
                    log_event(
                        RECALL_GUARD_TRIPPED,
                        subject="recall_guard",
                        summary=_msg,
                        context={"goal_preview": reason[:200], "job_id": job_id, **_sig},
                    )
                except Exception:
                    pass
                return _handle_mod.HandleResult(
                    handle_id="",
                    lane="agenda",
                    lane_confidence=1.0,
                    classification_reason="recall_guard",
                    message=reason,
                    status="error",
                    result=_msg,
                )
            # Dispatch-class cutover (navigator.act_dispatch, default OFF):
            # the navigator's dispatch decision acts instead of being
            # shadow-only. Earned by shadow agreement data (NAVIGATOR_SCHEMA
            # cutover rule); the recall guard above stays as the deterministic
            # backstop and always gets the first word. Conservative by
            # construction: only escalate/close act, only at or above the
            # confidence floor, only on this autonomous requeue path —
            # everything else falls through to execute.
            _nav_acted = _handle_mod._navigator_act_dispatch(
                _nav_decision, reason, job_id=job_id,
                source=source or "task_store",
            )
            if _nav_acted is not None:
                return _nav_acted
            # Thread the navigator's dispatch rationale into the run this
            # dispatch is about to spawn (MILESTONES #3b): no run dir exists
            # at decision time, so it rides the origin dict — create_run_dir
            # appends it to the new thread's goal-brain Decisions section
            # (and it lands in run metadata via extra_metadata for free).
            if _nav_decision is not None:
                try:
                    _origin["dispatch_navigator"] = {
                        "move": str(getattr(_nav_decision, "move", "")),
                        "confidence": float(
                            getattr(_nav_decision, "confidence", 0.0) or 0.0),
                        "reasoning": str(
                            getattr(_nav_decision, "reasoning", ""))[:300],
                    }
                except Exception:
                    pass
        return _handle_mod.handle(reason, adapter=adapter, dry_run=dry_run, verbose=verbose, origin=_origin)


def drain_task_store(
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
    max_tasks: int = 3,
    sources: tuple = ("loop_continuation", "loop_escalation", "user_goal"),
    job_ids: Optional[set] = None,
) -> int:
    """Claim and process queued task_store tasks with known sources.

    Called from the heartbeat or scheduler to consume continuation,
    escalation, and user-enqueued goals. Returns the number processed.

    Args:
        max_tasks: Max tasks to process per call (avoids monopolizing the heartbeat).
        sources: Which task sources to drain. Includes user_goal for
                 ad-hoc goals enqueued via ``maro-enqueue``.
        job_ids: If set, drain ONLY these tasks. This is the substrate
                 dispatch contract (enqueue --drain): a dispatch must run
                 exactly what it enqueued — never an older queued task, whose
                 notify event the substrate would misattribute and whose
                 tokens nobody consented to spend right now.
    """
    import handle as _handle_mod

    try:
        from task_store import list_tasks, claim, complete, fail as task_fail
    except ImportError:
        log.warning("drain_task_store: task_store not available")
        return 0

    queued = [
        t for t in list_tasks(status_filter="queued")
        if t.get("source") in sources
        and (job_ids is None or t.get("job_id") in job_ids)
    ]
    if not queued:
        return 0

    log.info("drain_task_store: %d queued task(s) to process", len(queued))
    processed = 0

    for task in queued[:max_tasks]:
        job_id = task.get("job_id", "unknown")
        try:
            claim(job_id)
        except Exception as exc:
            log.warning("drain_task_store: failed to claim %s: %s", job_id, exc)
            continue

        try:
            _handle_mod.handle_task(task, adapter=adapter, dry_run=dry_run, verbose=verbose)
            try:
                complete(job_id)
            except Exception as _ce:
                log.warning("drain_task_store: failed to mark %s complete: %s", job_id, _ce)
            processed += 1
            log.info("drain_task_store: completed %s", job_id)
            # Emit observable event so the dashboard shows continuation/escalation activity
            try:
                from observe import write_event as _write_event
                _write_event(
                    "task_drained",
                    goal=task.get("reason", "")[:80],
                    project=task.get("parent_job_id", ""),
                    loop_id=job_id,
                    status=task.get("source", ""),
                    detail=f"depth={task.get('continuation_depth', 0)}",
                )
            except Exception:
                pass
        except Exception as exc:
            log.warning("drain_task_store: task %s failed: %s", job_id, exc)
            try:
                task_fail(job_id, str(exc))
            except Exception:
                pass

    return processed


# ---------------------------------------------------------------------------
# Goal queue — user-facing mission enqueue
# ---------------------------------------------------------------------------

def enqueue_goal(
    goal: str,
    *,
    reason: str = "",
    blocked_by: Optional[List[str]] = None,
) -> str:
    """Enqueue a user goal for the director to process sequentially.

    Returns the job_id. The goal will be picked up by ``drain_task_store``
    on the next heartbeat tick (or can be drained manually).

    This is the user-facing "drop goals here" API. Each goal runs through
    ``handle()`` in order — the director gets full discretion over how to
    decompose and execute each one.
    """
    from task_store import enqueue
    task = enqueue(
        lane="agenda",
        source="user_goal",
        reason=reason or goal,
        blocked_by=blocked_by,
    )
    job_id = task["job_id"]
    log.info("enqueue_goal: queued %s — %s", job_id, goal[:80])
    return job_id


def enqueue_goals(goals: List[str], *, sequential: bool = True) -> List[str]:
    """Enqueue multiple goals. If sequential=True, each goal is blocked_by the previous."""
    job_ids = []
    for goal in goals:
        blocked = [job_ids[-1]] if sequential and job_ids else None
        jid = enqueue_goal(goal, blocked_by=blocked)
        job_ids.append(jid)
    return job_ids
