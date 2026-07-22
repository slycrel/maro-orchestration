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

## Data Stores (all JSONL under `~/.maro/workspace/memory/`)

| File | What | Written by | Read by |
|------|------|-----------|---------|
| outcomes.jsonl | One record per loop run | reflect_and_record() | evolver, inspector, bootstrap |
| medium/lessons.jsonl | Active lessons (decay 15%/day) | record_tiered_lesson() | inject_tiered_lessons() |
| long/lessons.jsonl | Promoted lessons (no decay) | promote_lesson() | inject_tiered_lessons() |
| standing_rules.jsonl | Permanent rules (zero cost) | observe_pattern() → promote | inject_standing_rules() |
| hypotheses.jsonl | Lessons being validated | observe_pattern() | check before promotion |
| decisions.jsonl | ADR-style decision journal | step DECISION directive (step_exec/loop_post_step), scope proxy commitment (scope.py), `python3 -m knowledge_lens decision` (SF-13 decrees) — all via record_decision() | inject_decisions() (recall substrate #3) |
| captains_log.jsonl | Event stream (11K+ entries) | Various — lifecycle events | captain's log read bridge |
| task_ledger.jsonl | Per-step execution trace | record_step_trace() | evolver context |
| verification_outcomes.jsonl | Claim verification history | record_verification() | calibration threshold |
| knowledge_nodes.jsonl | Structured knowledge (K2) | import_link_farm, append_knowledge_node() | query_knowledge(), inject_knowledge_for_goal() |
| knowledge_edges.jsonl | Node relationships (K2) | import_link_farm, append_knowledge_edge() | load_knowledge_edges() |

## Write Flow (after each run)

```
Loop completes
  → reflect_and_record(goal, status, summary, loop_id=...)
    → LLM extracts 1-3 typed lessons (execution/planning/recovery/verification/cost)
    → record_outcome() → outcomes.jsonl + daily .md log
    → For each lesson: record_tiered_lesson() → medium/lessons.jsonl
      (confidence 0.5-0.7 depending on k_samples)
    → Captain's log: LESSON_RECORDED event
Closure judges the goal (handle.py, AFTER finalization)
  → stamp_outcome_verdict(loop_id, goal_achieved, goal_verdict_source)
    → stamps the verdict tri-state onto the already-written outcomes row
    → returns updated / missing / write_failed (never use as a boolean)
      (SF-2, done ≠ achieved: True/False when judged, ABSENT key = unjudged;
       NOW lane records its self-verdict directly at record_outcome time)
  → if a delivered stamp write fails, audit_policy quarantines that loop's
    deferred learning and appends its exact idempotent patch to audit_repairs
  → audit_repair later replays only that patch and named row's deferred
    lesson/knowledge extraction (manual CLI or autonomy/evolver cadence)
```

Audit repair never synthesizes a missing outcome or reconstructs skill
crystallization: the latter needs ephemeral `StepOutcome` inputs that are not
in the repair record. A workspace lock serializes paid extraction and a
`surface_pending` metadata checkpoint makes derived-card refresh crash-safe.
Multi-loop runs keep one record per loop; run-level quarantine clears only
after every sibling repair completes. Automatic failures are bounded and leave
manual quarantine visible rather than spending forever.

**Verdict tri-state convention (SF-2 / data-02):** `goal_achieved` on an
outcomes/lessons row is True/False only when a verdict exists; an unjudged
row OMITS the key (never null, never False). Consumers must prefer the
verdict when present and treat absence as unjudged — not success, not
failure. Rows before 2026-07-09 are all unjudged (historical, no backfill).

## Read Flow (before/during runs)

```
Loop starting (recall.py loop slice)
  → inject_standing_rules(domain) → promoted rules (zero-cost match)
  → query_lessons() tiered-first (ranked, decay-scored; chunk 6 rewire)
    → legacy lessons.jsonl tops up lessons never dual-written
  → inject_decisions(goal) → TF-IDF search of decision journal
  → inject_playbook / inject_knowledge_for_goal → wisdom + ACTIVE nodes
  → Captain's log bridge → recent lifecycle events
```

(`bootstrap_context()` — top outcomes + lessons — is CLI-only
(`cli.py`), not part of loop start. Full store-by-store liveness map:
`docs/history/2026-07-21-wiring-inventory.md`.)

## Tiered Memory Model

- **MEDIUM**: Score 0.2–1.3. Decays 15%/day (score *= 0.85^days). New lessons start at `1.0 + 0.3 * novelty` (chunk 6 — novelty = 1 − max store similarity at record time, measured for free in the dedup scans; killswitch `knowledge.novelty_term_enabled`). A fully novel lesson starts at 1.3 (~2 extra days above the GC line); a repeat-shaped one at ~1.0.
- **LONG**: Promoted when score ≥ 0.9 AND sessions_validated ≥ 3. No decay (enforced tier-aware since session 40 — earlier code decayed long-tier on load).
- **Standing Rules**: Promoted from long-tier after 2+ pattern confirmations. Zero cost, always active.

Reinforcement: When a lesson is re-confirmed, score += 0.3 capped at 1.0, sessions_validated++ — but a novelty-boosted score above 1.0 is never lowered (`min(max(1.0, score), score + 0.3)`). At threshold: promote to LONG.

**Re-confirmation side effects (session 40 M2, `_post_reinforce_hooks` in knowledge_web.py):** every reinforcement — whether via `reinforce_lesson()` or `record_tiered_lesson()`'s near-duplicate dedup — runs the hooks: a MEDIUM lesson meeting eligibility (score ≥ 0.9, sessions ≥ 3) promotes to LONG *immediately* (the returned lesson's `.tier` changes), and a LONG re-confirmation calls `observe_pattern()` so hypotheses accrue confirmations and standing rules accrete. `record_tiered_lesson(tier=MEDIUM)` also dedups against LONG first — re-learning an already-promoted lesson reinforces the long-tier record instead of creating a medium duplicate. Full accretion path: medium lesson → eligibility at reinforcement → LONG (promote_lesson seeds hypothesis, confirmation 1) → re-learned once more → standing rule (RULE_PROMOTE_CONFIRMATIONS = 2).

