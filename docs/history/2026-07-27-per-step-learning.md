---
status: record
---

# Per-step learning — provisional lessons from verified steps + achieved-not-done

Jeremy's ask (2026-07-27): "Should we tweak the learning to be possible on a
per step basis (or series of steps... or sidequest success)? Seems silly to
toss possible good learning out just because the high level didn't work out."

**The principle shipped: learn at the granularity where verification actually
happened — and inject conservatively.** The run-level gate
(`is_learnable_outcome`) was right about runs; it was just applied at the only
granularity that existed when it was built. Independent convergence: Jeremy's
work-box session (2026-07-27 handoff doc §5) invented the same
confirmed/provisional shape for Claude Code's `memory:` feature the same week.

## What was tossed before this chunk

1. **Achieved-but-stuck runs** (the star find): `classify_outcome` consulted
   `goal_achieved is True` only inside `_SUCCESS_STATUSES`, so a judged
   SUCCESS with status "stuck" classified `failed` — excluded from
   crystallization, evolver examples, and curation surfacing. Two live
   specimens: `692bd96f-brisk-lichen`, `d9f01e13-golden-birch`.
2. **Verified-step method evidence on failed runs**: run-level extraction runs
   failure-framed on the whole-run summary; a run stuck at step 9/10 with
   eight individually-verified steps contributed nothing step-scoped.

## What shipped

### 1. `achieved-not-done` success class (verdict-preferred branch)

`classify_outcome` gains an `elif achieved is True` branch after the
chunk-9 #4 incomplete-rebucket, before `_PARTIAL_STATUSES`/`_FAIL_STATUSES`:
a judged True with a non-success status is achievement evidence — the mirror
of `done-not-achieved`. Consumer census:

- `outcome_policy`: class added to `_LEARNABLE_SUCCESS_CLASSES`; raw-row twin
  added (`goal_achieved is True → True`, after the curated-class branch so
  curation still wins, after the budget/interrupt fail-closed check which
  only fires when achieved is NOT True).
- `loop_report._SUCCESS_CLASS_INFO` + `notify_telegram._CLASS_LABEL`: rows
  added (and the missing `interrupted` rows backfilled — both maps lagged the
  chunk-9 vocabulary; fallbacks had covered them).
- `recall._failing`: already verdict-preferred (`goal_achieved is True → not
  failing`) — no change needed.
- `decision_prior`: carries the class as an opaque string beside
  `goal_achieved` — no change needed.
- Interrupt + judged True → `achieved-not-done` (decree-consistent: the
  interrupt stays in the status channel; the verdict is the map observation).

### 2. Provisional lesson lifecycle (`knowledge_web`)

- `TieredLesson.provisional: bool = False` (old rows deserialize False).
- `record_tiered_lesson(provisional=True)`: entry score
  `0.6 + 0.3·novelty` — same novelty bonus, ceiling 0.9 stays under the
  confirmed 1.0 floor; decay disposes of unconfirmed rows in ~1 week.
- **Promote-on-evidence**: a confirmed-context re-record that dedup-matches a
  provisional lesson clears the flag (`_reinforce_tiered_lesson(confirming=)`).
  A provisional-context match reinforces score/sessions (the observation is
  real) but cannot confirm. Deliberate: two failed-run sightings are not
  confirmation.
- **Never promotes to LONG** while provisional — guarded in BOTH
  `_post_reinforce_hooks` and the `run_decay_cycle` backstop (LONG is
  decay-free; an unconfirmed row reaching it would be permanent).
- **Excluded from every injection surface**: `query_lessons` (new
  `include_provisional=False` default), `inject_tiered_lessons` (both tiers,
  uniform), `memory_bridge.ingest_lessons_to_store` (skip at parse).
  Known lag, accepted: the bridge is ingest-once by offset, so a lesson
  confirmed after being skipped only ingests after an offset reset; the live
  tiered store — the primary injection surface — sees confirmation
  immediately.

