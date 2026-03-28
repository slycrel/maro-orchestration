#!/usr/bin/env python3
"""Phase 1: Autonomous loop runner for Poe orchestration.

The critical unlock: give Poe a goal, watch it work until done or stuck.

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

import json
import logging
import os
import sys
import textwrap
import time
import re as _re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("poe.loop")

_logging_configured = False

def _configure_logging(verbose: bool = False) -> None:
    """Set up poe.* logger hierarchy once.

    Level resolution (first match wins):
      1. POE_LOG_LEVEL env var (DEBUG, INFO, WARNING, ERROR)
      2. verbose=True → DEBUG
      3. default → WARNING (quiet)

    Format: compact timestamp + level + logger name + message.
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    env_level = os.environ.get("POE_LOG_LEVEL", "").upper()
    if env_level and hasattr(logging, env_level):
        level = getattr(logging, env_level)
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    root_logger = logging.getLogger("poe")
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-.1s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        root_logger.addHandler(handler)
    root_logger.setLevel(level)

# ---------------------------------------------------------------------------
# Imports (lazy to avoid circular with orch)
# ---------------------------------------------------------------------------

def _orch():
    """Lazy import of orch module — resolves sys.path issues."""
    import orch
    return orch


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class StepOutcome:
    index: int
    text: str
    status: str          # "done" | "blocked" | "skipped"
    result: str          # LLM's text output for this step
    iteration: int       # which loop iteration produced this
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0


@dataclass
class LoopResult:
    loop_id: str
    project: str
    goal: str
    status: str          # "done" | "stuck" | "error" | "interrupted"
    steps: List[StepOutcome] = field(default_factory=list)
    stuck_reason: Optional[str] = None
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    elapsed_ms: int = 0
    log_path: Optional[str] = None
    interrupts_applied: int = 0
    march_of_nines_alert: bool = False    # Phase 19: chain_success < 0.5 alert

    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.status == "done")
        blocked = sum(1 for s in self.steps if s.status == "blocked")
        lines = [
            f"loop_id={self.loop_id}",
            f"project={self.project}",
            f"goal={self.goal!r}",
            f"status={self.status}",
            f"steps_done={done}/{len(self.steps)} blocked={blocked}",
            f"tokens={self.total_tokens_in}in+{self.total_tokens_out}out",
            f"elapsed_ms={self.elapsed_ms}",
            *([ f"interrupts_applied={self.interrupts_applied}"] if self.interrupts_applied else []),
            *([ "march_of_nines_alert=True"] if self.march_of_nines_alert else []),
        ]
        if self.stuck_reason:
            lines.append(f"stuck_reason={self.stuck_reason!r}")
        if self.log_path:
            lines.append(f"log={self.log_path}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_DECOMPOSE_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous planning agent.
    When given a goal, decompose it into 3-8 concrete, independently-executable steps.
    Each step should be a clear action or deliverable, not vague meta-steps.

    STEP GRANULARITY — CRITICAL:
    Each step must be completable in under 60 seconds of LLM thinking time.
    If a step involves setup, infrastructure, or external operations, break it into
    smaller atomic actions. Never combine "acquire X and then analyze X" into one step.

    BAD:  "Check out the repo at github.com/foo/bar and review the code structure"
    GOOD: "Clone github.com/foo/bar into a local working directory"
          "List the top-level files and identify the main source directories"
          "Read the README and summarize the project architecture"

    BAD:  "Research topic X and compile a report"
    GOOD: "Identify 3-5 key sources or search queries for topic X"
          "Extract key findings from each source"
          "Synthesize findings into a structured summary"

    BAD:  "Read all Python source files in src/"
    GOOD: "Read the main entry point and map the module dependency graph"
          "Read the 3 core modules (agent_loop, memory, skills) and summarize their APIs"
          "Read the I/O layer (telegram, slack, gateway) and note integration points"

    CODE REVIEW STEPS: never ask to read "all" files in a directory. Instead,
    target specific files or groups of 3-5 related files per step. A single step
    should touch at most ~2000 lines of code. If a directory has 20+ files,
    split the review across multiple steps by functional area.

    Prefer more steps that are each fast and concrete over fewer steps that are
    broad and slow. Setup steps (clone, fetch, install) should always be their
    own step, never bundled with analysis work.

    Respond ONLY with a JSON array of step strings. No prose. Example:
    ["step one description", "step two description", "step three description"]
""").strip()

_EXECUTE_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous execution agent.
    You are given a goal and a specific step to complete.
    Complete the step to the best of your ability, producing a concrete result.
    Then call exactly one tool:
      - complete_step: if you have successfully completed this step
      - flag_stuck: if you genuinely cannot complete this step (explain why precisely)
    Do NOT call flag_stuck for solvable problems — work through them first.
    Be thorough but concise. Output quality matters.

    URL FETCHING POLICY — IMPORTANT:
    All URL content has been pre-fetched and is provided in the PRE-FETCHED URL CONTENT
    block below. Use ONLY that pre-fetched content for any URLs mentioned in the step.
    Do NOT use Bash to curl/wget URLs. Do NOT use any tool to fetch URLs.
    If a URL's content is missing from the pre-fetch block, note it as unavailable and
    work with what you have — do not attempt to fetch it yourself.

    TOKEN EFFICIENCY — IMPORTANT:
    Minimize token usage at every step. Prefer low-cost approaches first:
    1. Use only the pre-fetched content already in context — never fetch more.
    2. Summarize and extract; do not quote long passages verbatim.
    3. Work with partial information rather than declaring stuck due to missing detail.
    4. Produce concise, structured output: bullet points over paragraphs where possible.
    5. Never use a tool call, Bash command, or file read if the answer is already in context.
    If you are unsure how to proceed, pick the interpretation that requires the fewest tokens
    to produce a useful result.
""").strip()

_EXECUTE_TOOLS = [
    {
        "name": "complete_step",
        "description": "Mark this step as complete and record the result.",
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The work product, findings, or output of this step.",
                },
                "summary": {
                    "type": "string",
                    "description": "One-sentence summary of what was accomplished.",
                },
            },
            "required": ["result", "summary"],
        },
    },
    {
        "name": "flag_stuck",
        "description": "Signal that this step cannot be completed, with a precise reason.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why this step cannot be completed.",
                },
                "attempted": {
                    "type": "string",
                    "description": "What was tried before giving up.",
                },
            },
            "required": ["reason"],
        },
    },
]


# ---------------------------------------------------------------------------
# Parallel fan-out helpers (Phase 35 P1)
# ---------------------------------------------------------------------------

