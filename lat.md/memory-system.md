# Memory System

Multi-tier memory: episodic outcomes, extracted lessons, skill crystallization, and graveyard recovery.

## Tiers

Four memory tiers from raw run data up to promoted standing skills, each building on the previous.

1. **Outcomes** (`memory/outcomes.jsonl`) — raw loop results; every run recorded
2. **Lessons** (`memory/lessons.jsonl`) — extracted from outcomes via Reflexion-style reflection
3. **Skills** (`src/skills.py`) — promoted from repeated successful lessons; auto-scored, FTS-searched
4. **Graveyard** — decayed lessons resurrected when goal matches

## Key Source Files

Modules implementing memory recording, skill promotion, and failure classification.

- `src/memory.py` — `record_outcome()`, `inject_lessons_for_task()`, `extract_lessons()`
- `src/skills.py` — `SkillLibrary`: auto-promote, score, search, test skills
- `src/evolver.py` — meta-improvement every ~10 heartbeats; scans outcomes for signals
- `src/introspect.py` — failure classification; feeds recovery planner

## Promotion Cycle (Phase 56 — DONE)

Observation → hypothesis (2+ confirmations) → standing rule. Contradictions demote. Decision journal (ADR-style) searched before new decisions.

- `observe_pattern()` — records an observation; promotes to standing rule after 2+ confirmations
- `contradict_pattern()` — demotes a hypothesis; prevents false standing rules
- `inject_standing_rules()` — injects promoted rules into every decompose call
- `record_decision()` / `inject_decisions()` — decision journal; both injected at decompose time

Wired into evolver meta-improvement loop. Standing rules and decisions live in `memory/standing_rules.jsonl` and `memory/decisions.jsonl`.

## Related Concepts

Systems that feed into or consume the memory pipeline.

- [[core-loop]] — memory injected into every decompose call via `inject_lessons_for_task()`
- [[self-improvement]] — evolver + thinkback use memory as input
- [[checkpointing]] — separate from memory; step-level intra-loop state, not loop-level outcomes
- [[quality-gates]] — inspector feeds failure notes into lesson extraction
