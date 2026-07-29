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
| 2026-07-28 | [M1 vs box workflow contrast](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-07-28-m1-vs-box-workflow-contrast.md) | **One question left** (§7 "Still open"): which 2014 Mac Mini is the live Poe/Telegram test prototype, and does "develop against that same copy" relax mini2's zero-creds/propose-only decree? The §§1–6 rewrite (two lanes → three contexts) blocks on it. |
| 2026-07-28 | docs/history/2026-07-28-telegram-runs-review.md | Telegram-runs review: Poe scaffolding contamination + adoptable items — dispositions to be decided in the forked review session |

## Done

| Added | Doc | Resolved |
|---|---|---|
| 2026-07-28 | docs/history/2026-07-28-thread-census.md | 2026-07-28 — split ratified (cap 7), drift batch adjudicated (all four re-parked falsifiably), free slot = closure-check unification |
| 2026-07-28 | M1 vs box workflow contrast — *questions 1–4* | 2026-07-28 — all four answered (§7). Load-bearing claim SPLIT: diff-vs-run observation confirmed, merge-gate remedy rejected ("no master/slave between boxes"); observe-only thresholds rejected as too heavy; out-of-the-box invariant newly decreed. Row stays in Queue for the one residual question. |
