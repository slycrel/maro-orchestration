#!/usr/bin/env python3
# @lat: [[core-loop]]
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
from typing import Any, Callable, ClassVar, Dict, List, Optional

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


def _project_dir_root():
    """Canonical projects root — delegates to orch_items.projects_root().

    Replaces the hardcoded `orch_root() / "prototypes" / "poe-orchestration" / "projects"`
    that previously caused output files to land in wrong directories.
    """
    from orch_items import projects_root
    return projects_root()


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
    confidence: str = ""         # "strong" | "weak" | "inferred" | "unverified" | ""
    injected_steps: List[str] = field(default_factory=list)  # steps added mid-plan by this step


def step_from_decompose(
    text: str,
    index: int,
    *,
    status: str = "pending",
    result: str = "",
    iteration: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    elapsed_ms: int = 0,
    confidence: str = "unverified",
    injected_steps: Optional[List[str]] = None,
) -> StepOutcome:
    """Factory for StepOutcome — centralises defaults so inline construction sites stay DRY."""
    return StepOutcome(
        index=index,
        text=text,
        status=status,
        result=result,
        iteration=iteration,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        elapsed_ms=elapsed_ms,
        confidence=confidence,
        injected_steps=injected_steps if injected_steps is not None else [],
    )


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
    pre_flight_review: Optional[Any] = None  # Phase 58: PlanReview if pre-flight ran

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
# Loop state machine types
# ---------------------------------------------------------------------------

class LoopPhase:
    """Named constants for the major phases of run_agent_loop."""
    INIT = "init"                    # Phase A: setup, adapter, project
    DECOMPOSE = "decompose"          # Phase B: goal → steps
    PRE_FLIGHT = "pre_flight"        # Phase C: gates, resume, cost estimate
    PARALLEL = "parallel"            # Phase D: parallel fan-out (early return)
    PREPARE = "prepare"              # Phase E: shape steps, NEXT.md
    EXECUTE = "execute"              # Phase F: main while loop
    FINALIZE = "finalize"            # Phase G: reflection, recovery, return


class InvalidTransitionError(Exception):
    """Raised when an invalid LoopPhase transition is attempted."""


# LoopStateMachine is defined after LoopContext below (it inherits LoopContext).
# See class definition following @dataclass LoopContext.


@dataclass
class LoopContext:
    """Mutable state bundle for run_agent_loop.

    Instead of 30+ local variables threaded through 1,800 lines, all
    mutable loop state lives here. Passed to extracted phase methods.

    Architecture note: this is step 1 of the monolith decomposition.
    Once all phases are extracted as methods taking LoopContext, the
    natural next step is a LoopStateMachine class with LoopContext as
    self-state. But that refactor can happen incrementally.
    """
    # Identity
    loop_id: str = ""
    project: str = ""
    goal: str = ""

    # Execution state
    step_outcomes: List[StepOutcome] = field(default_factory=list)
    remaining_steps: List[str] = field(default_factory=list)
    remaining_indices: List[int] = field(default_factory=list)
    completed_context: List[str] = field(default_factory=list)
    iteration: int = 0
    step_idx: int = 0

    # Status
    loop_status: str = "done"  # "done" | "stuck" | "interrupted" | "error"
    stuck_reason: Optional[str] = None
    phase: str = LoopPhase.INIT

    # Token/cost tracking
    total_tokens_in: int = 0
    total_tokens_out: int = 0

    # Stuck detection
    stuck_streak: int = 0
    last_action: Optional[str] = None

    # Budget
    cost_budget: Optional[float] = None
    token_budget: Optional[int] = None

    # Retry state
    step_retries: Dict[str, int] = field(default_factory=dict)
    step_tier_overrides: Dict[str, str] = field(default_factory=dict)
    session_verify_failures: int = 0
    session_tier_floor: str = ""
    failure_chain: List[str] = field(default_factory=list)
    recovery_step_count: int = 0
    consecutive_max_timeouts: int = 0

    # Hooks & interrupts
    next_step_injected_context: str = ""
    interrupts_applied: int = 0

    # Flags
    march_of_nines_alert: bool = False
    milestone_expanded: set = field(default_factory=set)

    # Configuration (set during init, read-only after)
    adapter: Any = None
    verbose: bool = False
    dry_run: bool = False
    max_iterations: int = 40
    continuation_depth: int = 0
    ralph_verify: bool = False
    step_callback: Optional[Callable] = None
    interrupt_queue: Any = None
    hook_registry: Any = None
    perm_ctx: Any = None

    # Computed during init
    ancestry_context: str = ""
    started_at: float = 0.0
    start_ts: str = ""
    loop_timeout_secs: Optional[float] = None
    repo_path: str = ""  # optional target repo path for stack context injection


@dataclass
class LoopStateMachine(LoopContext):
    """LoopContext + phase transition enforcement.

    Replaces the two-class pattern (LoopContext state + LoopStateMachine classmethod).
    LoopContext becomes `self` — ctx.set_phase(X) validates and transitions in one call.

    Allowed transitions (all phases may also advance to FINALIZE for early-exit paths):
        INIT       → DECOMPOSE
        DECOMPOSE  → PRE_FLIGHT
        PRE_FLIGHT → PARALLEL | PREPARE
        PARALLEL   → PREPARE
        PREPARE    → EXECUTE
        EXECUTE    → FINALIZE
    """

    _ALLOWED: ClassVar[Dict[str, set]] = {
        LoopPhase.INIT:       {LoopPhase.DECOMPOSE,  LoopPhase.FINALIZE},
        LoopPhase.DECOMPOSE:  {LoopPhase.PRE_FLIGHT, LoopPhase.FINALIZE},
        LoopPhase.PRE_FLIGHT: {LoopPhase.PARALLEL,   LoopPhase.PREPARE, LoopPhase.FINALIZE},
        LoopPhase.PARALLEL:   {LoopPhase.PREPARE,    LoopPhase.FINALIZE},
        LoopPhase.PREPARE:    {LoopPhase.EXECUTE,    LoopPhase.FINALIZE},
        LoopPhase.EXECUTE:    {LoopPhase.FINALIZE},
        LoopPhase.FINALIZE:   set(),
    }

    def set_phase(self, new_phase: str) -> None:
        """Advance self.phase to new_phase, raising InvalidTransitionError on bad transitions."""
        allowed = self._ALLOWED.get(self.phase, set())
        if new_phase not in allowed:
            raise InvalidTransitionError(
                f"Invalid loop phase transition: {self.phase!r} → {new_phase!r}"
            )
        self.phase = new_phase


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


def _handle_budget_ceiling(
    ctx: LoopContext,
    step_outcomes: List[StepOutcome],
    remaining_steps: List[str],
    total_tokens_in: int,
    total_tokens_out: int,
    iteration: int,
    max_iterations: int,
    continuation_depth: int,
) -> Optional[str]:
    """Phase F1: Handle max_iterations budget ceiling.

    Returns stuck_reason suffix if continuation/escalation was enqueued, empty string otherwise.
    """
    log.warning("max_iterations reached: %d/%d steps done, %d remaining, tokens=%d",
                len(step_outcomes), len(step_outcomes) + len(remaining_steps),
                len(remaining_steps), total_tokens_in + total_tokens_out)

    _suffix = ""
    if remaining_steps:
        try:
            from task_store import enqueue as _ts_enqueue
            _done_count = sum(1 for s in step_outcomes if s.status == "done")
            _done_summary = "; ".join(
                s.text[:80] for s in step_outcomes if s.status == "done"
            )
            _remaining_summary = "\n".join(
                f"- {s[:120]}" for s in remaining_steps[:10]
            )
            _next_depth = continuation_depth + 1

            _max_depth = int(os.environ.get("POE_MAX_CONTINUATION_DEPTH", "4"))

            if continuation_depth >= _max_depth:
                _esc_reason = (
                    f"ESCALATION — task has been through {continuation_depth} continuation "
                    f"pass(es) without completing.\n\n"
                    f"Original goal: {ctx.goal}\n\n"
                    f"Accomplished ({_done_count} steps):\n{_done_summary or '(none)'}\n\n"
                    f"Remaining ({len(remaining_steps)} steps):\n{_remaining_summary}\n\n"
                    f"Options: (1) enqueue a new continuation with continuation_depth="
                    f"{_next_depth} to keep going; (2) rewrite the goal to reduce scope; "
                    f"(3) accept the partial result as-is.\n"
                    f"Parent loop: {ctx.loop_id}"
                )
                _esc_task = _ts_enqueue(
                    lane="agenda",
                    source="loop_escalation",
                    reason=_esc_reason,
                    parent_job_id=ctx.loop_id,
                    continuation_depth=continuation_depth,
                )
                log.warning(
                    "budget_ceiling_escalation: depth=%d >= max=%d, escalated to %s "
                    "(parent=%s, %d steps done, %d remaining)",
                    continuation_depth, _max_depth, _esc_task["job_id"],
                    ctx.loop_id, _done_count, len(remaining_steps),
                )
                _suffix = (
                    f"; escalated (depth {continuation_depth} >= max {_max_depth}) "
                    f"as {_esc_task['job_id']}"
                )
            else:
                _cont_reason = (
                    f"CONTINUATION of: {ctx.goal}\n\n"
                    f"Pass {continuation_depth + 1} of a multi-pass task. "
                    f"Previous pass completed {_done_count}/{len(step_outcomes)} steps "
                    f"before hitting budget ceiling (max_iterations={max_iterations}).\n\n"
                    f"Accomplished so far:\n{_done_summary or '(none)'}\n\n"
                    f"Remaining work ({len(remaining_steps)} steps):\n{_remaining_summary}"
                )
                _cont_task = _ts_enqueue(
                    lane="agenda",
                    source="loop_continuation",
                    reason=_cont_reason,
                    parent_job_id=ctx.loop_id,
                    continuation_depth=_next_depth,
                )
                log.info(
                    "budget_ceiling_continuation: enqueued %s depth=%d with %d remaining "
                    "steps (parent=%s)",
                    _cont_task["job_id"], _next_depth, len(remaining_steps), ctx.loop_id,
                )
                _suffix = (
                    f"; continuation (depth {_next_depth}) enqueued as "
                    f"{_cont_task['job_id']}"
                )
        except Exception as _ce:
            log.warning("failed to enqueue continuation/escalation task: %s", _ce)

    return _suffix


