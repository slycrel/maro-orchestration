"""Goal decomposition and pre-flight planning for the agent loop (Tier 3 split
of agent_loop.py).

Extracted verbatim from agent_loop.py — dependency-pattern detection for safe
parallelism, exec+analyze step splitting, loop-context assembly (memory/skills/
cost/codebase/repo context for the decompose prompt), goal decomposition, and
the pre-flight/prepare-execution phases that run before step execution begins.
"""

from __future__ import annotations

import logging
import re as _re
import sys
from typing import Any, Dict, List, Optional

from loop_types import (
    _orch,
    _project_dir_root,
    LoopContext,
    LoopResult,
    StepOutcome,
    step_from_decompose,
)
from loop_artifacts import _write_plan_manifest
from loop_report import write_run_report as _write_run_report
from planner import decompose as _decompose_impl

log = logging.getLogger("maro.loop")

# ---------------------------------------------------------------------------
# Parallel fan-out helpers (Phase 35 P1)
# ---------------------------------------------------------------------------

# Phrases that indicate a step depends on a prior step's output.
#
# Session 20 adversarial review finding 3.9: the original list missed common
# aggregation/synthesis verbs ("compile", "summarize", "synthesize") that
# implicitly depend on prior step output without naming them. Expanded to
# include those plus generic noun-phrase references ("the findings",
# "the report", "the data"). False positives (marking independent steps as
# dependent) just disable parallelism — safe. False negatives (marking
# dependent steps as independent) cause race conditions — what we're guarding.
_DEPENDENCY_PATTERNS = [
    # Explicit step references
    r"\bstep \d+\b",                                          # "step 2", "step N"
    r"\bfrom (the )?(previous|above|prior|last) step\b",
    r"\bidentified in step\b",
    r"\bfollowing (the|from) step\b",
    # Generic prior-output references
    r"\bbased on (the )?(above|previous|prior|results?|findings?|outputs?|data)\b",
    r"\busing (the )?(result|output|finding|content|data) (from|of) (step|above)\b",
    r"\bfrom the (result|output|content) (above|of step)\b",
    r"\bgiven (the )?(above|results?|findings?|data)\b",
    r"\bwith (the )?(above|prior|previous)\b",   # "with the above ...", "with prior ..."
    r"\bwith (the )?(results?|findings?|data) in (mind|hand)\b",
    # Aggregation/synthesis verbs that imply prior outputs
    r"\b(compile|aggregate|consolidate|synthesize|combine|merge) (the |all )?(results?|findings?|outputs?|data|reports?)\b",
    r"\bsummari[sz]e (the |all |these |those )?(above|results?|findings?|outputs?|reports?|data)\b",
    r"\banaly[sz]e (the |all |these |those )?(above|results?|findings?|outputs?|reports?|data)\b",
    r"\b(produce|generate|write|build) (a |the )?(final |overall |comprehensive )?(report|summary|comparison|synthesis)\b",
    r"\bcomparing (the |all )?(results?|findings?|outputs?)\b",
]
_DEP_RE = _re.compile("|".join(_DEPENDENCY_PATTERNS), _re.I)


def _steps_are_independent(steps: List[str]) -> bool:
    """Return True if no step references a prior step's output.

    Heuristic only — false positives (marking independent steps as dependent)
    just disable parallelism, which is safe. False negatives (marking
    dependent steps as independent) cause race conditions — adversarial
    review finding 3.9 expanded the pattern set to reduce those.
    """
    return not any(_DEP_RE.search(s) for s in steps)


def _prepare_execution(
    ctx: LoopContext,
    steps: List[str],
    manifest_steps: List[str],
) -> tuple:
    """Phase E: Shape steps and write NEXT.md.

    Returns (steps, step_indices, manifest_steps) — steps may be reshaped.
    """
    _shaped_steps = _shape_steps(steps, label="initial-plan")
    if len(_shaped_steps) != len(steps):
        if ctx.verbose:
            print(
                f"[maro] step-shape: {len(steps)} planned → {len(_shaped_steps)} after splitting "
                f"combined exec+analyze steps",
                file=sys.stderr, flush=True,
            )
        steps = _shaped_steps
        manifest_steps = list(steps)

    o = _orch()
    step_indices = o.append_next_items(ctx.project, steps)
    o.append_decision(ctx.project, [
        f"[loop:{ctx.loop_id}] Goal: {ctx.goal}",
        *[f"- step {i}: {s}" for i, s in enumerate(steps, 1)],
    ])

    return steps, step_indices, manifest_steps


