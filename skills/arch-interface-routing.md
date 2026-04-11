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
Goal arrives (Telegram / Slack / CLI / Python API)
  → handle.py: parse prefixes, classify intent
    → NOW lane: _run_now() → single LLM call → response
    → AGENDA lane:
      → Clarity check (ambiguous? ask user first, unless yolo mode)
      → Director (if complex): plan → delegate to workers → review → compile
      → OR skip-director (if simple ≤15 words): straight to agent_loop
      → run_agent_loop() → LoopResult
      → Quality gate (optional multi-pass review)
      → Format result → return HandleResult
```

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
| src/intent.py | ~200 | NOW/AGENDA classification |
| src/director.py | ~500 | Plan-delegate-review hierarchy |
| src/workers.py | ~400 | Specialized executors |
| src/persona.py | ~350 | Composable identities |
