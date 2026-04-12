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
- **LONG**: Promoted when score ≥ 0.9 AND sessions_validated ≥ 3. No decay.
- **Standing Rules**: Promoted from long-tier after 2+ pattern confirmations. Zero cost, always active.

Reinforcement: When a lesson is re-confirmed, score += 0.3, sessions_validated++. At threshold: promote to LONG.

## Captain's Log

Append-only event stream tracking knowledge lifecycle:
- LESSON_RECORDED → LESSON_REINFORCED → HYPOTHESIS_CREATED → HYPOTHESIS_PROMOTED → STANDING_RULE_CONTRADICTED
- Read bridge (K3 partial): recent events injected into decompose + evolver prompts

## Known Gaps (Intent vs Implementation)

1. **No Stage 2→3 pathway.** Canon promotion (10+ applies, 3+ task types) is spec'd but not coded.
2. **No Stage 4→5 pathway.** Skill → rule promotion is conceptual only.
3. **Reinforcement is passive.** Lessons only reinforce when explicitly re-confirmed in a run. System doesn't proactively test its own lessons.
4. **Captain's log reads are coarse.** Dumps recent events rather than targeted retrieval.
5. **Decay works but creates cold-start.** A valid lesson that isn't used for 7 days decays to ~0.32 — it effectively dies even if it's correct. No mechanism to "wake up" dormant lessons.

## File Map

| File | Lines | Role |
|------|-------|------|
| src/memory.py | ~530 | Core: outcomes, lessons, injection, reflection |
| src/memory_ledger.py | ~943 | Task execution traces |
| src/knowledge_web.py | ~1330 | Cross-linked concept nodes, K2 schema/storage/query |
| src/knowledge_lens.py | ~835 | Focused analysis lenses |
| src/playbook.py | ~221 | Director operational wisdom (append/read) |
| docs/KNOWLEDGE_CRYSTALLIZATION.md | | Design spec (sapling→tree) |
