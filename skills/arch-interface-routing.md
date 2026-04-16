---
name: arch-interface-routing
description: Architecture context for goal entry, classification, routing, director, workers, personas
roles_allowed: [worker, director, researcher]
triggers: [handle, intent, director, workers, persona, routing, NOW lane, AGENDA lane, prefix, dispatch]
always_inject: false
---

# Interface & Routing Architecture

How goals enter the system, get classified, and reach the right execution path.

## Goal Flow

```
Goal arrives (Telegram / Slack / CLI / Python API / Dashboard)
  → handle.py: parse prefixes, classify intent
    → NOW lane: _run_now() → single LLM call → response
    → AGENDA lane:
      → Clarity check (ambiguous? ask user first, unless yolo mode)
      → BLE rewrite (strip imperative steps → outcome-focused goal)
      → Completion standard injected (user/COMPLETION_STANDARD.md)
      → Director (if complex): plan → delegate to workers → review → compile
      → OR skip-director (if simple ≤15 words): straight to agent_loop
      → run_agent_loop() → LoopResult
      → Director closure check: generate verification commands → run → interpret
          → emits verification event (check results) + needs_work event (gaps)
      → Quality gate (optional multi-pass review)
      → Format result → return HandleResult
```

## Director Closure Check (verify_goal_completion)

Post-loop gate that answers "was the goal actually achieved?" — distinct from:
- **Ralph verify** (step-level: did this step address its own goal?)
- **Inspector** (friction detection: is the loop struggling?)
- **Quality gate** (skeptic review of final output)

The closure check is **goal-level** and uses **real exit codes**, not LLM judgment:

1. Director generates 2–5 executable shell commands for the goal type
   (e.g. for a Go port: `go build ./...`, `test -f cmd/server/main.go`)
2. System runs them mechanically — exit 0 = pass, anything else = fail
3. Director interprets the results and declares complete/incomplete + specific gaps
4. If incomplete: `needs_work` event surfaces gaps to channel (continue UI appears)
5. If complete: `verification` event confirms what was checked

**Non-fatal by design** — any exception returns complete=True, never blocks.
**Research/writing goals** return no checks and are skipped automatically.

Key insight: the loop's "done" status means it ran out of steps it could take,
not that the goal was satisfied. The director is the only entity that holds the
original intent and should be the one signing off on completion.

## Director Adaptive Supervision (director_evaluate — Phase 64)

The director also acts as a **persistent supervisor** mid-execution, not just at start and end.
Gated by `adaptive_execution: true` in config (default off).

```
director_evaluate(goal, eval_ctx, trigger) → DirectorDecision
```

**Trigger points** (all call the same function, same decision space):
- `stuck` — fires inside `stuck_streak >= 2` block, before existing advisor
- `verify_failure` — fires when `session_verify_failures >= 2`
- `step_threshold` — fires every K=5 steps unconditionally

**Decision space:**

| Action | What happens |
|--------|-------------|
| `continue` | proceed as-is |
| `adjust` | replace remaining steps tail with director's revised list |
| `replan` | call `planner.decompose()` with `new_approach` as ancestry; replace steps |
| `restart` | break loop with `loop_status="restart"`; handle.py re-runs with context injected |
| `escalate` | call `channel.ask(user_question)` mid-loop; reply injected as next-step context |

**Budget enforcement:** `director_replan_count >= director_budget_ceiling` (default 2) → replan/restart clamped to continue. Counter persists across restarts (on LoopContext).

**Restart re-entry:** handle.py detects `loop_result.status == "restart"`, appends restart context to ancestry, increments `continuation_depth`, re-runs (capped at depth 3).

## Two Lanes

**NOW**: Single-shot response. Simple factual or generative requests. No decomposition, no memory recording, no quality gate. Fast (<10s).

**AGENDA**: Multi-step autonomous execution. Full pipeline: decompose → execute → introspect → learn. Memory recording, quality gate, retry escalation. Slow (30s–30min).

Classification: LLM-based with heuristic fallback. `force_lane` parameter overrides.

## Magic Prefixes

Prefixes mutate execution behavior without changing the goal text. Applied in handle.py before classification.

| Prefix | Effect |
|--------|--------|
| `direct:` | Skip director, skip quality gate, straight to agent_loop |
| `btw:` | Observation only, no execution |
| `effort:low/mid/high` | Force model tier |
| `verify:` / `ralph:` | Add post-step verification |
| `strict:` | Higher quality thresholds |
| `pipeline:` | DAG validation mode |
| `team:` | Multi-worker coordination |
| `garrytan:` | Force power tier + specific persona |
| `mode:thin` | Route to factory_thin loop |

Prefixes stack (non-exclusive) except effort levels (first wins). Registry in handle.py `_PREFIX_REGISTRY`.

## Director (director.py)

Plans work, delegates to specialized workers, reviews output. Three-phase:
1. **Spec**: LLM produces approach + worker tickets
2. **Dispatch**: Assign to workers (research/build/ops/general)
3. **Review**: Check output against requirements, may revise (up to 2 rounds)

**In practice:** Mostly bypassed via `skip_if_simple=True`. Director overhead (3+ LLM calls) only justified for complex multi-worker tasks.

## Workers (workers.py)

Specialized executors with constrained tool access:
- **Research**: Multi-source synthesis with citations
- **Build**: Minimal, testable implementations
- **Ops**: Infrastructure with safety-first diagnostics
- **General**: Miscellaneous tasks

Worker dispatch is stateless — each ticket is independent.

## Persona System (persona.py)

Composable agent identities from YAML + markdown files. Each spec defines:
- Role, model tier, tool access, memory scope
- Communication style, system prompt sections
- Composition rules (e.g., researcher + skepticism merge prompts)

**Gap:** Personas exist but aren't auto-selected based on goal type. The vision (Phase 62 candidate) is bundled persona+skill packaging where the system picks the right identity for the job.

## File Map

| File | Lines | Role |
|------|-------|------|
| src/handle.py | ~1104 | Unified entry point, prefix registry, lane dispatch |
| src/intent.py | ~230 | NOW/AGENDA classification |
| src/director.py | ~1590 | Plan-delegate-review hierarchy + adaptive supervision |
| src/workers.py | ~388 | Specialized executors |
| src/persona.py | ~1218 | Composable identities |
