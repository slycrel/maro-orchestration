# Core Loop

The central autonomous execution loop. Takes a goal, decomposes it into steps, executes each step, learns from outcomes.

## Entry Points

The three ways execution can enter the core loop, depending on goal type and routing.

- **NOW lane** — immediate single-shot execution via [[intent-classification]]
- **AGENDA lane** — multi-step mission with memory + ancestry context
- **Director** — high-level planner that delegates to workers via [[worker-agents]]

## Key Source Files

Python modules that implement the core loop and its supporting pipeline.

- `src/agent_loop.py` — `run_agent_loop()`: main loop entry, interrupt handling, [[checkpointing]]
- `src/loop_init.py`, `src/loop_planning.py`, `src/loop_parallel.py`, `src/loop_execute.py`, `src/loop_post_step.py`, `src/loop_blocked.py`, `src/loop_artifacts.py`, `src/loop_finalize.py`, `src/loop_types.py` — the physical phase split of the loop (2026-07): init → planning → parallel → execute → post-step / blocked-step handling → artifact checks → finalize; shared dataclasses in loop_types
- `src/handle.py` — entry point; routes to NOW or AGENDA via [[intent-classification]]
- `src/director.py` — mission planning; decomposes goals into milestones, delegates to workers
- `src/planner.py` — `decompose()`: multi-plan generation (3 candidates → best composite)
- `src/step_exec.py` — individual step execution; tool dispatch, [[constraint-system]] enforcement

## Execution Flow

High-level data flow from entry point through decomposition, step execution, and memory recording.

```
handle.py → intent.py → [NOW: agent_loop directly] [AGENDA: director → workers]
agent_loop: decompose → [step_1, step_2, ...] → execute each → checkpoint → memory
```

## Session Continuity

Mechanisms that preserve state and context across steps and between sessions.

- [[checkpointing]] — written after each step; loop resumable on stuck/partial
- [[memory-system]] — outcomes + lessons recorded after every loop

## Related Concepts

Other subsystems that the core loop directly invokes or feeds into.

- [[quality-gates]] — quality gate fires after loop completion
- [[worker-agents]] — director and workers are the agents doing the work
- [[self-improvement]] — evolver and thinkback use loop outcomes as input
- `docs/research/productive_persistence.md` — persistence model: `stuck_streak`, tiered retry budget, failure taxonomy, and gap analysis for the retry/escalation ladder