### 3. `extract_step_lessons` (`memory.py`)

One LLM call on runs that fail the learnability gate, over steps with
`status="done"` AND `confidence="strong"` (the verify ladder's positive
verdict; weak/inferred/unverified don't qualify). Capped at 8 steps (logged,
not silent). Records 0-3 lessons `provisional=True` with
`evidence_sources=["loop:<id>"]`.

**Asymmetric bar (prompt-level)**: the extractor is forbidden goal-level
success claims AND negative/deadness claims ("X doesn't work") — a failed run
is not evidence a method is dead, and a wrongly-recorded dead-end costs more
than a missed tip. The "recovery" lesson type is deliberately absent from the
step-pass vocabulary: speculating about the failure is the run-level
extraction's job; the step pass is scoped to what verifiably worked.

**Idempotent** via a `step_lesson_count` stamp on the outcomes row
(`stamp_outcome_step_lessons` / `outcome_row_has_step_lessons`) —
`run_deferred_learning` is called for loops that didn't defer, and without
the stamp a failure-shaped run would re-pay the call every post-closure pass.
Stamped even at 0 recorded: the pass ran; same steps → same nothing.

Killswitch: `memory.step_learning_enabled` (default ON, DEFAULTS.md row,
census-enforced).

### 4. Call sites (`loop_finalize`)

- **Immediate lane** (`_finalize_loop`, after `record_step_trace`): fires when
  `loop_status != "done"`. Verdict unknown here; if closure later judges
  achieved=True the run also becomes learnable, and the provisional lessons
  remain valid (verified steps are verified regardless). *Post-review note:*
  run-level extraction now ALSO defers for non-done closure-lane runs (the
  review's three-lens HIGH — see the companion review record); the step-lesson
  immediate lane stays at finalize because provisional evidence is
  verdict-independent by construction.
- **Post-verdict lane** (`run_deferred_learning`, before the skill half):
  fires when the stamped row fails `is_learnable_outcome` — the judged-False
  deferred runs. Final loop only (earlier attempts' steps are gone, same cut
  as the skill half).
- The lanes are disjoint by status; the row stamp guards the overlap where
  `run_deferred_learning` receives not-deferred loops.

### 5. Step traces gain `confidence`

`record_step_trace` persists the per-step verify confidence so the trace can
answer "which steps individually verified" after the ephemeral StepOutcome
objects are gone (evolver context today; a future trace-driven re-extraction
path would need it).

## Deliberate cuts

- **Side-quest success learning**: no runtime unit exists yet (§13d side-quest
  DAG is design-only). The rule generalizes when it ships: a side-quest that
  closed verified is a learnable unit regardless of parent outcome.
- **`blocked_on` diagnosis capture**: star is prompting-only alpha; a
  surviving typed diagnosis is arguably the most valuable learning a failed
  run produces, but the runtime seam (`_BlockDecision` graduation) doesn't
  exist yet. Wire when it does.
- **No provisional-count readout**: rows are greppable
  (`jq 'select(.provisional)'` over medium/lessons.jsonl); a discretion-readout
  tally can ride a later chunk if the population grows enough to matter.
- **Legacy flat-store non-exposure**: step-pass lessons ride the tiered store
  only (never the outcome-row `lessons` field), so the legacy top-up path
  can't leak them by construction.

## Tests

`tests/test_step_learning.py` (24 pins): classify branch + order vs the
incomplete-rebucket, learnability (curated + raw twins), label-map census,
provisional entry score / round-trip / old-row default, injection exclusion
(query + inject + bridge), confirm-clears / provisional-doesn't,
LONG-promotion guard (both paths, plus flag-cleared-does-promote), no-LLM-call
short-circuits (killswitch, no verified steps), evidence stamping, type
coercion, idempotence stamp, prompt contract pins, step cap, trace
confidence. One legacy pin updated: `test_outcome_policy` outcome13
(stuck+True) flipped False→True — that row encoded the inversion this chunk
exists to fix.