def _preflight_checks(
    ctx: LoopContext,
    steps: List[str],
    *,
    resume_from_loop_id: Optional[str],
    parallel_fan_out: int,
) -> tuple:
    """Phase C: Pre-flight — resume, cost gate, plan review, dep parsing, manifest.

    Returns (steps, preflight_results: dict, early_return: Optional[LoopResult]).
    If early_return is not None, caller should return it immediately.
    steps may be modified by checkpoint resume.
    """
    # Session resume — load checkpoint and skip completed steps
    resume_completed: List[StepOutcome] = []
    if resume_from_loop_id:
        try:
            from checkpoint import load_checkpoint, resume_from as _resume_from
            _ckpt = load_checkpoint(resume_from_loop_id)
            if _ckpt is not None:
                _remaining, _done = _resume_from(_ckpt)
                for _cs in _done:
                    resume_completed.append(step_from_decompose(
                        _cs.text, _cs.index,
                        status=_cs.status,
                        result=_cs.result,
                        iteration=getattr(_cs, "iteration", 0),
                        tokens_in=_cs.tokens_in,
                        tokens_out=_cs.tokens_out,
                        elapsed_ms=_cs.elapsed_ms,
                        confidence=getattr(_cs, "confidence", ""),
                        injected_steps=list(getattr(_cs, "injected_steps", [])),
                        # 2026-07-08 adversarial review (finding #5): checkpoints
                        # don't persist ended_ts, so this reconstruction happens
                        # long after the step actually ran — ended_ts="" opts out
                        # of the "now" default so the report's timeline correctly
                        # falls back to its approximate mode instead of showing
                        # a resumed step as if it just finished.
                        ended_ts="",
                    ))
                steps = _remaining
                if ctx.verbose:
                    print(
                        f"[maro] resuming from checkpoint {resume_from_loop_id}: "
                        f"{len(resume_completed)} steps already done, {len(steps)} remaining",
                        file=sys.stderr, flush=True,
                    )
                log.info("checkpoint resume: loop_id=%s done=%d remaining=%d",
                         resume_from_loop_id, len(resume_completed), len(steps))
            else:
                log.warning("checkpoint not found for resume_from_loop_id=%s, starting fresh", resume_from_loop_id)
        except Exception as _ckpt_err:
            log.warning("checkpoint resume failed (%s), starting fresh", _ckpt_err)

    # Upfront cost estimation — fail fast if estimate exceeds budget
    if ctx.cost_budget is not None:
        try:
            from metrics import estimate_loop_cost
            _estimated = estimate_loop_cost(len(steps), step_texts=steps)
            if _estimated > 0:
                _slush = ctx.cost_budget * 0.2
                if _estimated > ctx.cost_budget + _slush:
                    log.warning("cost estimate $%.2f exceeds budget $%.2f + slush $%.2f — aborting",
                                _estimated, ctx.cost_budget, _slush)
                    return steps, {}, LoopResult(
                        loop_id=ctx.loop_id, project=ctx.project or "", goal=ctx.goal,
                        status="stuck",
                        stuck_reason=f"Estimated cost ${_estimated:.2f} exceeds budget ${ctx.cost_budget:.2f} "
                                     f"(with ${_slush:.2f} slush). Reduce step count or use cheaper models.",
                    )
                elif _estimated > ctx.cost_budget * 0.8:
                    log.info("cost estimate $%.2f approaching budget $%.2f (%.0f%%)",
                             _estimated, ctx.cost_budget, _estimated / ctx.cost_budget * 100)
        except ImportError:
            pass

    # Pre-run observability
    try:
        from metrics import estimate_loop_cost as _elc
        _pre_est = _elc(len(steps), step_texts=steps)
        if _pre_est > 0:
            log.info("pre-run estimate: %d steps, ~$%.2f", len(steps), _pre_est)
            if ctx.verbose:
                print(f"[maro] pre-run: {len(steps)} steps, estimated ~${_pre_est:.2f}", file=sys.stderr, flush=True)
        else:
            log.info("pre-run: %d steps (no cost estimate available)", len(steps))
    except Exception:
        log.info("pre-run: %d steps", len(steps))

    # Pre-flight plan review
    pf_review = None
    if not ctx.dry_run:
        try:
            from pre_flight import review_plan as _review_plan
            pf_review = _review_plan(ctx.goal, steps, ctx.adapter, verbose=ctx.verbose)
            if pf_review.milestone_step_indices:
                log.info("pre-flight: steps %s flagged as milestone candidates — "
                         "may need own planning pass", pf_review.milestone_step_indices)
        except Exception as _pf_exc:
            log.debug("pre-flight plan review failed: %s", _pf_exc)

    # Parse step dependencies for level-based and DAG-aware parallel execution
    clean_steps = steps
    deps: Dict[int, Any] = {}
    levels: Optional[List[Any]] = None
    parallel_levels: List[Any] = []
    try:
        from planner import parse_dependencies, build_execution_levels
        clean_steps, deps = parse_dependencies(steps)
        levels = build_execution_levels(deps)
        parallel_levels = [l for l in levels if len(l) > 1]
        if parallel_levels:
            log.info("dependency graph: %d levels, %d parallelizable (%s)",
                     len(levels), len(parallel_levels),
                     ", ".join(f"L{i+1}={len(l)}" for i, l in enumerate(levels)))
    except ImportError:
        pass

    # Phase 36: emit loop_start event
    try:
        from observe import write_event as _write_event
        _write_event("loop_start", goal=ctx.goal, project=ctx.project or "", loop_id=ctx.loop_id, status="start")
    except Exception as _obs_exc:
        log.debug("loop_start observe event failed: %s", _obs_exc)

    # Emit plan manifest
    manifest_steps: List[str] = list(steps)
    manifest_path_str: Optional[str] = None
    if ctx.project:
        try:
            manifest_path_str = _write_plan_manifest(
                project=ctx.project,
                loop_id=ctx.loop_id,
                goal=ctx.goal,
                planned_steps=manifest_steps,
                start_ts=ctx.start_ts,
            )
            if ctx.verbose and manifest_path_str:
                print(f"[maro] plan manifest: {manifest_path_str}", file=sys.stderr, flush=True)
        except Exception as _mf_exc:
            log.warning("initial plan manifest write failed: %s", _mf_exc)

        try:
            _write_run_report(
                project=ctx.project,
                loop_id=ctx.loop_id,
                goal=ctx.goal,
                planned_steps=manifest_steps,
                start_ts=ctx.start_ts,
                step_outcomes=[],
            )
        except Exception as _rep_exc:
            log.warning("initial run report write failed: %s", _rep_exc)

    # Shared state for team workers
    loop_shared_ctx: Dict[str, Any] = {}

    # Compute parallel path info
    proj_fanout_dir = ""
    if ctx.project:
        try:
            proj_fanout_dir = str(_project_dir_root() / ctx.project)
        except Exception as _dir_exc:
            log.debug("project fanout dir resolution failed: %s", _dir_exc)

    use_dag = parallel_fan_out > 0 and len(clean_steps) > 1 and bool(parallel_levels)
    use_fanout = (not use_dag and parallel_fan_out > 0
                  and len(steps) > 1 and _steps_are_independent(steps))

    pf = {
        "resume_completed": resume_completed,
        "pf_review": pf_review,
        "clean_steps": clean_steps,
        "deps": deps,
        "levels": levels,
        "parallel_levels": parallel_levels,
        "manifest_steps": manifest_steps,
        "replan_count": 0,
        "manifest_path_str": manifest_path_str,
        "loop_shared_ctx": loop_shared_ctx,
        "proj_fanout_dir": proj_fanout_dir,
        "use_dag": use_dag,
        "use_fanout": use_fanout,
    }
    return steps, pf, None


