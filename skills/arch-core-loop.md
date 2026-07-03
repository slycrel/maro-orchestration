---
name: arch-core-loop
description: Architecture context for working on the core execution loop (agent_loop, planner, step_exec, pre_flight)
roles_allowed: [worker, director, researcher]
triggers: [agent_loop, core loop, execution, decompose, step execution, pre-flight, planner]
always_inject: false
---

# Core Loop Architecture

The core loop takes a goal and autonomously decomposes → executes → introspects.

## Flow (7 phases)

```
run_agent_loop(goal, adapter, ...)
  → A: _initialize_loop()     — build adapter, create project, load ancestry
  → B: _decompose_goal()      — break goal into steps via planner.decompose()
  → C: _preflight_checks()    — cheap plan review, DAG parsing, checkpoint resume
  → D: _run_parallel_path()   — if steps are independent, fan-out via ThreadPoolExecutor
  → E: _prepare_execution()   — shape steps (split compound exec+analyze), write manifest
  → F: _execute_main_loop()   — iterate steps: execute, verify, handle blocked/done
  → G: _build_result_and_finalize() — aggregate outcomes, record to memory, return LoopResult
```

All 7 phases (A–G) are module-level functions taking `LoopContext`; `run_agent_loop()` (in the `agent_loop.py` facade) is the thin orchestrator that sets the phase and calls each in turn. `_execute_main_loop()` returns a dict of terminal loop state (outcomes, status, token totals, mutated manifest/replan/goal/max_iterations) consumed by Phase G and the auto-recovery re-run.

Since the 2026-07-02 physical split, the phases live in `loop_*.py` modules (see File Map); `agent_loop.py` re-exports the public names, so `from agent_loop import X` keeps working — import from the facade unless you're editing loop internals.

## Key Data Structures

- **LoopContext** (mutable state bundle): loop_id, project, goal, step_outcomes, remaining_steps, adapter, phase, token totals. Passed to all phase methods.
- **LoopResult** (return value): steps, status (done/stuck/interrupted/error), token totals, elapsed_ms, pre_flight_review, march_of_nines_alert.
- **StepOutcome**: index, text, status (done/blocked/skipped), result, confidence, tokens, injected_steps.
- **LoopPhase**: String constants (INIT, DECOMPOSE, PRE_FLIGHT, PARALLEL, PREPARE, EXECUTE, FINALIZE).

## Decomposition (planner.py)

Goal scope determines strategy:
- **Narrow** (≤15 words, simple): single LLM call → 1-4 steps
- **Medium**: multi-plan comparison (3 candidates, pick best) → 6-12 steps
- **Wide/Deep**: staged-pass decomposition → domain-specific passes

Injects into decompose prompt: skills library, prior lessons, cost estimates, lat.md knowledge, standing rules, user CONTEXT.md.

## Step Execution (step_exec.py)

Each step: build user_msg (goal + step + completed_context + injected_context) → call adapter.complete() with EXECUTE_SYSTEM prompt + tools (complete_step, flag_stuck, web_fetch) → parse tool call response.

Completed context: last 3 steps full, older compressed. Prevents context snowball.

## Pre-Flight (pre_flight.py)

Cheap plan criticism (one Haiku call). Returns PlanReview: scope (narrow/medium/wide), assumption flags, milestone candidates (sub-goals disguised as steps).

**Important:** Uses its own adapter (NOT the main loop adapter). Tries openrouter/anthropic backends only — never subprocess (hangs during interactive sessions).

## Retry & Recovery

- Blocked step → decide: retry (with hint), split (into sub-steps), or terminal (`loop_blocked._handle_blocked_step` → `_BlockDecision`)
- MISSING_INPUT guard: a missing-external-input block on an input-consuming step short-circuits to an honest stuck (no fabricated-input recovery)
- Navigator blocked-step act (2026-07-03 cutover): a high-confidence (≥0.9) navigator **escalate** overrides a forward recovery decision with an honest stop — escalate-only, config-gated `navigator.act_blocked_step`, logs `NAVIGATOR_ACTED` + Telegram escalation
- Tier escalation: cheap → mid → power on consecutive failures (Phase 57)
- Session-level floor: 3+ consecutive verify failures raises baseline model for all remaining steps
- Ralph verify (optional): post-execution verifier on cheaper model

## Milestone Expansion (Phase 58)

Pre-flight flags steps that are really sub-goals. At execution time, those steps get re-decomposed into 5 sub-steps before running. Depth-gated at continuation_depth==0.

## Known Gaps

- Checkpoint resume exists but isn't auto-triggered on crash
- Budget ceiling creates continuation tasks but doesn't auto-enqueue them
- Parallel fan-out is conservative (heuristic independence check only)

## File Map

Physical split 2026-07-02 (docs/REFACTOR_PLAN.md): `agent_loop.py` is a
facade; each phase group is its own module.

| File | Lines | Role |
|------|-------|------|
| src/agent_loop.py | ~550 | Facade: `run_agent_loop()` orchestrator, loop-entry cwd fence, re-exports |
| src/loop_types.py | ~305 | LoopContext / LoopResult / StepOutcome / LoopPhase |
| src/loop_init.py | ~340 | Phase A: budget gate, adapter build, project creation, ancestry |
| src/loop_planning.py | ~650 | Phases B/C/E: `_build_loop_context`, `_decompose_goal`, `_preflight_checks`, `_prepare_execution` |
| src/loop_parallel.py | ~515 | Phase D: independence check + ThreadPoolExecutor fan-out |
| src/loop_execute.py | ~1250 | Phase F: `_execute_main_loop` step iteration |
| src/loop_blocked.py | ~930 | Blocked-step recovery: `_handle_blocked_step`, `_BlockDecision`, MISSING_INPUT guard, navigator shadow tap + escalate act |
| src/loop_post_step.py | ~830 | Post-step: budget ceiling, ralph verify, done-step processing, interrupts, march-of-nines, iteration artifacts, `_record_loop_decision` |
| src/loop_finalize.py | ~525 | Phase G: result build, memory record, per-run statistical scans |
| src/loop_artifacts.py | ~230 | Artifact/manifest/loop-log writers, `_goal_to_slug` |
| src/step_exec.py | ~1340 | Single step execution |
| src/planner.py | ~610 | Goal decomposition + scope estimation |
| src/pre_flight.py | ~510 | Plan review + multi-lens |
