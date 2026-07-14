"""Loop finalization for the agent loop (Tier 3 split of agent_loop.py).

Extracted verbatim from agent_loop.py — building the final LoopResult and
writing terminal artifacts (plan manifest, loop log, transcript, scratchpad),
plus the post-loop side effects (introspection/diagnosis, Reflexion memory
recording, skill crystallisation/synthesis, Telegram notification).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from loop_types import LoopContext, LoopResult, StepOutcome, _orch, _project_dir_root
from loop_artifacts import _write_loop_log, _write_plan_manifest
from loop_report import write_run_report as _write_run_report, write_runs_index as _write_runs_index

log = logging.getLogger("maro.loop")

def _auto_prune_days() -> float:
    """User-level retention knob (`artifacts.auto_prune_days`, default 0 =
    never delete). Retention decree (Jeremy, 2026-07-10): the system never
    decides run data is clutter — "the result isn't always just the outcome,
    it's also the path that gets you there." Auto-pruning is strictly a user
    opt-in; retiring the old `keep_artifacts` flag (whose default was
    delete) closes the bug class where finalize destroyed audit evidence."""
    try:
        from config import get as _cfg_get
        return max(0.0, float(_cfg_get("artifacts.auto_prune_days", 0) or 0))
    except Exception:
        return 0.0


def cleanup_step_artifacts(project: str, *, exclude_loop_id: str = "") -> int:
    """Opt-in per-step artifact pruning (retention decree, 2026-07-10).

    No-op unless the user set `artifacts.auto_prune_days` > 0. When enabled,
    deletes `loop-*-step-*.md` files in the project's artifacts dir older
    than that many days, never touching `exclude_loop_id`'s files — the
    just-finished loop's step artifacts always outlive its verdict and audit
    window, regardless of which lane invoked the loop (BACKLOG #18).
    Returns the number of files deleted.
    """
    if not project:
        return 0
    _days = _auto_prune_days()
    if _days <= 0:
        return 0
    grace_s = _days * 86400.0
    try:
        try:
            from runs import artifact_dir as _runs_artifact_dir
            _art_dir = _runs_artifact_dir(project, project_root_fn=_project_dir_root)
        except Exception:
            _art_dir = _project_dir_root() / project / "artifacts"
        _deleted = 0
        _now = time.time()
        _exclude_prefix = f"loop-{exclude_loop_id}-" if exclude_loop_id else None
        for _f in _art_dir.glob("loop-*-step-*.md"):
            if _exclude_prefix and _f.name.startswith(_exclude_prefix):
                continue
            try:
                if _now - _f.stat().st_mtime < grace_s:
                    continue
                _f.unlink()
                _deleted += 1
            except OSError:
                pass
        if _deleted:
            log.debug("artifact auto-prune: deleted %d per-step artifact(s) "
                      "older than %.1fd (artifacts.auto_prune_days opt-in)",
                      _deleted, _days)
        return _deleted
    except Exception as _art_exc:
        log.debug("artifact cleanup failed: %s", _art_exc)
        return 0


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

        try:
            _write_run_report(
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
        except Exception as _rep_exc:
            log.warning("run report final write failed: %s", _rep_exc)

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

    # 2026-07-08 adversarial review (finding #3): the index reads totals from
    # this run's build/loop-*-log.json, so the forced write has to happen
    # AFTER _write_loop_log() above, not before it — otherwise the just-
    # finished run's own totals are missing from its own index entry. (A
    # separate, still-open half of this finding: metadata.json/run_card.json
    # finalize even later, in handle.py's finally block outside
    # agent_loop.py's control — see docs/RUN_VISIBILITY_DESIGN.md.)
    try:
        _write_runs_index(force=True)
    except Exception as _idx_exc:
        log.warning("runs index write failed: %s", _idx_exc)

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
            from file_lock import locked_append
            locked_append(_fb_path, json.dumps(_fb_entry))
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
        had_no_matching_skill=had_no_matching_skill,
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
        defer_learning=getattr(ctx, "defer_learning", False),
        measurement_class=ctx.measurement_class,
        handle_id=ctx.handle_id,
    )

    # Checkpoints are KEPT on completion (retention decree, 2026-07-10).
    # The old delete-on-done crossed wires with closure verification, which
    # runs AFTER finalize: a run demoted done→incomplete had already lost
    # its resume state. Finalized runs are excluded from the stranded-run
    # sweep via run metadata status, so a kept checkpoint is inert; users
    # can remove one explicitly with `checkpoint delete` or run pruning.

    # Artifact retention (decree, 2026-07-10): per-step artifacts are KEPT by
    # default — the system never decides run data is clutter; deleting the
    # path a result took destroyed audit evidence (BACKLOG #18, hermes
    # specimen). Pruning is a user opt-in (`artifacts.auto_prune_days`), and
    # even then the just-finished loop's files are never touched — the
    # closure/goal verdict is judged AFTER the loop returns.
    if not ctx.dry_run and ctx.project:
        cleanup_step_artifacts(ctx.project, exclude_loop_id=ctx.loop_id)

    # Containerized self-dev (C3, CONTAINER_EXECUTOR_DESIGN §4): merge the
    # worker's scratch clone back into the fence repo FIRST — the clone's parent
    # is the fence dir (a worktree path when busy_policy=worktree is also active,
    # else the project dir), so clone→fence must land before any fence→project
    # merge below. Same serialized semantics; conflict never drops work.
    if getattr(ctx, "container_clone", None) is not None:
        _clone = ctx.container_clone
        try:
            import worktree as _wtmod
            _cmerge = _wtmod.merge_back_clone(_clone, message=f"container: run {ctx.loop_id}")
            _wtmod.cleanup_clone(_clone, keep_on_failure=not _cmerge.ok)
            if not _cmerge.ok:
                log.warning("container scratch-clone merge failed: %s", _cmerge.detail)
                if result.status == "done":
                    result.status = "partial"
                result.stuck_reason = (
                    (result.stuck_reason + "; " if result.stuck_reason else "")
                    + f"container clone merge failed — work preserved: {_cmerge.detail}"
                )
        except Exception as _cc_exc:
            # A merge-back exception must NOT be reported as a clean 'done' — the
            # worker's clone work never reached the fence. Downgrade and name the
            # retained clone/branch so nothing is silently lost (adversarial-review
            # 2026-07-13, finding A6). Leave the clone on disk (no cleanup here).
            log.warning("container scratch-clone finalize error: %s", _cc_exc)
            if result.status == "done":
                result.status = "partial"
            result.stuck_reason = (
                (result.stuck_reason + "; " if result.stuck_reason else "")
                + f"container clone merge errored — work preserved in "
                + f"{getattr(_clone, 'path', '?')} (branch {getattr(_clone, 'branch', '?')}): {_cc_exc}"
            )
        ctx.container_clone = None

    # busy_policy=worktree: merge the run's isolated worktree back into the
    # project checkout before releasing the slot. Conflict never drops work —
    # the branch is preserved and the run downgrades to "partial" naming it.
    if getattr(ctx, "run_worktree", None) is not None:
        _wt = ctx.run_worktree
        try:
            import worktree as _wtmod
            _merge = _wtmod.merge_back(_wt, message=f"wt: run {ctx.loop_id}")
            _wtmod.cleanup(_wt, keep_on_failure=not _merge.ok)
            _wtmod.prune(_wt.repo_dir)
            if not _merge.ok:
                log.warning("run worktree merge failed: %s", _merge.detail)
                if result.status == "done":
                    result.status = "partial"
                result.stuck_reason = (
                    (result.stuck_reason + "; " if result.stuck_reason else "")
                    + f"worktree merge failed — work preserved on {_merge.branch}: "
                    + _merge.detail
                )
        except Exception as _wt_exc:
            log.warning("run worktree finalize error: %s", _wt_exc)
        ctx.run_worktree = None

    # Release loop lock — the admission slot first (per-project flock),
    # then the global informational lockfile.
    try:
        if getattr(ctx, "project_slot", None) is not None:
            ctx.project_slot.release()
            ctx.project_slot = None
    except Exception as _slot_exc:
        log.debug("project slot release failed: %s", _slot_exc)
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
    defer_learning: bool = False,
    measurement_class: str = "",
    handle_id: str = "",
) -> None:
    """Run all post-loop side effects after the main execution loop ends.

    Handles: Reflexion/memory recording, skill crystallisation, skill synthesis.
    All failures are swallowed — post-loop side effects must never raise.

    defer_learning (data-r2-01): the caller runs closure judging after this
    and promises to call finalize_deferred_learning() — for a "done" run,
    lesson extraction and skill crystallization/synthesis are skipped here so
    they can run with the verdict in hand instead of verdict-blind. Non-done
    statuses still learn immediately (their status is already honest).
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
            # Verdict tri-state (SF-2): closure judging runs AFTER finalization
            # (handle.py), so the verdict is unknown here — the row is written
            # unjudged with its loop_id, and stamp_outcome_verdict() stamps
            # the verdict onto it once closure has judged.
            loop_id=loop_id,
            # data-r2-01: when the caller will run closure, a "done" run's
            # lessons wait for the verdict (extract_deferred_lessons).
            defer_lessons=defer_learning and loop_status == "done",
            measurement_class=measurement_class,
            handle_id=handle_id,
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

    # Skill crystallization + synthesis for successful loops. Deferred with
    # the lessons (data-r2-01) when the caller runs closure — a done-but-not-
    # achieved run must not crystallize its pattern into the skill library.
    if loop_status == "done" and not dry_run and step_outcomes and not defer_learning:
        _crystallize_and_synthesize(
            loop_id=loop_id,
            goal=goal,
            project=project,
            loop_status=loop_status,
            step_outcomes=step_outcomes,
            adapter=adapter,
            verbose=verbose,
            had_no_matching_skill=had_no_matching_skill,
        )

    # Phase 32: auto-promote skills that meet threshold (don't wait for evolver heartbeat)
    if not dry_run:
        try:
            from evolver import run_skill_maintenance
            # adapter threaded through (arch-04 fix, 2026-07-09): without it the
            # refight_rule half of decay-by-invalidation was structurally
            # unreachable — this is the only live caller path.
            run_skill_maintenance(adapter=adapter)
        except ImportError:
            pass
        except Exception as _maint_exc:
            log.debug("skill maintenance failed (non-critical): %s", _maint_exc)

    # BACKLOG #13 (2026-07-03): evolver's 5 statistical scanners, per-run
    # instead of per-heartbeat-tick — "app, not OS": no daemon, no LLM calls
    # (safe at this cadence), observational only (never auto-applies). Gives
    # scan_suggestion_outcomes()/scan_evolver_impact() real data to work with,
    # which they've never had (see BACKLOG.md #13).
    if not dry_run:
        try:
            from evolver import run_statistical_scans
            from evolver_store import _save_suggestions
            _stat_suggestions = run_statistical_scans(verbose=verbose)
            if _stat_suggestions:
                _save_suggestions(_stat_suggestions)
                log.info("post-run statistical scan: %d suggestion(s) saved", len(_stat_suggestions))
        except ImportError:
            pass
        except Exception as _scan_exc:
            log.debug("post-run statistical scan failed (non-critical): %s", _scan_exc)

    # Evolver meta-cycle on run-cadence (2026-07-09 supervision decision):
    # every N-th real run finalization triggers run_evolver() — the meta-cycle
    # rides run completions instead of a timer ("app, not systemic": no
    # daemon, no self-rearming loop; no runs → no evolver, which is also the
    # correct no-op). `evolver.run_cadence` default 0 = off (fresh installs
    # unchanged); dry_run runs neither count nor trigger. Never fatal to
    # finalization.
    if not dry_run:
        try:
            from config import get as _cfg_get
            from evolver_store import evolver_cadence_tick
            _cadence = int(_cfg_get("evolver.run_cadence", 0) or 0)
            if evolver_cadence_tick(_cadence):
                from evolver import run_evolver
                _evo_report = run_evolver(adapter=adapter, verbose=verbose)
                log.info(
                    "run-cadence evolver cycle fired (cadence=%d): reviewed=%d suggestions=%d",
                    _cadence,
                    getattr(_evo_report, "outcomes_reviewed", 0),
                    len(getattr(_evo_report, "suggestions", []) or []),
                )
        except ImportError:
            pass
        except Exception as _evo_exc:
            log.warning("run-cadence evolver cycle failed (non-fatal): %s", _evo_exc)

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


def _crystallize_and_synthesize(
    *,
    loop_id: str,
    goal: str,
    project: str,
    loop_status: str,
    step_outcomes: List["StepOutcome"],
    adapter,
    verbose: bool,
    had_no_matching_skill: bool,
) -> None:
    """Skill crystallization + no-matching-skill synthesis for a done run.

    Runs at finalize on the immediate path, or from
    finalize_deferred_learning() once closure has judged (data-r2-01) —
    callers gate on status/dry_run/verdict. Failures are swallowed: skill
    writes must never break finalization or handle delivery.
    """
    # Auto-extract skills from successful loops (crystallise patterns)
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
    if had_no_matching_skill:
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


def finalize_deferred_learning(
    loop_result,
    *,
    adapter=None,
    project: str = "",
    dry_run: bool = False,
    verbose: bool = False,
    extra_loop_ids: Optional[List[str]] = None,
    unstamped_loop_ids: Optional[Iterable[str]] = None,
) -> None:
    """Run the learning that finalize deferred, now that closure has judged
    (data-r2-01: lessons + skills must not be extracted verdict-blind).

    Call AFTER the closure/provenance verdict has been stamped onto the
    outcomes row (stamp_outcome_verdict). Two halves:

    - Lessons: extract_deferred_lessons() per loop — reads each row back,
      verdict included, and extracts failure-flavored lessons for a
      done-but-not-achieved run. Idempotent (rows with lessons are skipped),
      so it's safe to pass loops that didn't defer.
    - Skills: crystallization + synthesis for the final loop_result, skipped
      when the row's verdict is a judged False — a run that didn't deliver
      must not crystallize its pattern. Unjudged (verdict absent) keeps the
      pre-fix behavior: done is enough.

    extra_loop_ids: earlier attempts this handle ran (director/closure
    restarts) — they get lesson extraction only; their steps are gone and
    they were superseded, so no skill writes.

    unstamped_loop_ids (EXT-AUDIT-2 residual): loops whose closure/provenance
    verdict was judged but whose stamp_outcome_verdict() write failed or
    raised. The on-disk row for these may still read back as unjudged,
    absent, or stale, so both lesson extraction and skill crystallization
    are skipped entirely for them — durable quarantine, the same posture as
    the judged-False skip below, rather than letting a persistence gap fall
    back to "unjudged" permissiveness.

    Never raises — deferred learning must not break result delivery.
    """
    loop_id = getattr(loop_result, "loop_id", "") or ""
    _unstamped = set(unstamped_loop_ids or ())
    try:
        from memory import extract_deferred_lessons
        for _lid in [*(extra_loop_ids or []), loop_id]:
            if not _lid:
                continue
            if _lid in _unstamped:
                log.info(
                    "deferred lesson extraction skipped — loop %s verdict "
                    "stamp failed, quarantined pending reconciliation", _lid)
                continue
            try:
                extract_deferred_lessons(_lid, adapter=adapter, dry_run=dry_run)
            except Exception as _dl_exc:
                log.warning("deferred lesson extraction failed for loop %s: %s", _lid, _dl_exc)
    except Exception as _imp_exc:
        log.warning("deferred lesson extraction unavailable: %s", _imp_exc)

    step_outcomes = getattr(loop_result, "steps", None) or []
    # Provenance demotion already downgraded status from "done", so a
    # provenance-failed run never reaches the skill branch via status alone.
    if dry_run or getattr(loop_result, "status", "") != "done" or not step_outcomes:
        return
    if loop_id in _unstamped:
        log.info(
            "deferred skill crystallization skipped — loop %s verdict "
            "stamp failed, quarantined pending reconciliation", loop_id)
        return
    try:
        from memory_ledger import load_outcome_by_loop_id
        _row = load_outcome_by_loop_id(loop_id)
        if _row is not None and _row.goal_achieved is False:
            log.info("deferred skill crystallization skipped — loop %s judged not-achieved (%s)",
                     loop_id, _row.goal_verdict_source)
            return
    except Exception:
        pass  # fail open to pre-fix behavior: done is enough when unreadable
    _crystallize_and_synthesize(
        loop_id=loop_id,
        goal=getattr(loop_result, "goal", "") or "",
        project=project or getattr(loop_result, "project", "") or "",
        loop_status="done",
        step_outcomes=step_outcomes,
        adapter=adapter,
        verbose=verbose,
        had_no_matching_skill=bool(getattr(loop_result, "had_no_matching_skill", False)),
    )