def _decompose_goal(
    ctx: LoopContext,
    *,
    preset_steps: Optional[List[str]],
    max_steps: int,
    knowledge_sub_goals: bool,
    permission_context,
) -> tuple:
    """Phase B: Decompose goal into steps, run prereq checks.

    Returns (steps, prereq_context, lessons_context, skills_context, cost_context).
    """
    from llm import build_adapter, MODEL_MID, THINKING_HIGH

    if ctx.verbose:
        print("[maro] decomposing goal...", file=sys.stderr, flush=True)
    _lessons_context, _skills_context, _cost_context, _had_no_matching_skill, _matched_rule = (
        _build_loop_context(ctx.goal, verbose=ctx.verbose, permission_context=permission_context,
                            project=ctx.project or "", repo_path=ctx.repo_path or "")
    )

    # Stage 5: rule hit — use deterministic steps, skip LLM decompose
    if preset_steps is not None and preset_steps:
        steps = [str(s).strip() for s in preset_steps if str(s).strip()]
        if ctx.verbose:
            print(f"[maro] pipeline: using {len(steps)} preset steps (no decompose)", file=sys.stderr, flush=True)
    elif _matched_rule is not None and _matched_rule.steps_template:
        steps = list(_matched_rule.steps_template)
        if ctx.verbose:
            print(f"[maro] using {len(steps)} rule steps from {_matched_rule.name!r}", file=sys.stderr, flush=True)
    else:
        steps = None

    if steps is None:
        # Planning runs once per loop and biases every subsequent step. Use the
        # central role→model policy (assign_model_by_role("planner") → MODEL_POWER)
        # — same surface director.py uses, so the planner-tier choice lives in
        # one place. Step execution stays on whatever the loop adapter selected.
        from llm import LLMAdapter as _LLMAdapterBase
        from conductor import assign_model_by_role as _assign
        _decompose_adapter = ctx.adapter
        # Only lift adapters we know how to rebuild (build_adapter products all
        # subclass LLMAdapter). Dry-run and injected test doubles are plain
        # classes — swapping them for a live subprocess/SDK adapter would burn
        # real LLM calls and break the injection seam.
        if not ctx.dry_run and isinstance(ctx.adapter, _LLMAdapterBase):
            _planner_tier = _assign("planner")
            try:
                _decompose_adapter = build_adapter(model=_planner_tier)
                log.debug("decompose: lifted adapter to %s for plan quality", _planner_tier)
            except Exception as _power_exc:
                log.debug("decompose: %s unavailable (%s); falling back to mid",
                          _planner_tier, _power_exc)
                try:
                    _decompose_adapter = build_adapter(model=MODEL_MID)
                except Exception:
                    _decompose_adapter = ctx.adapter
        # Enable extended thinking for decomposition when using Anthropic SDK
        # (planning benefits most from deeper reasoning)
        _decompose_thinking = None
        if getattr(_decompose_adapter, "backend", "") == "anthropic":
            _decompose_thinking = THINKING_HIGH
        steps = _decompose(
            ctx.goal, _decompose_adapter, max_steps=max_steps, verbose=ctx.verbose,
            lessons_context=_lessons_context, ancestry_context=ctx.ancestry_context,
            skills_context=_skills_context, cost_context=_cost_context,
            thinking_budget=_decompose_thinking,
        )
    if ctx.verbose:
        print(f"[maro] plan ({len(steps)} steps) loop_id={ctx.loop_id}:", file=sys.stderr, flush=True)
        for _pi, _ps in enumerate(steps, 1):
            print(f"  {_pi}. {_ps[:100]}", file=sys.stderr, flush=True)

    # Phase 27: Per-step knowledge prerequisite check.
    _prereq_context: dict = {}
    if not ctx.dry_run:
        try:
            from prereq import check_prerequisites as _check_prereqs
            _prereq_context = _check_prereqs(
                steps,
                goal_id=ctx.loop_id,
                adapter=ctx.adapter,
                continuation_depth=ctx.continuation_depth,
                knowledge_sub_goals=knowledge_sub_goals,
                verbose=ctx.verbose,
            )
            if _prereq_context and ctx.verbose:
                print(
                    f"[maro] prereq: {len(_prereq_context)} step(s) have injected knowledge context",
                    file=sys.stderr, flush=True,
                )
        except Exception:
            pass  # prereq failures must never break the main loop

    return steps, _prereq_context, _lessons_context, _skills_context, _cost_context, _had_no_matching_skill


