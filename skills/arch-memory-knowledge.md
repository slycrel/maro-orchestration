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
| verification_outcomes.jsonl | RETIRED 2026-08-08 — Phases 59-60 cluster removed (dead at both ends, decision 1addc859); file kept per data retention, frozen since 2026-04-12 | — | — |
| knowledge_nodes.jsonl | Structured knowledge (K2) | import_link_farm, append_knowledge_node() (bridge mints CANDIDATE), promote_knowledge_candidates() (candidate → active flip) | query_knowledge(), inject_knowledge_for_goal() |
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
Run fails the learnability gate (per-step learning, 2026-07-27)
  → extract_step_lessons(goal, step_outcomes) — memory.py
    → over individually-verified steps only (status "done" + verify
      confidence "strong"); one LLM call, 0-3 step-scoped lessons;
      prompt forbids goal-level success claims AND negative/deadness
      claims; killswitch memory.step_learning_enabled
    → record_tiered_lesson(provisional=True) → medium/lessons.jsonl
    → idempotent via step_lesson_count stamp on the outcomes row
    → two call sites (loop_finalize): immediate on loop_status != "done";
      post-verdict in run_deferred_learning when the judged row is
      not learnable
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

**Knowledge-node lifecycle (battery V3, promotion shipped 2026-08-02):**
bridge-minted nodes (`knowledge_bridge.outcome_to_knowledge`,
author="knowledge_bridge") are born `NODE_CANDIDATE` at confidence 0.3 —
invisible to every live reader (ACTIVE-only loads; lf- reference-corpus
nodes are excluded from queries regardless of status). Both link-farm
import lanes (scripts/import_link_farm.py and the `knowledge
import-links` CLI verb) stamp the lf- prefix and mint ACTIVE — reference
data is consult-ready via `include_reference=True`, never
trust-earning — and the bridge's dedup upsert skips lf- rows, so a maro
lesson that title-collides with a reference row mints its own
first-party node instead of being swallowed into the excluded corpus
(both fixed 2026-08-03). Re-deriving the same
generalization from a later run hits the bridge's dedup upsert (Jaccard ≥
0.7 title match): confidence +0.05, times_applied +1 — on a candidate,
times_applied counts exactly these independent re-observations (the
injection-surface bump touches ACTIVE nodes only).
`promote_knowledge_candidates()` (knowledge_web.py, riding
`run_skill_maintenance` beside skill promotion — Jeremy decree "same as
skills") flips earned candidates to ACTIVE: times_applied ≥ 2 AND
confidence ≥ 0.4 (epsilon-tolerant — two float bumps land at 0.3999…),
optional LLM gate stamped passed/unjudged/skipped (fail-open, the
SKILL_PROMOTED contract), one `KNOWLEDGE_NODE_PROMOTED` event per flip,
capped 10/sweep. The permanent-vs-useful user gate is a deferred UX layer,
not this sweep. Pin: `test_knowledge_bridge.py::TestCandidateInvisibilityPin`
(born invisible → earned promotion → surfaces in live recall).

## Tiered Memory Model

- **MEDIUM**: Score 0.2–1.3. Decays 15%/day (score *= 0.85^days). New lessons start at `1.0 + 0.3 * novelty` (chunk 6 — novelty = 1 − max store similarity at record time, measured for free in the dedup scans; killswitch `knowledge.novelty_term_enabled`). A fully novel lesson starts at 1.3 (~2 extra days above the GC line); a repeat-shaped one at ~1.0.
- **LONG**: Promoted when score ≥ 0.9 AND sessions_validated ≥ 3. No decay (enforced tier-aware since session 40 — earlier code decayed long-tier on load).
- **Standing Rules**: Promoted from long-tier after 2+ pattern confirmations. Zero cost, always active.

Reinforcement: When a lesson is re-confirmed, score += 0.3 capped at 1.0, sessions_validated++ — but a novelty-boosted score above 1.0 is never lowered (`min(max(1.0, score), score + 0.3)`). At threshold: promote to LONG.

**Re-confirmation side effects (session 40 M2, `_post_reinforce_hooks` in knowledge_web.py):** every reinforcement — whether via `reinforce_lesson()` or `record_tiered_lesson()`'s near-duplicate dedup — runs the hooks: a MEDIUM lesson meeting eligibility (score ≥ 0.9, sessions ≥ 3) promotes to LONG *immediately* (the returned lesson's `.tier` changes), and a LONG re-confirmation calls `observe_pattern()` so hypotheses accrue confirmations and standing rules accrete. `record_tiered_lesson(tier=MEDIUM)` also dedups against LONG first — re-learning an already-promoted lesson reinforces the long-tier record instead of creating a medium duplicate. Full accretion path: medium lesson → eligibility at reinforcement → LONG (promote_lesson seeds hypothesis, confirmation 1) → re-learned once more → standing rule (RULE_PROMOTE_CONFIRMATIONS = 2).

