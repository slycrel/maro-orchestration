---
status: living
---

# Reading Queue — docs awaiting Jeremy

Rows here render to the **Reading** tab on the viz server
(`https://maro.feifdom.com/reading.html`, regenerated whenever the runs
index is). Each row links to the GitHub-rendered copy on `main` — nothing
is self-hosted (Jeremy 2026-07-28: "better just as a link to the
github-pushed artifact so we don't reinvent the wheel"). A row is only
useful once its doc has landed — land first, then queue.

Add a row when a landed doc needs Jeremy's eyes for a decision — not for
every record. When it's read/decided, MOVE the row to Done (don't delete
it; the queue's history is part of the decision record).

## Queue

| Added | Doc | Why / decision needed |
|---|---|---|
| 2026-07-31 | [Lesson-corpus surprise read](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-07-31-lesson-corpus-surprise-read.md) | LeAct surprise-count diagnostic: count entries that SURPRISE you (not wrong ones — instructions in the doc). Zero = mirroring collapse confirmed; nonzero = the seed set a Δ-gate gets evaluated against. React in a new session with entry numbers. |

## Done

| Added | Doc | Resolved |
|---|---|---|
| 2026-08-01 | [BACKLOG § Fail-open judge-error edges](https://github.com/slycrel/maro-orchestration/blob/main/BACKLOG.md) | 2026-08-01 — Jeremy: "let's fix the promote validation." Adapter wired same session; validation harness live for the first time, SKILL_PROMOTED events stamp `validation: passed/unjudged/skipped`. |
| 2026-07-28 | [Telegram-runs review](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-07-28-telegram-runs-review.md) | 2026-07-29 — forked session adjudicated all dispositions (Jeremy granted direction/extraction/priority); provenance gate SHIPPED, db37d525+9d6b63fe quarantined, envelope specced (docs/DISPATCH_ENVELOPE.md), maro-dispatch skill 0.2.0 installed. "Already recovered" finding corrected: true on mini2, false-in-effect here — no artifact channel at the dispatch boundary. |
| 2026-07-28 | docs/history/2026-07-28-thread-census.md | 2026-07-28 — split ratified (cap 7), drift batch adjudicated (all four re-parked falsifiably), free slot = closure-check unification |
| 2026-07-28 | [M1 vs box workflow contrast](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-07-28-m1-vs-box-workflow-contrast.md) | 2026-07-29 — **CLOSED, item archived.** All four questions answered (§7) + the residual machine question (§8). Load-bearing claim SPLIT: diff-vs-run observation confirmed, merge-gate remedy rejected ("no master/slave between boxes"); observe-only thresholds rejected as too heavy. Spine corrected to two machines / three kinds of evidence. Produced four new items incl. the newly-decreed out-of-the-box invariant. |