def _build_loop_context(
    goal: str,
    verbose: bool = False,
    permission_context=None,
    project: str = "",
    repo_path: str = "",
) -> tuple:
    """Load all context needed before decomposing a goal.

    Returns:
        (lessons_context, skills_context, cost_context, had_no_matching_skill, matched_rule)

    matched_rule is a Rule object if a Stage 5 rule matches the goal, else None.
    When matched_rule is set, the caller should use rule.steps_template directly
    and skip the LLM decompose call entirely.

    All failures are swallowed — missing memory or skills never block a loop.
    """
    # Memory context — the eight substrates (lessons, standing rules,
    # decisions, graveyard, failure notes, learning activity, playbook,
    # knowledge nodes) live behind the recall() seam (docs/RECALL_DESIGN.md);
    # this used to be ~110 inline lines here. recall() swallows per-substrate
    # failures itself and instruments the read (RECALL_PERFORMED, including
    # the lesson-cited stamp).
    lessons_context = ""
    try:
        from recall import recall as _recall_fn
        _rr = _recall_fn(goal, slice="loop", project=project)
        lessons_context = _rr.as_loop_block()
        if verbose and _rr.sources.get("graveyard_count"):
            print(
                f"[maro] resurrecting {_rr.sources['graveyard_count']} "
                f"graveyard lesson(s) for goal",
                file=sys.stderr, flush=True,
            )
    except Exception as _exc:
        log.debug("recall loop slice failed: %s", _exc)

    # Matching skills for decompose prompt injection
    skills_context = ""
    had_no_matching_skill = False
    try:
        from skills import find_matching_skills, format_skills_for_prompt, select_variant_for_task
        _matching_skills = find_matching_skills(goal, project=project)
        # A/B routing: for each matched skill, select parent or active challenger
        # using a hash of the goal as a stable routing key (loop_id not yet assigned)
        import hashlib as _hashlib
        _routing_key = _hashlib.sha1(goal.encode()).hexdigest()[:8]
        _matched_and_routed = [select_variant_for_task(s, _routing_key) for s in _matching_skills]
        skills_context = format_skills_for_prompt(_matched_and_routed)
        # Run-keyed record of what actually entered the prompt, post-routing.
        # Without this, A/B variant selection is invisible in the run record
        # and skill changes can't be attributed to outcome shifts.
        if _matched_and_routed:
            try:
                from runs import append_skills_manifest as _append_skills_manifest
                _append_skills_manifest(
                    [
                        {
                            "id": getattr(s, "id", ""),
                            "name": getattr(s, "name", ""),
                            "content_hash": getattr(s, "content_hash", ""),
                            "variant_of": getattr(s, "variant_of", None),
                            "tier": getattr(s, "tier", None),
                            "routing_key": _routing_key,
                        }
                        for s in _matched_and_routed
                    ],
                    stage="decompose",
                )
            except Exception:
                pass
        if _matched_and_routed and verbose:
            print(
                f"[maro] injecting {len(_matched_and_routed)} skill(s) into decompose",
                file=sys.stderr, flush=True,
            )
        had_no_matching_skill = not _matched_and_routed
    except Exception:
        had_no_matching_skill = True

    # Phase 41 step 4: curated SKILL.md summaries (progressive disclosure)
    # Summaries (name + description + triggers) are shown upfront; full body
    # is loaded on demand by the step executor when a skill name is invoked.
    curated_skills_context = ""
    try:
        from skill_loader import skill_loader as _skill_loader
        _role = getattr(permission_context, "role", None) if permission_context else None
        _curated_block = _skill_loader.get_summaries_block(role=_role, goal=goal)
        if _curated_block:
            curated_skills_context = _curated_block
            _curated_matches = _skill_loader.find_matching(goal, role=_role)
            try:
                from runs import append_skills_manifest as _append_skills_manifest
                _append_skills_manifest(
                    [
                        {
                            "name": getattr(s, "name", ""),
                            "file_path": str(getattr(s, "file_path", "")),
                        }
                        for s in _curated_matches
                    ],
                    stage="curated_summaries",
                )
            except Exception:
                pass
            if verbose:
                print(
                    f"[maro] injecting {len(_curated_matches)} curated skill(s) into decompose",
                    file=sys.stderr, flush=True,
                )
            if had_no_matching_skill:
                had_no_matching_skill = False
    except Exception as _csk_exc:
        log.debug("curated skill loader failed: %s", _csk_exc)

    # Cost awareness: expensive step types from metrics history
    cost_context = ""
    try:
        from metrics import analyze_step_costs
        _cost_analysis = analyze_step_costs()
        _expensive = _cost_analysis.get("expensive_types", [])
        if _expensive:
            cost_context = (
                "COST AWARENESS: The following step types have historically consumed "
                "disproportionate tokens — prefer cheaper alternatives when possible: "
                + ", ".join(_expensive)
            )
    except Exception as _cst_exc:
        log.debug("cost context analysis failed: %s", _cst_exc)

    # Codebase graph context — ranked call graph injected before decompose (non-blocking, fail-open)
    # Injects top files by import centrality so the planner can navigate the codebase surgically.
    # Only fires when a target repo is identifiable (explicit repo_path or project heuristic).
    try:
        from codebase_graph import build_codebase_graph, format_graph_context
        from pathlib import Path as _CGPath
        _cg_repo = repo_path or ""
        if not _cg_repo and project:
            _candidate = _CGPath.home() / "claude" / project
            if _candidate.exists():
                _cg_repo = str(_candidate)
        if _cg_repo:
            _cg = build_codebase_graph(_cg_repo, max_files=150)
            if not _cg.error and _cg.total_files > 0:
                _cg_ctx = format_graph_context(_cg, goal=goal, top_files=6, top_functions=8)
                if _cg_ctx:
                    lessons_context = (lessons_context + "\n\n" + _cg_ctx) if lessons_context else _cg_ctx
                    if verbose:
                        print(f"[maro] codebase graph: {_cg.total_files} files, top={_cg.ranked_files[0] if _cg.ranked_files else '?'}", file=sys.stderr, flush=True)
    except Exception as _cg_exc:
        log.debug("codebase graph injection failed: %s", _cg_exc)

    # Repo stack context — auto-detected tech stack for the target project repo (non-blocking)
    try:
        from repo_scan import scan_repo, format_repo_context
        from pathlib import Path as _Path
        _repo_to_scan = ""
        if repo_path:
            _repo_to_scan = repo_path
        elif project:
            # Heuristic: check ~/claude/{project}/ on this machine
            _candidate = _Path.home() / "claude" / project
            if _candidate.exists():
                _repo_to_scan = str(_candidate)
            else:
                # Try glob: ~/claude/{project}-*/  or  ~/claude/*-{project}/
                _home_claude = _Path.home() / "claude"
                if _home_claude.exists():
                    for _d in _home_claude.iterdir():
                        if _d.is_dir() and (
                            _d.name.startswith(project) or _d.name.endswith(project) or
                            project in _d.name
                        ):
                            _repo_to_scan = str(_d)
                            break
        if _repo_to_scan:
            _stack = scan_repo(_repo_to_scan)
            if _stack.primary_languages:
                _repo_ctx = format_repo_context(_stack)
                lessons_context = (lessons_context + "\n\n" + _repo_ctx) if lessons_context else _repo_ctx
                if verbose:
                    print(f"[maro] repo context: {_stack.summary}", file=sys.stderr, flush=True)
    except Exception as _repo_exc:
        log.debug("repo context injection failed: %s", _repo_exc)

    # Stage 5: check for a Rule match before returning (caller skips LLM decompose)
    matched_rule = None
    try:
        from rules import find_matching_rule, record_rule_use
        matched_rule = find_matching_rule(goal)
        if matched_rule is not None:
            record_rule_use(matched_rule.id)
            if verbose:
                print(
                    f"[maro] Stage 5 rule hit: {matched_rule.name!r} — skipping LLM decompose",
                    file=sys.stderr, flush=True,
                )
    except Exception as _rul_exc:
        log.debug("rule match check failed: %s", _rul_exc)

    # Merge curated SKILL.md summaries with runtime skill context
    if curated_skills_context:
        skills_context = (
            (skills_context + "\n\n" + curated_skills_context).strip()
            if skills_context
            else curated_skills_context
        )

    return lessons_context, skills_context, cost_context, had_no_matching_skill, matched_rule