@dataclass
class BlockedStepContext:
    """Bundle of inputs + mutable state passed to `_process_blocked_step`.

    Session 20 adversarial review finding 3.12: `_process_blocked_step` had
    21 parameters — a design smell that resists testing and is rarely called
    correctly. This dataclass packages them all into one object passed by
    reference. Mutable collections (step_retries, failure_chain, etc.) still
    mutate in place because the dataclass field is just a reference; scalar
    in/out values (consecutive_max_timeouts, replan_count, next_step_*) are
    returned via the function's tuple result, so assignments to them inside
    the function don't need to write back to the dataclass.
    """
    # Per-step inputs
    step_text: str
    step_idx: int
    step_result: str
    step_elapsed: int
    outcome: dict
    item_index: int
    iteration: int
    step_adapter: Any
    # Mutable shared state (referenced by both caller and callee)
    step_retries: Dict[str, int]
    step_tier_overrides: Dict[str, str]
    failure_chain: List[str]
    step_outcomes: List[StepOutcome]
    remaining_steps: List[str]
    remaining_indices: List[int]
    manifest_steps: List[str]
    error_fingerprints: Dict[str, List[str]] = field(default_factory=dict)
    # Loop-level scalars (in via init, out via tuple return)
    next_step_injected_context: str = ""
    consecutive_max_timeouts: int = 0
    max_consecutive_timeouts: int = 3
    replan_count: int = 0


def _process_blocked_step(ctx: LoopContext, blk: BlockedStepContext) -> tuple:
    """Phase F11: Process a blocked step — retry, split, redecompose, or terminal.

    Returns (flow: str, step_idx, loop_status, stuck_reason, next_step_injected_context,
             consecutive_max_timeouts, recovery_step_count_delta, replan_count).
    flow is "continue" (retry/split/redecompose), "break" (adapter hung), or "normal" (terminal, fall through).
    Mutates blk.step_retries, blk.step_tier_overrides, blk.failure_chain,
    blk.step_outcomes, blk.remaining_steps/indices, blk.manifest_steps,
    blk.error_fingerprints in place.
    """
    from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER

    # Unpack into local names so the function body below is unchanged.
    # Session 20.5 refactor: the body still uses bare names; this preserves
    # the call-site change without rewriting 300+ lines of internals.
    step_text = blk.step_text
    step_idx = blk.step_idx
    step_result = blk.step_result
    step_elapsed = blk.step_elapsed
    outcome = blk.outcome
    item_index = blk.item_index
    iteration = blk.iteration
    step_adapter = blk.step_adapter
    step_retries = blk.step_retries
    step_tier_overrides = blk.step_tier_overrides
    failure_chain = blk.failure_chain
    step_outcomes = blk.step_outcomes
    remaining_steps = blk.remaining_steps
    remaining_indices = blk.remaining_indices
    manifest_steps = blk.manifest_steps
    next_step_injected_context = blk.next_step_injected_context
    consecutive_max_timeouts = blk.consecutive_max_timeouts
    max_consecutive_timeouts = blk.max_consecutive_timeouts
    replan_count = blk.replan_count
    error_fingerprints = blk.error_fingerprints

    o = _orch()
    _prior_retries = step_retries.get(step_text, 0)
    if error_fingerprints is None:
        error_fingerprints = {}

    # Phase 62: Track error fingerprint for convergence detection
    _fp = _error_fingerprint(outcome)
    _fps = error_fingerprints.setdefault(step_text, [])
    _fps.append(_fp)

    _decision = _handle_blocked_step(
        step_text, outcome, _prior_retries, ctx.adapter,
        error_fingerprints=_fps,
        step_outcomes=step_outcomes,
        replan_count=replan_count,
        loop_id=ctx.loop_id,
    )

    # Phase 62: Log metacognitive reasoning
    if _decision.metacognitive_reason:
        log.info("metacognitive decision: %s", _decision.metacognitive_reason)
        try:
            from captains_log import log_event
            log_event(
                event_type="METACOGNITIVE_DECISION",
                subject=step_text[:80],
                summary=_decision.metacognitive_reason,
                context={
                    "step_idx": step_idx,
                    "retries": _prior_retries,
                    "fingerprints": _fps[-3:],  # last 3
                    "replan_count": replan_count,
                    "action": "retry" if _decision.retry else (
                        "redecompose" if _decision.redecompose else (
                            "split" if _decision.split_into else "stuck"
                        )
                    ),
                },
            )
        except Exception as _exc:
            log.debug("captain's log emit for recovery decision failed: %s", _exc)
    _recovery_delta = 0

    if _decision.retry:
        step_retries[step_text] = _prior_retries + 1
        _recovery_delta = 1
        # Tier escalation
        _cur_tier = getattr(step_adapter, "model_key", MODEL_CHEAP)
        if _cur_tier == MODEL_CHEAP:
            step_tier_overrides[step_text] = MODEL_MID
            log.info("step %d retry tier-up: cheap → mid", step_idx)
        elif _cur_tier == MODEL_MID:
            step_tier_overrides[step_text] = MODEL_POWER
            log.info("step %d retry tier-up: mid → power", step_idx)
        _br_reason = outcome.get("stuck_reason", "blocked")
        failure_chain.append(
            f"step {step_idx} blocked ({_br_reason[:60]}); retry {_prior_retries + 1} with hint"
        )
        _retry_reminder = (
            f"RETRY REMINDER — ORIGINAL GOAL: {ctx.goal}\n"
            "Focus only on completing the step above. "
            "Use data already in context. Target <500 tokens."
        )
        _hint_with_reminder = (
            (_decision.hint + "\n\n" + _retry_reminder).strip()
            if _decision.hint
            else _retry_reminder
        )
        next_step_injected_context = (
            (next_step_injected_context + "\n\n" + _hint_with_reminder).strip()
            if next_step_injected_context
            else _hint_with_reminder
        )
        remaining_steps.insert(0, step_text)
        remaining_indices.insert(0, item_index)
        step_idx -= 1
        if ctx.verbose:
            _br = outcome.get("stuck_reason", "blocked")
            print(f"[poe] step {step_idx+1} blocked ({_br[:80]}), retrying with fallback hint", file=sys.stderr, flush=True)
        step_outcomes.append(step_from_decompose(
            step_text, item_index,
            status="blocked", result=step_result, iteration=iteration,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            elapsed_ms=step_elapsed,
        ))
        return ("continue", step_idx, "", None, next_step_injected_context,
                consecutive_max_timeouts, _recovery_delta, replan_count)

    elif _decision.redecompose:
        # Phase 62: Mid-loop re-decomposition — the step (or plan) needs
        # to be broken down differently, not just retried.
        _recovery_delta = 1
        failure_chain.append(
            f"step {step_idx} re-decomposing: {_decision.metacognitive_reason[:80]}"
        )
        try:
            from planner import decompose
            _sub_steps = decompose(
                step_text,
                ctx.adapter,
                max_steps=5,
            )
            if _sub_steps and len(_sub_steps) >= 2:
                _sub_shaped = _shape_steps(list(_sub_steps), label="redecompose")
                for _new_step in reversed(_sub_shaped):
                    remaining_steps.insert(0, _new_step)
                    remaining_indices.insert(0, -1)
                manifest_steps.extend(_sub_shaped)
                replan_count += 1
                log.info("mid-loop re-decompose: step %d → %d sub-steps (replan #%d)",
                         step_idx, len(_sub_shaped), replan_count)
                if ctx.verbose:
                    print(
                        f"[poe] step {step_idx} re-decomposed into {len(_sub_shaped)} sub-steps "
                        f"(replan #{replan_count})",
                        file=sys.stderr, flush=True,
                    )
                step_outcomes.append(step_from_decompose(
                    step_text, item_index,
                    status="blocked", result=step_result, iteration=iteration,
                    tokens_in=outcome.get("tokens_in", 0),
                    tokens_out=outcome.get("tokens_out", 0),
                    elapsed_ms=step_elapsed,
                ))
                return ("continue", step_idx, "", None, next_step_injected_context,
                        consecutive_max_timeouts, _recovery_delta, replan_count)
        except Exception as exc:
            log.warning("mid-loop re-decompose failed: %s — falling through to stuck", exc)

        # Re-decompose failed — fall through to terminal
        _decision = _BlockDecision(
            retry=False, hint="", loop_status="stuck",
            stuck_reason=f"re-decompose failed after {_prior_retries} retries: {outcome.get('stuck_reason', 'blocked')}",
            metacognitive_reason="re-decompose failed — terminal",
        )
        # Fall through to terminal handler below

    elif _decision.split_into:
        failure_chain.append(
            f"step {step_idx} split: combined step split into {len(_decision.split_into)} parts"
        )
        _recovery_delta = 1
        _split_reason = outcome.get("stuck_reason", "")
        if "timed out" in _split_reason.lower() or "timeout" in _split_reason.lower():
            consecutive_max_timeouts += 1
            if consecutive_max_timeouts >= max_consecutive_timeouts:
                _stuck_reason = (
                    f"Adapter appears hung: {consecutive_max_timeouts} consecutive steps all "
                    f"timed out at the {600}s ceiling across different step texts. "
                    "This is an adapter/transport failure, not a step-size issue. "
                    "Check that 'claude -p' is functional and authenticated."
                )
                log.warning("adapter-hung detection: %d consecutive max-timeouts — bailing out",
                            consecutive_max_timeouts)
                if ctx.verbose:
                    print(f"[poe] adapter appears hung ({consecutive_max_timeouts} consecutive "
                          f"ceiling timeouts) — stopping loop", file=sys.stderr, flush=True)
                return ("break", step_idx, "stuck", _stuck_reason, next_step_injected_context,
                        consecutive_max_timeouts, _recovery_delta, replan_count)
        else:
            consecutive_max_timeouts = 0
        _split_shaped = _shape_steps(list(_decision.split_into), label="replan-split")
        for _new_step in reversed(_split_shaped):
            remaining_steps.insert(0, _new_step)
            remaining_indices.insert(0, -1)
        manifest_steps.extend(_split_shaped)
        replan_count += 1
        if ctx.verbose:
            print(
                f"[poe] step {step_idx} timed out — split into {len(_decision.split_into)} steps "
                f"(step-shape replan #{replan_count})",
                file=sys.stderr, flush=True,
            )
        step_outcomes.append(step_from_decompose(
            step_text, item_index,
            status="blocked", result=step_result, iteration=iteration,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            elapsed_ms=step_elapsed,
        ))
        return ("continue", step_idx, "", None, next_step_injected_context,
                consecutive_max_timeouts, _recovery_delta, replan_count)

    # Terminal failure — reached when no branch returned (redecompose fallthrough, or
    # explicit stuck decision from _handle_blocked_step)
    _loop_status = _decision.loop_status or "stuck"
    _stuck_reason = _decision.stuck_reason or block_reason
    failure_chain.append(f"step {step_idx} terminal: {_stuck_reason[:80]}")
    if item_index >= 0:
        o.mark_item(ctx.project, item_index, o.STATE_BLOCKED)
    if ctx.verbose:
        print(f"[poe] step {step_idx} stuck after retry: {_stuck_reason}", file=sys.stderr, flush=True)
    try:
        from skills import attribute_failure_to_skills, find_matching_skills, record_variant_outcome, record_skill_outcome
        from metrics import estimate_cost as _est_cost
        attribute_failure_to_skills(step_text, _stuck_reason, goal=ctx.goal)
        _fail_cost = _est_cost(
            int(outcome.get("tokens_in", 0)),
            int(outcome.get("tokens_out", 0)),
            getattr(step_adapter, "model_key", None),
        )
        for _sk in find_matching_skills(step_text + " " + ctx.goal, use_router=False):
            if getattr(_sk, "variant_of", None) is not None:
                record_variant_outcome(_sk.id, success=False)
            # Phase 59: record failure telemetry per skill
            record_skill_outcome(
                _sk.id,
                success=False,
                cost_usd=_fail_cost,
                latency_ms=float(step_elapsed),
            )
    except Exception as _exc:
        # Affects the evolver's per-skill telemetry — silent loss skews learning.
        log.warning("skill outcome recording failed for stuck step %d: %s", step_idx, _exc)
    try:
        from metrics import record_step_cost
        record_step_cost(
            step_text=step_text,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            status="blocked",
            goal=ctx.goal,
            model=getattr(ctx.adapter, "model_key", ""),
            elapsed_ms=step_elapsed,
        )
    except Exception as _exc:
        log.debug("metrics.record_step_cost failed for stuck step %d: %s", step_idx, _exc)
    if ctx.step_callback is not None:
        try:
            ctx.step_callback(step_idx, step_text, _stuck_reason or "blocked", "blocked")
        except Exception as _exc:
            log.debug("step_callback raised for stuck step %d: %s", step_idx, _exc)
    return ("normal", step_idx, _loop_status, _stuck_reason, next_step_injected_context,
            consecutive_max_timeouts, _recovery_delta, replan_count)


