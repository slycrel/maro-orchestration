"""Core types and state machine for the agent loop (extracted from agent_loop.py).

Holds the data types (StepOutcome, LoopResult), phase constants (LoopPhase),
the mutable per-run state bundle (LoopContext/LoopStateMachine), and the small
module-level helpers (_orch, _project_dir_root, _configure_logging) that
nearly every other loop_*.py module needs. Kept here specifically so those
helpers have exactly one home that every other extracted module can import
from without creating a load-order cycle back through agent_loop.py.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Dict, List, Optional

log = logging.getLogger("maro.loop")

_logging_configured = False

def _configure_logging(verbose: bool = False) -> None:
    """Set up maro.* logger hierarchy once.

    Level resolution (first match wins):
      1. MARO_LOG_LEVEL env var (DEBUG, INFO, WARNING, ERROR)
      2. MARO_DEBUG=1 env var → DEBUG
      3. config `debug: true` → DEBUG
      4. verbose=True → DEBUG
      5. default → WARNING (quiet)

    Format: compact timestamp + level + logger name + message.
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    env_level = os.environ.get("MARO_LOG_LEVEL", "").upper()
    debug_on = os.environ.get("MARO_DEBUG") == "1"
    if not debug_on and not env_level:
        try:
            from config import get as _cfg_get
            debug_on = bool(_cfg_get("debug", False))
        except Exception:
            debug_on = False
    if env_level and hasattr(logging, env_level):
        level = getattr(logging, env_level)
    elif debug_on or verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    root_logger = logging.getLogger("maro")
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

    Replaces the hardcoded `orch_root() / "prototypes" / "maro-orchestration" / "projects"`
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
    cache_read_tokens: int = 0   # subset of tokens_in served from cache (~0.1x cost)
    provider_cost_usd: float = 0.0
    elapsed_ms: int = 0
    confidence: str = ""         # "strong" | "weak" | "inferred" | "unverified" | ""
    injected_steps: List[str] = field(default_factory=list)  # steps added mid-plan by this step
    call_record: str = ""        # path to the byte-level record of this step's LLM call
                                 # (<run-dir>/build/calls/call-NNNNN.json) when record-mode captured it
    ended_ts: str = ""           # ISO UTC timestamp when this step finished — lets the run
                                 # report position steps on a timeline as [ended_ts - elapsed_ms,
                                 # ended_ts] instead of a cumulative-sum approximation that
                                 # silently absorbs inter-step overhead (ralph verify, hooks,
                                 # replans) into the wrong step's segment.
    executor_session_id: str = ""
    executor_session_resumed: bool = False


def step_from_decompose(
    text: str,
    index: int,
    *,
    status: str = "pending",
    result: str = "",
    iteration: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read_tokens: int = 0,
    provider_cost_usd: float = 0.0,
    elapsed_ms: int = 0,
    confidence: str = "unverified",
    injected_steps: Optional[List[str]] = None,
    call_record: str = "",
    executor_session_id: str = "",
    executor_session_resumed: bool = False,
    ended_ts: Optional[str] = None,
) -> StepOutcome:
    """Factory for StepOutcome — centralises defaults so inline construction sites stay DRY.

    ended_ts sentinel (2026-07-08 adversarial review, findings #2/#5): omitted
    (None) defaults to "now" (UTC) — correct at every call site that constructs
    the outcome immediately after the step actually finished (the sequential
    main loop, blocked-step retries). Passing ended_ts="" explicitly instead
    opts OUT of that default and leaves it genuinely empty, for the two classes
    of call site where "now" would be a fabricated timestamp: bulk
    reconstruction well after the fact (checkpoint resume) and parallel/fan-out
    batch processing (where per-step elapsed_ms/ended_ts aren't real individual
    timings — see loop_parallel.py). loop_report.py's _step_windows() already
    falls back to an explicitly-flagged approximate timeline when ended_ts is
    empty; this sentinel is what lets a caller deliberately request that
    fallback instead of it only ever firing for pre-field-existing data.
    """
    return StepOutcome(
        index=index,
        text=text,
        status=status,
        result=result,
        iteration=iteration,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read_tokens=cache_read_tokens,
        provider_cost_usd=provider_cost_usd,
        elapsed_ms=elapsed_ms,
        confidence=confidence,
        injected_steps=injected_steps if injected_steps is not None else [],
        call_record=call_record,
        executor_session_id=executor_session_id,
        executor_session_resumed=executor_session_resumed,
        ended_ts=ended_ts if ended_ts is not None else datetime.now(timezone.utc).isoformat(),
    )


