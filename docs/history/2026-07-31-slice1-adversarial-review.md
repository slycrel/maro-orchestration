---
status: record
---

# Adversarial review — §13e slice 1 (typed pause reasons), 2026-07-31

Post-land review of the slice-1 diff (landed `f1f868e`, reviewed at
`a11501e`) per the per-chunk discipline. Three Codex lenses (skeptic /
architect / minimalist) via `codex exec`, common context + diff in
`/tmp/adversarial-review.lwPuNP/`. Every finding verified against the
tree before action (feedback_verify_before_fix). **11/11 checkable
claims verified real — 0 hallucinated** (continues the clean streak;
the census-grounded prompt style appears to be load-bearing).

## Intent

Give the paused lifecycle state (decree 7afe8b3a) a typed, durable WHY:
vocabulary + stamp rail + writer sites + run-card forwarding, as an
honest-good-enough slice with named upgrade edges — without building
the commanded pause/resume lifecycle.

## Verdict: CONTESTED → remediated same session

High-severity findings were real but all narrowly fixable; fixes landed
with pins in `tests/test_pause_reasons.py::TestReviewFixes`.

## Findings

1. **[high, FIXED]** Pre-start refusal stamps never reached durable
   provenance (all three lenses — unanimous). `_stamp_refusal_verdict`
   (loop_init.py) wrote only stop fields; the explicit
   `manual-intervention` on the kill-switch LoopResult evaporated, and
   status "interrupted" deliberately has no curation fallback — so the
   one writer site that motivated the decree produced untyped cards.
   Fix: the refusal stamp now carries `pause_reason`; both call sites
   pass their typed reason. Lens: all. Principle: consumer-first (a
   writer whose value never reaches a reader isn't a writer).
2. **[high, FIXED]** Resume erased pause history (skeptic + architect).
   `stamp_run_metadata` skips None but writes "" — and loop_finalize
   stamped `result.pause_reason` unconditionally, so a stranded →
   resumed → done run lost its `writer-died` stamp, contradicting the
   stated "done runs keep pause provenance" contract (the
   paused-then-finished pin passed only because it hand-built metadata).
   Fix: `result.pause_reason or None` — empty preserves, a new typed
   reason still overwrites. Lens: skeptic/architect. Principle:
   done-means (the contract test must survive the real lifecycle).
3. **[high, FIXED]** Stranded-sweep commit raced finalize (skeptic).
   The sweep read metadata unlocked and `atomic_write`-replaced the
   whole file; finalize's locked RMW landing in the window would be
   clobbered with forged stranded/writer-died provenance. Pre-existing
   hazard (predates §13e), but the pause stamp made it forged
   *provenance*, squarely this slice's business. Fix: commit via
   `locked_rmw` with an in-lock `status` re-check (finalized-while-
   checking → leave untouched). Race-window interleaving is not
   unit-tested (would need thread hooks); the guard is code-review
   covered + existing sweep pins.
4. **[medium, FIXED]** Vocabulary enforced only at the stamp rail
   (skeptic + architect). `record_outcome` persisted any nonempty
   string while curation silently fell back — stores disagreeing
   instead of rejecting at ingress. Fix: ledger drops off-vocabulary
   reasons (logged), pinned.
5. **[medium, ACCEPTED as edge]** Stranded runs have no outcome-ledger
   path (skeptic). True: the sweep stamps metadata only; a writer that
   died mid-finalize can leave an untyped outcome row (or none). The
   card derives from metadata, which is the §13e post-hoc home; a
   ledger back-stamp needs the outcome join the LT arc is currently
   repairing. BACKLOG'd under the paused-state edges.
6. **[medium, ACCEPTED as edge]** Legacy cards stay untyped (skeptic).
   `list_runs` returns stored `run_card.json` verbatim; the fallback
   map only runs at curation time, so the census's 57 stranded + 24
   clarification_needed pre-slice runs keep untyped cards. Recuration
   would rebuild old cards under a drifted schema — riskier than the
   value; denominators live in the committed census. BACKLOG'd.
7. **[medium, ACCEPTED as known]** Parallel/DAG lane bypasses the rail
   (skeptic). Real, pre-existing, and already recorded in the code
   comment at the early return (2026-07-08 review finding #1): that
   lane bypasses ALL finalize side effects, not just pause stamps.
   Fixing it is the existing larger effort, not a pause-slice patch.
8. **[low–medium, REJECTED]** `PAUSED_STATUSES` alias unused /
   "false abstraction" (architect + minimalist). Deliberate layered-
   decree design: the alias documents that paused subsumes the
   interrupt family without rewriting history, at ~zero cost.
   Architect's lifecycle-predicate suggestion is noted as the natural
   shape when a commanded pause ships.
9. **[medium, REJECTED]** Reserved reasons without writers should be
   deferred (minimalist). Vocabulary-first was the design: the reasons
   are Jeremy's own enumeration, the stamp sites are named BACKLOG
   edges, and forwarding an explicitly stamped reserved reason is
   correct behavior, not a hole.
10. **[medium, REJECTED — tension noted]** `pause_family` is derived
    state with no reader yet (architect + minimalist). True today;
    kept because it's deterministically derived at curation (no drift
    source but the vocabulary itself) and its consumer is the named
    readout join. This is a consumer-first tension carried openly —
    if no reader exists when the readout lands, delete it then.
11. **[low, RESOLVED by #2]** Paused-then-finished pin promoted an
    unimplemented lifecycle (minimalist). Correct as reviewed — the
    real resume path contradicted the pin. With #2 fixed the pin now
    describes actual behavior.

## What Went Well

- The fallback map's refusal to guess for ambiguous "interrupted"
  survived all three lenses unchallenged (two explicitly endorsed it).
- Centralizing vocabulary + fallback beside the stop-verdict module
  (not in curation) was endorsed as sound placement.
- The reflect_and_record kwarg regression pin was singled out
  (skeptic) as the one test guarding a genuinely live failure mode.

## Lead Judgment

Accept #1–#4 (fixed), accept #5–#7 as named edges (two BACKLOG'd, one
already recorded at the code seam), reject #8–#10 with rationale above.
The unanimous #1 is the finding that mattered: the slice shipped its
flagship writer site with a broken pipe to durability, and the tests
proved helpers rather than the lifecycle (skeptic #8 — fair; the new
TestReviewFixes pins go through metadata, not hand-built dicts).
Minimalist's #9/#10 mistake deliberate decree-shaped scaffolding for
speculation; rejected on intent, not on their reading of the tree,
which was accurate throughout.
