# Adaptive Execution Design — Director as Persistent Supervisor

**Status:** Design (pre-implementation, post-review)
**Phase:** 64 candidate
**Context:** Emerged from slycrel-go port analysis — loop declared "done" after phase 1 of N, no structural mechanism to catch premature completion or adapt approach mid-run.

---

## Problem

The current execution model has three structural gaps:

**1. Static plan.** `planner.decompose()` generates a plan in one LLM call before any execution. The plan is a hypothesis about what "done" looks like, written with zero information about what execution will discover. Later steps often reveal that earlier assumptions were wrong, but the remaining plan doesn't update.

**2. "Done" means the loop ran out of steps, not that the goal was achieved.** The loop self-reports completion. There's no external check mid-run that asks "is this approach actually converging toward the original goal?"

**3. Director involvement is bookended.** The director plans at the start and the closure check runs at the end. Between those two points, the loop executes without director oversight. Failure or misdirection mid-run has no escalation path until the loop finishes.

---

## Core Design

Make the director a **persistent supervisor** with one unified decision function, called at multiple points during execution:

```
director_evaluate(goal, eval_ctx, trigger) → DirectorDecision
```

Same question at every callsite: *"Given where we are and what we know, what should happen next?"*

Same decision space everywhere:

| Action | Meaning |
|--------|---------|
| `continue` | Current approach is fine, proceed |
| `adjust` | Sharpen remaining steps based on discoveries (tactical) |
| `replan` | Step back, reconceptualize approach from current state (strategic) |
| `restart` | Current work isn't worth preserving, start fresh |
| `escalate` | Human decision required — surface to user via channel |

**Default is autonomous.** The director makes the call. `escalate` is for genuine decision points only — conflicting goals, irreversible actions, ambiguity that can't be resolved from context.

---

## EvaluationContext (new lightweight struct)

The `current_state` passed to `director_evaluate` is **not** the full `LoopContext`. It is a compact, serializable snapshot of what the director actually needs:

```python
@dataclass
class EvaluationContext:
    goal: str                        # original goal, immutable
    current_pass_scope: str          # for staged-pass goals: the current pass's scope
    steps_completed: List[str]       # step descriptions only (not full results)
    steps_remaining: List[str]       # remaining plan tail from current cursor
    step_results_summary: str        # last 3 *completed step results*, first 600 chars each
    verify_failure_count: int        # consecutive ralph verify failures (resets to 0 on first pass)
    total_steps_taken: int
    max_steps: int
    current_approach: str            # from ExecutionPlan if available, else ""
    convergence_budget_remaining: int  # steps until next director check is mandatory
```

**`step_results_summary` unit:** the last 3 completed step result strings, each truncated to 600 chars, joined with separators. Not tool calls or LLM turns — completed step outputs only. Access via `StepOutcome.result` field (typed object built by `step_from_decompose()`). Filter to `status == "done"` outcomes only.

**`verify_failure_count` reset rule:** resets to 0 on the first passing ralph verify. A single pass zeroes it — prevents flaky verifiers from permanently elevating director call frequency.

`convergence_budget_remaining` is computed as `director_budget_ceiling - director_replan_count`. In Phase A both are inert (0 and 2 respectively), so this field always reads 2. The LLM prompt should label it informational-only in Phase A to avoid confusing the model with a value it can't act on.

This keeps the LLM context small and the interface stable regardless of LoopContext internals.

---

## DirectorDecision (new dataclass)

```python
@dataclass
class DirectorDecision:
    action: str                           # continue | adjust | replan | restart | escalate
    reasoning: str                        # one sentence — logged + shown in channel
    revised_steps: Optional[List[str]]    # populated for 'adjust' — replaces remaining tail
    new_approach: Optional[str]           # populated for 'replan' — narrative description
    restart_context: Optional[str]        # populated for 'restart' — what was learned
    user_question: Optional[str]          # populated for 'escalate'
    next_check_in: int = 3                # steps before next mandatory director check
```

**`adjust` semantics:** replaces the remaining steps tail from the current cursor forward — no splicing, no interaction with parallel execution. Clean replacement only. If `revised_steps` is empty or None on an `adjust` action, treat as `continue` (not as "done", not as error). Implementation note: `remaining_steps` and `remaining_indices` are kept in sync in `LoopContext` — when `adjust` replaces `remaining_steps`, `remaining_indices` must be rebuilt to match (sequential indices from `len(step_outcomes)` onward). The existing `remaining_indices` manipulation pattern (used by interrupt injection) is the reference for how to do this safely.

