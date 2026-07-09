"""Parallel and DAG-aware step execution (Tier 3 split of agent_loop.py, step 5)."""

from __future__ import annotations

import logging
import os
import sys
import time
import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from loop_types import LoopContext, LoopResult, StepOutcome, _orch, step_from_decompose
from loop_planning import _shape_steps
from step_exec import execute_step as _execute_step

log = logging.getLogger("maro.loop")


def _run_in_step_worktree(step_label: str, run_fn):
    """Isolate one parallel step in its own git worktree (phase 3b).

    Parallel steps sharing the fence checkout was the production incident
    class: forks writing over each other's working tree, git-stash races.
    If the fence dir (run-scoped subprocess cwd) is a git repo, the step
    runs in a fresh worktree on branch maro/<loop_id>/<label>; merge-back
    into the base branch is serialized per-repo as steps complete. Merge
    conflict → step blocked with the branch named, work preserved.

    Non-git fence dirs run in place — byte-identical to pre-3b behavior.
    """
    from llm import get_default_subprocess_cwd, default_subprocess_cwd

    base = get_default_subprocess_cwd()
    wt = None
    wtmod = None
    if base:
        try:
            import worktree as wtmod
            try:
                from runs import current_handle_id
                lid = current_handle_id()
            except Exception:
                lid = None
            if not lid:
                import uuid as _uuid
                lid = _uuid.uuid4().hex[:8]
            wt = wtmod.provision(base, step_label, loop_id=lid)
        except Exception as _wt_exc:
            log.debug("step worktree provision skipped for %s: %s", step_label, _wt_exc)
            wt = None
    if wt is None:
        return run_fn()

    try:
        with default_subprocess_cwd(str(wt.path)):
            outcome = run_fn()
    except BaseException:
        wtmod.cleanup(wt, keep_on_failure=True)
        raise
    merge = wtmod.merge_back(wt, message=f"wt: {step_label}")
    wtmod.cleanup(wt, keep_on_failure=not merge.ok)
    if not merge.ok:
        outcome = dict(outcome)
        outcome["status"] = "blocked"
        outcome["worktree_branch"] = merge.branch
        outcome["stuck_reason"] = f"worktree merge failed: {merge.detail}"
        outcome["summary"] = (
            (outcome.get("summary", "") or "")
            + f" [worktree merge failed — work preserved on {merge.branch}]"
        ).strip()
        log.warning("parallel step %s: %s", step_label, merge.detail)
    return outcome


