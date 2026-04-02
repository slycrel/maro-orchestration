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

## Pending: Promotion Cycle (Phase 56)

The gap Poe has: lessons recorded but not systematically promoted to standing rules applied by default.

**Implementation plan:**
- `src/memory.py` — add `promote_lesson()` + hypothesis tracking (observation → 2+ confirmations → standing rule; contradiction demotes)
- `src/memory.py` — add decision journal: ADR-style log searched before new decisions
- `src/evolver.py` — wire promotion cycle into meta-improvement loop
- `src/inspector.py` — quality gate trigger history → self-tightening (triggers promote, never-fires prune)

## Related Concepts

Systems that feed into or consume the memory pipeline.

- [[core-loop]] — memory injected into every decompose call via `inject_lessons_for_task()`
- [[self-improvement]] — evolver + thinkback use memory as input
- [[checkpointing]] — separate from memory; step-level intra-loop state, not loop-level outcomes
- [[quality-gates]] — inspector feeds failure notes into lesson extraction
