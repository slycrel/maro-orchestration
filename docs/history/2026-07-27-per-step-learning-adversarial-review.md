---
status: record
---

# Adversarial review — per-step learning chunk (d1b442c)

Post-land review per the per-chunk discipline. 3 Codex lenses
(`codex exec`, opposite model family): Skeptic + Architect + Minimalist
(972 insertions / 19 files = large). Thirteenth consecutive round with
every verified claim checked against the tree before fixing.

## Intent

Enable learning from failed runs at the granularity where verification
actually happened, without polluting injection surfaces: achieved-not-done
classification, provisional step-lesson extraction, provisional lifecycle
(reduced score / no injection / promote-on-evidence).

## Verdict: CONTESTED → remediated same session

11 raw findings deduplicated to 7 distinct; **6/7 verified real, 0
hallucinated** (the seventh is factually accurate code-reading whose
"defect" framing didn't survive context — see rejected). Score to date
across thirteen rounds: reviewer code-claims keep verifying at far above
the historical 30-50% fabrication rate; evidence-path lenses + a concrete
commit target appear to hold the floor.

## Findings and dispositions

1. **HIGH (three-lens consensus — Skeptic 1 / Architect 1 / Minimalist 1):
   non-done closure-lane runs extracted run-level lessons verdict-blind,
   before the verdict existed.** `defer_lessons=defer_learning and
   loop_status == "done"` meant a stuck run later judged
   goal_achieved=True (achieved-not-done — the exact bug population this
   chunk names) had already recorded failure-framed, NON-provisional
   lessons into confirmed injection surfaces at finalize; the deferred
   path then skipped it (row already extracted). The chunk's
   classification fix was real but the primary learning path still
   counted judged successes as failure evidence. **ACCEPTED — fixed:**
   `defer_lessons=defer_learning` (all closure-lane statuses defer; the
   deferred rail was already verdict-aware end to end —
   `extract_deferred_lessons` passes `goal_achieved` and the extraction
   prompt frames "stuck — goal verified achieved"). The fail-safe is
   unchanged: `defer_learning=False` callers (no closure will run) still
   extract immediately. Pins: parametrized defer-all-statuses +
   fail-safe-unchanged.

2. **HIGH (Minimalist 2, worse than reported): provisional lessons leaked
   through the graveyard injection path — and resurrection CLEARED the
   flag.** `search_graveyard()` had no provisional filter (live or
   archived scan); `recall` (substrate #4) and `prereq` both inject its
   results with `resurrect=True`, and resurrection calls
   `reinforce_lesson` → `_reinforce_tiered_lesson(confirming=True)` — a
   topic match would have both injected the unconfirmed lesson AND
   counted as the confirming re-record. **ACCEPTED — fixed:** provisional
   rows excluded from both graveyard scans. Pin: bait-and-control test
   (confirmed row surfaces, provisional doesn't; flag + times_reinforced
   intact after resurrect=True).

3. **MEDIUM (Skeptic 3): provisional-context reinforcement accrued
   promotion-grade validation while hidden.** `confirming=False` still
   incremented `sessions_validated`; three failed-run sightings + one
   confirmation = instant LONG promotion via `_post_reinforce_hooks` in
   the same call that cleared the flag. **ACCEPTED — fixed:**
   `sessions_validated` (and the F5 multi-session confidence bump) now
   move only on confirming reinforcement; score still reinforces (the
   observation is real). Promotion now requires PROMOTE_MIN_SESSIONS
   *confirmed* observations. Pin extended.

4. **MEDIUM (Minimalist 3): `promote_lesson()` itself had no provisional
   guard — the CLI (`maro memory promote`) calls it directly.**
   **ACCEPTED — fixed:** guard at the promotion boundary (returns False,
   logged); the hook/decay-cycle caller guards stay as early-skips.
   Pin: direct-call refusal with eligibility forced.

5. **MEDIUM (Skeptic 2 / Architect 3): the asymmetric bar is prompt-only —
   the recorder accepts any text, and a test pinned a goal-success claim
   ("The goal landed perfectly") as recordable.** **ACCEPTED IN PART:**
   test string replaced (a pin must not enshrine the contract's
   violation). The deterministic-semantic-filter ask is REJECTED:
   validating prose claims deterministically isn't feasible, and the
   structural blast-radius control IS the provisional lifecycle (no
   injection until a learnable-context re-record independently produces
   the same lesson). That is the boundary mechanism; a lexical blocklist
   would be brittle theater.

6. **MEDIUM/LOW (Skeptic 4 / Architect 4): idempotence is not atomic and
   failed passes retry.** Both true, both deliberate — REJECTED as
   defects, docstring made honest: the stamp lands only after a
   SUCCESSFUL pass (a transient LLM failure must not permanently forfeit
   the learning — same retriable convention as the contradiction
   adjudicator); the check→stamp window across the two lanes is
   temporally disjoint (finalize precedes closure) and a duplicate call's
   lessons dedup-reinforce rather than duplicate.

7. **MEDIUM (Architect 2): the new learnability/classify branches bypass
   `verdict_trust` — a DIRECTIONAL (low-confidence) judged-True could
   gate as learnable.** Factually accurate, REJECTED as a chunk defect:
   the pre-existing sibling branches (`done+True → success`, raw
   `done+True → learnable`) are equally trust-blind — the new branches
   are consistent with the architecture, which applies `verdict_trust`
   at the seams with teeth (V2 cadence windows, contradiction emitter's
   era-10 single-gate law, evolver scans), and skill crystallization is
   structurally unreachable for non-done statuses. Whether
   classify_outcome should become trust-aware is a real design question
   for ALL its verdict branches, not this chunk's two — BACKLOG'd.

## What went well (reviewer consensus)

Injection-surface exclusion on the three named surfaces was implemented
uniformly; the promote-on-evidence shape and the entry-score ceiling
under the confirmed floor drew no findings; the test suite's census
style (label maps, prompt contract, both promotion guards) was called
out as the reason several would-be findings died in the reviewers' own
drafting.

## Lead judgment notes

Finding 1 is the round's lesson: the chunk censused *consumers* of the
new class thoroughly and never re-examined the *producer* timing it sat
on — the immediate-extraction lane predated the chunk, so it read as
landscape. Three independent lenses converged on it immediately.
Producer-side timing belongs in the consumer census.