def _run_parallel_batch(
    ctx: LoopContext,
    step_text: str,
    parallel_peers: List[str],
    *,
    step_outcomes: List[StepOutcome],
    completed_context: List[str],
    remaining_steps: List[str],
    remaining_indices: List[int],
    loop_shared_ctx: Dict[str, Any],
    resolve_tools_fn,
    parallel_fan_out: int,
    proj_artifact_dir: str,
    iteration: int,
    step_idx: int,
    batch_item_indices: Optional[List[int]] = None,
) -> tuple:
    """Phase F4: Run this step + peers in parallel batch.

    batch_item_indices carries the NEXT.md item index for the lead step +
    each peer (parallel to [step_text] + parallel_peers); -1 = not a project
    item. Recording these (instead of hardcoding -1) is the BACKLOG #2 fix:
    outcomes keep a real index and done items get marked in NEXT.md.

    Returns (iteration, step_idx, total_tokens_in_delta, total_tokens_out_delta).
    Mutates step_outcomes, completed_context, remaining_steps/indices in place.
    """
    from llm import LLMTool

    _batch_steps = [step_text] + parallel_peers
    iteration += len(_batch_steps)
    _batch_start = time.monotonic()
    if ctx.verbose:
        print(f"[maro] parallel batch: {len(_batch_steps)} steps at level", file=sys.stderr, flush=True)

    _batch_outcomes = _run_steps_parallel(
        goal=ctx.goal,
        steps=_batch_steps,
        adapter=ctx.adapter,
        ancestry_context=ctx.ancestry_context,
        tools=[LLMTool(**t) for t in resolve_tools_fn()],
        verbose=ctx.verbose,
        max_workers=min(parallel_fan_out, len(_batch_steps)),
        project_dir=proj_artifact_dir,
        shared_ctx=loop_shared_ctx,
    )

    # Process batch outcomes
    _tokens_in_delta = 0
    _tokens_out_delta = 0
    _batch_injected: List[str] = []
    for _bi, (_batch_text, _batch_oc) in enumerate(zip(_batch_steps, _batch_outcomes)):
        step_idx += 1
        _b_status = _batch_oc.get("status", "blocked")
        _b_elapsed = int((time.monotonic() - _batch_start) * 1000)
        _tokens_in_delta += _batch_oc.get("tokens_in", 0)
        _tokens_out_delta += _batch_oc.get("tokens_out", 0)
        _b_item_idx = (batch_item_indices[_bi]
                       if batch_item_indices and _bi < len(batch_item_indices)
                       else -1)

        step_outcomes.append(step_from_decompose(
            _batch_text, _b_item_idx,
            status=_b_status,
            result=_batch_oc.get("result", ""),
            iteration=iteration,
            tokens_in=_batch_oc.get("tokens_in", 0),
            tokens_out=_batch_oc.get("tokens_out", 0),
            elapsed_ms=_b_elapsed,
            confidence=_batch_oc.get("confidence", "unverified"),
            injected_steps=_batch_oc.get("inject_steps", []),
            call_record=_batch_oc.get("call_record", ""),
            # 2026-07-08 adversarial review (finding #2): _b_elapsed is time
            # since the whole batch started, assigned near-identically to
            # every step in it — not this step's real individual duration.
            # ended_ts="" opts out of step_from_decompose's "now" default so
            # the run-visibility report's timeline falls back to its existing
            # approximate mode instead of rendering fabricated precision.
            ended_ts="",
        ))

        if _b_status == "done":
            if _b_item_idx >= 0:
                try:
                    _o = _orch()
                    _o.mark_item(ctx.project, _b_item_idx, _o.STATE_DONE)
                except Exception as _mark_exc:
                    log.debug("parallel batch mark_item failed: %s", _mark_exc)
            _b_result = _batch_oc.get("result", "")
            _b_excerpt = _b_result[:800] if _b_result else ""
            completed_context.append(f"Step {step_idx} ({_batch_text[:80]}):\n{_b_excerpt}")
            if ctx.verbose:
                print(f"[maro] step {step_idx} done (parallel): {_batch_oc.get('summary', '')[:80]}", file=sys.stderr, flush=True)
            _bi_inject = _batch_oc.get("inject_steps", [])
            if _bi_inject and isinstance(_bi_inject, list):
                _batch_injected.extend(
                    str(s).strip() for s in _bi_inject if str(s).strip()
                )
        elif _b_status == "blocked":
            if ctx.verbose:
                print(f"[maro] step {step_idx} blocked (parallel): {_batch_oc.get('stuck_reason', '')[:80]}", file=sys.stderr, flush=True)

    # Inject collected steps from batch
    if _batch_injected:
        _capped_inject = _shape_steps(_batch_injected[:6], label="parallel-inject")
        remaining_steps[:0] = _capped_inject
        remaining_indices[:0] = [-1] * len(_capped_inject)
        log.info("parallel batch: injected %d step(s) from batch into plan",
                 len(_capped_inject))
        if ctx.verbose:
            for _s in _capped_inject:
                print(f"[maro] injected step (from parallel batch): {_s[:80]}",
                      file=sys.stderr, flush=True)

    # Log batch cost
    try:
        _batch_tokens = sum(o.get("tokens_in", 0) + o.get("tokens_out", 0) for o in _batch_outcomes)
        log.info("parallel batch done: %d steps, %d tokens, %dms",
                 len(_batch_steps), _batch_tokens, int((time.monotonic() - _batch_start) * 1000))
    except Exception as _exc:
        log.debug("parallel batch cost logging failed: %s", _exc)

    return iteration, step_idx, _tokens_in_delta, _tokens_out_delta


