---
status: record
---

# Chunk-9 #4 adversarial review — stop-verdict split (fc93dfa)

Post-land review per the per-chunk discipline. Large change (26 files,
+1144/−33) → 3 Codex lenses (Skeptic / Architect / Minimalist) via
`codex exec`, each with the stated intent, its lens, the brain
principles, the full diff (90ef39c..fc93dfa), and read access to the
tree. All three returned; no reviewer failed or produced empty output.

## Intent

Wire typed stop verdicts (4 goal verdicts + external-interrupt event
marker per Jeremy's item-5 decree) from break sites through
LoopResult/metadata/outcome-row/run-card, fix the survey's accounting
conflations (interrupted class, demotion rebucket, landing-synthesis
learnability), and rewire 4 raw-status consumers — without changing the
status vocabulary.

## Verdict: CONTESTED → remediated same session

No high-severity findings; the mediums were real and unanimous on the
headliner. **6 distinct findings, 6/6 verified against the tree, 0
hallucinated — eleventh consecutive clean round.** 4 accepted (1 in
part), 2 rejected.

## Findings

1. **[medium] The outcome-ledger row dropped the evidence half of the
   verdict** — all three lenses independently (the round's consensus
   headliner). `Outcome` had `stop_verdict` but no `stop_evidence`;
   `record_outcome`/`reflect_and_record`/`stamp_outcome_stop_verdict`
   forwarded only the verdict; a comment in `loop_blocked.py` promised
   the convergence evidence "rides to the outcome row as stop_evidence"
   — a field that could not exist; the director close's `[refines: …]`
   context survived only in metadata.
   - Lens: Skeptic #3 + Architect #2 + Minimalist #1.
   - Principle: prove-it-works / boundary-discipline /
     outcome-oriented-execution.
   - **ACCEPTED — fixed.** `stop_evidence` added to `Outcome` (empty
     dropped from the row, ≤500 chars), threaded through
     `record_outcome` → `reflect_and_record` → `_finalize_loop` →
     `_run_post_loop_side_effects`; `stamp_outcome_stop_verdict` gained
     an evidence parameter; handle demotion and director close now pass
     their evidence (the close passes the metadata-merged string, so
     `[refines: …]` reaches the row).

2. **[medium] Merge-back failures left the ledger row with the
   pre-merge status/verdict** — the row is written by `_finalize_loop`
   BEFORE the merge-back blocks; a failed merge downgraded
   status/stamped external-interrupt in LoopResult + metadata but the
   row still read e.g. clean `done`, which the chunk's own consumers
   now trust. This was a documented deliberate cut, but the Skeptic
   argued the cut became unsafe the moment consumers were rewired to
   trust rows — accepted on exactly those grounds.
   - Lens: Skeptic #1. Principle: fix-root-causes.
   - **ACCEPTED — fixed** without reordering the row write (that
     ordering is load-bearing for lesson dedup): finalize captures the
     pre-merge verdict, and when a merge block adds one, re-stamps the
     already-written row post-hoc via `stamp_outcome_stop_verdict`
     (evidence included). Companion policy fix: `is_learnable_outcome`
     now fails closed on `external-interrupt` without a positive goal
     verdict, same shape as the out-of-budget rule — a merge-failed
     "done" row cannot seed learning as a success.

3. **[medium] Pre-start refusal paths (budget gate, kill switch, busy
   refusal, write fence) never write an outcome row** — metadata/card
   see them, the ledger doesn't.
   - Lens: Skeptic #2. Principle: prove-it-works.
   - **REJECTED.** Factually correct but pre-existing shape, not a
     regression: these paths never reached `reflect_and_record` before
     the chunk either. Deliberately kept: a run that never attempted
     the goal is not an outcome — recording refusals in the outcomes
     ledger would pollute strategy/attribution denominators with
     non-attempts. Run cards carry them via the metadata stamp the
     chunk added, and `classify_outcome` gives them the "interrupted"
     class. If a ledger consumer ever needs refusal events, that's a
     new emitter decision, not a gap in this rail.

4. **[medium] The interrupt event was invisible to the repeat guard
   when a supported verdict owned the field** — a run that stamped
   out-of-budget at its landing site and was then operator-stopped
   ends `status="interrupted"` + `stop_verdict="out-of-budget"`;
   `_failing` only exempted the verdict spelling, so the attempt armed
   `all_failing` despite being an unjudged interrupt. The decree's
   two-channel shape (run-level interrupted:reason PLUS the supported
   verdict) was recorded but not machine-honored by this consumer.
   - Lens: Architect #1. Principle: boundary-discipline /
     foundational-thinking.
   - **ACCEPTED — fixed.** `_failing` now honors either channel for
     unjudged attempts: `stop_verdict == external-interrupt` OR
     `status in INTERRUPT_STATUSES` → not failing (judged
     goal_achieved=False still arms). The prior-attempts brief count
     uses the same two-channel test.

5. **[low] Vocabulary centralized but unenforced** — `stamp_stop`
   accepted any string; producers and consumers spell literals by
   convention; a typo would persist yet match no policy.
   - Lens: Architect #3. Principle: boundary-discipline.
   - **ACCEPTED IN PART — fixed at the seams.** `stamp_stop` and
     `stamp_outcome_stop_verdict` now validate against
     `VALID_STOP_VALUES` and drop off-vocabulary values (fail to
     unstamped — status fallbacks still apply; a phantom value can
     never persist). The four consumers now import the constants
     (outcome_policy, strategy_evaluator, attribution; run_curation
     already imported INTERRUPT_STATUSES). NOT done: swapping the ~20
     break-site producer literals for constants — the validation seam
     catches the failure mode (typo → dropped loudly in the log →
     fallback classification), and the churn buys nothing further.

6. **[low] Status-derived external-interrupt fallback is duplicate
   state** — cards synthesize a verdict from the same status that
   already produced `success_class="interrupted"`; recall mirrors the
   derivation.
   - Lens: Minimalist #2. Principle: subtract-before-you-add.
   - **REJECTED.** Deliberate legacy bridge: pre-stamp runs (every run
     before fc93dfa, including the tire-run specimens) need the typed
     field populated for verdict-reading consumers, and the evidence
     honestly self-identifies (`derived from status=…`). Recall's
     mirror exists because recall reads metadata directly, not cards.
     Cost is ~10 lines; sunset is natural (new runs stamp at break
     sites).

## What went well

- No reviewer challenged the decree implementation itself — the
  one-field-two-channels precedence design, the defer/overwrite
  policies, and the GOAL_VERDICTS/VALID_STOP_VALUES split all passed
  three lenses unchallenged (the Architect probed exactly this and
  found the gap in a *consumer*, not the design).
- The choke-point accounting fixes (interrupted class, demotion
  rebucket, rescue-partial widening) drew zero findings.
- Zero hallucinated claims for the eleventh consecutive round; every
  cited file:line checked out.

## Lead judgment

Accept 1, 2, 4, 5-in-part; reject 3 (pre-existing, and the "fix" would
be worse), 6 (deliberate, honest, self-sunsetting). Finding 2 is the
round's best catch: it correctly re-litigated a documented cut by
showing the chunk itself changed the cut's safety basis — the exact
failure mode the review discipline exists for. Finding 1's three-lens
consensus against an intent line I wrote ("persisted along the rail…")
is a reminder that a rail claim in the intent statement gets audited
literally — good.

Pin tests added for every accepted fix
(`tests/test_stop_verdicts.py`: evidence on rows + cap + empty-drop,
post-hoc evidence, off-vocabulary rejection at both seams, two-channel
repeat guard, interrupted-row learnability, close evidence reaching the
ledger row). BACKLOG's "Stop-verdict deliberate cuts" entry updated:
the merge-failure ordering gap is now CLOSED via the post-hoc re-stamp.
