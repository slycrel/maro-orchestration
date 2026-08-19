---
status: living
---

# Run Trace — the edges, written down

**What this is.** Every transition a run takes now appends one row to that
run's own `build/trace.jsonl`, at the moment it is taken. `src/run_trace.py`
owns the writer and the node vocabulary.

Jeremy, 2026-08-18: *"add all of the edges that aren't written down into the
system as tracked metadata or artifacts."* Before this, the pipeline recorded
its **nodes** — a step ran, a verdict was stamped, a gate fired — but almost
none of its **edges**. Which way a run went at a branch had to be inferred
afterwards from artifact presence plus a captain's-log slice that is not even
run-scoped. That inference gets the shape right, the ordering wrong, and it
cannot tell "this edge was not taken" from "this edge was taken and nothing
recorded it".

## The row

```json
{"ts": "...", "loop_id": "83f98e68", "from": "exec.blocked", "to": "exec.retry",
 "attrs": {"step_idx": 4, "retries": 1, "navigator_overrode": false}}
```

**Append order is the record — there is deliberately no sequence number.** A
row's position in the file is when it actually happened. This matters because
of the answer-first split (below), where wall-clock order and source order
disagree and only one of them is true.

## What is recorded, and where

| Edge family | Instrumented at | Why there |
|---|---|---|
| **Phase track** (`phase.init → … → phase.finalize`) | `loop_types.LoopStateMachine.set_phase` | The one place every legal transition passes through; it cannot drift from the state machine it lives in. |
| **Pre-start refusals** (`plan.killswitch`, `plan.budget_gate`, `plan.busy_refused`) | `loop_init._stamp_refusal_verdict` | All three refusals already funnelled through this one helper. |
| **Cost-estimate abort** (`plan.cost_gate`) | `loop_planning._preflight_checks` | It was the only terminal in the pipeline leaving *no* durable record at all. |
| **Per-step execution** (`phase.execute → exec.step`) | `loop_execute`, after `step_status` is read | After every demotion has landed, so the recorded status is the final one. |
| **step_exec demotions** (async-escape, unprobed env claim, deliverable-path miss) | same site, one layer up from `step_exec` | `execute_step` has no ctx, no loop_id and no run-dir; their only other trace is a tag prefix on `stuck_reason`. |
| **Write fence, fabrication** | `loop_execute`, at each demotion | Distinct causes; collapsing them loses why a done step became blocked. |
| **Blocked-step recovery** (`retry` / `redecompose` / `split` / `stuck`) | `loop_blocked._process_blocked_step`, after the navigator override | The whole recovery subgraph funnels here. Placed after the override so it records the final decision, not the heuristic's proposal. |
| **Recovery return edges** (`exec.retry → exec.step`, …) | the three re-queue sites | The re-queue itself was invisible: the blocked outcome persisted, nothing said the step went back around. |
| **Named terminals** (`exec.timeout`, `exec.missing_input`, `exec.budget_break`) | `loop_blocked` terminal block | Distinct dead ends the recovery tree reached deliberately. |
| **Parallel fan-out** (`phase.parallel → exec.parallel → fin.*`) | `agent_loop`, at the early return | See "the parallel path" below. |
| **Auto-recovery** (`fin.diagnose → fin.auto_recovery`) | `agent_loop`, before the recursive re-run | The child inherits this run-dir, so without the marker its edges look like a continuation of the parent. |
| **Closure decision** (`verify.closure → verify.restart` / `verify.stamp` / `verify.contested`) | `director.evaluate_closure`, at the return | The verdict was persisted; the **decision derived from it** existed only in a `log.info`. |
| **Gate decision** (`gate.verdict → gate.pass` / `gate.escalate`) | `quality_gate.run_quality_gate` | The verdict reached the captain's log but never the run dir. |
| **Terminal** (`close.curate → close.terminal → term.<class>`) | `runs.close_run`, after curation | Once per *actual* close — see the double-close note. |

## Two orderings that look like bugs and are not

**The answer-first split.** When `notify.verdict_followup` is on and a run ends
`done`, `handle.py` stamps a `verdict_pending` marker and runs
`close_run` + `curate_run` + the completion notify **before** closure
verification and the quality gate. So `close_run` fires **twice**: first
stamping `success_class = done-verdict-pending`, then again after the verdict
with the real class. Both rows are true when written, and the trace records
both. The last `term.*` row is the outcome that stands. A reader that assumes
one terminal per run will be wrong on this path — which is exactly why the
recorder does not deduplicate.

**The parallel path bypasses finalize.** A fan-out or DAG run returns early
from `agent_loop` and never reaches `_build_result_and_finalize`. None of the
execute-loop, finalize, or verify edges exist there. Without an explicit record
a fan-out run's trace would stop at `phase.parallel` and read exactly like a
crashed serial run, so the early return records the fan-out and its terminal
with `bypassed_finalize: true`.

## Honesty properties

- **Recording never changes a run.** Every call site is wrapped; `record_edge`
  itself never raises.
- **A dropped edge is announced, not swallowed.** A silently lost edge reads
  downstream as "this run never went there" — a false negative that looks like
  a fact. Failures are counted, and the first one for a run leaves a
  `trace.degraded` marker row in the file. Consumers must treat a trace
  carrying that marker as incomplete rather than authoritative.
- **Unknown node ids are recorded and flagged**, never dropped: losing the row
  is worse, but an unflagged typo would invent a phantom node downstream.
- **String attributes are clipped centrally** through `context_budget.clip`
  (`EVIDENCE_CAP`), which marks what it removed — one announced cut instead of
  bare slices scattered across call sites.
- **Reads use `loads_clean`**, so a byte-tainted row stays announced-as-lost
  rather than being re-serialized into legitimate-looking content, and skipped
  rows are WARNed and counted.

## Where the run-dir is not resolvable

Some transitions genuinely have nowhere to write, and the census that preceded
this work named them rather than leaving them to be discovered later:

- **Dispatch-time refusals** — the recall guard and the dispatch navigator's
  escalate both return `handle_id=""`; no run is ever created. Their only
  durable home remains the captain's log.
- **The heartbeat backlog lane** calls `run_agent_loop` directly with no
  `open_run`, so no run-dir exists for the whole run.
- **`run_parallel_loops` pool threads** deliberately do not copy the context,
  so `current_run_dir()` is unset inside them.
- **Bare library / direct `agent_loop` calls** (including most tests).

In all of these `record_edge` returns False and counts the miss. That is a real
coverage gap, not a silent one — `dropped_count()` exposes it.

## Consumers

`scripts/run_atlas/` reads the trace when a run has one and lights exactly the
edges recorded (drawn solid); runs predating the trace fall back to inferring
edges from node presence (drawn dashed). The node vocabulary is a contract
between `src/run_trace.py` and the atlas topology, pinned by
`tests/test_run_trace.py::test_recorder_and_atlas_share_one_vocabulary` — drift
either way is silent otherwise.

Config: `trace.enabled` (default on) — see `docs/DEFAULTS.md`.

## What this unblocks

BACKLOG's **"Phase Transition Contracts"** item defers typed contracts and hard
gates between phases until "operational data shows which gates actually
matter". This is that data: the trace records which transitions real runs take,
how often each branch fires, and which phases are entered and abandoned.