_MARCH_OF_NINES_WINDOW = 5
_MARCH_OF_NINES_THRESHOLD = 0.5


def _compute_march_of_nines(step_outcomes: List["StepOutcome"]) -> Optional[tuple]:
    """Return (rate, completed, window_size) if recent-window success rate is
    below threshold; None if no alert should fire.

    Session 20 adversarial review finding 3.10: the previous formula was
       chain_success = (completed/attempted) ** attempted
    which fired false alerts on healthy long runs — a 90% step rate over
    8 steps looked like 0.43 (below the 0.5 threshold) and produced an
    alert despite the run being fine. The pathology: penalizing chain
    LENGTH rather than per-step rate degradation.

    New behavior: look at the last N steps and alert if the success rate
    within the window drops below threshold. Catches actual recent
    degradation without punishing otherwise-healthy long runs.
    """
    if len(step_outcomes) < 3:
        return None
    window = step_outcomes[-_MARCH_OF_NINES_WINDOW:]
    completed = sum(1 for s in window if s.status == "done")
    size = len(window)
    if size == 0:
        return None
    rate = completed / size
    if rate >= _MARCH_OF_NINES_THRESHOLD:
        return None
    return (rate, completed, size)


def _write_iteration_artifacts(
    ctx: LoopContext,
    step_text: str,
    step_status: str,
    outcome: dict,
    step_outcomes: List[StepOutcome],
    steps: List[str],
    manifest_steps: List[str],
    replan_count: int,
    start_ts: str,
    dead_ends_available: bool,
    update_dead_ends_fn=None,
) -> bool:
    """Write checkpoint, manifest, dead ends, march of nines after each step.

    Returns True if march_of_nines_alert was triggered.
    """
    o = _orch()

    # Checkpoint
    try:
        from checkpoint import write_checkpoint as _write_ckpt
        _write_ckpt(ctx.loop_id, ctx.goal, ctx.project or "", steps, step_outcomes)
    except Exception as _exc:
        # Affects loop resumability — silent loss means a crashed loop can't restart.
        log.warning("checkpoint write failed for loop %s: %s", ctx.loop_id, _exc)

    # Update plan manifest
    if ctx.project and manifest_steps:
        try:
            _write_plan_manifest(
                project=ctx.project,
                loop_id=ctx.loop_id,
                goal=ctx.goal,
                planned_steps=manifest_steps,
                start_ts=start_ts,
                step_outcomes=step_outcomes,
                replan_count=replan_count,
            )
        except Exception as _exc:
            log.warning("plan manifest update failed for loop %s: %s", ctx.loop_id, _exc)

    # Dead ends
    if step_status == "blocked" and dead_ends_available:
        try:
            _reason = outcome.get("stuck_reason", f"step blocked: {step_text[:80]}")
            _attempted = outcome.get("result", "")[:200]
            _dead_end_text = (
                f"Loop {ctx.loop_id} — Step: {step_text[:80]}\n"
                f"Reason: {_reason}\n"
                f"Attempted: {_attempted}"
            )
            update_dead_ends_fn(ctx.project, [_dead_end_text])
        except Exception as _exc:
            log.warning("dead_ends update failed for loop %s: %s", ctx.loop_id, _exc)

    # March of Nines — sliding-window success rate over recent steps.
    _window_result = _compute_march_of_nines(step_outcomes)
    _alert = False
    if _window_result is not None:
        rate, completed, size = _window_result
        _alert = True
        try:
            o.append_decision(ctx.project, [
                f"[loop:{ctx.loop_id}] March of Nines alert: "
                f"recent_success_rate={rate:.2f} "
                f"({completed}/{size} of last {size} steps done)"
            ])
        except Exception as _exc:
            log.debug("march-of-nines alert append failed for loop %s: %s", ctx.loop_id, _exc)

    return _alert


def _check_loop_interrupts(
    ctx: LoopContext,
    *,
    remaining_steps: List[str],
    remaining_indices: List[int],
    interrupt_queue,
    apply_interrupt_fn,
    goal: str,
    interrupts_applied: int,
) -> tuple:
    """Check kill switch, wall-clock timeout, and interrupt queue.

    Returns (loop_status, stuck_reason, goal, interrupts_applied, remaining_steps, remaining_indices).
    loop_status is "" if no interruption, "interrupted" if should break.
    """
    o = _orch()
    loop_status = ""
    stuck_reason = None

    # Kill switch
    try:
        from killswitch import is_active as _ks_active, read_reason as _ks_reason
        if _ks_active():
            _ks_msg = _ks_reason() or "kill switch engaged"
            log.warning("loop %s stopping — kill switch active: %s", ctx.loop_id, _ks_msg)
            loop_status = "interrupted"
            stuck_reason = f"kill switch: {_ks_msg}"
            if ctx.verbose:
                print(f"[poe] kill switch active — stopping loop", file=sys.stderr, flush=True)
    except Exception as _exc:
        # Safety mechanism — silent failure means a kill switch could be ignored.
        log.error("kill switch check FAILED for loop %s — safety mechanism may be compromised: %s",
                  ctx.loop_id, _exc)

    # Wall-clock timeout
    if not loop_status and ctx.loop_timeout_secs is not None:
        _elapsed_secs = time.monotonic() - ctx.started_at
        if _elapsed_secs >= ctx.loop_timeout_secs:
            log.warning("loop %s wall-clock timeout after %.0fs", ctx.loop_id, _elapsed_secs)
            loop_status = "interrupted"
            stuck_reason = f"wall-clock timeout ({ctx.loop_timeout_secs:.0f}s)"
            if ctx.verbose:
                print(f"[poe] wall-clock timeout after {_elapsed_secs:.0f}s — stopping", file=sys.stderr, flush=True)

    if loop_status:
        return loop_status, stuck_reason, goal, interrupts_applied, remaining_steps, remaining_indices

    # Interrupt polling
    if interrupt_queue is not None:
        try:
            pending = interrupt_queue.poll()
            for intr in pending:
                interrupts_applied += 1
                new_remaining, goal, should_stop = apply_interrupt_fn(
                    intr, remaining_steps, goal
                )
                if should_stop:
                    loop_status = "interrupted"
                    stuck_reason = f"stopped by {intr.source}: {intr.message[:80]}"
                    if ctx.verbose:
                        print(
                            f"[poe] interrupt: stop requested by {intr.source}",
                            file=sys.stderr, flush=True,
                        )
                    remaining_steps = []
                    remaining_indices = []
                    break
                else:
                    new_remaining = _shape_steps(new_remaining, label="interrupt")
                    added = [s for s in new_remaining if s not in remaining_steps]
                    if added:
                        new_idxs = o.append_next_items(ctx.project, added)
                        existing_count = len(remaining_steps)
                        remaining_steps = new_remaining
                        remaining_indices = remaining_indices[:existing_count] + new_idxs
                    else:
                        remaining_steps = new_remaining
                    o.append_decision(ctx.project, [
                        f"[loop:{ctx.loop_id}] interrupt({intr.intent}) from {intr.source}: {intr.message[:60]}",
                    ])
                    if ctx.verbose:
                        print(
                            f"[poe] interrupt({intr.intent}) from {intr.source}: {len(remaining_steps)} steps remaining",
                            file=sys.stderr, flush=True,
                        )
        except Exception as _exc:
            # Safety: silent failure means user-initiated interrupts (stop/pivot)
            # could be silently dropped while the loop keeps running.
            log.error("interrupt queue processing FAILED for loop %s — pending interrupts may be lost: %s",
                      ctx.loop_id, _exc)

    return loop_status, stuck_reason, goal, interrupts_applied, remaining_steps, remaining_indices


def _post_step_checks(
    ctx: LoopContext,
    step_text: str,
    step_idx: int,
    step_status: str,
    step_result: str,
    step_summary: str,
    step_elapsed: int,
    outcome: dict,
    *,
    security_available: bool,
    scan_content_fn=None,
    injection_risk_cls=None,
) -> tuple:
    """Phase F9: Post-step observability, security, claim verification, hooks.

    Returns (step_status, step_result, step_injected_context).
    May mutate outcome dict in place.
    """
    # Emit step event
    try:
        from observe import write_event
        write_event(
            "step_done" if step_status == "done" else "step_stuck",
            goal=ctx.goal,
            project=ctx.project or "",
            loop_id=ctx.loop_id,
            step=step_text,
            step_idx=step_idx,
            status=step_status,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            elapsed_ms=step_elapsed,
            detail=step_summary[:200] if step_summary else "",
        )
    except Exception as _exc:
        log.debug("write_event(step_done/stuck) failed for step %d: %s", step_idx, _exc)

    # Security scan for prompt injection
    _has_external = "PRE-FETCHED" in step_text or "http" in step_text.lower()
    if security_available and _has_external and step_status == "done" and len(step_result) > 200:
        try:
            _scan = scan_content_fn(
                step_result,
                log_fn=lambda msg: print(f"[poe] {msg}", file=sys.stderr, flush=True),
            )
            if _scan.risk >= injection_risk_cls.HIGH:
                log.warning("step %d injection HIGH in result — redacting before context injection (signals=%s)",
                            step_idx, _scan.signals)
                step_result = _scan.sanitized
                outcome["result"] = step_result
        except Exception as _exc:
            # Security: silent failure means external content passes unscanned
            # into downstream LLM context. Fail loudly so it's visible.
            log.error("security injection scan FAILED for step %d — external content may pass unsanitized: %s",
                      step_idx, _exc)

    # Claim verifier on synthesis steps
    if step_status == "done" and step_result:
        try:
            from claim_verifier import is_synthesis_step as _is_synth, annotate_result as _annotate
            if _is_synth(step_text):
                _annotated = _annotate(step_result, only_if_hallucinations=True)
                if _annotated != step_result:
                    log.warning("step %d [claim-verifier] hallucinated file paths detected", step_idx)
                    step_result = _annotated
                    outcome["result"] = step_result
        except Exception as _exc:
            log.warning("claim verifier failed for step %d (annotations skipped): %s", step_idx, _exc)

    # Step-level hooks
    _step_injected_context = ""
    if ctx.hook_registry is not None:
        try:
            from hooks import run_hooks as _run_hooks, any_blocking as _any_blocking, get_injected_context as _get_injected_ctx, SCOPE_STEP as _SCOPE_STEP
            _step_hook_ctx = {
                "goal": ctx.goal,
                "step": step_text,
                "step_result": step_result,
                "project": ctx.project,
                "step_num": step_idx,
            }
            _step_results = _run_hooks(
                _SCOPE_STEP, _step_hook_ctx,
                registry=ctx.hook_registry, adapter=ctx.adapter,
                dry_run=ctx.dry_run, fire_on="after",
            )
            if _any_blocking(_step_results):
                step_status = "blocked"
                _block_outputs = [r.output for r in _step_results if r.should_block]
                outcome["stuck_reason"] = "blocked by hook reviewer: " + "; ".join(_block_outputs[:2])
            _step_injected_context = _get_injected_ctx(_step_results)
        except Exception as _exc:
            # Correctness: hooks can BLOCK steps. If the hook system errors, a step
            # that should have been blocked proceeds as if approved. Surface loudly.
            log.error("step-level hook execution FAILED for step %d — should-block hooks may not have run: %s",
                      step_idx, _exc)

    return step_status, step_result, _step_injected_context


