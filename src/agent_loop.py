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
    confidence: str = ""  # "strong" | "weak" | "inferred" | "unverified" | ""


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

# Prompts and tools — extracted to planner.py and step_exec.py for readability.
# Re-exported here for backward compatibility with existing imports.
from planner import DECOMPOSE_SYSTEM, parse_steps as _parse_steps, decompose as _decompose_impl
from step_exec import (
    EXECUTE_SYSTEM, EXECUTE_TOOLS,
    EXECUTE_TOOLS_WORKER, EXECUTE_TOOLS_SHORT, EXECUTE_TOOLS_INSPECTOR,
    execute_step as _execute_step,
    generate_refinement_hint as _generate_refinement_hint,
    verify_step as _verify_step,
)

_DECOMPOSE_SYSTEM = DECOMPOSE_SYSTEM
_EXECUTE_SYSTEM = EXECUTE_SYSTEM
_EXECUTE_TOOLS = EXECUTE_TOOLS


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
    project_dir: str = "",
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
            project_dir=project_dir,
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

    # Error nodes: relevant failure patterns from diagnoses.jsonl (Phase 46 follow-on)
    try:
        from introspect import find_relevant_failure_notes
        _failure_notes = find_relevant_failure_notes(goal, limit=2)
        if _failure_notes:
            lessons_context += "\n\nKnown failure patterns for similar goals:\n" + "\n".join(
                f"- {note}" for note in _failure_notes
            )
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
            except Exception:
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

    # Phase 32: auto-promote skills that meet threshold (don't wait for evolver heartbeat)
    if not dry_run:
        try:
            from evolver import run_skill_maintenance
            run_skill_maintenance()
        except ImportError:
            pass
        except Exception:
            pass

    # Post-mission Telegram notification
    if not dry_run:
        try:
            from telegram_listener import TelegramBot, _resolve_token, _resolve_allowed_chats
            _token = _resolve_token()
            _chat_ids = _resolve_allowed_chats()
            if _token and _chat_ids:
                _bot = TelegramBot(_token)
                _done_count = sum(1 for s in step_outcomes if s.status == "done")
                _total_tokens = total_tokens_in + total_tokens_out
                _status_icon = "✅" if loop_status == "done" else ("⚠️" if loop_status == "partial" else "❌")
                _msg = (
                    f"{_status_icon} *Mission complete* — `{project or goal[:40]}`\n"
                    f"Status: {loop_status} | Steps: {_done_count}/{len(step_outcomes)} done\n"
                    f"Tokens: {_total_tokens:,} | Time: {elapsed_ms // 1000}s"
                )
                for _cid in _chat_ids:
                    _bot.send_message(_cid, _msg)
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
    cost_budget: Optional[float] = None,
    ralph_verify: bool = False,
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

    # Upfront cost estimation — fail fast if estimate exceeds budget
    if cost_budget is not None:
        try:
            from metrics import estimate_loop_cost
            _estimated = estimate_loop_cost(len(steps), step_texts=steps)
            if _estimated > 0:
                _slush = cost_budget * 0.2  # 20% overage allowance
                if _estimated > cost_budget + _slush:
                    log.warning("cost estimate $%.2f exceeds budget $%.2f + slush $%.2f — aborting",
                                _estimated, cost_budget, _slush)
                    return LoopResult(
                        loop_id=loop_id, project=project or "", goal=goal,
                        status="stuck",
                        stuck_reason=f"Estimated cost ${_estimated:.2f} exceeds budget ${cost_budget:.2f} "
                                     f"(with ${_slush:.2f} slush). Reduce step count or use cheaper models.",
                    )
                elif _estimated > cost_budget * 0.8:
                    log.info("cost estimate $%.2f approaching budget $%.2f (%.0f%%)",
                             _estimated, cost_budget, _estimated / cost_budget * 100)
        except ImportError:
            pass

    # Parse step dependencies for level-based parallel execution
    _clean_steps = steps
    _deps = {}
    _levels = None
    try:
        from planner import parse_dependencies, build_execution_levels
        _clean_steps, _deps = parse_dependencies(steps)
        _levels = build_execution_levels(_deps)
        _parallel_levels = [l for l in _levels if len(l) > 1]
        if _parallel_levels:
            log.info("dependency graph: %d levels, %d parallelizable (%s)",
                     len(_levels), len(_parallel_levels),
                     ", ".join(f"L{i+1}={len(l)}" for i, l in enumerate(_levels)))
    except ImportError:
        pass

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
        _fanout_proj_dir = ""
        if project:
            try:
                _fanout_proj_dir = str(o.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project)
            except Exception:
                pass
        _fanout_outcomes = _run_steps_parallel(
            goal=goal,
            steps=steps,
            adapter=adapter,
            ancestry_context=_ancestry_context,
            tools=[LLMTool(**t) for t in _EXECUTE_TOOLS],
            verbose=verbose,
            max_workers=parallel_fan_out,
            project_dir=_fanout_proj_dir,
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

        # Check for parallel peers: if this step has siblings at the same
        # dependency level, batch them for parallel execution
        _parallel_peers: List[str] = []
        if _levels and parallel_fan_out > 0 and remaining_steps:
            _current_step_num = step_idx + 1  # 1-based
            # Find which level this step belongs to
            for _lvl in _levels:
                if _current_step_num in _lvl and len(_lvl) > 1:
                    # Pop remaining peers from the front of remaining_steps
                    _peer_count = 0
                    for _peer_idx in _lvl:
                        if _peer_idx != _current_step_num and remaining_steps:
                            _parallel_peers.append(remaining_steps.pop(0))
                            if remaining_indices:
                                remaining_indices.pop(0)
                            _peer_count += 1
                    if _parallel_peers:
                        log.info("parallel batch: step %d + %d peers at same level",
                                 _current_step_num, len(_parallel_peers))
                    break

        if _parallel_peers:
            # Run this step + peers in parallel
            _batch_steps = [step_text] + _parallel_peers
            iteration += len(_batch_steps)
            _batch_start = time.monotonic()
            if verbose:
                print(f"[poe] parallel batch: {len(_batch_steps)} steps at level", file=sys.stderr, flush=True)

            _batch_outcomes = _run_steps_parallel(
                goal=goal,
                steps=_batch_steps,
                adapter=adapter,
                ancestry_context=_ancestry_context,
                tools=[LLMTool(**t) for t in _EXECUTE_TOOLS],
                verbose=verbose,
                max_workers=min(parallel_fan_out, len(_batch_steps)),
                project_dir=_proj_artifact_dir,
            )

            # Process batch outcomes
            for _bi, (_batch_text, _batch_oc) in enumerate(zip(_batch_steps, _batch_outcomes)):
                step_idx += 1
                _b_status = _batch_oc.get("status", "blocked")
                _b_elapsed = int((time.monotonic() - _batch_start) * 1000)
                total_tokens_in += _batch_oc.get("tokens_in", 0)
                total_tokens_out += _batch_oc.get("tokens_out", 0)

                step_outcomes.append(StepOutcome(
                    index=-1, text=_batch_text, status=_b_status,
                    result=_batch_oc.get("result", ""),
                    iteration=iteration,
                    tokens_in=_batch_oc.get("tokens_in", 0),
                    tokens_out=_batch_oc.get("tokens_out", 0),
                    elapsed_ms=_b_elapsed,
                ))

                if _b_status == "done":
                    _b_result = _batch_oc.get("result", "")
                    _b_excerpt = _b_result[:800] if _b_result else ""
                    completed_context.append(f"Step {step_idx} ({_batch_text[:80]}):\n{_b_excerpt}")
                    if verbose:
                        print(f"[poe] step {step_idx} done (parallel): {_batch_oc.get('summary', '')[:80]}", file=sys.stderr, flush=True)
                elif _b_status == "blocked":
                    if verbose:
                        print(f"[poe] step {step_idx} blocked (parallel): {_batch_oc.get('stuck_reason', '')[:80]}", file=sys.stderr, flush=True)

            # Log batch cost
            try:
                from metrics import estimate_cost
                _batch_tokens = sum(o.get("tokens_in", 0) + o.get("tokens_out", 0) for o in _batch_outcomes)
                log.info("parallel batch done: %d steps, %d tokens, %dms",
                         len(_batch_steps), _batch_tokens, int((time.monotonic() - _batch_start) * 1000))
            except ImportError:
                pass

            continue  # Skip the single-step execution below

        iteration += 1
        step_idx += 1
        if verbose:
            print(f"[poe] step {step_idx}: {step_text!r}", file=sys.stderr, flush=True)

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

        step_start = time.monotonic()
        # Phase 35 P1: per-step model selection — cheap retrieval/classify steps use Haiku
        # Skip if caller explicitly specified a non-tier model string (e.g. --model claude-haiku-...)
        _step_adapter = adapter
        _explicit_model = getattr(adapter, "model_key", "") not in ("cheap", "mid", "power", "")
        if not _explicit_model:
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
        _proj_artifact_dir = ""
        if project:
            try:
                _proj_artifact_dir = str(o.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project)
            except Exception:
                pass
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
            project_dir=_proj_artifact_dir,
        )
        step_elapsed = int((time.monotonic() - step_start) * 1000)

        total_tokens_in += outcome.get("tokens_in", 0)
        total_tokens_out += outcome.get("tokens_out", 0)
        # Per-step cost estimate
        _step_model = getattr(_step_adapter, "model_key", "")
        try:
            from metrics import estimate_cost
            _step_cost = estimate_cost(outcome.get("tokens_in", 0), outcome.get("tokens_out", 0), model=_step_model)
            _total_cost = estimate_cost(total_tokens_in, total_tokens_out, model=_step_model)
        except ImportError:
            _step_cost = 0.0
            _total_cost = 0.0
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
                print(f"[poe] {stuck_reason}", file=sys.stderr, flush=True)
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
                    print(f"[poe] {stuck_reason}", file=sys.stderr, flush=True)
                break
            elif _cost_pct >= 80 and not getattr(run_agent_loop, "_cost_warned", False):
                log.warning("cost approaching budget: $%.4f / $%.2f (%.0f%%)",
                            _total_cost, cost_budget, _cost_pct)
                run_agent_loop._cost_warned = True  # type: ignore[attr-defined]

        step_status = outcome["status"]
        step_result = outcome.get("result", "")
        step_summary = outcome.get("summary", step_text)

        # Ralph verify loop — check that a "done" step actually addressed its goal.
        # Triggered by "ralph:" or "verify:" prefix in goal text, or ralph_verify=True kwarg
        # (handle.py passes this from user/CONFIG.md ralph_verify: true).
        _ralph_active = ralph_verify or goal.lower().startswith(("ralph:", "verify:"))
        if step_status == "done" and _ralph_active and step_result:
            try:
                _vr = _verify_step(step_text, step_result, _step_adapter)
                if not _vr["passed"]:
                    log.info("ralph verify FAIL step=%d reason=%r — marking blocked for retry",
                             step_idx, _vr["reason"][:80])
                    if verbose:
                        print(f"[poe] ralph verify: step {step_idx} RETRY — {_vr['reason'][:80]}",
                              file=sys.stderr, flush=True)
                    # Treat as blocked so the loop's existing retry machinery handles it
                    outcome["status"] = "blocked"
                    outcome["stuck_reason"] = f"[ralph verify] {_vr['reason']}"
                    step_status = "blocked"
                    step_result = outcome.get("result", "")
            except Exception:
                pass  # verify never blocks loop progress

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

        # Lightweight verification: check if .py filenames cited in the result
        # actually exist. Append a correction note if hallucinated files found.
        if step_status == "done" and step_result and ".py" in step_result:
            try:
                import re as _verify_re
                _cited_files = set(_verify_re.findall(r'\b([a-z_]+\.py)\b', step_result))
                _src_files = set(f.name for f in Path("src").glob("*.py")) if Path("src").exists() else set()
                _test_files = set(f.name for f in Path("tests").glob("*.py")) if Path("tests").exists() else set()
                _all_real = _src_files | _test_files
                _hallucinated = _cited_files - _all_real - {"__init__.py", "setup.py", "conftest.py"}
                if _hallucinated and len(_hallucinated) <= len(_cited_files):
                    _note = f"\n[VERIFICATION: {len(_hallucinated)} file(s) cited but not found: {', '.join(sorted(_hallucinated)[:5])}]"
                    step_result = step_result + _note
                    outcome["result"] = step_result
                    log.warning("step %d verification: %d hallucinated files: %s",
                                step_idx, len(_hallucinated), ", ".join(sorted(_hallucinated)[:5]))
            except Exception:
                pass  # verification must never break the loop

        if step_status == "done":
            if item_index >= 0:
                o.mark_item(project, item_index, o.STATE_DONE)

            # Write to scratchpad: structured data for subsequent steps
            _result_excerpt = step_result[:2000] if step_result else ""
            _cited_files: List[str] = []
            try:
                import re as _scratchpad_re
                _cited_files = sorted(set(
                    _scratchpad_re.findall(r'\b([a-z_]+\.py)\b', step_result or "")
                ))
            except Exception:
                pass
            with _scratchpad_lock:
                _scratchpad[f"step_{step_idx}"] = {
                    "text": step_text[:200],
                    "summary": step_summary[:200],
                    "result_excerpt": _result_excerpt,
                    "files_cited": _cited_files[:20],
                }
                # Update shared state: accumulate all real files found across steps
                _all_files = _scratchpad.get("shared", {}).get("files_found", [])
                _src_files = set(f.name for f in Path("src").glob("*.py")) if Path("src").exists() else set()
                _real_cited = [f for f in _cited_files if f in _src_files]
                _all_files = sorted(set(_all_files + _real_cited))
                _scratchpad.setdefault("shared", {})["files_found"] = _all_files

            # Build context entry: summary + truncated result for inline use
            _ctx_excerpt = step_result[:800] if step_result else ""
            if len(step_result) > 800:
                _ctx_excerpt += f"\n... ({len(step_result)} chars total — full result in scratchpad step_{step_idx})"
            _step_confidence = outcome.get("confidence", "")
            _confidence_tag = f" [confidence:{_step_confidence}]" if _step_confidence else ""
            _ctx_entry = f"Step {step_idx} ({step_text[:80]}){_confidence_tag}:\n{_ctx_excerpt}"
            completed_context.append(_ctx_entry)

            # Completed context compression (BACKLOG: prevent linear growth).
            # Keep last 3 entries at full length; compress older ones to a one-liner.
            # Older steps matter less — recent context dominates step execution quality.
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
                completed_context = _compressed + list(_new_entries)

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
                # vtrivedy10: re-inject original goal on retry to counter instruction fade-out
                _retry_reminder = (
                    f"RETRY REMINDER — ORIGINAL GOAL: {goal}\n"
                    "Focus only on completing the step above. "
                    "Use data already in context. Target <500 tokens."
                )
                _hint_with_reminder = (
                    (_decision.hint + "\n\n" + _retry_reminder).strip()
                    if _decision.hint
                    else _retry_reminder
                )
                _next_step_injected_context = (
                    (_next_step_injected_context + "\n\n" + _hint_with_reminder).strip()
                    if _next_step_injected_context
                    else _hint_with_reminder
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
            confidence=outcome.get("confidence", ""),
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
            # Persist scratchpad: one index file + per-step detail files
            # Index maps to individual step files so parallel writes don't collide
            _scratch_dir = _art_dir / f"loop-{loop_id}-scratchpad"
            _scratch_dir.mkdir(exist_ok=True)
            with _scratchpad_lock:
                for _sk, _sv in _scratchpad.items():
                    (_scratch_dir / f"{_sk}.json").write_text(
                        json.dumps(_sv, indent=2, default=str), encoding="utf-8")
                # Write index
                (_scratch_dir / "index.json").write_text(
                    json.dumps({"keys": list(_scratchpad.keys())}, indent=2), encoding="utf-8")
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


def _decompose(goal, adapter, max_steps, verbose=False, lessons_context="",
               ancestry_context="", skills_context="", cost_context=""):
    """Delegate to planner.decompose(). See planner.py for full implementation."""
    from planner import maybe_add_verification_step
    steps = _decompose_impl(goal, adapter, max_steps, verbose=verbose,
                            lessons_context=lessons_context, ancestry_context=ancestry_context,
                            skills_context=skills_context, cost_context=cost_context)
    return maybe_add_verification_step(steps, goal, max_steps=max_steps)

# _execute_step and _generate_refinement_hint are imported from step_exec at module top.
# _decompose delegates to _decompose_impl (from planner.py) above.

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
