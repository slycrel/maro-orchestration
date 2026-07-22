---
status: record
---

# Chunk-4 Adversarial Review — contradiction wiring (afe5c5a)

Per-chunk review discipline (Jeremy's /goal 2026-07-21). Three Codex lenses
(`codex exec`, opposite-model rule) against the chunk-4 diff
(bdbbf13..afe5c5a), prompts = stated intent + lens + mapped brain principles
+ full diff + verify-against-tree instruction. Raw reviewer output preserved
at session scratchpad `adv-review-c4.xLWCqc/` (ephemeral; this record is the
durable summary).

## Intent

Give `contradict_pattern` its first runtime writer so the already-built
contested→refight lifecycle becomes reachable: recall stamps durable
citation IDs + run-keyed citations file → FULL-trust failed verdict emits
CONTRADICTION_CANDIDATE → capped tri-state LLM adjudication at maintenance
cadence → only "yes" contests. Plus two prerequisites: the battery-V2
domain-vocabulary fix and full `source_lesson_ids` provenance.

## Verdict: REJECT as reviewed — remediated same session

Three-lens consensus on candidate starvation (F1), two-lens consensus on
collateral contestation (F4). All eight deduped findings verified against
the tree before fixing: **8/8 real, 0 hallucinated** — fourth consecutive
clean round (historical reviewer hallucination rate 30–78%).

## Findings (deduped, severity order)

1. **[high — Skeptic+Architect+Minimalist, unanimous]** Candidate
   starvation: the adjudicator read `query_log(..., limit=100)`, which
   truncates NEWEST-first — so "FIFO" was actually "oldest of the newest
   100". With >100 pending, the oldest candidates became permanently
   invisible, breaking the "unparsable retries next cycle" contract.
   Principle: prove-it-works / fix-root-causes. **Fixed**: unlimited reads
   (`limit=0`) for both candidate and adjudicated queries; pinned with a
   105-candidate test asserting the three OLDEST are judged.
2. **[high — Skeptic]** No serialization of the candidate claim lifecycle:
   `run_skill_maintenance` runs at EVERY loop finalize, so two concurrent
   finalizes could both see the same pending candidate, both pay the LLM,
   and both call `contradict_pattern` — double-counting contradictions
   (the per-file store locks protect each increment, not the claim).
   Principle: serialize-shared-state-mutations. **Fixed**: dedicated
   non-blocking cycle lock (`contradiction_adjudication.lock`, flock
   LOCK_NB — loser skips, holder drains); plus within-batch dedup by
   loop_id (duplicate candidates for one loop judged once). Both pinned.
3. **[high/medium — Architect+Skeptic]** Collateral contestation: one
   run-level scalar verdict fanned out to EVERY cited artifact, but a run
   cites its whole injected bundle — a single guilty stale rule would
   contest 11 innocent bystanders. **Fixed**: per-artifact attribution —
   the judge must name `contradicted_ids` (validated subset of the cited
   ids); only named artifacts are contested; a "yes" naming nothing valid
   is unparsable-and-retried, never fanned out. Pinned
   (guilty-vs-innocent test).
4. **[high — Architect]** The citation join used the ambient run-dir
   ContextVar, not the stamped loop's identity — a cross-process re-stamp
   (audit_repair) had either no context or somebody else's. **Fixed**: the
   emitter now joins via `runs.resolve_run_dir(loop_id)` (the durable run
   index). Strictly better both ways: kills the wrong-context risk AND
   upgrades audit re-stamps from silently-no-event to a correct join.
5. **[medium — Skeptic]** Refight starved of causal evidence:
   `_rule_contradiction_evidence` read only STANDING_RULE_CONTRADICTED
   summaries (counts + rule text), so the keep/revise/retire judge saw a
   tally, not a collision. **Fixed**: adjudicated events now carry
   failure_summary/goal_preview/reasoning, and the evidence gatherer
   surfaces yes-events for the rule ("run X failed (…); judge: …").
   Pinned.
6. **[medium — Skeptic]** Lesson-only "yes" counted as contradicted while
   mutating nothing (`contradict_pattern` return ignored). **Fixed in
   part**: the adjudicated event records `applied` (artifacts that
   actually took the hit) so no-ops are honest; pinned. A lesson-store
   contested tier is deliberately NOT built — deferred consumer-first
   (lessons already have their own decay lifecycle).
7. **[medium — Minimalist]** "Evolver cadence" was wrong: maintenance runs
   at every non-dry loop finalize, so adjudication is per-run spend when
   candidates pend. **Accepted in part**: DEFAULTS.md and the arch skill
   now say so plainly. Default stays ON — candidates are rare by
   construction (FULL-trust failures with citations), cap 3, cycle lock;
   and the refight scan beside it already spends at the same cadence
   (pre-existing architecture, not this chunk's regression).
8. **[medium — Minimalist]** Refight retirement rebuilt the hypothesis
   from `source_lesson_id` alone, discarding the full contributor list the
   chunk introduced — provenance decorative in the exact lifecycle it
   exists for. **Fixed**: retirement carries `source_lesson_ids` (single-id
   fallback for pre-2026-07-21 rules); pinned.

## What Went Well

- No reviewer contested the verdict_trust gate (era-10 law), the
  undecided-is-unjudged tri-state, the emitter's never-raise posture, or
  the domain migration + provenance prerequisite work.
- The moot-clear path (artifacts all gone → deterministic "no") and
  unparsable-retry contract were endorsed as correct shapes; the findings
  were about windows and attribution, not the lifecycle design.
- The e2e one-maintenance-pass pin (candidate→contested→refought) held —
  reviewers attacked the inputs to that pass, not its ordering.

## Lead Judgment

- F1 accept in full — unanimous and exactly right; my FIFO comment was
  aspirational prose over a bounded window.
- F2 accept — Skeptic's "the lock protects the increment, not the claim"
  framing is the decisive version. The single-purpose lock held across LLM
  calls is deliberate and does not touch the chunk-2 LLM-outside-lock rule
  (that rule protects shared-store locks; this lock serializes only
  adjudication cycles).
- F3 accept — attribution is the difference between a collision detector
  and a blast radius. One JSON field, no extra LLM call.
- F4 (ambient join) accept — Architect's boundary-discipline read was the
  deepest finding of the round; `resolve_run_dir` existed and was simply
  not used.
- F5 accept — evidence-starved refight would have made "one pass" work
  procedurally while judging blind.
- F6 accept in part — honesty shipped; a lesson contradiction lifecycle is
  real design work with no consumer yet.
- F7 accept in part — the doc lie is fixed; the spend posture is a
  standing decree (cost-isn't-end-all) and the cadence question belongs to
  the pre-existing maintenance architecture, not this chunk.
- F8 accept — the review caught the new field being dropped in the exact
  path that justified adding it.

All eight verified real before fixing; the affected suites and the full
185-item suite run green post-fix.