**Provisional lessons (per-step learning, 2026-07-27):** `TieredLesson.provisional=True` marks step-scoped lessons from runs that failed the learnability gate. Entry score `0.6 + 0.3·novelty` (ceiling 0.9, under the confirmed 1.0 floor). Excluded from every injection surface — `query_lessons()` (opt back in via `include_provisional=True`), `inject_tiered_lessons()`, the memory-bridge ingest, and `search_graveyard()` (both live and archived scans — resurrection reinforces confirming=True, so a topic match must neither inject nor confirm). Blocked from LONG promotion at the boundary itself (`promote_lesson()` refuses, covering the CLI) plus the `_post_reinforce_hooks`/`run_decay_cycle` early-skips. A confirmed-context re-record that dedup-matches clears the flag (`_reinforce_tiered_lesson(confirming=True)`); a provisional-context match reinforces score only — `sessions_validated` (the promotion/confidence counter) moves exclusively on confirmed-context reinforcement, so promotion always requires PROMOTE_MIN_SESSIONS confirmed observations. Decay disposes of never-confirmed rows in ~a week. Since the same-day adversarial review, ALL closure-lane statuses defer run-level extraction (`defer_lessons=defer_learning`, no status condition) so a stuck-but-judged-achieved run's lessons are extracted verdict-aware. Records: `docs/history/2026-07-27-per-step-learning.md` + companion review record.

**Contested lessons (retirement-by-contradiction, 2026-08-02 — Jeremy: "time to level the decay up"):** `TieredLesson.contested` (dict; empty = full citizen, else `{reason, source, contested_at}`) is the lesson-store mirror of the standing-rule grey flip. Set by `contest_lesson(lesson_id, reason, source=...)` in knowledge_web.py — called by contradiction adjudication when a certified-failure verdict names a cited lesson (was an honest no-op before), and by the operator verb `maro-memory contest`. Stamps BOTH stores (UU-4 dual-written rows share ids; `memory_ledger.contest_flat_lesson` handles the flat ledger), emits one `LESSON_CONTESTED` event. Excluded from the same surfaces as provisional/quarantined: `inject_tiered_lessons()`, `query_lessons()` (`include_contested=True` to opt in), `search_graveyard()` both scans, flat `load_lessons()`, canon candidates; blocked from promotion at `promote_lesson()` + both hook sites; a contested LONG row stops feeding `observe_pattern`. Anti-laundering: a dedup re-sighting bumps `times_reinforced` ONLY (evidence for a future refight) — score and `last_reinforced` freeze so decay retires contested MEDIUM rows on schedule regardless of re-derivation, and no flag (provisional/quarantine/contested) ever clears through a duplicate write. For decay-free LONG, contestation IS the retirement mechanism. Sticky against duplicate writes; the deliberate exit is `refight_lesson` (2026-08-09, §5 cut) — the lesson mirror of `refight_rule`: keep (contested cleared on BOTH stores, decay anchor re-set to today) / revise (corrected text re-enters provisional with zeroed counters; the flat row keeps the refuted original and stays contested) / retire (archived `reason="refight_retire"`, excluded from graveyard resurrection). The contest stamp snapshots `times_reinforced_at_contest` per store so re-sightings since the contest are countable; the maintenance-cadence scan (`run_skill_maintenance`, capped 3/cycle like rules) only spends on rows with post-contest sightings — quiet MEDIUM rows decay out for free, quiet LONG rows stay retired-in-place. Operator verb: `maro-memory refight <id>`; flat-only contested rows aren't refightable (named v1 cut). Emits `LESSON_REFOUGHT`. Tests: `tests/test_lesson_refight.py`. First applied 2026-08-02 to the six chunk-1 surprise-read contradictions (L4=6287e494 long; M6=9d6b63fe, M8=c304b9b2, M9=c85c9a09, M13=47e8f5e3, M14=655ea616), reasons = Jeremy's verbatim reads. Tests: `tests/test_lesson_contested.py`.

**Mint form: observations, not procedures (what-not-how, 2026-08-02 — Jeremy: "how is ok when asking for work, but usually we aren't — asking for the right result is the more important part"):** every LLM mint-site prompt carries `memory._LESSON_FORM_RULES` — lessons state WHAT was derived (the mismatch, the requirement, the observed failure), never a prescribed procedure; a repeated failure is stated as the observation, not a countermeasure; no self-credit capability claim without the observed instance; procedure form only when the goal itself asked for a procedure. Composed into `_REFLECT_SYSTEM` (deferred extraction rides it too) and `_STEP_LESSON_SYSTEM`; thinkback constrains only `key_lessons` (step reviews/retry_strategy stay prescriptive — review output, not memory). Finalize's deterministic templates use `_recovery_plan_lesson_text`/`_auto_diagnosis_lesson_text` (diagnosis as observation, action marked "advisor-proposed (unverified)"; deterministic per input so recurring plans still dedup-reinforce). Evidence stamping is the structural half: reflect/deferred/finalize tiered mints pass `evidence_sources=[loop:<id>]`, and `_reinforce_tiered_lesson` merges incoming refs (cap `_REINFORCE_EVIDENCE_CAP`=8, contested rows excluded) so a row records *where* it repeated, not just how often — this is what makes the Phase 60 citation-penalty ranking meaningful. The S2 seed-reader exemplar ("emulate this style" + top LONG lesson verbatim) was REMOVED 2026-08-06 on the sanctioned A/B verdict (13 runs × 2 arms: no measurable quality gain, 3.5× lesson_type anchoring toward the seed's type, ~60% cross-run homogenization; `~/.maro/workspace/output/seed-reader-ab/RESULTS.md`) — the extraction prompt names no stored lesson as a style model, pinned in `test_mint_form.TestNoSeedExemplar`. Certified failure shapes: surprise-read L4/M9/M13/M14. Tests: `tests/test_mint_form.py`.

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