def _run_parallel_path(
    ctx: LoopContext,
    steps: List[str],
    *,
    clean_steps: List[str],
    deps: Dict[int, Any],
    levels: Optional[List[Any]],
    parallel_levels: List[Any],
    parallel_fan_out: int,
    proj_fanout_dir: str,
    loop_shared_ctx: Dict[str, Any],
    use_dag: bool,
    resolve_tools_fn,
) -> Optional[LoopResult]:
    """Phase D: Parallel fan-out early return path.

    Returns LoopResult if parallel execution was used, None otherwise
    (caller falls through to sequential execution).
    """
    from llm import LLMTool

    if use_dag:
        if ctx.verbose:
            print(
                f"[maro] dag: running {len(clean_steps)} steps with dep-aware scheduling "
                f"(max_workers={parallel_fan_out}, levels={len(levels)}, "
                f"parallel_levels={len(parallel_levels)})",
                file=sys.stderr, flush=True,
            )
        _fanout_outcomes = _run_steps_dag(
            goal=ctx.goal,
            steps=clean_steps,
            deps=deps,
            adapter=ctx.adapter,
            ancestry_context=ctx.ancestry_context,
            tools=[LLMTool(**t) for t in resolve_tools_fn()],
            verbose=ctx.verbose,
            max_workers=parallel_fan_out,
            project_dir=proj_fanout_dir,
            shared_ctx=loop_shared_ctx,
        )
        _fanout_step_texts = clean_steps
    else:
        if ctx.verbose:
            print(f"[maro] fan-out: running {len(steps)} steps in parallel (max_workers={parallel_fan_out})", file=sys.stderr, flush=True)
        _fanout_outcomes = _run_steps_parallel(
            goal=ctx.goal,
            steps=steps,
            adapter=ctx.adapter,
            ancestry_context=ctx.ancestry_context,
            tools=[LLMTool(**t) for t in resolve_tools_fn()],
            verbose=ctx.verbose,
            max_workers=parallel_fan_out,
            project_dir=proj_fanout_dir,
            shared_ctx=loop_shared_ctx,
        )
        _fanout_step_texts = steps

    # Build LoopResult from parallel/dag outcomes
    _fanout_step_outcomes: List[StepOutcome] = []
    _fanout_tokens_in = 0
    _fanout_tokens_out = 0
    _fanout_loop_status = "done"
    _fanout_stuck_reason = None
    for _i, (_step_text, _oc) in enumerate(zip(_fanout_step_texts, _fanout_outcomes), 1):
        _st = _oc.get("status", "blocked")
        _fanout_step_outcomes.append(step_from_decompose(
            _step_text, _i,
            status=_st,
            result=_oc.get("result", ""),
            iteration=_i,
            tokens_in=_oc.get("tokens_in", 0),
            tokens_out=_oc.get("tokens_out", 0),
            confidence=_oc.get("confidence", "unverified"),
            injected_steps=_oc.get("inject_steps", []),
            call_record=_oc.get("call_record", ""),
            # 2026-07-08 adversarial review (finding #2): no elapsed_ms is
            # tracked per fan-out worker at all here (defaults to 0) — ended_ts=""
            # keeps the run-visibility report's timeline in its approximate
            # fallback rather than rendering these as false zero-duration steps.
            ended_ts="",
        ))
        _fanout_tokens_in += _oc.get("tokens_in", 0)
        _fanout_tokens_out += _oc.get("tokens_out", 0)
        if _st == "blocked":
            _fanout_loop_status = "stuck"
            _fanout_stuck_reason = _oc.get("stuck_reason", f"step {_i} blocked")
        if ctx.step_callback is not None:
            try:
                ctx.step_callback(_i, _step_text, _oc.get("result", "")[:120], _st)
            except Exception as _cb_exc:
                log.debug("step_callback raised on parallel step %d: %s", _i, _cb_exc)
    elapsed = int((time.monotonic() - ctx.started_at) * 1000)
    return LoopResult(
        loop_id=ctx.loop_id,
        project=ctx.project,
        goal=ctx.goal,
        status=_fanout_loop_status,
        steps=_fanout_step_outcomes,
        total_tokens_in=_fanout_tokens_in,
        total_tokens_out=_fanout_tokens_out,
        elapsed_ms=elapsed,
        stuck_reason=_fanout_stuck_reason,
    )