# Phrases that indicate a step depends on a prior step's output.
_DEPENDENCY_PATTERNS = [
    r"\bstep \d+\b",               # "step 2", "step N"
    r"\bfrom (the )?(previous|above|prior|last) step\b",
    r"\bbased on (the )?(above|previous|prior|results?)\b",
    r"\busing (the )?(result|output|finding|content) (from|of) (step|above)\b",
    r"\bfrom the (result|output|content) (above|of step)\b",
    r"\bidentified in step\b",
    r"\bfollowing (the|from) step\b",
]
_DEP_RE = _re.compile("|".join(_DEPENDENCY_PATTERNS), _re.I)


def _steps_are_independent(steps: List[str]) -> bool:
    """Return True if no step references a prior step's output.

    Heuristic only — misses implicit dependencies but is safe:
    false negatives (marking dependent steps as independent) can't happen
    because we require ALL steps to pass the check.
    """
    return not any(_DEP_RE.search(s) for s in steps)


def _run_steps_parallel(
    *,
    goal: str,
    steps: List[str],
    adapter,
    ancestry_context: str,
    tools: list,
    verbose: bool,
    max_workers: int,
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
            from poe import classify_step_model
            step_model = classify_step_model(step_text)
            step_adapter = build_adapter(model=step_model) if step_model != adapter.model_key else adapter
        except Exception:
            step_adapter = adapter

        # _execute_step handles prefetch internally
        outcome = _execute_step(
            goal=goal,
            step_text=step_text,
            step_num=step_idx,
            total_steps=len(steps),
            completed_context=[],
            adapter=step_adapter,
            tools=tools,
            verbose=verbose,
            ancestry_context=ancestry_context,
        )
        if verbose:
            status_label = outcome.get("status", "?")
            summary = outcome.get("summary", "")[:80]
            print(f"[poe] parallel step {step_idx} {status_label}: {summary}", file=sys.stderr, flush=True)
        return step_idx, outcome

    n_workers = min(max_workers, len(steps))
    outcomes_by_idx: Dict[int, dict] = {}

    _fanout_timeout = int(os.environ.get("POE_STEP_TIMEOUT", "600"))  # 10 min default

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_run_one, i + 1, s): i
            for i, s in enumerate(steps)
        }
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

    return [outcomes_by_idx[i + 1] for i in range(len(steps))]


# ---------------------------------------------------------------------------
# Loop context / decompose helpers
# ---------------------------------------------------------------------------

@dataclass
class _BlockDecision:
    """Outcome of _handle_blocked_step(): what should the main loop do next."""
    retry: bool            # True → re-queue step; False → terminate loop
    hint: str              # context to prepend on retry
    loop_status: str       # "stuck" on terminate, unchanged on retry
    stuck_reason: str      # non-empty on terminate


def _build_loop_context(
    goal: str,
    verbose: bool = False,
) -> tuple:
    """Load all context needed before decomposing a goal.

    Returns:
        (lessons_context, skills_context, cost_context, had_no_matching_skill, matched_rule)

    matched_rule is a Rule object if a Stage 5 rule matches the goal, else None.
    When matched_rule is set, the caller should use rule.steps_template directly
    and skip the LLM decompose call entirely.

    All failures are swallowed — missing memory or skills never block a loop.
    """
    # Lessons from tiered memory
    lessons_context = ""
    try:
        from memory import inject_lessons_for_task
        lessons_context = inject_lessons_for_task("agenda", goal, max_lessons=3)
    except Exception:
        pass

    # Resurrected graveyard lessons (topic-relevant, decayed)
    try:
        from memory import search_graveyard
        _graveyard = search_graveyard(goal, resurrect=True)
        if _graveyard:
            if verbose:
                print(
                    f"[poe] resurrecting {len(_graveyard)} graveyard lesson(s) for goal",
                    file=sys.stderr, flush=True,
                )
            _graveyard_lines = "\n".join(f"- {l.lesson}" for l in _graveyard[:3])
            lessons_context += f"\n\nPreviously-learned (resurrected from decay):\n{_graveyard_lines}"
    except Exception:
        pass

    # Matching skills for decompose prompt injection
    skills_context = ""
    had_no_matching_skill = False
    try:
        from skills import find_matching_skills, format_skills_for_prompt
        _matching_skills = find_matching_skills(goal)
        skills_context = format_skills_for_prompt(_matching_skills)
        if _matching_skills and verbose:
            print(
                f"[poe] injecting {len(_matching_skills)} skill(s) into decompose",
                file=sys.stderr, flush=True,
            )
        had_no_matching_skill = not _matching_skills
    except Exception:
        had_no_matching_skill = True

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
    except Exception:
        pass

    # Stage 5: check for a Rule match before returning (caller skips LLM decompose)
    matched_rule = None
    try:
        from rules import find_matching_rule, record_rule_use
        matched_rule = find_matching_rule(goal)
        if matched_rule is not None:
            record_rule_use(matched_rule.id)
            if verbose:
                print(
                    f"[poe] Stage 5 rule hit: {matched_rule.name!r} — skipping LLM decompose",
                    file=sys.stderr, flush=True,
                )
    except Exception:
        pass

    return lessons_context, skills_context, cost_context, had_no_matching_skill, matched_rule


