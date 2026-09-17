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
  →    _load_resume()         — explicit resume: restore the checkpoint BEFORE planning
                                (suffix = preset plan; carried NEXT.md items verified;
                                other-project / unreadable → refuse)
  → B: _decompose_goal()      — break goal into steps via planner.decompose()
  → C: _preflight_checks()    — cheap plan review, DAG parsing (+ suffix edge remap on resume)
  → D: _run_parallel_path()   — if steps are independent, fan-out via ThreadPoolExecutor
                                (items bound first by _mirror_plan_items; rows carry + mark them)
  → E: _prepare_execution()   — shape steps (split compound exec+analyze), mirror plan to
                                NEXT.md / keep carried items, bind plan numbers → items
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
- Prerequisite gate (`step_gate.py`, 2026-09-16) enforces only DECLARED `[after:]` edges (sequential lane and DAG lane); the sequential-default edge (71.5% of steps on this box, `scripts/prereq-census.py`) is soft unless `execution.gate_implicit_prerequisites` — closing that is item 3 (`docs/PCD_PREREQUISITE_FIELD_DESIGN.md`). A tag's number resolves through the ORIGINAL plan's binding (`LoopContext.plan_items` = plan number → NEXT.md item, persisted verbatim as `Checkpoint.plan_items`), so a resumed suffix keeps its edges; the binding is dropped whole (edges soft) when the plan was reshaped, a fresh run's items are not 1:1, or the carried items no longer name the step texts in NEXT.md (item ids are line offsets — immutable ids are a BACKLOG lead)
- The run that ends a resume settles its source (chunk 9, 2026-09-17): `ctx.resume_claim_release` is a typed `checkpoint.ResumePermit` (source, nonce, source_loop_id; `permit_of`); ONE function `checkpoint.settle_resume_source` — `source_is_settled` (re-read of the PINNED canonical path — never re-resolved, a symlink there is refused: the complete successor overwrote it, or it is consumed naming this successor) else `consume_claimed` (compare-and-consume on the claim nonce under `locked_write(require=True)`, checkpoint-module `atomic_write`, read back) then re-prove — called by whoever makes the LAST status decision: `loop_finalize.settle_resume_claim` at the end of Phase G after the merge-backs (and on the parallel lane's result, and after an auto-recovery child returns, naming the child), or the CLI right after its closure verification and BEFORE deferred learning (`defer_resume_settlement=True`; the run's close status stays `error` until the settlement decides); failure → status `incomplete` + `RESUME_UNSETTLED_REASON` + `external-interrupt`; any other ending keeps the claim as the replay barrier; a dry run cannot resume (refused before any claim)
- A NEXT.md mark a row still owes is durable state settled from the checkpoint (chunk 8, 2026-09-17): `StepOutcome.item_mark` / `CompletedStep.item_mark` ∈ {applied, pending, drifted, attempt}; a terminal row on a real item is born pending unless its producer records the applied mark (attempt rows — retry / re-decompose / split / stuck-advisor — are born `attempt`: not a verdict, they owe nothing and supersede nothing); ONE settler `loop_planning.settle_item_marks` (latest VERDICT row per item; blocked → `!`, done/skipped → `x`) runs before every sequential snapshot, at every parallel snapshot for carried rows, at loop exit when anything is owed or a row was appended after the last snapshot, and by the resume before any step; every settlement is a compare-and-mark (`orch_items.mark_item(expected_text=)` under the ledger lock, `normalize_item_text` identity form) — `ItemIdentityError` → drifted (surfaced, never retried), any other failure → still pending; the parallel lane's checkpoint rows and returned rows share `_row_mark` (this hop's rows only; the carried prefix is not indexed by node)
- A resume CLAIMS its source before it executes (chunk 7, 2026-09-17): `checkpoint.mark_checkpoint_claimed` writes `resume_claim` {handle_id, pid, token, nonce, successor_loop_id, successor_path} into the exact selected file by a digest-keyed compare-and-swap under `file_lock.locked_rmw` (refuses consumed / complete / claim-gated files, validates the claim before writing, fsyncs, reads back) and returns the object carrying the transient permit (`resume_permit` = nonce, `resume_source`) — the ONLY thing the loop admits as a preloaded checkpoint (permit taken atomically, one-shot; exact source re-read once; on-disk nonce must match); `resume_claim_status` → None / live / superseded / unresolved / indeterminate (only a PROVEN-absent successor is reclaimable; `--reclaim` overrides unresolved only; the API path has no override); `_load_resume` admits under the CLI's pidfile lock, probes the source's owner, claims after every refusal check, never for a complete file; every refusal before the first step releases the claim (`finalize_refusal` → `release_checkpoint_claim`, init early return / exception, adapter built before the claim); `branch_checkpoint` refuses a claimed source; the heartbeat surfaces unresolved / indeterminate rows (with `claim_state`, `claim_handle`, `finalized_status`) and never auto-resumes them; a present-but-malformed claim is LOOKUP_INVALID; consumed dominates
- Explicit resume is fail-closed end to end (chunk 6, 2026-09-16): `checkpoint.find_checkpoint(loop_id) -> CheckpointLookup` is the ONE loader (found / absent / invalid / mismatch / io_error; the id-addressed file decides on its own, a damaged run-dir file is attributed by its run dir's metadata and reported as UNATTRIBUTABLE — never absent — when the metadata cannot say; every address is read, never existence-checked); `_load_resume(ctx, id, preloaded=)` starts fresh ONLY on absent and refuses consumed files; `maro resume` resolves handle-first (ref grammar, namespace-collision refusal), hands its validated object in as `run_agent_loop(resume_checkpoint=)`, requires the locked re-read to be the same file and the same snapshot, and after `done` proves the source was overwritten or consumes it in place; every pre-execution refusal (cost gate `out-of-budget`, resume refusal, fence refusal) ends through `loop_finalize.finalize_refusal` (typed verdict stamped, clone + worktree discarded, `release_loop_resources`: slot → lease → running marker, heartbeat woken)
- The DAG / fan-out lane checkpoints after every committed node and marks its item as it commits (chunk 5, 2026-09-16: `_run_steps_dag._commit` → `on_progress` → `_run_parallel_path._write_progress`); the status domain is closed at the executor boundary (`_normalize_outcome`: done/blocked/skipped, else blocked with a reason), a node's effects (decisions, world facts, regression) land BEFORE its row, and `Checkpoint.parallel_fan_out` carries the width so `maro resume` re-enters the DAG lane; it writes no in-flight marker (several nodes are in flight; a missing row re-runs)
- Regression obligations (`regression_ledger.py`) are harvested only in the sequential lane and re-run on the closure host, not through the container executor; a closure plan with zero generated checks skips the re-run

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
