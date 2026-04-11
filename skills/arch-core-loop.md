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
  → F: main loop (inline)     — iterate steps: execute, verify, handle blocked/done
  → G: _build_result_and_finalize() — aggregate outcomes, record to memory, return LoopResult
```

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

- Blocked step → decide: retry (with hint), split (into sub-steps), or terminal
- Tier escalation: cheap → mid → power on consecutive failures (Phase 57)
- Session-level floor: 3+ consecutive verify failures raises baseline model for all remaining steps
- Ralph verify (optional): post-execution verifier on cheaper model

## Milestone Expansion (Phase 58)

Pre-flight flags steps that are really sub-goals. At execution time, those steps get re-decomposed into 5 sub-steps before running. Depth-gated at continuation_depth==0.

## Known Gaps

- Phase F (main execute loop) still inline in run_agent_loop() — not yet extracted
- Checkpoint resume exists but isn't auto-triggered on crash
- Budget ceiling creates continuation tasks but doesn't auto-enqueue them
- Parallel fan-out is conservative (heuristic independence check only)

## File Map

| File | Lines | Role |
|------|-------|------|
| src/agent_loop.py | ~3938 | Core loop, all 7 phases |
| src/step_exec.py | ~400 | Single step execution |
| src/planner.py | ~350 | Goal decomposition + scope estimation |
| src/pre_flight.py | ~320 | Plan review + multi-lens |