_EXEC_KEYWORDS = frozenset([
    "pytest", "python", "run ", "execute", "make ", "npm ", "yarn ", "docker",
    "git ", "bash ", "sh ", "cargo ", "go test", "mvn ", "gradle",
    "install ", "build ", "compile", "lint ", "mypy ", "ruff ",
    "grep ", "find ", "curl ", "fetch", "rg ", "wget ", "cat ",
    "invoke ", "launch ", "trigger ", "call ", "exec ",
])
_ANALYZE_KEYWORDS = frozenset([
    "analyz", "summariz", "review", "identify failure", "check result",
    "interpret", "categoriz", "parse output", "parse result",
    "count pass", "count fail", "report on", "describe result",
    "judge", "critique", "conclude", "evaluate", "assess",
    "examine", "determine", "count the", "verify result",
    "see if", "check if", "identify", "inspect result",
    "inspect output", "look at result",
])


def _is_combined_exec_analyze(step: str) -> bool:
    """Return True if a step combines command execution with output analysis.

    These are the steps that routinely fail on long-running commands because
    the executor can't fit both the command timeout and analysis into one call.
    """
    low = step.lower()
    has_exec = any(kw in low for kw in _EXEC_KEYWORDS)
    has_analyze = any(kw in low for kw in _ANALYZE_KEYWORDS)
    return has_exec and has_analyze


