---
status: record
---

# Chunk-9 #4 — Stop-verdict split (§13b wiring)

The stop-path survey (2026-07-23) mapped ~50 seams where runs end and
found the endings collapsed into a handful of statuses that conflate
"why it stopped" with "how it looks in accounting". This chunk ships the
typed stop verdict: a machine-readable WHY, recorded at the break site
that decided the ending, riding BESIDE `status`/`stuck_reason` (status
vocabulary unchanged).

## Vocabulary (`src/stop_verdicts.py`)

Four goal-directed verdicts + one interrupt **event marker**, each with
a type-derived reopen condition (documented in the module, not a
free-text field):

| verdict | meaning | reopens when |
|---|---|---|
| `out-of-budget` | preset cap ended it (iterations, tokens, cost, wall clock, landing synthesis) | budget granted |
| `thesis-refuted` | convergent evidence the approach can't work (stuck-streak, retry churn, exhausted options) | new landmark/vantage |
| `reachable-but-not-worth-it` | judged close: achievable, not worth the spend (director escalation close) | cost/value estimate moves |
| `lost-the-plot` | the run's map diverged from the territory (closure/provenance demotion of a "done") | re-anchor on the actual goal |
| `external-interrupt` | process-level cut-down (kill switch, dead backend, infra/merge failure, stranded owner, busy refusal, awaiting input) | the outage clears — carries NO goal evidence |

`external-interrupt` implements Jeremy's decree (GOAL_BRAIN 2026-07-27
item 5, answering the chunk-9a review's open question): it is **NOT a
fifth verdict** — "the four verdicts are observations about the map; an
interrupt is an event about the run." The decree asks for run-level
interrupted:reason plus whichever verdict the evidence supported at
interrupt time. Implementation call (mine): one field with precedence
rather than two fields — evidence-backed verdicts stamp first-write-wins
at their own break sites, so a supported verdict always owns the field
before interrupt machinery runs (run 3's shape reads `out-of-budget`,
not the marker); the marker lands only when no map observation existed,
with the reason in `stop_evidence`. `GOAL_VERDICTS` excludes the marker
structurally, so learning consumers can't mistake it for a verdict. If
Jeremy prefers the literal two-field shape, the rename is mechanical
(the marker becomes an `interrupted_reason` metadata key; consumers
already gate on the one string). The §13b contract holds for all five
values: verdict/marker + evidence (≤500 chars) + typed reopen condition.
`INTERRUPT_STATUSES` names the four statuses the survey found falling
into the success_class "unknown" hole.

## The rail

`ctx.stamp_stop(verdict, evidence)` on LoopContext — **first write
wins** within a run: the break site closest to the evidence knows the
specific cause; later generic machinery can't overwrite. From there:
LoopResult fields → unconditional metadata stamp at finalize (after the
merge-back blocks — they can add an external-interrupt of their own;
empty clears a stale verdict from an earlier restarted loop, so last
LOOP wins across restarts, first SITE wins within one) → outcome-ledger
row (`Outcome.stop_verdict` via reflect_and_record → record_outcome) →
run card (classify_outcome passthrough + status-derived
external-interrupt fallback for pre-stamp runs).

Post-hoc stamps, with explicit cross-layer policies:

- **Handle demotions defer** (`_stamp_stop_on_demotion`): a
  loop-stamped verdict (e.g. landing-synthesis out-of-budget) outranks
  the handle-level lost-the-plot — same stop event, closest site wins.
- **Director escalation close overwrites**
  (`_stamp_close_stop_verdict` + `stamp_outcome_stop_verdict`): the
  close is a *later, better-informed judgment ending the chain* — on
  the max-depth escalation path an out-of-budget stamp is always
  already present, so defer-if-present would make the
  reachable-but-not-worth-it seam dead code (the survey's "recorded
  NOWHERE" headliner). The prior verdict stays visible as
  `[refines: …]` in evidence.

## Break sites stamped

- loop_init: budget gate (out-of-budget), kill switch, busy refusal
  (external-interrupt); agent_loop write-fence (external-interrupt)
- loop_execute: max_iterations ceiling, landing synthesis, token/cost
  budget breakers, budget_runaway (out-of-budget); stuck-streak
  terminal (thesis-refuted)
- loop_post_step interrupts: kill switch, operator should_stop
  (external-interrupt); wall-clock timeout (out-of-budget — it's a
  preset cap, resolving the survey's ambiguity)
- loop_blocked decisions: re-decompose-failed + MISSING_INPUT +
  adapter-hung (external-interrupt); TIMEOUT split-recovery-failed
  (out-of-budget); retry-churn + exhausted-all-options
  (thesis-refuted). Skill-failure attribution is skipped when the
  decision is external-interrupt (cause-blind blame fix).
- loop_finalize: three merge-failure branches (external-interrupt)
- handle: provenance guard + closure-contradicts-done + post-escalate
  demotions (lost-the-plot)
- director: escalation close (reachable-but-not-worth-it)

## Accounting fixes at the choke point (`classify_outcome`)

- New success_class **"interrupted"** for `INTERRUPT_STATUSES`
  (replacing the "unknown" hole). Consumers verified graceful on the
  new value before it shipped (loop_report badge, notify_telegram,
  outcome_policy frozenset, evolver/decision_prior passthrough).
- **Demotion rebucket**: status `incomplete` + judged
  `goal_achieved=False` → `done-not-achieved` (the status flip no
  longer launders judged demotions into "partial"). The salvage block
  (`rescue_partial`) still fires for demoted runs.
- Card passthrough of `stop_verdict`/`stop_evidence` +
  status-derived external-interrupt fallback.

## Four raw-status consumers rewired

1. `outcome_policy.is_learnable_outcome` — out-of-budget with no
   positive goal verdict is not learnable (kills the
   landing-synthesis → "done" → learning-seed path).
2. `recall` repeat-guard — unjudged external-interrupt attempts don't
   arm `all_failing` (an outage is not goal disproof); interrupt count
   surfaced in the prior-attempts brief.
3. `strategy_evaluator._outcome_weight` — unjudged external-interrupt
   scores 0.5 neutral instead of 0.0 (don't blame the strategy for the
   outage). Judged verdicts still win.
4. `attribution.attribute_batch` — external-interrupt rows excluded
   from failure attribution unless a judged failure verdict exists.

## Deliberate cuts (BACKLOG'd)

- Parallel/fan-out lane unstamped (ambiguous aggregation; bypasses
  `_build_result_and_finalize`).
- Navigator escalate unstamped (who-decides-next, orthogonal).
- DirectorResult/WorkerResult + build_loop_runner keep their own
  vocabularies (consumers named in BACKLOG).
- NOW-lane demotions: success-class accounting fixed via the
  choke-point rebucket (they write `incomplete` + judged False);
  the lost-the-plot metadata stamp itself is deferred.
- Known ordering gap: the outcome row is written at `_finalize_loop`,
  BEFORE the merge-back blocks — a merge-failure external-interrupt
  reaches LoopResult + metadata but not the ledger row (pre-existing
  row-write ordering; the post-hoc stamp exists if it ever matters).

## Tests

`tests/test_stop_verdicts.py` (vocabulary, rail, post-hoc stamps, all
four consumers) + `tests/test_run_curation.py` extensions (interrupted
family, rebucket, passthrough). The old
`test_classify_incomplete_is_partial` pin was superseded in place.