**Decay is a read-time derivation, never persisted** (session 40 invariant). The stored score is the score as of `last_reinforced`; the effective score is computed on load. Any code that rewrites a lessons file MUST load with `raw=True, limit=None` — persisting an effective (decayed) score without re-anchoring `last_reinforced` compounds decay, and the default `limit=50` silently truncates larger stores on rewrite.

## Consolidation (the "dream cycle", session 40)

`maybe_consolidate()` in knowledge_web.py runs `run_decay_cycle` (medium tier: promote eligibles, GC effective-score < 0.2 — GC ARCHIVES to `memory/lessons_archive.jsonl`, never deletes; retention decree 2026-07-10; `search_graveyard` reaches the archive and `resurrect_archived_lesson()` restores) at most once per `memory.consolidation_interval_hours` (default 24h; `memory.consolidation_enabled` to turn off), gated by a `memory/last_consolidation.json` marker. **In-process by design — no cron/daemon** (rogue-process history). Entry points: end of every `handle()` call (try/finally, skipped on dry_run, can never affect the request outcome), every heartbeat tick (even health-only mode — pure local file work), and `poe-memory consolidate [--force]`. Logs a `MEMORY_CONSOLIDATED` captain's-log event. Concurrent double-run is safe: decay is read-derived, promotion is eligibility-gated, GC is idempotent.

## Captain's Log

Append-only event stream tracking knowledge lifecycle:
- LESSON_RECORDED → LESSON_REINFORCED → HYPOTHESIS_CREATED → HYPOTHESIS_PROMOTED → STANDING_RULE_CONTRADICTED
- Read bridge (K3 partial): recent events injected into decompose + evolver prompts

**Contradiction wiring (chunk 4, 2026-07-21 — `contradict_pattern` finally has
a runtime writer):** recall's loop slice stamps `rules_cited`/`lesson_ids_cited`
(durable IDs) into RECALL_PERFORMED AND writes `source/recall_citations.json`
into the current run dir. When `stamp_outcome_verdict` lands a FULL-trust
(`verdict_trust`) `goal_achieved=False` on a citation-bearing run, it emits
CONTRADICTION_CANDIDATE — joined to the run by durable identity
(`runs.resolve_run_dir(loop_id)`), never the ambient run-dir ContextVar. At
skill-maintenance cadence (`run_skill_maintenance` — every loop finalize plus
evolver cycles; gated by `knowledge.contradiction_adjudication_enabled`, cap
3/cycle, non-blocking cycle lock against concurrent finalizes) an LLM
tri-state verdict with per-artifact attribution adjudicates each candidate:
only exact "yes" naming specific cited ids calls `contradict_pattern` on
those (undecided = unjudged, never contested; a yes naming nothing is
unparsable and retried); the rule drops to the contested injection tier and
`refight_rule` reaches it in the same maintenance pass, with the adjudicated
events' failure/reasoning as refight evidence. Standing-rule `domain` vocabulary is PROJECT slug or ""
(global); promotion writes "" — task-type domains never matched the
project-filtered reader (battery V2). Promotion also keeps every contributing
lesson id in `source_lesson_ids` (era-09 provenance).

## Test Coverage

- **knowledge_web.py**: 103 tests in test_knowledge_web.py (session 17) — covers decay, reinforcement, TF-IDF ranking, tiered lessons CRUD, near-duplicate detection, graveyard search, prompt injection formatting.
- **playbook.py**: `append_to_playbook()` rejects empty entries and truncates at 500 chars (session 17). Since 2026-07-21 (chunk 2): `inject_playbook()` is RANKED (learned-over-seed, newest first, deduped — the head-window horizon bug is gone), and `curate_playbook()` rides `maybe_consolidate()` (dedup + size-gated LLM compress, prior version archived to `playbook_history/`). The director's compact context block still omits the playbook (wiring row 17, BACKLOG).

## Known Gaps (Intent vs Implementation)

1. **No Stage 2→3 pathway.** Canon promotion (10+ applies, 3+ task types) is spec'd but not coded.
2. **No Stage 4→5 pathway.** Skill → rule promotion is conceptual only.
3. **Reinforcement is passive.** Lessons only reinforce when explicitly re-confirmed in a run. System doesn't proactively test its own lessons.
4. **Captain's log reads are coarse.** Dumps recent events rather than targeted retrieval.
5. **Decay works but creates cold-start.** A valid lesson that isn't used for 7 days decays to ~0.32 — it effectively dies even if it's correct. `search_graveyard(resurrect=True)` can wake matches, but nothing calls it proactively. Partially mitigated (chunk 6): the novelty boost buys a fully novel lesson ~2 extra days above the GC line (dies ~day 11.5 instead of ~9.9), and recall's loop slice now actually reads the tiered store so being applied — and thus reinforced — is possible at all.
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