**`next_check_in`:** the director sets the interval to the next check. Floor: 1. Ceiling: steps remaining. If the director returns 0 or a negative value, clamp to 1. This is NOT the convergence budget counter — it is a suggestion. The actual counter lives in `LoopContext` (see below).

---

## Convergence Budget — Ownership and Persistence

**The budget counter lives in `LoopContext`, not in `DirectorDecision`.**

```python
# In LoopContext:
director_replan_count: int = 0     # increments on each 'replan' or 'restart' action
director_budget_ceiling: int = 2   # max replans before escalation is forced
steps_since_last_check: int = 0    # increments each step, resets on director check
```

Rules:
- Each `replan` action: `director_replan_count += 1`
- Each `restart` action: `director_replan_count += 1` (restart counts as a replan)
- If `director_replan_count >= director_budget_ceiling`: next director decision **must** be `escalate` or `continue` — `replan` and `restart` are disallowed
- A `restart` does **not** reset `director_replan_count` — it persists across restart boundaries
- `director_budget_ceiling` is configurable; default 2 (one replan + one restart before escalation)

This is what makes the anti-meandering guarantee real: the counter survives restarts.

---

## Trigger Points

Three callsites, same function, different trigger context:

### 1. Mid-execution — on signals (Phase A, opt-in)
Called from `agent_loop.py` during the step loop. Gated behind config flag `adaptive_execution` (default: off), following the same opt-in pattern as `ralph_verify`. When enabled, fires on:
- `ctx.session_verify_failures >= 2` — field already exists on `LoopContext` (line 243). The local `_session_verify_failures` shadows it in the execute section and must be synced back to `ctx` each iteration so the trigger can read it.
- `ctx.steps_since_last_check >= K` (default 5) — field must be added to `LoopContext`, incremented in the main while-loop alongside `stuck_streak`.
- `ctx.stuck_streak >= 2` — use `stuck_streak` (already on `LoopContext` line 233) as the mid-loop stuck signal. **Not** `loop_status == "stuck"`: that value is written at line 3674 immediately before `break` at 3683 — the loop has already exited and no mid-loop call is possible. `stuck_streak` is visible and incrementing before the break decision is made.

**`steps_since_last_check` reset rule:** resets to 0 after any director call, regardless of the decision returned. Prevents an `adjust` with no meaningful changes from immediately re-triggering on the next step.

For Phase A: only `continue` and `adjust` actions are wired. `replan`, `restart`, `escalate` are deferred to Phase C.

### 2. Strategic threshold — periodic convergence check (Phase B)
Every K steps unconditionally (not just on failure signals). Evaluates whether the approach is still converging toward the original goal. Can return `adjust` or `replan`. Introduces `ExecutionPlan` approach metadata.

### 3. Closure — after loop returns "done" (Phase C, unify interface)
Currently `verify_goal_completion()` in `director.py`. This becomes a `DirectorDecision` with action `continue` (goal achieved) or `adjust`/`replan` (gaps found, needs more work). The `ClosureVerdict` dataclass is retired.

---

## Restart Semantics

When `action == "restart"`:
- Original goal is preserved
- `restart_context` carries what was learned (failed approach, discoveries made)
- `director_replan_count` increments (does not reset)
- Loop re-enters via a new `run_agent_loop()` call with `restart_context` injected as `ancestry_context_extra`
- `continuation_depth` increments — restart is treated as a continuation, not a reset, so the escalation ceiling still applies
- The channel receives a `restart` event showing reasoning and what was preserved
- **What is NOT preserved:** tool state, subprocess state, file handles. Only text context survives. Side effects already applied (files written, commits made) remain as-is on disk — the new loop starts with awareness of them via `restart_context`, not by inheriting any live state.
- **`continuation_depth` ceiling:** the existing ceiling that terminates deep continuation chains applies to restarts. If the ceiling is too low for a goal that legitimately needs multiple restarts, raise `director_budget_ceiling` rather than relaxing `continuation_depth` — these are separate concerns.