@dataclass
class LoopResult:
    loop_id: str
    project: str
    goal: str
    status: str          # "done" | "stuck" | "error" | "interrupted" | "restart"
    steps: List[StepOutcome] = field(default_factory=list)
    stuck_reason: Optional[str] = None
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    elapsed_ms: int = 0
    log_path: Optional[str] = None
    interrupts_applied: int = 0
    march_of_nines_alert: bool = False    # Phase 19: chain_success < 0.5 alert
    pre_flight_review: Optional[Any] = None  # Phase 58: PlanReview if pre-flight ran
    # data-r2-01: carried out so deferred (post-closure) skill synthesis knows
    # whether this run started with no matching skill — the synthesis trigger.
    had_no_matching_skill: bool = False
    # Direct CLI closure runs after loop finalization. These declared fields
    # carry its audit decision to output/learning without an untyped side
    # channel or making human-facing warning text the policy predicate.
    audit_learning_allowed: bool = True
    audit_incomplete_warning: str = ""

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

# Shared ceiling for every quality-loop "try again" mechanism: director
# restart and closure restart (handle.py, gate `continuation_depth <
# MAX_RESTART_DEPTH`), continuation-pass respawn (loop_post_step.py,
# `MARO_MAX_CONTINUATION_DEPTH` env default), and in-loop director replan
# (LoopContext.director_budget_ceiling below). These are distinct counters
# on distinct mechanisms (cross-loop restart chains vs an in-loop replan
# budget) but were three independently-drifted magic numbers — 4 / <3 / 2
# — that don't share a constant (docs/BACKEND_RESILIENCE_DESIGN.md "three
# depth caps"; unified per GOAL_BRAIN Decisions 2026-07-12, backend-
# resilience ratification: "depth-cap inconsistency to be fixed to one
# documented number"). One number so an operator reasoning about "how many
# times will Maro retry before giving up" only has to know one value.
# tests/test_depth_cap_unified.py tripwires every site against this.
#
# Only handle.py's two restart gates are hard-locked to this constant.
# `MARO_MAX_CONTINUATION_DEPTH` remains an intentional env override (an
# operator can still set it to something other than 3, independent of the
# restart gates) — the unification made the *default* shared, not the
# override mechanism itself, which pre-dates this change and is documented
# in maro-doctor's output (adversarial review 2026-07-12).
MAX_RESTART_DEPTH = 3


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
    # Explicit top-level request provenance. These travel with the loop rather
    # than being rediscovered from ambient run-dir context at finalize.
    measurement_class: str = ""
    handle_id: str = ""

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
    cost_warned: bool = False  # per-run cost-approaching-budget warn-once flag

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
    # data-r2-01: caller (handle.py) runs closure after this loop and promises
    # to call finalize_deferred_learning() — finalize skips lesson extraction
    # and skill crystallization so they can run verdict-aware instead of blind.
    defer_learning: bool = False

    # Adaptive execution (Phase 64)
    steps_since_last_check: int = 0
    director_replan_count: int = 0
    director_budget_ceiling: int = MAX_RESTART_DEPTH

    step_callback: Optional[Callable] = None
    channel: Any = None  # Optional ConversationChannel for mid-loop escalation (Phase 64C)
    interrupt_queue: Any = None
    hook_registry: Any = None
    perm_ctx: Any = None

    # Computed during init
    ancestry_context: str = ""
    started_at: float = 0.0
    start_ts: str = ""
    loop_timeout_secs: Optional[float] = None
    repo_path: str = ""  # optional target repo path for stack context injection
    project_slot: Optional[Any] = None  # held admission slot (interrupt.ProjectSlot); released at finalize
    run_worktree: Optional[Any] = None  # busy_policy=worktree isolation (worktree.Worktree); merged at finalize
    container_clone: Optional[Any] = None  # containerized self-dev scratch clone (worktree.ScratchClone); merged at finalize


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