def _handle_blocked_step(
    step_text: str,
    outcome: dict,
    prior_retries: int,
    adapter,
) -> _BlockDecision:
    """Decide what to do when a step returns status != 'done'.

    Does not mutate any loop state — returns a decision the caller applies.

    Args:
        step_text:     The step text that failed.
        outcome:       The raw outcome dict from _execute_step().
        prior_retries: Number of times this step has already been retried.
        adapter:       LLM adapter (used for round-2 refinement hint).

    Returns:
        _BlockDecision — retry=True means re-queue; retry=False means terminate.
    """
    block_reason = outcome.get("stuck_reason", "blocked")
    step_result = outcome.get("result", "")

    if prior_retries < 2:
        if prior_retries == 0:
            # Round 1: generic fallback hint
            hint = (
                f"[Previous attempt blocked: {block_reason[:120]}] "
                "Try an alternative approach: use a different tool, rephrase the request, "
                "work around the obstacle, or summarize what you know so far and mark complete."
            )
        else:
            # Round 2: LLM-assisted targeted refinement hint
            hint = _generate_refinement_hint(
                step_text=step_text,
                block_reason=block_reason,
                partial_result=step_result,
                adapter=adapter,
            )
        return _BlockDecision(retry=True, hint=hint, loop_status="", stuck_reason="")
    else:
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="stuck",
            stuck_reason=block_reason,
        )


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
        _diag = _diagnose(loop_id)
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
            _recovery = _plan_recovery(_diag)
            if _recovery:
                _tag = "AUTO-RECOVERABLE" if _recovery.auto_apply else "NEEDS-REVIEW"
                log.warning("recovery[%s] risk=%s: %s", _tag, _recovery.risk, _recovery.action)
    except ImportError:
        pass
    except Exception as exc:
        log.debug("introspect failed: %s", exc)

    # Phase 5: Reflexion — record outcome + extract lessons
    try:
        from memory import reflect_and_record
        done_steps = [s for s in step_outcomes if s.status == "done"]
        summary = (
            f"Completed {len(done_steps)}/{len(step_outcomes)} steps. "
            + (step_outcomes[-1].result[:80] if step_outcomes and loop_status == "done" else "")
        )
        reflect_and_record(
            goal=goal,
            status=loop_status,
            result_summary=summary,
            task_type="agenda",
            project=project,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            elapsed_ms=elapsed_ms,
            adapter=adapter if not dry_run else None,
            dry_run=dry_run,
        )
    except Exception:
        pass

    # Auto-extract skills from successful loops (crystallise patterns)
    if loop_status == "done" and not dry_run and step_outcomes:
        try:
            from skills import extract_skills, save_skill, load_skills
            done_summaries = [s.summary for s in step_outcomes if s.status == "done" and s.summary]
            outcome_for_extraction = {
                "goal": goal,
                "status": loop_status,
                "task_type": "agenda",
                "summary": ". ".join(done_summaries[:4]),
                "steps": [
                    {"step": s.step, "status": s.status, "result": s.result, "summary": s.summary}
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
                        print(f"[poe] skill crystallised: {skill.name}", file=sys.stderr, flush=True)
        except Exception:
            pass

    # Phase 32: skill synthesis — when no skill matched at start, synthesize from this run
    if loop_status == "done" and had_no_matching_skill and not dry_run and step_outcomes:
        try:
            from evolver import synthesize_skill
            done_steps = [s for s in step_outcomes if s.status == "done" and s.summary]
            _synth_summary = ". ".join(s.summary for s in done_steps[:3])
            synthesize_skill(
                goal=goal,
                outcome_summary=_synth_summary or "completed successfully",
                source_loop_id=loop_id,
                adapter=adapter,
                verbose=verbose,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def run_agent_loop(
    goal: str,
    *,
    project: Optional[str] = None,
    model: Optional[str] = None,
    adapter=None,
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

    Returns:
        LoopResult with full outcome.
    """
    from llm import LLMMessage, LLMTool, build_adapter, MODEL_CHEAP
    from interrupt import InterruptQueue, apply_interrupt_to_steps, set_loop_running, clear_loop_running
    from poe import assign_model_by_role

    loop_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()
    start_ts = datetime.now(timezone.utc).isoformat()

    # Configure logging if verbose or POE_LOG_LEVEL is set
    _configure_logging(verbose)

    log.info("loop_start loop_id=%s goal=%r project=%s max_steps=%d",
             loop_id, goal[:80], project or "(auto)", max_steps)

    if verbose:
        print(f"[poe] loop_id={loop_id} goal={goal!r}", file=sys.stderr, flush=True)

    # Build adapter — worker role uses MODEL_MID by default (role-semantic selection)
    if adapter is None and not dry_run:
        adapter = build_adapter(model=model or assign_model_by_role("worker"))
    elif dry_run:
        adapter = _DryRunAdapter()

    # Set up interrupt queue — auto-create if not provided
    if interrupt_queue is None:
        try:
            interrupt_queue = InterruptQueue()
        except Exception:
            interrupt_queue = None  # Non-fatal: run without interrupt support

    # Advertise this loop as running so other interfaces can route interrupts
    try:
        set_loop_running(loop_id, goal)
    except Exception:
        pass

    # Resolve or create project
    o = _orch()
    if project and not o.project_dir(project).exists():
        o.ensure_project(project, goal[:80])
        if verbose:
            print(f"[poe] created project={project}", file=sys.stderr, flush=True)
    elif not project:
        slug = _goal_to_slug(goal)
        project = slug
        if not o.project_dir(project).exists():
            o.ensure_project(project, goal[:80])
            if verbose:
                print(f"[poe] created project={project}", file=sys.stderr, flush=True)

    # Load goal ancestry for prompt injection
    try:
        from ancestry import get_project_ancestry, build_ancestry_prompt
        _proj_dir = o.project_dir(project)
        _ancestry = get_project_ancestry(_proj_dir)
        _ancestry_context = build_ancestry_prompt(_ancestry, current_task=goal)
    except Exception:
        _ancestry_context = ""

    # Merge injected context from mission-level notification hooks (Phase 11)
    if ancestry_context_extra:
        _ancestry_context = (
            (_ancestry_context + "\n\n" + ancestry_context_extra)
            if _ancestry_context
            else ancestry_context_extra
        )

    # Load hook registry for step-level hooks (Phase 11)
    _hook_registry = hook_registry
    if _hook_registry is None:
        try:
            from hooks import load_registry as _load_registry
            _hook_registry = _load_registry()
        except Exception:
            _hook_registry = None

    # Step 1: Decompose goal into steps (inject memory + ancestry context)
    if verbose:
        print(f"[poe] decomposing goal...", file=sys.stderr, flush=True)
    _lessons_context, _skills_context, _cost_context, _had_no_matching_skill, _matched_rule = (
        _build_loop_context(goal, verbose=verbose)
    )

    # Stage 5: rule hit — use deterministic steps, skip LLM decompose
    if _matched_rule is not None and _matched_rule.steps_template:
        steps = list(_matched_rule.steps_template)
        if verbose:
            print(f"[poe] using {len(steps)} rule steps from {_matched_rule.name!r}", file=sys.stderr, flush=True)
    else:
        steps = None  # computed by _decompose below

    if steps is None:
        steps = _decompose(
            goal, adapter, max_steps=max_steps, verbose=verbose,
            lessons_context=_lessons_context, ancestry_context=_ancestry_context,
            skills_context=_skills_context, cost_context=_cost_context,
        )
    if verbose:
        print(f"[poe] decomposed into {len(steps)} steps", file=sys.stderr, flush=True)

    # Phase 36: emit loop_start event
    try:
        from observe import write_event as _write_event
        _write_event("loop_start", goal=goal, project=project or "", loop_id=loop_id, status="start")
    except Exception:
        pass

    # Phase 35 P1: parallel fan-out — run independent steps concurrently
    if parallel_fan_out > 0 and len(steps) > 1 and _steps_are_independent(steps):
        if verbose:
            print(f"[poe] fan-out: running {len(steps)} steps in parallel (max_workers={parallel_fan_out})", file=sys.stderr, flush=True)
        _fanout_outcomes = _run_steps_parallel(
            goal=goal,
            steps=steps,
            adapter=adapter,
            ancestry_context=_ancestry_context,
            tools=[LLMTool(**t) for t in _EXECUTE_TOOLS],
            verbose=verbose,
            max_workers=parallel_fan_out,
        )
        # Build LoopResult directly from parallel outcomes
        _fanout_step_outcomes: List[StepOutcome] = []
        _fanout_tokens_in = 0
        _fanout_tokens_out = 0
        _fanout_loop_status = "done"
        _fanout_stuck_reason = None
        for _i, (_step_text, _oc) in enumerate(zip(steps, _fanout_outcomes), 1):
            _st = _oc.get("status", "blocked")
            _fanout_step_outcomes.append(StepOutcome(
                index=_i,
                text=_step_text,
                status=_st,
                result=_oc.get("result", ""),
                iteration=_i,
                tokens_in=_oc.get("tokens_in", 0),
                tokens_out=_oc.get("tokens_out", 0),
            ))
            _fanout_tokens_in += _oc.get("tokens_in", 0)
            _fanout_tokens_out += _oc.get("tokens_out", 0)
            if _st == "blocked":
                _fanout_loop_status = "stuck"
                _fanout_stuck_reason = _oc.get("stuck_reason", f"step {_i} blocked")
            if step_callback is not None:
                try:
                    step_callback(_i, _step_text, _oc.get("result", "")[:120], _st)
                except Exception:
                    pass
        elapsed = int((time.monotonic() - started_at) * 1000)
        return LoopResult(
            loop_id=loop_id,
            project=project,
            goal=goal,
            status=_fanout_loop_status,
            steps=_fanout_step_outcomes,
            total_tokens_in=_fanout_tokens_in,
            total_tokens_out=_fanout_tokens_out,
            elapsed_ms=elapsed,
            stuck_reason=_fanout_stuck_reason,
        )

    # Add steps to project NEXT.md
    step_indices = o.append_next_items(project, steps)
    o.append_decision(project, [
        f"[loop:{loop_id}] Goal: {goal}",
        *[f"- step {i}: {s}" for i, s in enumerate(steps, 1)],
    ])

    # Step 2: Execute each step in order (dynamic — interrupts may add/replace steps)
    step_outcomes: List[StepOutcome] = []
    total_tokens_in = 0
    total_tokens_out = 0
    stuck_streak = 0
    last_action: Optional[str] = None
    iteration = 0
    loop_status = "done"
    stuck_reason = None
    completed_context: List[str] = []
    interrupts_applied = 0

    # Use mutable lists so interrupt handlers can modify remaining work
    remaining_steps: List[str] = list(steps)
    remaining_indices: List[int] = list(step_indices)
    step_idx = 0  # global step counter (for numbering, includes injected steps)
    _next_step_injected_context: str = ""  # Phase 11: injected context from previous step's hooks
    _march_of_nines_alert = False  # Phase 19: cumulative step success rate alert
    _step_retries: Dict[str, int] = {}  # roadblock resilience: retries per step text

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

    while remaining_steps:
        if iteration >= max_iterations:
            loop_status = "stuck"
            stuck_reason = f"hit max_iterations={max_iterations} before completing all steps"
            log.warning("max_iterations reached: %d/%d steps done, %d remaining, tokens=%d",
                        len(step_outcomes), len(step_outcomes) + len(remaining_steps),
                        len(remaining_steps), total_tokens_in + total_tokens_out)
            break

        # Budget-aware landing: when only 2 iterations remain and there are
        # still multiple steps, replace the remaining steps with a single
        # "synthesize what we have" step so the loop lands gracefully.
        _remaining_budget = max_iterations - iteration
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
                log.info("budget-aware landing: replaced %d remaining steps with synthesis step "
                         "(budget=%d iterations left, %d steps done)",
                         len(remaining_steps), _remaining_budget, _done_count)

        step_text = remaining_steps.pop(0)
        item_index = remaining_indices.pop(0) if remaining_indices else -1

        iteration += 1
        step_idx += 1
        if verbose:
            print(f"[poe] step {step_idx}: {step_text!r}", file=sys.stderr, flush=True)

        step_start = time.monotonic()
        # Phase 35 P1: per-step model selection — cheap retrieval/classify steps use Haiku
        _step_adapter = adapter
        try:
            from poe import classify_step_model
            _step_model = classify_step_model(step_text)
            if _step_model != adapter.model_key:
                _step_adapter = build_adapter(model=_step_model)
                if verbose:
                    _tier = "haiku" if _step_model == MODEL_CHEAP else "sonnet"
                    print(f"[poe] step {step_idx}: routing to {_tier} (classify_step_model)", file=sys.stderr, flush=True)
        except Exception:
            pass  # Model selection failures must never break the loop

        # _next_step_injected is set by the previous iteration's hook run
        _step_ancestry = (
            (_ancestry_context + "\n\n" + _next_step_injected_context)
            if _next_step_injected_context
            else _ancestry_context
        )
        outcome = _execute_step(
            goal=goal,
            step_text=step_text,
            step_num=step_idx,
            total_steps=step_idx + len(remaining_steps),
            completed_context=completed_context,
            adapter=_step_adapter,
            tools=[LLMTool(**t) for t in _EXECUTE_TOOLS],
            verbose=verbose,
            ancestry_context=_step_ancestry,
        )
        step_elapsed = int((time.monotonic() - step_start) * 1000)

        total_tokens_in += outcome.get("tokens_in", 0)
        total_tokens_out += outcome.get("tokens_out", 0)
        log.info("step %d %s tokens_step=%d tokens_total=%d elapsed_step=%dms iter=%d/%d",
                 step_idx, outcome.get("status", "?"),
                 outcome.get("tokens_in", 0) + outcome.get("tokens_out", 0),
                 total_tokens_in + total_tokens_out,
                 step_elapsed, iteration, max_iterations)

        # Phase 33: token budget — abort gracefully if exceeded
        if token_budget is not None and (total_tokens_in + total_tokens_out) >= token_budget:
            loop_status = "stuck"
            stuck_reason = (
                f"token_budget={token_budget} exceeded "
                f"({total_tokens_in + total_tokens_out} total tokens after step {step_idx})"
            )
            if verbose:
                print(f"[poe] {stuck_reason}", file=sys.stderr, flush=True)
            break

        step_status = outcome["status"]
        step_result = outcome.get("result", "")
        step_summary = outcome.get("summary", step_text)

        # Phase 36: emit step event to events.jsonl for live observability
        try:
            from observe import write_event
            write_event(
                "step_done" if step_status == "done" else "step_stuck",
                goal=goal,
                project=project or "",
                loop_id=loop_id,
                step=step_text,
                step_idx=step_idx,
                status=step_status,
                tokens_in=outcome.get("tokens_in", 0),
                tokens_out=outcome.get("tokens_out", 0),
                elapsed_ms=step_elapsed,
                detail=step_summary[:200] if step_summary else "",
            )
        except Exception:
            pass  # Event failures must never break the loop

        # Security: scan step result for prompt injection signals (external content defense).
        # Only scan results that contain pre-fetched external content (URLs, API responses).
        # LLM-generated analysis (code reviews, summaries) should NOT be scanned — it
        # false-positives on security terminology in reviewed code and corrupts the output.
        _has_external = "PRE-FETCHED" in step_text or "http" in step_text.lower()
        if _security_available and _has_external and step_status == "done" and len(step_result) > 200:
            try:
                _scan = _scan_content(
                    step_result,
                    log_fn=lambda msg: print(f"[poe] {msg}", file=sys.stderr, flush=True),
                )
                if _scan.risk >= _InjectionRisk.HIGH:
                    log.warning("step %d injection HIGH in result — redacting before context injection (signals=%s)",
                                step_idx, _scan.signals)
                    step_result = _scan.sanitized
                    outcome["result"] = step_result
            except Exception:
                pass  # Security scan failures must never break the loop

        # Phase 11: Step-level hooks
        _step_injected_context = ""
        if _hook_registry is not None:
            try:
                from hooks import run_hooks as _run_hooks, any_blocking as _any_blocking, get_injected_context as _get_injected_ctx, SCOPE_STEP as _SCOPE_STEP
                _step_hook_ctx = {
                    "goal": goal,
                    "step": step_text,
                    "step_result": step_result,
                    "project": project,
                    "step_num": step_idx,
                }
                _step_results = _run_hooks(
                    _SCOPE_STEP, _step_hook_ctx,
                    registry=_hook_registry, adapter=adapter,
                    dry_run=dry_run, fire_on="after",
                )
                if _any_blocking(_step_results):
                    step_status = "blocked"
                    _block_outputs = [r.output for r in _step_results if r.should_block]
                    outcome["stuck_reason"] = "blocked by hook reviewer: " + "; ".join(_block_outputs[:2])
                _step_injected_context = _get_injected_ctx(_step_results)
            except Exception:
                pass  # Hook failures must never break the loop

        # Stuck detection: same action repeated 3x
        action_key = f"{step_text}:{step_status}"
        if action_key == last_action:
            stuck_streak += 1
        else:
            stuck_streak = 0
            last_action = action_key

        if stuck_streak >= 2:  # 3rd repeat
            loop_status = "stuck"
            stuck_reason = f"same outcome '{step_status}' on '{step_text}' repeated 3 times"
            step_outcomes.append(StepOutcome(
                index=item_index,
                text=step_text,
                status="blocked",
                result=step_result,
                iteration=iteration,
                tokens_in=outcome.get("tokens_in", 0),
                tokens_out=outcome.get("tokens_out", 0),
                elapsed_ms=step_elapsed,
            ))
            if item_index >= 0:
                o.mark_item(project, item_index, o.STATE_BLOCKED)
            o.append_decision(project, [f"[loop:{loop_id}] stuck on step {step_idx}: {stuck_reason}"])
            break

        # Write artifact
        _write_step_artifact(project, loop_id, step_idx, step_text, step_result)

        if step_status == "done":
            if item_index >= 0:
                o.mark_item(project, item_index, o.STATE_DONE)
            _ctx_entry = f"Step {step_idx} ({step_text[:80]}): {step_summary[:120]}"
            completed_context.append(_ctx_entry)
            if verbose:
                print(f"[poe] step {step_idx} done: {step_summary[:120]}", file=sys.stderr, flush=True)
            # Phase 32: update utility score for any matching skills
            try:
                from skills import find_matching_skills, update_skill_utility
                for _sk in find_matching_skills(step_text + " " + goal, use_router=False):
                    update_skill_utility(_sk.id, success=True)
            except Exception:
                pass
            # Phase 33: record per-step cost
            try:
                from metrics import record_step_cost
                record_step_cost(
                    step_text=step_text,
                    tokens_in=outcome.get("tokens_in", 0),
                    tokens_out=outcome.get("tokens_out", 0),
                    status="done",
                    goal=goal,
                    model=getattr(adapter, "model_key", ""),
                    elapsed_ms=step_elapsed,
                )
            except Exception:
                pass
            if step_callback is not None:
                try:
                    step_callback(step_idx, step_text, step_summary, "done")
                except Exception:
                    pass
        else:
            _prior_retries = _step_retries.get(step_text, 0)
            _decision = _handle_blocked_step(step_text, outcome, _prior_retries, adapter)
            if _decision.retry:
                _step_retries[step_text] = _prior_retries + 1
                _next_step_injected_context = (
                    (_next_step_injected_context + "\n\n" + _decision.hint).strip()
                    if _next_step_injected_context
                    else _decision.hint
                )
                remaining_steps.insert(0, step_text)
                remaining_indices.insert(0, item_index)
                step_idx -= 1  # will be re-incremented at top of iteration
                if verbose:
                    _br = outcome.get("stuck_reason", "blocked")
                    print(f"[poe] step {step_idx+1} blocked ({_br[:80]}), retrying with fallback hint", file=sys.stderr, flush=True)
                # Record the blocked attempt but don't terminate
                step_outcomes.append(StepOutcome(
                    index=item_index,
                    text=step_text,
                    status="blocked",
                    result=step_result,
                    iteration=iteration,
                    tokens_in=outcome.get("tokens_in", 0),
                    tokens_out=outcome.get("tokens_out", 0),
                    elapsed_ms=step_elapsed,
                ))
                continue
            else:
                loop_status = _decision.loop_status
                stuck_reason = _decision.stuck_reason
                if item_index >= 0:
                    o.mark_item(project, item_index, o.STATE_BLOCKED)
                if verbose:
                    print(f"[poe] step {step_idx} stuck after retry: {stuck_reason}", file=sys.stderr, flush=True)
                # Phase 32: attribute failure to any matching skills
                try:
                    from skills import attribute_failure_to_skills
                    attribute_failure_to_skills(step_text, stuck_reason, goal=goal)
                except Exception:
                    pass
                # Phase 33: record per-step cost (blocked)
                try:
                    from metrics import record_step_cost
                    record_step_cost(
                        step_text=step_text,
                        tokens_in=outcome.get("tokens_in", 0),
                        tokens_out=outcome.get("tokens_out", 0),
                        status="blocked",
                        goal=goal,
                        model=getattr(adapter, "model_key", ""),
                        elapsed_ms=step_elapsed,
                    )
                except Exception:
                    pass
                if step_callback is not None:
                    try:
                        step_callback(step_idx, step_text, stuck_reason or "blocked", "blocked")
                    except Exception:
                        pass

        step_outcomes.append(StepOutcome(
            index=item_index,
            text=step_text,
            status=step_status,
            result=step_result,
            iteration=iteration,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            elapsed_ms=step_elapsed,
        ))

        # Phase 19: write dead end to DEAD_ENDS.md when step is blocked
        if step_status == "blocked" and _dead_ends_available:
            try:
                _reason = outcome.get("stuck_reason", f"step blocked: {step_text[:80]}")
                _attempted = outcome.get("result", "")[:200]
                _dead_end_text = (
                    f"Loop {loop_id} — Step: {step_text[:80]}\n"
                    f"Reason: {_reason}\n"
                    f"Attempted: {_attempted}"
                )
                _update_dead_ends(project, [_dead_end_text])
            except Exception:
                pass

        # Phase 19: March of Nines defense — track cumulative chain success rate
        if len(step_outcomes) >= 3:
            try:
                _steps_completed = sum(1 for s in step_outcomes if s.status == "done")
                _steps_attempted = len(step_outcomes)
                _cumulative_rate = _steps_completed / _steps_attempted
                _chain_success = _cumulative_rate ** _steps_attempted
                if _chain_success < 0.5:
                    _march_of_nines_alert = True
                    o.append_decision(project, [
                        f"[loop:{loop_id}] March of Nines alert: "
                        f"chain_success={_chain_success:.3f} "
                        f"({_steps_completed}/{_steps_attempted} steps done)"
                    ])
            except Exception:
                pass

        if loop_status == "stuck":
            break

        # Phase 11: carry injected_context forward to next step
        _next_step_injected_context = _step_injected_context

        # --- Interrupt polling: check for new instructions between steps ---
        if interrupt_queue is not None:
            try:
                pending = interrupt_queue.poll()
                for intr in pending:
                    interrupts_applied += 1
                    new_remaining, goal, should_stop = apply_interrupt_to_steps(
                        intr, remaining_steps, goal
                    )
                    if should_stop:
                        loop_status = "interrupted"
                        stuck_reason = f"stopped by {intr.source}: {intr.message[:80]}"
                        if verbose:
                            print(
                                f"[poe] interrupt: stop requested by {intr.source}",
                                file=sys.stderr, flush=True,
                            )
                        remaining_steps = []
                        remaining_indices = []
                        break
                    else:
                        # Inject new steps into NEXT.md and get their indices
                        added = [s for s in new_remaining if s not in remaining_steps]
                        if added:
                            new_idxs = o.append_next_items(project, added)
                            # Reconstruct remaining with proper indices
                            # Keep existing indices for pre-existing steps, append new ones
                            existing_count = len(remaining_steps)
                            remaining_steps = new_remaining
                            remaining_indices = remaining_indices[:existing_count] + new_idxs
                        else:
                            remaining_steps = new_remaining
                        o.append_decision(project, [
                            f"[loop:{loop_id}] interrupt({intr.intent}) from {intr.source}: {intr.message[:60]}",
                        ])
                        if verbose:
                            print(
                                f"[poe] interrupt({intr.intent}) from {intr.source}: {len(remaining_steps)} steps remaining",
                                file=sys.stderr, flush=True,
                            )
                if loop_status == "interrupted":
                    break
            except Exception:
                pass  # Interrupt failures must never break the loop

    # Final summary artifact
    elapsed_total = int((time.monotonic() - started_at) * 1000)
    log_path = _write_loop_log(
        project=project,
        loop_id=loop_id,
        goal=goal,
        status=loop_status,
        steps=step_outcomes,
        start_ts=start_ts,
        elapsed_ms=elapsed_total,
        stuck_reason=stuck_reason,
    )

    o.append_decision(project, [
        f"[loop:{loop_id}] finished status={loop_status} steps={len(step_outcomes)} tokens={total_tokens_in}+{total_tokens_out}",
    ])
    o.write_operator_status()

    # Phase 36: emit loop_done event
    try:
        from observe import write_event as _write_event_done
        _write_event_done(
            "loop_done",
            goal=goal,
            project=project or "",
            loop_id=loop_id,
            status=loop_status,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            elapsed_ms=elapsed_total,
            detail=stuck_reason or "",
        )
    except Exception:
        pass

    result = LoopResult(
        loop_id=loop_id,
        project=project,
        goal=goal,
        status=loop_status,
        steps=step_outcomes,
        interrupts_applied=interrupts_applied,
        stuck_reason=stuck_reason,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        elapsed_ms=elapsed_total,
        log_path=log_path,
        march_of_nines_alert=_march_of_nines_alert,
    )

    # Write a combined partial-result artifact so completed work is never lost
    _done_steps = [s for s in step_outcomes if s.status == "done"]
    if _done_steps:
        try:
            _partial_lines = [f"# Partial result: {goal}\n"]
            _partial_lines.append(f"Status: {loop_status} | "
                                  f"{len(_done_steps)}/{len(step_outcomes)} steps done | "
                                  f"tokens: {total_tokens_in+total_tokens_out} | "
                                  f"elapsed: {elapsed_total}ms\n")
            if stuck_reason:
                _partial_lines.append(f"Stuck reason: {stuck_reason}\n")
            _partial_lines.append("---\n")
            for s in step_outcomes:
                _icon = "Done" if s.status == "done" else "BLOCKED"
                _partial_lines.append(f"\n## Step {s.index if s.index >= 0 else '?'}: {s.text[:100]}")
                _partial_lines.append(f"*[{_icon}]*\n")
                if s.result:
                    _partial_lines.append(s.result[:2000])
                    if len(s.result) > 2000:
                        _partial_lines.append(f"\n... (truncated, {len(s.result)} chars total)")
                _partial_lines.append("")
            o = _orch()
            _art_dir = o.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project / "artifacts"
            _art_dir.mkdir(parents=True, exist_ok=True)
            (_art_dir / f"loop-{loop_id}-PARTIAL.md").write_text(
                "\n".join(_partial_lines), encoding="utf-8")
            log.info("wrote partial result: %s (%d steps)", f"loop-{loop_id}-PARTIAL.md", len(_done_steps))
        except Exception as exc:
            log.debug("partial result write failed: %s", exc)

    if verbose:
        print(f"[poe] {result.summary()}", file=sys.stderr, flush=True)

    _finalize_loop(
        loop_id=loop_id,
        goal=goal,
        project=project,
        loop_status=loop_status,
        step_outcomes=step_outcomes,
        adapter=adapter,
        dry_run=dry_run,
        verbose=verbose,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        elapsed_ms=elapsed_total,
        had_no_matching_skill=_had_no_matching_skill,
    )

    # Release loop lock so interfaces know we're idle
    try:
        clear_loop_running()
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Decompose
# ---------------------------------------------------------------------------

def _decompose(
    goal: str,
    adapter,
    max_steps: int,
    verbose: bool = False,
    lessons_context: str = "",
    ancestry_context: str = "",
    skills_context: str = "",
    cost_context: str = "",
) -> List[str]:
    """Ask the LLM to decompose a goal into steps. Falls back to heuristic."""
    from llm import LLMMessage

    system = _DECOMPOSE_SYSTEM
    extras = [x for x in [skills_context, ancestry_context, lessons_context, cost_context] if x]
    if extras:
        system = _DECOMPOSE_SYSTEM + "\n\n" + "\n\n".join(extras)

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", system),
                LLMMessage("user", f"Goal: {goal}\n\nDecompose into {max_steps} or fewer concrete steps."),
            ],
            max_tokens=1024,
            temperature=0.2,
        )
        content = resp.content.strip()
        # Extract JSON array from response (LLM may wrap in markdown)
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            steps = json.loads(content[start:end])
            if isinstance(steps, list) and all(isinstance(s, str) for s in steps):
                return [s.strip() for s in steps if s.strip()][:max_steps]
    except Exception as exc:
        log.warning("decompose LLM failed, falling back to heuristic: %s", exc)
        if verbose:
            print(f"[poe] decompose LLM call failed, using heuristic: {exc}", file=sys.stderr, flush=True)

    # Fallback: heuristic decomposition (reuse orch logic)
    o = _orch()
    _heuristic_steps = o.decompose_goal(goal, max_steps=max_steps)
    log.info("decompose heuristic produced %d steps (goal=%r)", len(_heuristic_steps), goal[:60])
    return _heuristic_steps


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 35 P2: Iterative refinement — LLM-assisted patch hint (round 2 retry)
# ---------------------------------------------------------------------------

def _generate_refinement_hint(
    step_text: str,
    block_reason: str,
    partial_result: str = "",
    *,
    adapter=None,
) -> str:
    """Generate a targeted refinement hint using a cheap LLM call.

    Called on the second retry of a blocked step. Uses the cheap model
    to analyze the specific failure and suggest a concrete patch.

    Falls back to a generic hint if the adapter is unavailable or errors.
    """
    _fallback = (
        f"[Refinement attempt 2 — blocked: {block_reason[:100]}] "
        "Analyze the failure carefully. Try a completely different approach: "
        "decompose this step further, use only information already available, "
        "or produce a partial result and mark the step complete."
    )

    if adapter is None:
        return _fallback

    try:
        from llm import LLMMessage, MODEL_CHEAP
        _refine_prompt = (
            f"A step in an autonomous agent loop failed twice.\n\n"
            f"Step: {step_text}\n"
            f"Failure reason: {block_reason[:200]}\n"
        )
        if partial_result:
            _refine_prompt += f"Partial result so far: {partial_result[:300]}\n"
        _refine_prompt += (
            "\nIn ONE sentence, suggest the most likely fix or alternative approach. "
            "Be specific and actionable. Do not suggest giving up."
        )

        # Use cheap model for refinement analysis
        try:
            from llm import build_adapter
            _cheap_adapter = build_adapter(model=MODEL_CHEAP)
        except Exception:
            _cheap_adapter = adapter

        resp = _cheap_adapter.complete(
            [LLMMessage("user", _refine_prompt)],
            max_tokens=150,
            temperature=0.3,
        )
        hint = resp.content.strip()
        if hint:
            return f"[Refinement suggestion: {hint}] Previous failure: {block_reason[:80]}"
    except Exception:
        pass

    return _fallback


# Execute step
# ---------------------------------------------------------------------------

def _execute_step(
    goal: str,
    step_text: str,
    step_num: int,
    total_steps: int,
    completed_context: List[str],
    adapter,
    tools: List[Any],
    verbose: bool = False,
    ancestry_context: str = "",
) -> Dict[str, Any]:
    """Execute one step via the LLM. Returns outcome dict."""
    _step_t0 = time.monotonic()
    log.info("step_start %d/%d: %s", step_num, total_steps, step_text[:100])
    from llm import LLMMessage

    context_block = ""
    if completed_context:
        context_block = "\n\nCompleted steps so far:\n" + "\n".join(
            f"  - {c}" for c in completed_context
        )

    ancestry_block = f"\n\n{ancestry_context}" if ancestry_context else ""

    # Phase 35 P1/P2: HITL constraint check — block/warn before any LLM call
    try:
        from constraint import hitl_policy, ACTION_TIER_DESTROY, ACTION_TIER_EXTERNAL, ACTION_TIER_WRITE
        _hp = hitl_policy(step_text, goal=goal)
        log.debug("step %d constraint: tier=%s risk=%s allowed=%s",
                  step_num, _hp["tier"], _hp["risk_level"], _hp["allowed"])
        if not _hp["allowed"]:
            _block_detail = _hp["reason"] or f"tier={_hp['tier']}"
            log.warning("step %d BLOCKED by constraint: %s (tier=%s risk=%s) elapsed=%.1fs",
                        step_num, _block_detail, _hp["tier"], _hp["risk_level"],
                        time.monotonic() - _step_t0)
            return {
                "status": "blocked",
                "stuck_reason": f"constraint violation ({_hp['risk_level']}, tier={_hp['tier']}): {_block_detail}",
                "result": "",
                "tokens_in": 0,
                "tokens_out": 0,
            }
        _tier = _hp["tier"]
        if _tier == ACTION_TIER_EXTERNAL:
            # Headless mode: can't interactively confirm — log prominently and proceed
            print(
                f"[poe] HITL confirm: step {step_num} is EXTERNAL (gate=confirm) — proceeding autonomously",
                file=sys.stderr, flush=True,
            )
        elif _tier == ACTION_TIER_WRITE and verbose:
            print(f"[poe] HITL warn: step {step_num} is WRITE tier", file=sys.stderr, flush=True)
        elif _hp["risk_level"] == "MEDIUM" and verbose:
            print(f"[poe] constraint MEDIUM on step {step_num}: {_hp['reason']}", file=sys.stderr, flush=True)
    except ImportError:
        pass  # constraint module optional

    # Pre-fetch URLs found in the step text AND completed_context so raw HTML never
    # enters the LLM context. Scanning prior step summaries ensures later steps
    # can still access content fetched by earlier steps (e.g. a URL fetched in
    # step 1 referenced again in step 4 without being repeated in the step text).
    prefetch_block = ""
    try:
        from web_fetch import enrich_step_with_urls
        _prior_ctx = "\n".join(completed_context) if completed_context else ""
        prefetch_block = enrich_step_with_urls(step_text, extra_context=_prior_ctx)
        if prefetch_block:
            prefetch_block = "\n\n" + prefetch_block
    except Exception:
        pass  # degrade gracefully if web_fetch unavailable

    user_msg = (
        f"Overall goal: {goal}{ancestry_block}\n\n"
        f"Current step ({step_num}/{total_steps}): {step_text}"
        f"{context_block}"
        f"{prefetch_block}\n\n"
        f"Complete this step now. Call complete_step when done or flag_stuck if blocked."
    )

    log.debug("step %d adapter_call start adapter=%s", step_num, type(adapter).__name__)
    _llm_t0 = time.monotonic()
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _EXECUTE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            tools=tools,
            tool_choice="required",
            max_tokens=4096,
            temperature=0.3,
        )
    except Exception as exc:
        _elapsed = time.monotonic() - _step_t0
        log.warning("step %d adapter_error: %s elapsed=%.1fs", step_num, exc, _elapsed)
        return {
            "status": "blocked",
            "stuck_reason": f"LLM call failed: {exc}",
            "result": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }

    _llm_elapsed = time.monotonic() - _llm_t0
    _tok = resp.input_tokens + resp.output_tokens
    _has_tool = bool(resp.tool_calls)
    _content_len = len(resp.content) if resp.content else 0
    log.debug("step %d adapter_done: llm=%.1fs tokens=%d tool_calls=%s content_len=%d",
              step_num, _llm_elapsed, _tok, _has_tool, _content_len)

    # Parse tool call
    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc.name == "complete_step":
            log.info("step %d DONE (complete_step) tokens=%d elapsed=%.1fs",
                     step_num, _tok, time.monotonic() - _step_t0)
            return {
                "status": "done",
                "result": tc.arguments.get("result", resp.content),
                "summary": tc.arguments.get("summary", step_text),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
        elif tc.name == "flag_stuck":
            _reason = tc.arguments.get("reason", "unknown")
            log.info("step %d BLOCKED (flag_stuck) reason=%r tokens=%d elapsed=%.1fs",
                     step_num, _reason[:80], _tok, time.monotonic() - _step_t0)
            return {
                "status": "blocked",
                "stuck_reason": _reason,
                "result": tc.arguments.get("attempted", ""),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }

    # No tool call — treat content as result (some models don't always call tools)
    if resp.content and len(resp.content) > 20:
        log.info("step %d DONE (content fallback, %d chars) tokens=%d elapsed=%.1fs",
                 step_num, _content_len, _tok, time.monotonic() - _step_t0)
        return {
            "status": "done",
            "result": resp.content,
            "summary": step_text,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
        }

    log.warning("step %d BLOCKED (no tool call, content=%d chars) tokens=%d elapsed=%.1fs content=%r",
                step_num, _content_len, _tok, time.monotonic() - _step_t0,
                (resp.content or "")[:120])
    return {
        "status": "blocked",
        "stuck_reason": "LLM did not call a tool and produced no useful content",
        "result": resp.content,
        "tokens_in": resp.input_tokens,
        "tokens_out": resp.output_tokens,
    }


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def _write_step_artifact(
    project: str,
    loop_id: str,
    step_num: int,
    step_text: str,
    result: str,
) -> Optional[str]:
    """Write a step's result to the project artifacts directory."""
    try:
        o = _orch()
        artifacts_dir = o.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"loop-{loop_id}-step-{step_num:02d}.md"
        path = artifacts_dir / fname
        content = f"# Step {step_num}: {step_text}\n\n{result}\n"
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(o.orch_root()))
    except Exception:
        return None


def _write_loop_log(
    project: str,
    loop_id: str,
    goal: str,
    status: str,
    steps: List[StepOutcome],
    start_ts: str,
    elapsed_ms: int,
    stuck_reason: Optional[str],
) -> Optional[str]:
    """Write the full loop log JSON to the project artifacts directory."""
    try:
        o = _orch()
        artifacts_dir = o.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"loop-{loop_id}-log.json"
        path = artifacts_dir / fname
        payload = {
            "loop_id": loop_id,
            "project": project,
            "goal": goal,
            "status": status,
            "started_at": start_ts,
            "elapsed_ms": elapsed_ms,
            "stuck_reason": stuck_reason,
            "steps": [
                {
                    "index": s.index,
                    "text": s.text,
                    "status": s.status,
                    "result_length": len(s.result),
                    "iteration": s.iteration,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "elapsed_ms": s.elapsed_ms,
                }
                for s in steps
            ],
            "totals": {
                "steps_done": sum(1 for s in steps if s.status == "done"),
                "steps_blocked": sum(1 for s in steps if s.status == "blocked"),
                "tokens_in": sum(s.tokens_in for s in steps),
                "tokens_out": sum(s.tokens_out for s in steps),
            },
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path.relative_to(o.orch_root()))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def _goal_to_slug(goal: str) -> str:
    """Convert a goal string to a filesystem-safe project slug."""
    import re
    words = re.sub(r"[^a-z0-9 ]", "", goal.lower()).split()
    slug = "-".join(words[:5])
    return slug or "unnamed-goal"


# ---------------------------------------------------------------------------
# Dry-run adapter (for testing without API credits)
# ---------------------------------------------------------------------------

class _DryRunAdapter:
    """Simulates LLM responses for testing."""

    def complete(self, messages, *, tools=None, tool_choice="auto", max_tokens=4096, temperature=0.3):
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

    parser = argparse.ArgumentParser(prog="poe-run", description="Run Poe's autonomous loop on a goal")
    parser.add_argument("goal", nargs="+", help="Goal description")
    parser.add_argument("--project", "-p", help="Project slug (auto-created if not exists)")
    parser.add_argument("--model", "-m", help="LLM model string (e.g. anthropic/claude-haiku-4-5)")
    parser.add_argument("--max-steps", type=int, default=6, help="Max decomposition steps (default: 6)")
    parser.add_argument("--max-iterations", type=int, default=20, help="Hard cap on LLM calls (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without LLM API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")

    args = parser.parse_args(argv)
    goal = " ".join(args.goal)

    result = run_agent_loop(
        goal,
        project=args.project,
        model=args.model,
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