def _split_exec_analyze(step: str) -> List[str]:
    """Split a combined exec+analyze step into two atomic steps.

    Returns a list of two step strings: [run_step, analyze_step].
    The analyze step is sanitized so it does not itself contain the original
    execution phrasing and re-trigger the compound-step detector.
    """
    low = step.lower()
    # Trim trailing clauses that describe analysis
    run_part = step
    for sep in (" and ", " then ", ", then ", " to ", "; "):
        if sep in low:
            idx = low.find(sep)
            candidate = step[:idx].strip()
            if any(kw in candidate.lower() for kw in _EXEC_KEYWORDS):
                run_part = candidate
                break

    analysis_part = "analyze the captured output for errors, results, and next actions"
    analysis_idx = min((low.find(kw) for kw in _ANALYZE_KEYWORDS if kw in low), default=-1)
    if analysis_idx >= 0:
        candidate = step[analysis_idx:].strip(" ,;:-")
        if candidate:
            analysis_part = candidate
    if any(kw in analysis_part.lower() for kw in _EXEC_KEYWORDS):
        analysis_part = "analyze the captured output for errors, results, and next actions"

    # Drop any analysis clause remaining in the run part — a produced step
    # that still matches the compound detector would re-split forever at the
    # executor-side leak guard (splits must strictly converge).
    _rp_low = run_part.lower()
    _an_idx = min((_rp_low.find(kw) for kw in _ANALYZE_KEYWORDS if kw in _rp_low), default=-1)
    if _an_idx > 0:
        run_part = run_part[:_an_idx].strip(" ,;:-")

    _rp = run_part.strip()
    if _rp.lower().startswith("run "):
        _rp = _rp[4:]
    run_step = f"Run {_rp[:120]} and save output to a file"
    analyze_step = f"Read the captured output and {analysis_part[:120]}"
    return [run_step, analyze_step]