def _run_ralph_verify(
    ctx: LoopContext,
    step_text: str,
    step_idx: int,
    step_result: str,
    step_status: str,
    outcome: dict,
    step_adapter,
    *,
    step_tier_overrides: Dict[str, str],
    session_verify_failures: int,
    session_tier_floor: str,
    verify_fail_threshold: int,
) -> tuple:
    """Phase F8: Ralph verify loop — check done step actually addressed its goal.

    Returns (step_status, step_result, session_verify_failures, session_tier_floor).
    May mutate outcome and step_tier_overrides dicts in place.
    """
    from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER

    try:
        _vr = _verify_step(step_text, step_result, step_adapter)
        if not _vr["passed"]:
            log.info("ralph verify FAIL step=%d reason=%r — marking blocked for retry",
                     step_idx, _vr["reason"][:80])
            # Per-step tier escalation on verify failure
            _vf_tier = getattr(step_adapter, "model_key", MODEL_CHEAP)
            if _vf_tier == MODEL_CHEAP:
                step_tier_overrides[step_text] = MODEL_MID
                log.info("step %d verify-fail tier-up: cheap → mid", step_idx)
            elif _vf_tier == MODEL_MID:
                step_tier_overrides[step_text] = MODEL_POWER
                log.info("step %d verify-fail tier-up: mid → power", step_idx)
            # Session-level lagging signal
            session_verify_failures += 1
            if (session_verify_failures >= verify_fail_threshold
                    and not session_tier_floor):
                _current_tier = getattr(ctx.adapter, "model_key", MODEL_CHEAP)
                if _current_tier == MODEL_CHEAP:
                    session_tier_floor = MODEL_MID
                    log.warning("session-level tier-up: %d consecutive verify failures → "
                                "raising floor to mid for remaining steps",
                                session_verify_failures)
                    if ctx.verbose:
                        print(f"[poe] session tier-up: {session_verify_failures} verify "
                              "failures → floor raised to mid",
                              file=sys.stderr, flush=True)
            if ctx.verbose:
                print(f"[poe] ralph verify: step {step_idx} RETRY — {_vr['reason'][:80]}",
                      file=sys.stderr, flush=True)
            outcome["status"] = "blocked"
            outcome["stuck_reason"] = f"[ralph verify] {_vr['reason']}"
            step_status = "blocked"
            step_result = outcome.get("result", "")
        else:
            session_verify_failures = 0
    except Exception:
        pass  # verify never blocks loop progress

    return step_status, step_result, session_verify_failures, session_tier_floor


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
) -> tuple:
    """Phase F4: Run this step + peers in parallel batch.

    Returns (iteration, step_idx, total_tokens_in_delta, total_tokens_out_delta).
    Mutates step_outcomes, completed_context, remaining_steps/indices in place.
    """
    from llm import LLMTool

    _batch_steps = [step_text] + parallel_peers
    iteration += len(_batch_steps)
    _batch_start = time.monotonic()
    if ctx.verbose:
        print(f"[poe] parallel batch: {len(_batch_steps)} steps at level", file=sys.stderr, flush=True)

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

        step_outcomes.append(step_from_decompose(
            _batch_text, -1,
            status=_b_status,
            result=_batch_oc.get("result", ""),
            iteration=iteration,
            tokens_in=_batch_oc.get("tokens_in", 0),
            tokens_out=_batch_oc.get("tokens_out", 0),
            elapsed_ms=_b_elapsed,
            confidence=_batch_oc.get("confidence", "unverified"),
            injected_steps=_batch_oc.get("inject_steps", []),
        ))

        if _b_status == "done":
            _b_result = _batch_oc.get("result", "")
            _b_excerpt = _b_result[:800] if _b_result else ""
            completed_context.append(f"Step {step_idx} ({_batch_text[:80]}):\n{_b_excerpt}")
            if ctx.verbose:
                print(f"[poe] step {step_idx} done (parallel): {_batch_oc.get('summary', '')[:80]}", file=sys.stderr, flush=True)
            _bi_inject = _batch_oc.get("inject_steps", [])
            if _bi_inject and isinstance(_bi_inject, list):
                _batch_injected.extend(
                    str(s).strip() for s in _bi_inject if str(s).strip()
                )
        elif _b_status == "blocked":
            if ctx.verbose:
                print(f"[poe] step {step_idx} blocked (parallel): {_batch_oc.get('stuck_reason', '')[:80]}", file=sys.stderr, flush=True)

    # Inject collected steps from batch
    if _batch_injected:
        _capped_inject = _shape_steps(_batch_injected[:6], label="parallel-inject")
        remaining_steps[:0] = _capped_inject
        remaining_indices[:0] = [-1] * len(_capped_inject)
        log.info("parallel batch: injected %d step(s) from batch into plan",
                 len(_capped_inject))
        if ctx.verbose:
            for _s in _capped_inject:
                print(f"[poe] injected step (from parallel batch): {_s[:80]}",
                      file=sys.stderr, flush=True)

    # Log batch cost
    try:
        _batch_tokens = sum(o.get("tokens_in", 0) + o.get("tokens_out", 0) for o in _batch_outcomes)
        log.info("parallel batch done: %d steps, %d tokens, %dms",
                 len(_batch_steps), _batch_tokens, int((time.monotonic() - _batch_start) * 1000))
    except Exception as _exc:
        log.debug("parallel batch cost logging failed: %s", _exc)

    return iteration, step_idx, _tokens_in_delta, _tokens_out_delta


