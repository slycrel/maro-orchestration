---
name: arch-memory-knowledge
description: Architecture context for working on memory, knowledge lifecycle, tiered lessons, captain's log, crystallization
roles_allowed: [worker, director, researcher]
triggers: [memory, knowledge, lessons, outcomes, tiered, captain's log, crystallization, standing rules, decisions]
always_inject: false
---

# Memory & Knowledge Architecture

The system's intelligence should compound over time. Every LLM call that answers a question already answered 50 times is waste.

## The Crystallization Path (VISION)

```
Stage 1: Fluid     → Raw LLM reasoning (expensive, flexible)
Stage 2: Lesson    → Extracted pattern in tiered memory (guided LLM, cheaper)
Stage 3: Identity  → Canon in system prompt (always active, zero retrieval cost)
Stage 4: Skill     → Python code (deterministic, testable)
Stage 5: Rule      → Hardcoded path (zero inference cost)
```

**Current reality:** Stage 1→2 works. Stage 2→3 has no automated pathway. Stage 3→4 is manual. Stage 4→5 is conceptual only. This is the biggest gap between vision and implementation.

## Data Stores (all JSONL under `~/.poe/workspace/memory/`)

| File | What | Written by | Read by |
|------|------|-----------|---------|
| outcomes.jsonl | One record per loop run | reflect_and_record() | evolver, inspector, bootstrap |
| medium/lessons.jsonl | Active lessons (decay 15%/day) | record_tiered_lesson() | inject_tiered_lessons() |
| long/lessons.jsonl | Promoted lessons (no decay) | promote_lesson() | inject_tiered_lessons() |
| standing_rules.jsonl | Permanent rules (zero cost) | observe_pattern() → promote | inject_standing_rules() |
| hypotheses.jsonl | Lessons being validated | observe_pattern() | check before promotion |
| decisions.jsonl | ADR-style decision journal | record_decision() | inject_decisions() |
| captains_log.jsonl | Event stream (11K+ entries) | Various — lifecycle events | captain's log read bridge |
| task_ledger.jsonl | Per-step execution trace | record_step_trace() | evolver context |
| verification_outcomes.jsonl | Claim verification history | record_verification() | calibration threshold |
| knowledge_nodes.jsonl | Structured knowledge (K2) | import_link_farm, append_knowledge_node() | query_knowledge(), inject_knowledge_for_goal() |
| knowledge_edges.jsonl | Node relationships (K2) | import_link_farm, append_knowledge_edge() | load_knowledge_edges() |

## Write Flow (after each run)

```
Loop completes
  → reflect_and_record(goal, status, summary)
    → LLM extracts 1-3 typed lessons (execution/planning/recovery/verification/cost)
    → record_outcome() → outcomes.jsonl + daily .md log
    → For each lesson: record_tiered_lesson() → medium/lessons.jsonl
      (confidence 0.5-0.7 depending on k_samples)
    → Captain's log: LESSON_RECORDED event
```

## Read Flow (before/during runs)

```
Loop starting
  → bootstrap_context() → top 5 outcomes + top 10 lessons
  → inject_standing_rules(domain) → promoted rules (zero-cost match)
  → inject_tiered_lessons(task_type) → highest-scoring lessons
  → inject_decisions(goal) → TF-IDF search of decision journal
  → Captain's log bridge → recent lifecycle events
```

## Tiered Memory Model

- **MEDIUM**: Score 0.2–1.0. Decays 15%/day (score *= 0.85^days). New lessons start here at score 1.0.
- **LONG**: Promoted when score ≥ 0.9 AND sessions_validated ≥ 3. No decay (enforced tier-aware since session 40 — earlier code decayed long-tier on load).
- **Standing Rules**: Promoted from long-tier after 2+ pattern confirmations. Zero cost, always active.

Reinforcement: When a lesson is re-confirmed, score += 0.3, sessions_validated++. At threshold: promote to LONG.

**Re-confirmation side effects (session 40 M2, `_post_reinforce_hooks` in knowledge_web.py):** every reinforcement — whether via `reinforce_lesson()` or `record_tiered_lesson()`'s near-duplicate dedup — runs the hooks: a MEDIUM lesson meeting eligibility (score ≥ 0.9, sessions ≥ 3) promotes to LONG *immediately* (the returned lesson's `.tier` changes), and a LONG re-confirmation calls `observe_pattern()` so hypotheses accrue confirmations and standing rules accrete. `record_tiered_lesson(tier=MEDIUM)` also dedups against LONG first — re-learning an already-promoted lesson reinforces the long-tier record instead of creating a medium duplicate. Full accretion path: medium lesson → eligibility at reinforcement → LONG (promote_lesson seeds hypothesis, confirmation 1) → re-learned once more → standing rule (RULE_PROMOTE_CONFIRMATIONS = 2).

