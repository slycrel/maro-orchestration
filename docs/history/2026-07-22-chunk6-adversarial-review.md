---
status: record
---

# Chunk-6 Adversarial Review — 2026-07-22

Per-chunk review discipline (Jeremy's /goal). Reviewed: commit `1236270`
(chunk 6 — surprise as a capture signal: expectation-mismatch extraction
prompt, novelty term in tiered scoring, recall substrate #1 tiered-first
rewire). Reviewers: 3 Codex lenses (`codex exec`, opposite model), parallel,
read-only against the live tree.

reviewer_cli=codex; all three output files non-empty (skeptic.md,
architect.md, minimalist.md).

## Intent

Capture surprise as a learning signal without new taxonomy: (1) extraction
prompt leads with "what actually DIFFERED from what the plan assumed"; (2)
novelty = 1 − max store similarity boosts a new lesson's initial score
(1.0 + 0.3·novelty) so novel one-offs survive decay long enough to be
tested; (3) recall substrate #1 reads the tiered store first (its comment
always claimed it; the read was flat-only) with legacy flat top-up.

## Verdict: CONTESTED

One verified high (single-lens), five verified mediums with heavy
cross-lens agreement, one rejected low. **7/7 findings verified real
against the tree, 0 hallucinated — seventh consecutive clean round.**
All accepted findings fixed in the same session.

## Findings

1. **[high] Citations could name lessons truncation dropped** (Architect;
   prove-it-works / boundary-discipline). `lesson_ids_cited` was built
   per-lesson in the render loop, then the block was truncated to
   `_MAX_LESSON_INJECT_CHARS` afterwards — trailing lessons could be cut
   from the prompt while their IDs stayed in `RECALL_PERFORMED` and
   `source/recall_citations.json`. The chunk-4 contradiction join would
   then contest lessons the run never saw (the exact collateral-
   contestation class chunk-4's review eliminated elsewhere). Pre-existing
   shape, but chunk 6 re-asserted "citation stamping preserved," so it
   owns the fix. **Fixed**: budget-aware selection — a lesson is appended,
   aged, and cited only if its full line fits the budget; render and
   citations can no longer diverge. Pinned
   (`test_truncated_lesson_is_not_cited`).

2. **[medium] Novelty was measured against a task_type slice, not the
   store** (Architect + Minimalist; foundational-thinking). The dedup
   scans load task_type-filtered, so a lesson already stored under
   `research` re-recorded under `build` scored novelty 1.0 — a
   cross-domain repeat is not a surprise. **Fixed**: scans load untyped;
   dedup keeps its task_type scope inline (existing contract, pinned in
   test_tiered_memory — identical text under another type stays a
   separate lesson), novelty maxes over everything scanned. Pinned
   (`test_novelty_is_store_wide_not_task_type_scoped`).

3. **[medium] Scan-and-append raced concurrent writers** (Skeptic +
   Architect; serialize-shared-state-mutations / fix-root-causes). Dedup
   read and `locked_append` were separate critical sections — two workers
   recording the same novel lesson could both miss the match and both
   append boosted duplicates. Pre-existing dedup race; the boost made
   each duplicate worth more. **Fixed**: own-tier scan + novelty + append
   now run under one `locked_write` (reentrant per-thread, so the
   reinforce path inside is safe; matches `_mutate_tiered_lessons`'s
   documented pattern). Pinned
   (`test_record_scan_and_append_hold_the_tier_lock`).

4. **[medium] An agenda tiered match masked other-type tiered-only
   lessons** (Skeptic + Minimalist). The untyped `query_lessons` call ran
   only when the agenda query returned empty — one weak agenda match hid
   a relevant verify-learn/general tiered-only lesson that flat top-up
   can never recover. **Fixed**: untyped tiered query is a top-up (fills
   remaining slots, lesson_id dedup), not an empty-only fallback. Pinned
   (`test_agenda_match_does_not_mask_other_type_tiered_lessons`).

5. **[medium] `agenda or general` flat chaining masked general flat-only
   lessons** (Skeptic + Architect-low). A non-empty agenda flat result
   consisting entirely of already-selected twins stopped the general
   store from being consulted while slots stayed open. **Fixed**: both
   flat sources chained in order with twin-dedup until full. Pinned
   (`test_agenda_flat_twins_do_not_mask_general_flat_lessons`).

6. **[medium] `query_lessons` rank-filtered only the top score slice**
   (Skeptic; prove-it-works). Candidates loaded `limit=n*5` from a
   score-sorted list, so a textually exact match below the top decayed
   scores was invisible to the ranker — pre-existing, but recall now
   leads with this function. **Fixed**: `limit=None` — the ranker ranks
   the full live store (bounded by decay + GC, not by hiding rows).
   Pinned (`test_relevant_lesson_below_score_cutoff_is_still_found`).

7. **[low, REJECTED] Narrative documentation duplicated across
   GOAL_BRAIN / MILESTONES / DEFAULTS / skill** (Minimalist;
   subtract-before-you-add). Rejected: each target is a decreed
   convention, not drift — GOAL_BRAIN is the compiled record (SF-13),
   MILESTONES the queue snapshot, the DEFAULTS row is census-enforced
   (test_defaults_doc), the skill update is the currency rule. The
   duplication cost is real but adjudicated and accepted house style.

## Lead Judgment

- Accept 1: real divergence between rendered prompt and persisted
  citations; the contradiction join makes it consequential. Fix at the
  seam that owns the invariant (render loop), not downstream.
- Accept 2: "novel to the agent" is the stated intent; a partition-local
  measurement quietly redefined it. Dedup scope and novelty scope are
  different questions — now separated explicitly in code.
- Accept 3: the repo's own concurrency doctrine (and chunk-2's
  LLM-outside-lock precedent) demands the critical section; reentrant
  locked_write made it a cheap root fix.
- Accept 4, 5: both are the same shape — an early non-empty result
  masking a later source while capacity remained. Real bugs in the new
  top-up logic, cheap ordering fixes.
- Accept 6: pre-existing, but the rewire promoted query_lessons to the
  main-loop read path — its retrieval quality is now load-bearing.
- Reject 7: style-vs-substance; the "duplication" is the decreed record
  architecture. No action.

## What Went Well

- No reviewer faulted the killswitch normalization (quoted-"false"
  handled), the backward-compatible `novelty` field deserialization, the
  reinforce floor formula (`min(max(1.0, s), s+0.3)` — ≤1.0 behavior
  byte-identical), or the expectation-mismatch prompt change itself.
- The read-only real-store liveness methodology (near-dup 1.011 vs novel
  1.274) drew no challenge — the divergence claim stood as evidence.
- Seventh consecutive review round with zero hallucinated findings —
  every code claim in all three lenses verified true against the tree.

## Collateral

- `query_lessons` full-store ranking benefits its other consumers
  (worker slice via inject_tiered_lessons/strategy_evaluator paths) —
  same retrieval-quality fix everywhere.
- Novelty-vs-dedup scope separation is now documented in the code where
  the scans live, not just in this record.