def _process_done_step(
    ctx: LoopContext,
    step_text: str,
    step_idx: int,
    step_result: str,
    step_summary: str,
    step_elapsed: int,
    outcome: dict,
    item_index: int,
    iteration: int,
    *,
    completed_context: List[str],
    remaining_steps: List[str],
    remaining_indices: List[int],
    loop_shared_ctx: Dict[str, Any],
    scratchpad: Dict[str, Any],
    scratchpad_lock,
    step_model: Optional[str] = None,
) -> str:
    """Phase F10: Process a completed step — scratchpad, context, injection, skills.

    Returns the (possibly updated) step_result.
    """
    o = _orch()
    if item_index >= 0:
        o.mark_item(ctx.project, item_index, o.STATE_DONE)

    # Write to scratchpad
    if not isinstance(step_result, str):
        step_result = json.dumps(step_result)
        outcome["result"] = step_result
    _result_excerpt = step_result[:2000] if step_result else ""
    _cited_files: List[str] = []
    try:
        import re as _scratchpad_re
        _cited_files = sorted(set(
            _scratchpad_re.findall(r'\b([a-z_]+\.py)\b', step_result or "")
        ))
    except Exception as _exc:
        log.debug("scratchpad file citation extraction failed: %s", _exc)
    with scratchpad_lock:
        scratchpad[f"step_{step_idx}"] = {
            "text": step_text[:200],
            "summary": step_summary[:200],
            "result_excerpt": _result_excerpt,
            "files_cited": _cited_files[:20],
        }
        _all_files = scratchpad.get("shared", {}).get("files_found", [])
        _src_files = set(f.name for f in Path("src").glob("*.py")) if Path("src").exists() else set()
        _real_cited = [f for f in _cited_files if f in _src_files]
        _all_files = sorted(set(_all_files + _real_cited))
        scratchpad.setdefault("shared", {})["files_found"] = _all_files

    # Build context entry
    _ctx_excerpt = step_result[:800] if step_result else ""
    if len(step_result) > 800:
        _ctx_excerpt += f"\n... ({len(step_result)} chars total — full result in scratchpad step_{step_idx})"
    _step_confidence = outcome.get("confidence", "")
    _confidence_tag = f" [confidence:{_step_confidence}]" if _step_confidence else ""
    _ctx_entry = f"Step {step_idx} ({step_text[:80]}){_confidence_tag}:\n{_ctx_excerpt}"
    completed_context.append(_ctx_entry)

    # Environment snapshot
    _snap_key = f"step:{step_idx}:{step_text[:40]}"
    _snap_val = step_summary[:200] if step_summary else (step_result[:200] if step_result else "")
    if _snap_val:
        loop_shared_ctx[_snap_key] = _snap_val

    # Phase 62: Store structured artifacts in shared context
    _artifacts = outcome.get("artifacts")
    if _artifacts and isinstance(_artifacts, dict):
        for _art_name, _art_val in _artifacts.items():
            _art_key = f"artifact:{step_idx}:{_art_name}"
            loop_shared_ctx[_art_key] = _art_val
        log.info("step %d: stored %d artifact(s) in shared context", step_idx, len(_artifacts))

    # Mutable task graph: inject discovered steps
    _injected = outcome.get("inject_steps", [])
    if _injected and isinstance(_injected, list):
        _raw_injected = [str(s).strip() for s in _injected if str(s).strip()][:3]
        _clean_injected = _shape_steps(_raw_injected, label="inject")
        if _clean_injected:
            remaining_steps[:0] = _clean_injected
            remaining_indices[:0] = [-1] * len(_clean_injected)
            log.info("step %d injected %d step(s) into plan: %s",
                     step_idx, len(_clean_injected),
                     [s[:40] for s in _clean_injected])
            if ctx.verbose:
                for _s in _clean_injected:
                    print(f"[poe] injected step: {_s[:80]}", file=sys.stderr, flush=True)

    # Context compression
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
        completed_context[:] = _compressed + list(_new_entries)

    if ctx.verbose:
        print(f"[poe] step {step_idx} done: {step_summary[:120]}", file=sys.stderr, flush=True)

    # Phase 32: update skill utility + Phase 59: record skill cost/latency telemetry
    try:
        from skills import find_matching_skills, update_skill_utility, record_variant_outcome, record_skill_outcome
        from metrics import estimate_cost as _est_cost
        _confidence_val = {"strong": 1.0, "weak": 0.5, "inferred": 0.3, "unverified": 0.1}.get(
            outcome.get("confidence", ""), 1.0
        )
        _step_cost = _est_cost(
            int(outcome.get("tokens_in", 0)),
            int(outcome.get("tokens_out", 0)),
            step_model,
        )
        for _sk in find_matching_skills(step_text + " " + ctx.goal, use_router=False):
            update_skill_utility(_sk.id, success=True)
            if getattr(_sk, "variant_of", None) is not None:
                record_variant_outcome(_sk.id, success=True)
            # Phase 59: record cost/latency per skill invocation
            record_skill_outcome(
                _sk.id,
                success=True,
                cost_usd=_step_cost,
                latency_ms=float(step_elapsed),
                confidence=_confidence_val,
            )
    except Exception as _skill_attr_exc:
        log.debug("skill attribution failed for step %d (non-critical): %s", step_idx, _skill_attr_exc)

    # Phase 33: record per-step cost
    try:
        from metrics import record_step_cost
        record_step_cost(
            step_text=step_text,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            status="done",
            goal=ctx.goal,
            model=getattr(ctx.adapter, "model_key", ""),
            elapsed_ms=step_elapsed,
        )
    except Exception as _cost_exc:
        log.debug("record_step_cost failed (non-critical): %s", _cost_exc)

    if ctx.step_callback is not None:
        try:
            ctx.step_callback(step_idx, step_text, step_summary, "done")
        except Exception as _cb_exc:
            log.debug("step_callback raised on step %d: %s", step_idx, _cb_exc)

    return step_result


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
    from llm import build_adapter, MODEL_CHEAP, MODEL_MID

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
                    print(f"[poe] step {step_idx}: escalated to {_tier_name} (retry tier-up)", file=sys.stderr, flush=True)
            except Exception as _ta_exc:
                log.debug("tier-override adapter build failed for step %d, using default: %s", step_idx, _ta_exc)
        else:
            try:
                from poe import classify_step_model
                _step_model = classify_step_model(step_text)
                if session_tier_floor and tier_order.get(_step_model, 0) < tier_order.get(session_tier_floor, 0):
                    _step_model = session_tier_floor
                if _step_model != adapter.model_key:
                    _step_adapter = build_adapter(model=_step_model)
                    if ctx.verbose:
                        _tier = "haiku" if _step_model == MODEL_CHEAP else "sonnet"
                        print(f"[poe] step {step_idx}: routing to {_tier} (classify_step_model)", file=sys.stderr, flush=True)
            except Exception as _cm_exc:
                log.debug("classify_step_model failed for step %d, using default: %s", step_idx, _cm_exc)
    return _step_adapter


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
            with open(_fb_path, "a") as _fb_f:
                _fb_f.write(json.dumps(_fb_entry) + "\n")
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
    )

    # Write partial-result artifact
    _done_steps = [s for s in step_outcomes if s.status == "done"]
    if _done_steps:
        try:
            _partial_lines = [f"# Partial result: {ctx.goal}\n"]
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
            _art_dir = _project_dir_root() / ctx.project / "artifacts"
            _art_dir.mkdir(parents=True, exist_ok=True)
            (_art_dir / f"loop-{ctx.loop_id}-PARTIAL.md").write_text(
                "\n".join(_partial_lines), encoding="utf-8")
            log.info("wrote partial result: %s (%d steps)", f"loop-{ctx.loop_id}-PARTIAL.md", len(_done_steps))
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
        print(f"[poe] {result.summary()}", file=sys.stderr, flush=True)

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
    )

    # Delete checkpoint on successful completion
    if result.status == "done":
        try:
            from checkpoint import delete_checkpoint as _del_ckpt
            _del_ckpt(ctx.loop_id)
        except Exception as _ckpt_exc:
            log.debug("checkpoint delete failed: %s", _ckpt_exc)

    # Artifact cleanup: per-step artifacts are temp by default.
    # Only keep them if config `keep_artifacts: true` is set.
    # Plan manifests, PARTIAL.md, loop logs, and scratchpad are always kept.
    if not ctx.dry_run and ctx.project:
        try:
            from config import get as _cfg_get
            _keep = bool(_cfg_get("keep_artifacts", False))
        except Exception:
            _keep = False
        if not _keep:
            try:
                _art_dir = _project_dir_root() / ctx.project / "artifacts"
                _deleted = 0
                for _f in _art_dir.glob(f"loop-{ctx.loop_id}-step-*.md"):
                    try:
                        _f.unlink()
                        _deleted += 1
                    except OSError:
                        pass
                if _deleted:
                    log.debug("artifact cleanup: deleted %d per-step artifact(s) "
                              "(set keep_artifacts: true to retain)", _deleted)
            except Exception as _art_exc:
                log.debug("artifact cleanup failed: %s", _art_exc)

    # Release loop lock
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
                f"[poe] step-shape: {len(steps)} planned → {len(_shaped_steps)} after splitting "
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
                f"[poe] dag: running {len(clean_steps)} steps with dep-aware scheduling "
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
            print(f"[poe] fan-out: running {len(steps)} steps in parallel (max_workers={parallel_fan_out})", file=sys.stderr, flush=True)
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
                    ))
                steps = _remaining
                if ctx.verbose:
                    print(
                        f"[poe] resuming from checkpoint {resume_from_loop_id}: "
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
                print(f"[poe] pre-run: {len(steps)} steps, estimated ~${_pre_est:.2f}", file=sys.stderr, flush=True)
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
                print(f"[poe] plan manifest: {manifest_path_str}", file=sys.stderr, flush=True)
        except Exception as _mf_exc:
            log.warning("initial plan manifest write failed: %s", _mf_exc)

    # Shared state for team workers
    loop_shared_ctx: Dict[str, Any] = {}

    # Compute parallel path info
    o = _orch()
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
    from llm import build_adapter, MODEL_CHEAP, MODEL_MID, THINKING_HIGH

    if ctx.verbose:
        print(f"[poe] decomposing goal...", file=sys.stderr, flush=True)
    _lessons_context, _skills_context, _cost_context, _had_no_matching_skill, _matched_rule = (
        _build_loop_context(ctx.goal, verbose=ctx.verbose, permission_context=permission_context,
                            project=ctx.project or "", repo_path=ctx.repo_path or "")
    )

    # Stage 5: rule hit — use deterministic steps, skip LLM decompose
    if preset_steps is not None and preset_steps:
        steps = [str(s).strip() for s in preset_steps if str(s).strip()]
        if ctx.verbose:
            print(f"[poe] pipeline: using {len(steps)} preset steps (no decompose)", file=sys.stderr, flush=True)
    elif _matched_rule is not None and _matched_rule.steps_template:
        steps = list(_matched_rule.steps_template)
        if ctx.verbose:
            print(f"[poe] using {len(steps)} rule steps from {_matched_rule.name!r}", file=sys.stderr, flush=True)
    else:
        steps = None

    if steps is None:
        # Decompose uses at least mid (Sonnet) — a weak planner compounds across every step.
        _decompose_adapter = ctx.adapter
        if getattr(ctx.adapter, "model_key", "") == MODEL_CHEAP:
            try:
                _decompose_adapter = build_adapter(model=MODEL_MID)
                log.debug("decompose: lifted adapter cheap → mid for plan quality")
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
        print(f"[poe] plan ({len(steps)} steps) loop_id={ctx.loop_id}:", file=sys.stderr, flush=True)
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
                    f"[poe] prereq: {len(_prereq_context)} step(s) have injected knowledge context",
                    file=sys.stderr, flush=True,
                )
        except Exception:
            pass  # prereq failures must never break the main loop

    return steps, _prereq_context, _lessons_context, _skills_context, _cost_context, _had_no_matching_skill


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
) -> tuple:
    """Phase A: Initialize loop — setup adapter, project, ancestry, hooks.

    Returns (ctx: LoopContext, early_return: Optional[LoopResult]).
    If early_return is not None, caller should return it immediately.
    """
    from llm import build_adapter
    from interrupt import InterruptQueue, set_loop_running
    from poe import assign_model_by_role

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

    ctx.loop_id = str(uuid.uuid4())[:8]
    ctx.started_at = time.monotonic()
    ctx.start_ts = datetime.now(timezone.utc).isoformat()

    _configure_logging(verbose)

    log.info("loop_start loop_id=%s goal=%r project=%s max_steps=%d",
             ctx.loop_id, goal[:80], project or "(auto)", max_steps)

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

    # Wall-clock timeout — default 2 hours, override via POE_LOOP_TIMEOUT_SECS
    try:
        ctx.loop_timeout_secs = float(os.environ.get("POE_LOOP_TIMEOUT_SECS", "7200"))
    except (ValueError, TypeError):
        ctx.loop_timeout_secs = 7200.0

    if verbose:
        print(f"[poe] loop_id={ctx.loop_id} goal={goal!r}", file=sys.stderr, flush=True)

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

    # Advertise this loop as running so other interfaces can route interrupts
    try:
        set_loop_running(ctx.loop_id, goal)
    except Exception as _slr_exc:
        log.debug("set_loop_running failed: %s", _slr_exc)

    # Resolve or create project
    # Always call ensure_project (idempotent) — guards against partially-initialized
    # projects where the dir exists but NEXT.md was never written.
    o = _orch()
    if project:
        _proj_existed = o.project_dir(project).exists()
        o.ensure_project(project, goal[:80])
        if verbose and not _proj_existed:
            print(f"[poe] created project={project}", file=sys.stderr, flush=True)
    else:
        project = _goal_to_slug(goal)
        _proj_existed = o.project_dir(project).exists()
        o.ensure_project(project, goal[:80])
        if verbose and not _proj_existed:
            print(f"[poe] created project={project}", file=sys.stderr, flush=True)
    ctx.project = project

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
            from poe import classify_step_model
            step_model = classify_step_model(step_text)
            step_adapter = build_adapter(model=step_model) if step_model != adapter.model_key else adapter
        except Exception as _cla_exc:
            log.debug("classify_step_model failed for parallel step %d, using default: %s", step_idx, _cla_exc)
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
            shared_ctx=shared_ctx,
        )

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
                        outcome["status"] = "blocked"
                        outcome["stuck_reason"] = (
                            f"parallel step {step_idx}: security scan flagged HIGH-risk "
                            f"content in result ({', '.join(_sec_result.signals)})"
                        )
                        outcome["result"] = ""
                        log.warning(
                            "parallel step %d blocked by security scan: %s",
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

    _fanout_timeout = int(os.environ.get("POE_STEP_TIMEOUT", "600"))

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
            from poe import classify_step_model
            step_model = classify_step_model(step_text)
            step_adapter = build_adapter(model=step_model) if step_model != adapter.model_key else adapter
        except Exception as _cla_exc:
            log.debug("classify_step_model failed for DAG step %d, using default: %s", step_idx, _cla_exc)
            step_adapter = adapter

        outcome = _execute_step(
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
        )
        if verbose:
            status_label = outcome.get("status", "?")
            summary = outcome.get("summary", "")[:80]
            print(f"[poe] dag step {step_idx} {status_label}: {summary}", file=sys.stderr, flush=True)
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
                f = pool.submit(_run_one, step_idx)
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


# ---------------------------------------------------------------------------
# Convergence tracking (Phase 62)
# ---------------------------------------------------------------------------

def _error_fingerprint(outcome: dict) -> str:
    """Generate a stable fingerprint for a step failure.

    Two failures with the same fingerprint indicate no convergence — the step
    is failing identically. Different fingerprints indicate the error is
    evolving, which is progress.
    """
    import hashlib
    reason = outcome.get("stuck_reason", "")
    result = outcome.get("result", "")
    # Normalize: strip timestamps, whitespace, and take first 200 chars of each
    _norm_reason = " ".join(reason.split())[:200]
    _norm_result = " ".join(result.split())[:200]
    _combined = f"{_norm_reason}|{_norm_result}"
    return hashlib.md5(_combined.encode("utf-8")).hexdigest()[:12]


def _is_converging(fingerprints: List[str]) -> bool:
    """Check if a step's retries are converging (producing different errors).

    Returns True if at least half the fingerprints are unique — the error
    landscape is changing, so retries are making progress.
    Returns False if most retries produce the same fingerprint — stuck in
    a loop.
    """
    if len(fingerprints) < 2:
        return True  # too few data points to judge
    unique = len(set(fingerprints))
    return unique / len(fingerprints) > 0.5


def _sibling_failure_rate(step_outcomes: list) -> float:
    """Fraction of completed steps that are blocked (not done).

    Used to detect whether the decomposition itself is wrong — if most
    siblings are failing, retrying individual steps won't help.
    """
    if not step_outcomes:
        return 0.0
    blocked = sum(1 for s in step_outcomes if s.status == "blocked")
    return blocked / len(step_outcomes)


# Phase 62 thresholds (from zoom-metacognition research)
_RETRY_THRESHOLD = 3         # retries before considering redecompose
_SIBLING_THRESHOLD = 0.5     # >50% sibling failure → redecompose parent
_REDECOMPOSE_THRESHOLD = 2   # max re-decompositions before flagging stuck
_NEED_INFO_PREFIX = "NEED_INFO:"  # step output prefix requesting more context

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
    split_into: List[str] = field(default_factory=list)  # non-empty → replace stuck step with these
    redecompose: bool = False  # True → re-decompose this step into sub-steps
    metacognitive_reason: str = ""  # why we chose this action (Phase 62 logging)


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
    # Lessons from tiered memory — backend abstraction with goal-ranked retrieval
    lessons_context = ""
    try:
        from memory import load_lessons, _MAX_LESSON_INJECT_CHARS
        _lessons = load_lessons(task_type="agenda", query=goal, limit=3)
        if not _lessons:
            _lessons = load_lessons(task_type="general", query=goal, limit=3)
        if _lessons:
            _lines = ["## Lessons from Prior Runs (apply these)"]
            for _l in _lessons:
                _icon = "✓" if _l.outcome == "done" else "✗"
                _lines.append(f"- {_icon} {_l.lesson}")
            lessons_context = "\n".join(_lines)
            if len(lessons_context) > _MAX_LESSON_INJECT_CHARS:
                lessons_context = lessons_context[:_MAX_LESSON_INJECT_CHARS].rsplit("\n", 1)[0]
    except Exception:
        try:
            from memory import inject_lessons_for_task
            lessons_context = inject_lessons_for_task("agenda", goal, max_lessons=3)
        except Exception as _les_exc:
            log.debug("lessons fallback injection failed: %s", _les_exc)

    # Phase 56: Standing rules (top tier — apply unconditionally)
    # Scoped to project domain when available to prevent cross-project bleed
    try:
        from memory import inject_standing_rules
        _rules = inject_standing_rules(domain=project)
        if _rules:
            lessons_context = _rules + ("\n\n" + lessons_context if lessons_context else "")
    except Exception as _exc:
        log.debug("standing rules injection failed: %s", _exc)

    # Phase 56: Decision journal (relevant prior decisions)
    try:
        from memory import inject_decisions
        _decisions = inject_decisions(goal, domain=project)
        if _decisions:
            lessons_context = (lessons_context + "\n\n" + _decisions) if lessons_context else _decisions
    except Exception as _exc:
        log.debug("decision journal injection failed: %s", _exc)

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
    except Exception as _exc:
        log.debug("graveyard resurrection failed: %s", _exc)

    # Error nodes: relevant failure patterns from diagnoses.jsonl (Phase 46 follow-on)
    try:
        from introspect import find_relevant_failure_notes
        _failure_notes = find_relevant_failure_notes(goal, limit=2)
        if _failure_notes:
            lessons_context += "\n\nKnown failure patterns for similar goals:\n" + "\n".join(
                f"- {note}" for note in _failure_notes
            )
    except Exception as _exc:
        log.debug("failure notes injection failed: %s", _exc)

    # Captain's log context: what has the learning system been doing recently?
    # This is the K3 "read bridge" — the first consumer of the captain's log
    # for reasoning-time context injection. Surfaces recent skill changes,
    # evolver actions, and diagnoses so the planner can account for them.
    try:
        from captains_log import load_log
        # Focus on actionable events, not noise (skip LESSON_REINFORCED, DECISION_RECORDED)
        _actionable_types = {
            "SKILL_PROMOTED", "SKILL_DEMOTED", "SKILL_CIRCUIT_OPEN",
            "SKILL_REWRITE", "EVOLVER_APPLIED", "DIAGNOSIS",
            "HYPOTHESIS_PROMOTED", "STANDING_RULE_CONTRADICTED",
            "RULE_GRADUATED",
        }
        _recent = load_log(limit=30)  # last 30 entries
        _actionable = [e for e in _recent if e.get("event_type") in _actionable_types]
        if _actionable:
            _log_lines = [
                f"- [{e.get('event_type', '?')}] {e.get('summary', '')[:100]}"
                for e in _actionable[-5:]  # inject at most 5
            ]
            _log_ctx = "## Recent Learning System Activity\n" + "\n".join(_log_lines)
            lessons_context = (lessons_context + "\n\n" + _log_ctx) if lessons_context else _log_ctx
    except Exception as _exc:
        log.debug("captains log context injection failed: %s", _exc)

    # Director's operational playbook — evolved wisdom about how to do the job
    try:
        from playbook import inject_playbook
        _playbook = inject_playbook(max_chars=800)
        if _playbook:
            lessons_context = (lessons_context + "\n\n" + _playbook) if lessons_context else _playbook
    except Exception as _exc:
        log.debug("playbook injection failed: %s", _exc)

    # Knowledge nodes — structured knowledge from imported sources (K2)
    try:
        from knowledge_web import inject_knowledge_for_goal
        _knowledge = inject_knowledge_for_goal(goal, max_chars=600)
        if _knowledge:
            lessons_context = (lessons_context + "\n\n" + _knowledge) if lessons_context else _knowledge
    except Exception as _exc:
        log.debug("knowledge injection failed: %s", _exc)

    # Matching skills for decompose prompt injection
    skills_context = ""
    had_no_matching_skill = False
    try:
        from skills import find_matching_skills, format_skills_for_prompt, select_variant_for_task
        _matching_skills = find_matching_skills(goal)
        # A/B routing: for each matched skill, select parent or active challenger
        # using a hash of the goal as a stable routing key (loop_id not yet assigned)
        import hashlib as _hashlib
        _routing_key = _hashlib.sha1(goal.encode()).hexdigest()[:8]
        _matched_and_routed = [select_variant_for_task(s, _routing_key) for s in _matching_skills]
        skills_context = format_skills_for_prompt(_matched_and_routed)
        if _matched_and_routed and verbose:
            print(
                f"[poe] injecting {len(_matched_and_routed)} skill(s) into decompose",
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
            if verbose:
                _curated_count = len(_skill_loader.find_matching(goal, role=_role))
                print(
                    f"[poe] injecting {_curated_count} curated skill(s) into decompose",
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
                        print(f"[poe] codebase graph: {_cg.total_files} files, top={_cg.ranked_files[0] if _cg.ranked_files else '?'}", file=sys.stderr, flush=True)
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
                    print(f"[poe] repo context: {_stack.summary}", file=sys.stderr, flush=True)
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
                    f"[poe] Stage 5 rule hit: {matched_rule.name!r} — skipping LLM decompose",
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
    """
    low = step.lower()
    # Find which exec keyword matched to build a clean run step
    exec_kw = next((kw.strip() for kw in _EXEC_KEYWORDS if kw in low), "command")
    # Trim trailing clauses that describe analysis
    run_part = step
    for sep in (" and ", " then ", ", then ", " to ", "; "):
        if sep in low:
            idx = low.find(sep)
            candidate = step[:idx].strip()
            if any(kw in candidate.lower() for kw in _EXEC_KEYWORDS):
                run_part = candidate
                break

    run_step = f"Run {run_part.lstrip('Rr un').strip()[:120]} and save output to a file"
    analyze_step = f"Read the captured output and analyze results: {step[:80]}"
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


def _generate_timeout_split(step_text: str, adapter) -> List[str]:
    """Ask the cheap model to split a timed-out step into smaller atomic steps.

    Uses a short 45s timeout so a struggling adapter doesn't compound the delay.
    Falls back to a simple heuristic split (one sentence per line) if the LLM
    call fails. Returns [] only if both attempts produce nothing usable.
    """
    if adapter is not None:
        try:
            from llm import LLMMessage, MODEL_CHEAP, build_adapter
            _prompt = (
                f"An autonomous agent step timed out because it was too large to complete in time.\n\n"
                f"Timed-out step: {step_text}\n\n"
                f"Rewrite this as 2-4 smaller, atomic steps that together accomplish the same goal. "
                f"Each step must be self-contained and completable independently. "
                f"Return ONLY a numbered list, one step per line, no explanation."
            )
            try:
                _split_adapter = build_adapter(model=MODEL_CHEAP)
            except Exception as _sa_exc:
                log.debug("cheap adapter build for timeout-split failed, using default: %s", _sa_exc)
                _split_adapter = adapter
            resp = _split_adapter.complete(
                [LLMMessage("user", _prompt)],
                max_tokens=300,
                temperature=0.2,
                timeout=45,
            )
            lines = [
                ln.lstrip("0123456789.-) ").strip()
                for ln in resp.content.strip().splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            steps = [ln for ln in lines if len(ln) > 10]
            if len(steps) >= 2:
                return steps
        except Exception as exc:
            log.debug("timeout split LLM call failed: %s", exc)

    # Heuristic fallback: split on sentence boundaries / conjunctions in the step text.
    import re as _re
    parts = _re.split(r"\s*;\s*|\s+and\s+then\s+|\s*\band\b\s*(?=[A-Z])", step_text)
    parts = [p.strip().rstrip(",") for p in parts if len(p.strip()) > 10]
    if len(parts) >= 2:
        log.debug("timeout split heuristic: %d parts from %r", len(parts), step_text[:60])
        return parts

    return []


_DIAGNOSIS_RETRY_THRESHOLD = 2  # retries before we consult diagnose_loop()


def _consult_diagnosis(loop_id: str) -> Optional[tuple]:
    """Mid-loop consultation of Phase 44 diagnose_loop() + Phase 45 plan_recovery().

    Returns (failure_class, recovery_plan) if diagnosis classifies anything
    actionable (non-healthy, with a recovery plan), else None.

    Safe to call mid-loop — diagnose_loop() reads whatever events.jsonl has
    been flushed so far (write_event is synchronous).
    """
    if not loop_id:
        return None
    try:
        from introspect import diagnose_loop, plan_recovery
        diag = diagnose_loop(loop_id)
        if diag.failure_class == "healthy":
            return None
        plan = plan_recovery(diag)
        if plan is None:
            return None
        return (diag.failure_class, plan)
    except Exception as exc:
        log.debug("mid-loop diagnosis consult failed: %s", exc)
        return None


def _handle_blocked_step(
    step_text: str,
    outcome: dict,
    prior_retries: int,
    adapter,
    *,
    error_fingerprints: Optional[List[str]] = None,
    step_outcomes: Optional[list] = None,
    replan_count: int = 0,
    loop_id: str = "",
) -> _BlockDecision:
    """Decide what to do when a step returns status != 'done'.

    Phase 62: Implements the zoom-metacognition decision algorithm:
    - Track error convergence (are retries producing different errors?)
    - Check sibling failure rate (is the decomposition itself wrong?)
    - Choose retry / redecompose / stuck based on evidence

    Does not mutate any loop state — returns a decision the caller applies.

    Args:
        step_text:          The step text that failed.
        outcome:            The raw outcome dict from _execute_step().
        prior_retries:      Number of times this step has already been retried.
        adapter:            LLM adapter (used for round-2 refinement hint).
        error_fingerprints: List of error fingerprints from prior retries of this step.
        step_outcomes:      All step outcomes so far (for sibling failure correlation).
        replan_count:       Number of re-decompositions already attempted.

    Returns:
        _BlockDecision — retry=True means re-queue; retry=False means terminate or redecompose.
    """
    block_reason = outcome.get("stuck_reason", "blocked")
    step_result = outcome.get("result", "")
    fingerprints = error_fingerprints or []

    # NEED_INFO: step explicitly requests more context (Phase 62 deliverable 4)
    if block_reason.startswith(_NEED_INFO_PREFIX):
        _info_needed = block_reason[len(_NEED_INFO_PREFIX):].strip()
        log.info("step NEED_INFO: %s — generating research sub-steps", _info_needed[:80])
        _research_steps = [f"Research: {_info_needed}"]
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",
            stuck_reason="",
            split_into=_research_steps + [step_text],  # research first, then retry original
            metacognitive_reason=f"step requested info: {_info_needed[:100]}",
        )

    # Combined exec+analyze steps are structurally wrong — retrying identically
    # won't fix a bad step shape.  Split immediately on first block regardless
    # of the reason (timeout, LLM confusion, output overflow, etc.).
    if _is_combined_exec_analyze(step_text):
        _parts = _split_exec_analyze(step_text)
        log.info("step-shape: combined exec+analyze blocked (%s) — splitting into %d steps",
                 block_reason[:60], len(_parts))
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",      # not stuck — split recovers
            stuck_reason="",
            split_into=_parts,
            metacognitive_reason="combined exec+analyze step shape — structural split",
        )

    # Timeout failures must not be retried identically — the subprocess will
    # just time out again, burning wall-clock time with zero progress.
    # Instead: ask the cheap model to reason about how to split the step, then
    # inject the resulting steps via split_into so execution continues.
    _is_timeout = "timed out" in block_reason.lower()
    if _is_timeout:
        _split_steps = _generate_timeout_split(step_text, adapter)
        if _split_steps:
            log.info("step-shape: timeout on %r — LLM split into %d steps", step_text[:60], len(_split_steps))
            return _BlockDecision(
                retry=False,
                hint="",
                loop_status="",          # not stuck — split recovers
                stuck_reason="",
                split_into=_split_steps,
                metacognitive_reason="timeout — decomposed into smaller steps",
            )
        # Split generation itself failed — hard stop to avoid infinite spin.
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="stuck",
            stuck_reason=(
                f"TIMEOUT and split-recovery failed: {block_reason}. "
                "Consider narrowing the step scope or switching to an API adapter."
            ),
            metacognitive_reason="timeout and split-recovery failed — terminal",
        )

    # Phase 44+45 bridge: after N retries, consult the rich diagnosis
    # taxonomy (10 failure classes) before falling back to the convergence
    # heuristic. The diagnosis sees the whole loop trace — it can spot
    # retry_churn, decomposition_too_broad, etc. that per-step heuristics miss.
    if prior_retries >= _DIAGNOSIS_RETRY_THRESHOLD and loop_id:
        _diag_result = _consult_diagnosis(loop_id)
        if _diag_result is not None:
            _fc, _plan = _diag_result
            _meta = f"retries={prior_retries}, diag={_fc}, plan_action={_plan.action[:60]!r}"
            if _fc == "retry_churn":
                if replan_count < _REDECOMPOSE_THRESHOLD:
                    log.info("diagnosis (retry_churn) — re-decomposing to break churn (%s)", _meta)
                    return _BlockDecision(
                        retry=False, hint="", loop_status="", stuck_reason="",
                        redecompose=True,
                        metacognitive_reason=f"diagnose_loop: retry_churn — redecompose ({_meta})",
                    )
                log.warning("diagnosis (retry_churn) — exhausted re-decompositions (%s)", _meta)
                return _BlockDecision(
                    retry=False, hint="", loop_status="stuck",
                    stuck_reason=f"retry_churn after {replan_count} re-decompositions",
                    metacognitive_reason=f"diagnose_loop: retry_churn exhausted ({_meta})",
                )
            if _fc == "decomposition_too_broad" and replan_count < _REDECOMPOSE_THRESHOLD:
                log.info("diagnosis (decomposition_too_broad) — re-decomposing (%s)", _meta)
                return _BlockDecision(
                    retry=False, hint="", loop_status="", stuck_reason="",
                    redecompose=True,
                    metacognitive_reason=f"diagnose_loop: decomposition_too_broad ({_meta})",
                )
            if _fc == "empty_model_output" and prior_retries < _RETRY_THRESHOLD:
                _hint_txt = (
                    _plan.params.get("hint")
                    or "You MUST call complete_step or flag_stuck. Do not return bare text."
                )
                log.info("diagnosis (empty_model_output) — retry with tool-call hint (%s)", _meta)
                return _BlockDecision(
                    retry=True, hint=_hint_txt, loop_status="", stuck_reason="",
                    metacognitive_reason=f"diagnose_loop: empty_model_output — explicit tool-call hint ({_meta})",
                )
            if _fc == "constraint_false_positive" and prior_retries < _RETRY_THRESHOLD:
                log.info("diagnosis (constraint_false_positive) — retry (%s)", _meta)
                return _BlockDecision(
                    retry=True,
                    hint="[Constraint false-positive suspected; retrying with refreshed state]",
                    loop_status="", stuck_reason="",
                    metacognitive_reason=f"diagnose_loop: constraint_false_positive ({_meta})",
                )
            # Other classes (adapter_timeout, budget_exhaustion, setup_failure,
            # artifact_missing, integration_drift, token_explosion) fall through
            # to the convergence heuristic — they're either already handled by
            # earlier special cases or lack a clear mid-loop action.
            log.debug("diagnosis (%s) — no targeted mid-loop action; using heuristic (%s)", _fc, _meta)

    # Phase 62: Convergence-aware decision algorithm
    # (from zoom-metacognition research: Argyris double-loop / Boyd OODA)
    converging = _is_converging(fingerprints)
    sibling_rate = _sibling_failure_rate(step_outcomes) if step_outcomes else 0.0

    # Log the metacognitive state for every decision
    _meta_ctx = (
        f"retries={prior_retries}, converging={converging}, "
        f"sibling_fail_rate={sibling_rate:.0%}, replan_count={replan_count}"
    )

    # Check sibling failure correlation first (zoom-metacognition §3.3)
    # If most siblings are failing, the decomposition is wrong — redecompose
    if (sibling_rate > _SIBLING_THRESHOLD
            and len(step_outcomes or []) >= 3
            and replan_count < _REDECOMPOSE_THRESHOLD):
        log.info("sibling failure rate %.0f%% > %.0f%% — triggering re-decomposition "
                 "(%s)", sibling_rate * 100, _SIBLING_THRESHOLD * 100, _meta_ctx)
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",
            stuck_reason="",
            redecompose=True,
            metacognitive_reason=(
                f"sibling failure rate {sibling_rate:.0%} exceeds {_SIBLING_THRESHOLD:.0%} "
                f"threshold — decomposition is likely wrong ({_meta_ctx})"
            ),
        )

    # Standard retry path: retry if under threshold AND converging
    if prior_retries < _RETRY_THRESHOLD and converging:
        if prior_retries == 0:
            # Round 1: generic fallback hint
            hint = (
                f"[Previous attempt blocked: {block_reason[:120]}] "
                "Try an alternative approach: use a different tool, rephrase the request, "
                "work around the obstacle, or summarize what you know so far and mark complete. "
                "If you lack required information, say NEED_INFO: [what's missing] instead of guessing."
            )
        else:
            # Round 2+: LLM-assisted targeted refinement hint
            hint = _generate_refinement_hint(
                step_text=step_text,
                block_reason=block_reason,
                partial_result=step_result,
                adapter=adapter,
            )
        log.info("retry (converging): %s", _meta_ctx)
        return _BlockDecision(
            retry=True, hint=hint, loop_status="", stuck_reason="",
            metacognitive_reason=f"retry — errors converging, under threshold ({_meta_ctx})",
        )

    # Not converging or threshold exceeded — try re-decomposition
    if replan_count < _REDECOMPOSE_THRESHOLD:
        log.info("not converging or threshold exceeded — re-decomposing step (%s)", _meta_ctx)
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",
            stuck_reason="",
            redecompose=True,
            metacognitive_reason=(
                f"not converging after {prior_retries} retries — "
                f"re-decomposing step ({_meta_ctx})"
            ),
        )

    # Exhausted all options — terminal failure
    log.warning("terminal: exhausted retries and re-decompositions (%s)", _meta_ctx)
    return _BlockDecision(
        retry=False,
        hint="",
        loop_status="stuck",
        stuck_reason=block_reason,
        metacognitive_reason=(
            f"exhausted: {prior_retries} retries, {replan_count} re-decompositions, "
            f"converging={converging}, sibling_rate={sibling_rate:.0%}"
        ),
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
    failure_chain: Optional[List[str]] = None,
    recovery_steps: int = 0,
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
            _recovery = _plan_recovery(_diag, use_advisor=True)
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
            except Exception as _store_exc:
                log.warning("failed to persist diagnosis lesson (learning data lost): %s", _store_exc)
    except Exception as exc:
        log.debug("introspect failed: %s", exc)

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

    # Auto-extract skills from successful loops (crystallise patterns)
    if loop_status == "done" and not dry_run and step_outcomes:
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
                        print(f"[poe] skill crystallised: {skill.name}", file=sys.stderr, flush=True)
        except Exception as _skill_exc:
            log.warning("skill extraction failed — loop %s may not contribute to skill library: %s", loop_id, _skill_exc)

    # Phase 32: skill synthesis — when no skill matched at start, synthesize from this run
    if loop_status == "done" and had_no_matching_skill and not dry_run and step_outcomes:
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

    # Phase 32: auto-promote skills that meet threshold (don't wait for evolver heartbeat)
    if not dry_run:
        try:
            from evolver import run_skill_maintenance
            run_skill_maintenance()
        except ImportError:
            pass
        except Exception as _maint_exc:
            log.debug("skill maintenance failed (non-critical): %s", _maint_exc)

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
        except Exception as _tg_exc:
            log.debug("post-mission Telegram notification failed (non-critical): %s", _tg_exc)


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

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
    # Reset per-run state (cost-warn flag persists across calls otherwise)
    run_agent_loop._cost_warned = False  # type: ignore[attr-defined]

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
    )
    if _early_return is not None:
        return _early_return

    # Re-import lazy deps used by subsequent phases (same lazy-import pattern)
    from llm import LLMMessage, LLMTool, build_adapter, MODEL_CHEAP, MODEL_MID, MODEL_POWER
    from interrupt import InterruptQueue, apply_interrupt_to_steps, set_loop_running, clear_loop_running

    # Tier ordering for floor comparisons (Phase 57: session-level tier floor)
    _TIER_ORDER = {MODEL_CHEAP: 0, MODEL_MID: 1, MODEL_POWER: 2}

    # Unpack ctx into locals used by subsequent phases.
    # These aliases will be eliminated as phases B-G are extracted to use ctx directly.
    loop_id = ctx.loop_id
    started_at = ctx.started_at
    start_ts = ctx.start_ts
    project = ctx.project
    adapter = ctx.adapter
    interrupt_queue = ctx.interrupt_queue
    _hook_registry = ctx.hook_registry
    _ancestry_context = ctx.ancestry_context
    _loop_timeout_secs = ctx.loop_timeout_secs
    _perm_ctx = ctx.perm_ctx

    def _resolve_tools() -> list:
        """Re-query tool registry on each call to pick up runtime-registered tools."""
        return (
            _get_tools_for_role(_perm_ctx.role, _perm_ctx.deny_patterns)
            if _perm_ctx is not None else list(_EXECUTE_TOOLS)
        )

    o = _orch()

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
    _manifest_path_str = _pf["manifest_path_str"]
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

    # Step 2: Execute each step in order (dynamic — interrupts may add/replace steps)
    # Pre-populate with any completed steps from a checkpoint resume
    step_outcomes: List[StepOutcome] = list(_resume_completed)
    total_tokens_in = 0
    total_tokens_out = 0
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
    ctx.set_phase(LoopPhase.EXECUTE)
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
                _ms_sub = _ms_decompose(step_text, adapter, max_steps=5)
                if _ms_sub and len(_ms_sub) >= 2:
                    _ms_sub = _shape_steps(_ms_sub, label="milestone-expand")
                    remaining_steps[:0] = _ms_sub
                    remaining_indices[:0] = [-1] * len(_ms_sub)
                    log.info("milestone-aware: step %d %r → %d sub-steps",
                             _would_be_step_idx, step_text[:60], len(_ms_sub))
                    if verbose:
                        import sys as _ms_sys
                        print(f"[poe] milestone step {_would_be_step_idx} expanded → "
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
            )
            total_tokens_in += _tin
            total_tokens_out += _tout
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
                print(f"[poe] step {step_idx}: accumulated context {_ctx_chars:,} chars "
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
        # Invariant guard: if a compound step still reaches execution, log it so we
        # can trace which path introduced it (shaper gap detection — Codex Priority 1).
        if _is_combined_exec_analyze(step_text):
            log.warning(
                "step-shape-LEAK step=%d: compound exec+analyze step reached executor "
                "(shaper did not catch this — check injection path): %r",
                step_idx, step_text[:120],
            )
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
        _raw_result = outcome.get("result", "")
        # Guard: LLM can return a JSON schema object instead of a string value for
        # result/summary fields. If non-string, convert to empty string (result) or step_text (summary).
        step_result = _raw_result if isinstance(_raw_result, str) else str(_raw_result) if _raw_result else ""
        _raw_summary = outcome.get("summary", step_text)
        step_summary = _raw_summary if isinstance(_raw_summary, str) else step_text

        # Ralph verify loop (Phase F8)
        _ralph_active = ralph_verify or goal.lower().startswith(("ralph:", "verify:"))
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
                        print(f"[poe] advisor (Opus): rephrase step {step_idx}", file=sys.stderr)
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
                elapsed_ms=step_elapsed,
            ))
            if item_index >= 0:
                o.mark_item(project, item_index, o.STATE_BLOCKED)
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
            elapsed_ms=step_elapsed,
            confidence=outcome.get("confidence", ""),
            injected_steps=outcome.get("inject_steps", []),
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
                    print(f"[poe] trajectory check: {_traj_done}/{len(step_outcomes)} steps done "
                          f"({_traj_rate:.0%}) → floor raised to mid",
                          file=sys.stderr, flush=True)

        if loop_status == "stuck":
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
    _auto_recovery_attempted = getattr(run_agent_loop, "_recovery_in_progress", False)
    if (result.status == "stuck" and not dry_run and not _auto_recovery_attempted):
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
               ancestry_context="", skills_context="", cost_context="",
               thinking_budget=None):
    """Delegate to planner.decompose(). See planner.py for full implementation."""
    from planner import maybe_add_verification_step
    steps = _decompose_impl(goal, adapter, max_steps, verbose=verbose,
                            lessons_context=lessons_context, ancestry_context=ancestry_context,
                            skills_context=skills_context, cost_context=cost_context,
                            thinking_budget=thinking_budget)
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
        artifacts_dir = _project_dir_root() / project / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"loop-{loop_id}-step-{step_num:02d}.md"
        path = artifacts_dir / fname
        content = f"# Step {step_num}: {step_text}\n\n{result}\n"
        path.write_text(content, encoding="utf-8")
        return o.relative_display_path(path)
    except Exception:
        return None


def _plan_manifest_path(project: str, loop_id: str) -> Optional[Path]:
    """Return path for the human-readable plan manifest file."""
    if not project:
        return None
    try:
        o = _orch()
        artifacts_dir = o.orch_root() / "projects" / project / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        return artifacts_dir / f"loop-{loop_id}-plan.md"
    except Exception:
        return None


def _write_plan_manifest(
    project: str,
    loop_id: str,
    goal: str,
    planned_steps: List[str],
    start_ts: str,
    step_outcomes: Optional[List[StepOutcome]] = None,
    *,
    status: str = "running",
    elapsed_ms: int = 0,
    replan_count: int = 0,
) -> Optional[str]:
    """Write (or overwrite) the human-readable run plan manifest.

    Emitted immediately after decomposition (step_outcomes=[]) so the full
    plan is visible before execution begins. Overwritten after each step with
    current progress. Always human-readable — this is the primary debugging
    artifact for in-flight runs.

    Returns path written (relative to orch_root) or None on failure.
    """
    path = _plan_manifest_path(project, loop_id)
    if path is None:
        return None

    step_outcomes = step_outcomes or []
    _by_idx: Dict[int, StepOutcome] = {s.index: s for s in step_outcomes}
    _done = sum(1 for s in step_outcomes if s.status == "done")
    _blocked = sum(1 for s in step_outcomes if s.status == "blocked")
    _total = len(planned_steps)

    replan_note = f"  **Replans:** {replan_count}" if replan_count else ""
    header = [
        f"# Run Plan — `{loop_id}`",
        f"**Project:** {project}  **Goal:** {goal[:120]}",
        f"**Started:** {start_ts}  **Status:** {status}  "
        f"**Progress:** {_done}/{_total} done, {_blocked} blocked{replan_note}",
        "",
        f"## Steps ({_total} planned)",
        "",
    ]

    step_lines = []
    for i, step_text in enumerate(planned_steps, 1):
        outcome = _by_idx.get(i)
        step_type = _classify_step(step_text)
        _type_tag = f" `[{step_type}]`" if step_type != "general" else ""
        if outcome is None:
            icon = "⬜"
            suffix = ""
        elif outcome.status == "done":
            icon = "✅"
            t_total = outcome.tokens_in + outcome.tokens_out
            try:
                from metrics import estimate_cost as _est
                cost_str = f" | ${_est(outcome.tokens_in, outcome.tokens_out):.4f}"
            except Exception:
                cost_str = ""
            suffix = f" | {outcome.elapsed_ms}ms | {t_total} tok{cost_str}"
        else:
            icon = "❌"
            suffix = f" | {outcome.elapsed_ms}ms"
        step_lines.append(f"{i}. {icon}{_type_tag} {step_text[:120]}{suffix}")

    exec_lines: List[str] = []
    if step_outcomes:
        exec_lines = ["", "## Execution Log", ""]
        for s in step_outcomes:
            icon = "✅" if s.status == "done" else "❌"
            t_total = s.tokens_in + s.tokens_out
            exec_lines.append(f"### Step {s.index} {icon} | {s.elapsed_ms}ms | {t_total} tok")
            exec_lines.append(f"**{s.text[:120]}**")
            blurb = getattr(s, "summary", None) or s.result
            if blurb:
                exec_lines.append(f"> {blurb[:300]}")
            exec_lines.append("")

    footer: List[str] = []
    if status != "running":
        footer = [
            "---",
            f"**Final:** {status} | {_done}/{_total} done | {_blocked} blocked"
            + (f" | {elapsed_ms}ms total" if elapsed_ms else ""),
        ]

    content = "\n".join(header + step_lines + exec_lines + footer) + "\n"
    try:
        path.write_text(content, encoding="utf-8")
        try:
            o = _orch()
            return o.relative_display_path(path)
        except Exception:
            return str(path)
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
        artifacts_dir = _project_dir_root() / project / "artifacts"
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
        return o.relative_display_path(path)
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
    parser.add_argument(
        "--backend", "-b",
        choices=["auto", "anthropic", "openrouter", "openai", "subprocess", "codex"],
        default=None,
        help="LLM backend (default: auto-detect; POE_BACKEND env var also accepted)",
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