def _shape_steps(steps: List[str], *, label: str = "") -> List[str]:
    """Apply exec+analyze splitting to every step in a list.

    Single invariant gate — use instead of inline _is_combined_exec_analyze loops.
    Safe to call at any plan-mutation point: inject_steps, replan, interrupt replace,
    initial plan, DAG insertion.
    """
    shaped: List[str] = []
    for s in steps:
        if _is_combined_exec_analyze(s):
            parts = _split_exec_analyze(s)
            shaped.extend(parts)
            log.info("step-shape%s: split compound step: %r → %r",
                     f"[{label}]" if label else "", s[:60], [p[:40] for p in parts])
        else:
            shaped.append(s)
    return shaped


def _decompose(goal, adapter, max_steps, verbose=False, lessons_context="",
               ancestry_context="", skills_context="", cost_context="",
               thinking_budget=None):
    """Delegate to planner.decompose(). See planner.py for full implementation."""
    from planner import maybe_add_verification_step
    steps = _decompose_impl(goal, adapter, max_steps, verbose=verbose,
                            lessons_context=lessons_context, ancestry_context=ancestry_context,
                            skills_context=skills_context, cost_context=cost_context,
                            thinking_budget=thinking_budget)
    return maybe_add_verification_step(steps, goal, max_steps=max_steps)