def _run_steps_parallel(
    *,
    goal: str,
    steps: List[str],
    adapter,
    ancestry_context: str,
    tools: list,
    verbose: bool,
    max_workers: int,
    project_dir: str = "",
    shared_ctx: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    """Execute steps concurrently using ThreadPoolExecutor.

    Each step gets its own adapter instance (thread-safe: no shared state).
    completed_context is empty for all parallel steps (no inter-step dependencies
    by design — caller checked _steps_are_independent first).

    Returns outcomes list in step-index order.
    """
    from llm import build_adapter

    def _run_one(step_idx: int, step_text: str) -> tuple[int, dict]:
        try:
            from conductor import classify_step_model
            step_model = classify_step_model(step_text)
            step_adapter = build_adapter(model=step_model) if step_model != adapter.model_key else adapter
        except Exception as _cla_exc:
            log.debug("classify_step_model failed for parallel step %d, using default: %s", step_idx, _cla_exc)
            step_adapter = adapter

        # _execute_step handles prefetch internally
        outcome = _run_in_step_worktree(f"step{step_idx}", lambda: _execute_step(
            goal=goal,
            step_text=step_text,
            step_num=step_idx,
            total_steps=len(steps),
            completed_context=[],
            adapter=step_adapter,
            tools=tools,
            verbose=verbose,
            ancestry_context=ancestry_context,
            project_dir=project_dir,
            shared_ctx=shared_ctx,
        ))

        # Post-step security scan — parallel fan-out skips the main loop's
        # _post_step_checks, so we do a lightweight scan here.  Ralph verify
        # is not run in parallel mode (it requires session-level state).
        if outcome.get("status") == "done":
            _result_text = outcome.get("result", "") or ""
            if _result_text:
                try:
                    from security import scan_external_content as _sec_scan, InjectionRisk as _IRisk
                    _sec_result = _sec_scan(_result_text)
                    if _sec_result.risk >= _IRisk.HIGH:
                        # Use sanitized result rather than blocking the entire step.
                        # Step results are our agent's own output (possibly containing
                        # fetched web content); fully blocking them causes research runs
                        # to stall.  Sanitize-in-place redacts the suspicious spans
                        # while keeping the useful content.
                        outcome["result"] = _sec_result.sanitized
                        log.warning(
                            "parallel step %d: security scan HIGH-risk — sanitized result "
                            "(signals: %s)",
                            step_idx, ", ".join(_sec_result.signals),
                        )
                    elif _sec_result.risk > _IRisk.NONE:
                        # Sanitize in-place for lower risk levels
                        outcome["result"] = _sec_result.sanitized
                except Exception:
                    pass  # security module optional; never block legitimate parallel work

        if verbose:
            status_label = outcome.get("status", "?")
            summary = outcome.get("summary", "")[:80]
            print(f"[maro] parallel step {step_idx} {status_label}: {summary}", file=sys.stderr, flush=True)
        return step_idx, outcome

    n_workers = min(max_workers, len(steps))
    outcomes_by_idx: Dict[int, dict] = {}

    _fanout_timeout = int(os.environ.get("MARO_STEP_TIMEOUT", "600"))  # 10 min default

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        # copy_context: pool threads don't inherit ContextVars (run-dir,
        # default subprocess cwd) — a bare submit would strand run-scoped
        # writes in the legacy fallback paths. Fresh copy per submit.
        futures = {
            pool.submit(contextvars.copy_context().run, _run_one, i + 1, s): i
            for i, s in enumerate(steps)
        }
        try:
            for f in as_completed(futures, timeout=_fanout_timeout):
                try:
                    idx, outcome = f.result(timeout=30)
                    outcomes_by_idx[idx] = outcome
                except Exception as exc:
                    i = futures[f]
                    outcomes_by_idx[i + 1] = {
                        "status": "blocked",
                        "stuck_reason": f"parallel execution error: {exc}",
                        "result": "",
                        "summary": f"step {i + 1} failed in fan-out",
                        "tokens_in": 0,
                        "tokens_out": 0,
                    }
        except TimeoutError:
            # Some futures didn't complete within the timeout — mark them as blocked
            for f, i in futures.items():
                if (i + 1) not in outcomes_by_idx:
                    outcomes_by_idx[i + 1] = {
                        "status": "blocked",
                        "stuck_reason": f"parallel fan-out timeout ({_fanout_timeout}s)",
                        "result": "",
                        "summary": f"step {i + 1} timed out in fan-out",
                        "tokens_in": 0,
                        "tokens_out": 0,
                    }
                    log.warning("parallel step %d timed out after %ds", i + 1, _fanout_timeout)

    # Fill any missing indices (shouldn't happen, but defensive)
    for i in range(len(steps)):
        if (i + 1) not in outcomes_by_idx:
            outcomes_by_idx[i + 1] = {
                "status": "blocked", "stuck_reason": "missing from fan-out results",
                "result": "", "tokens_in": 0, "tokens_out": 0,
            }

    return [outcomes_by_idx[i + 1] for i in range(len(steps))]


def _run_steps_dag(
    *,
    goal: str,
    steps: List[str],
    deps: Dict[str, Any],
    adapter,
    ancestry_context: str,
    tools: list,
    verbose: bool,
    max_workers: int,
    project_dir: str = "",
    shared_ctx: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    """Dep-aware parallel execution — semaphore-gated pool with auto-unblock.

    Unlike _run_steps_parallel (which requires ALL steps to be independent),
    this handles arbitrary DAG topologies:
    - Tasks with no pending deps are submitted to the pool immediately.
    - When a task completes, its dependents whose deps are now all satisfied
      are submitted automatically (auto-unblock).
    - Completed dep results are passed as completed_context to each step,
      so downstream steps (e.g. "Synthesize [after:1,2]") get the actual
      outputs of their upstream steps.

    Args:
        steps: Clean step strings (tags stripped by parse_dependencies).
        deps:  1-based step index → set of dep indices (from parse_dependencies).

    Returns outcomes list in step-index order.
    """
    from llm import build_adapter
    import threading as _threading

    n = len(steps)
    results: Dict[int, dict] = {}
    results_lock = _threading.Lock()

    # Mutable copy — we discard entries as deps complete
    remaining_deps: Dict[int, Any] = {
        i: set(deps.get(i, set())) for i in range(1, n + 1)
    }

    _fanout_timeout = int(os.environ.get("MARO_STEP_TIMEOUT", "600"))

    def _run_one(step_idx: int) -> tuple:
        step_text = steps[step_idx - 1]
        # Build completed_context from direct dep results (already done when we start)
        dep_ctx: List[str] = []
        for dep_idx in sorted(deps.get(step_idx, set())):
            with results_lock:
                dep_oc = results.get(dep_idx, {})
            dep_result = dep_oc.get("result", "")
            dep_step_text = steps[dep_idx - 1] if 1 <= dep_idx <= n else ""
            if dep_result:
                dep_ctx.append(f"Step {dep_idx} ({dep_step_text[:60]}):\n{dep_result[:600]}")

        try:
            from conductor import classify_step_model
            step_model = classify_step_model(step_text)
            step_adapter = build_adapter(model=step_model) if step_model != adapter.model_key else adapter
        except Exception as _cla_exc:
            log.debug("classify_step_model failed for DAG step %d, using default: %s", step_idx, _cla_exc)
            step_adapter = adapter

        outcome = _run_in_step_worktree(f"dagstep{step_idx}", lambda: _execute_step(
            goal=goal,
            step_text=step_text,
            step_num=step_idx,
            total_steps=n,
            completed_context=dep_ctx,
            adapter=step_adapter,
            tools=tools,
            verbose=verbose,
            ancestry_context=ancestry_context,
            project_dir=project_dir,
            shared_ctx=shared_ctx,
        ))
        if verbose:
            status_label = outcome.get("status", "?")
            summary = outcome.get("summary", "")[:80]
            print(f"[maro] dag step {step_idx} {status_label}: {summary}", file=sys.stderr, flush=True)
        with results_lock:
            results[step_idx] = outcome
        return step_idx, outcome

    active: Dict[Any, int] = {}  # Future → step_idx

    def _submit_ready(pool) -> None:
        """Submit all tasks whose deps are now fully satisfied."""
        for step_idx in range(1, n + 1):
            if step_idx in results:
                continue  # already done
            if step_idx in active.values():
                continue  # already in-flight
            if not remaining_deps.get(step_idx):  # no remaining deps
                # copy_context: carry run-dir/cwd ContextVars into pool threads
                f = pool.submit(contextvars.copy_context().run, _run_one, step_idx)
                active[f] = step_idx

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        _submit_ready(pool)

        while active:
            _completed_f = None
            _timed_out = False
            try:
                for _f in as_completed(list(active), timeout=_fanout_timeout):
                    _completed_f = _f
                    break
            except TimeoutError:
                _timed_out = True

            if _timed_out:
                for _f, _idx in list(active.items()):
                    if _idx not in results:
                        results[_idx] = {
                            "status": "blocked",
                            "stuck_reason": f"dag timeout ({_fanout_timeout}s)",
                            "result": "", "tokens_in": 0, "tokens_out": 0,
                        }
                        log.warning("dag step %d timed out after %ds", _idx, _fanout_timeout)
                break

            completed_idx = active.pop(_completed_f)
            try:
                _completed_f.result(timeout=30)
            except Exception as exc:
                with results_lock:
                    results[completed_idx] = {
                        "status": "blocked",
                        "stuck_reason": f"dag execution error: {exc}",
                        "result": "", "tokens_in": 0, "tokens_out": 0,
                    }

            # Unblock tasks whose only remaining dep was the just-completed one
            for step_idx in range(1, n + 1):
                if step_idx not in results and step_idx not in active.values():
                    remaining_deps.get(step_idx, set()).discard(completed_idx)

            _submit_ready(pool)

    # Fill any unreached tasks (deps of a timed-out step)
    for i in range(1, n + 1):
        if i not in results:
            results[i] = {
                "status": "blocked",
                "stuck_reason": "dag: upstream dep did not complete",
                "result": "", "tokens_in": 0, "tokens_out": 0,
            }

    return [results[i] for i in range(1, n + 1)]