---

## "Goal is Immutable" — Staged-Pass Clarification

For staged-pass goals (large-scope review, multi-phase plans), the director evaluates against the **current pass scope**, not the root goal. The root goal remains immutable; the current pass scope is what's being executed and can be adjusted.

If the director determines the pass scope itself is wrong, that is an `escalate` — not a `replan`. Replanning stays within the current pass scope.

---

## Plan Metadata — Phased Introduction

**Phase A:** No `ExecutionPlan` struct. Operates on the existing `List[str]` remaining steps in `LoopContext`. `current_approach` in `EvaluationContext` defaults to `""`.

**Phase B:** Introduce `ExecutionPlan` as a wrapper around the step list. `planner.decompose()` returns `ExecutionPlan` instead of `List[str]`. All callers updated. `approach` and `phase` fields populated.

Reason for deferral: changing `planner.decompose()`'s return type touches the most-called path in the codebase. Phase A should not require this change.

---

## Cost Model

Phase A adds at most one cheap-model LLM call per director check. With default `K=5` steps between checks and `max_steps=40`, that is at most 8 director calls per run. Disabled by default — same opt-in pattern as `ralph_verify`.

Phase B adds checks unconditionally when enabled. Phase C adds one call at closure (already exists as `verify_goal_completion`).

---

## What This Replaces / Extends

| Existing mechanism | Relationship |
|---|---|
| `verify_goal_completion()` | Phase C: becomes `director_evaluate(trigger="closure")` |
| Ralph verify (per-step) | Trigger signal for director check, not replaced |
| Inspector friction | Additional trigger signal |
| Quality gate | Unchanged — output quality, not goal completeness |
| `handle_escalation()` | Becomes the `escalate` action path |
| `LoopContext` | Gains `director_replan_count`, `director_budget_ceiling`, `steps_since_last_check` |

---

## Known Limitations (accepted)

**The director sees step descriptions, not step objects.** `adjust` asks the director to emit a replacement step list based on string descriptions only — no step IDs, no dependency metadata, no internal structure. This is intentional: keeping the interface simple and the LLM context small. The risk is that the director may produce steps that duplicate or conflict with already-completed work. Mitigation: `steps_completed` is included in `EvaluationContext` so the director knows what has already been done. In Phase A (tactical sharpening only), this risk is low — the director is refining, not restructuring. In Phase B+ (replan), the director produces a fresh approach from scratch, so duplication is less of a concern than coherence.

## Phased Build

**Phase A (immediate):**
- `EvaluationContext` and `DirectorDecision` dataclasses
- `director_evaluate()` function in `director.py` — Phase A only wires `continue` and `adjust`
- Trigger in `agent_loop.py` on verify failure streak or step threshold
- Gated by `adaptive_execution` config flag (default off)
- `director_replan_count` and `director_budget_ceiling` added to LoopContext but **inert** in Phase A — wired but not enforced until Phase B ships `replan`. No test coverage of budget enforcement in Phase A.
- Tests

**Phase B:**
- Strategic threshold check every K steps
- `ExecutionPlan` metadata struct + planner changes
- Wire `replan` action

**Phase C:**
- Wire `restart` and `escalate` actions
- Unify `verify_goal_completion` → `director_evaluate(trigger="closure")`
- `restart` re-entry with `continuation_depth` increment

**Phase D:**
- Memory layer: record approach + outcome per goal type
- Director uses history to select initial approach

---

## Resolved Questions

| Question | Resolution |
|---|---|
| What is `current_state`? | `EvaluationContext` — compact snapshot, defined above |
| `adjust` mutation semantics? | Replaces remaining step tail from cursor, no splicing |
| What counts as a discovery signal? | Phase A: fire on verify failure streak or step threshold; director decides `continue` vs `adjust` |
| Where does convergence budget live? | `LoopContext` — survives restarts, not on `DirectorDecision` |
| How does `restart` interact with `continuation_depth`? | Increments depth — treated as continuation, not reset |
| Should Phase A be opt-in? | Yes — `adaptive_execution` config flag, default off |
| When to add `ExecutionPlan` metadata? | Phase B only — Phase A operates on existing `List[str]` |
| "Goal immutable" vs staged-pass? | Director evaluates against current pass scope; changing pass scope = escalate |