**Decay is a read-time derivation, never persisted** (session 40 invariant). The stored score is the score as of `last_reinforced`; the effective score is computed on load. Any code that rewrites a lessons file MUST load with `raw=True, limit=None` — persisting an effective (decayed) score without re-anchoring `last_reinforced` compounds decay, and the default `limit=50` silently truncates larger stores on rewrite.

## Consolidation (the "dream cycle", session 40)

`maybe_consolidate()` in knowledge_web.py runs `run_decay_cycle` (medium tier: promote eligibles, GC effective-score < 0.2) at most once per `memory.consolidation_interval_hours` (default 24h; `memory.consolidation_enabled` to turn off), gated by a `memory/last_consolidation.json` marker. **In-process by design — no cron/daemon** (rogue-process history). Entry points: end of every `handle()` call (try/finally, skipped on dry_run, can never affect the request outcome), every heartbeat tick (even health-only mode — pure local file work), and `poe-memory consolidate [--force]`. Logs a `MEMORY_CONSOLIDATED` captain's-log event. Concurrent double-run is safe: decay is read-derived, promotion is eligibility-gated, GC is idempotent.

## Captain's Log

Append-only event stream tracking knowledge lifecycle:
- LESSON_RECORDED → LESSON_REINFORCED → HYPOTHESIS_CREATED → HYPOTHESIS_PROMOTED → STANDING_RULE_CONTRADICTED
- Read bridge (K3 partial): recent events injected into decompose + evolver prompts

## Test Coverage

- **knowledge_web.py**: 103 tests in test_knowledge_web.py (session 17) — covers decay, reinforcement, TF-IDF ranking, tiered lessons CRUD, near-duplicate detection, graveyard search, prompt injection formatting.
- **playbook.py**: `append_to_playbook()` now rejects empty entries and truncates at 500 chars (session 17).

## Known Gaps (Intent vs Implementation)

1. **No Stage 2→3 pathway.** Canon promotion (10+ applies, 3+ task types) is spec'd but not coded.
2. **No Stage 4→5 pathway.** Skill → rule promotion is conceptual only.
3. **Reinforcement is passive.** Lessons only reinforce when explicitly re-confirmed in a run. System doesn't proactively test its own lessons.
4. **Captain's log reads are coarse.** Dumps recent events rather than targeted retrieval.
5. **Decay works but creates cold-start.** A valid lesson that isn't used for 7 days decays to ~0.32 — it effectively dies even if it's correct. `search_graveyard(resurrect=True)` can wake matches, but nothing calls it proactively.
6. ~~**Promotion timing race.**~~ FIXED (session 40 M2): promotion is now evaluated at reinforcement time (`_post_reinforce_hooks`), when the score is freshly re-anchored. The consolidation-cycle promotion check remains as a backstop but only catches same-day-reinforced lessons (one day of decay drops 1.0 → 0.85, below the 0.9 threshold).

## File Map

| File | Lines | Role |
|------|-------|------|
| src/memory.py | ~545 | Core: outcomes, lessons, injection, reflection |
| src/memory_ledger.py | ~1030 | Task execution traces |
| src/knowledge_web.py | ~1630 | Cross-linked concept nodes, K2 schema/storage/query |
| src/knowledge_lens.py | ~1100 | Focused analysis lenses |
| src/playbook.py | ~240 | Director operational wisdom (append/read) |
| docs/KNOWLEDGE_CRYSTALLIZATION.md | | Design spec (sapling→tree) |
