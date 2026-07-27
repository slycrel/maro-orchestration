---
status: record
---

# Tire-goal rerun — needle measurement after the token-brake land

Jeremy's ask (2026-07-26 evening): land the mid-step token brake, then
"re-run the test" — the 1971 F-250 tire goal whose run-3 baseline
motivated the brake (fetch-chain step ingesting 2.1M fresh tokens).

## Runs compared

| | baseline (run 3) | rerun |
|---|---|---|
| run / loop | `1bfd0894-noble-marsh` / 597558f4 | `b52b7731-swift-echo` / 3289a1b6 |
| code | pre-brake | brake landed (90ef39c) |
| steps recorded | 4 (all done) | 7 (6 done, 1 blocked) |
| tokens_in (billed, summed) | 4,372,646 | 3,653,980 (−16%) |
| tokens_out | 55,175 | 43,233 (−22%) |
| max single-step tokens_in | 2,128,381 | 1,955,183 |
| cost (metrics, loop-attributed) | $2.14 | $1.74 (−19%) |
| cost (run meter at stop) | ~$3.00 | $2.4932 |
| ending | stuck: cost hard stop | stuck: cost hard stop ($2.00 budget + $0.40 slush, after step 7) |
| deliverable | none (goal re-dispatched) | **complete FINAL_ANSWER.md** — 3 verified tiers (Milestar $144.06 / Falken A/T4W $233.00 / BFG KO3 $244.99), wheel spec, clearance caveat, winner |

## What the numbers do and don't say

**The goal finished this time.** All five required fields per tier
verified against live Amazon product pages; wheel spec labeled
verified-vs-assumed; winner named. The baseline burned its budget
before assembling any of that.

**The brake never fired.** No `step token brake` warnings in the run
log — the heaviest step (1.95M billed tokens_in across its turns)
stayed under both per-call ceilings (fresh 300K / weighted 600K are
per-subprocess-call accumulation, and this run's fetch work arrived
via URL pre-fetch + saved artifacts instead of one runaway
tool-fetch chain). The brake's contribution this run was zero direct,
backstop-only. Do not read the −16%/−19% deltas as brake savings.

**Warm-start confound.** Three artifacts survived from the baseline
run in the persistent project dir (`wheel_spec.md`, `size_verdict.md`,
`tire_budget.md`, mtimes 05:34–05:44Z vs rerun start 09:44Z). The
rerun's steps verified them instead of re-researching — that is the
persistent-project design working as intended (the goal was
repair-shaped: "Repair that failure now"), but it means this is a
warm-vs-cold comparison, n=1 each. The honest claims are only:

1. The repair-shaped re-dispatch over a persistent project converges:
   run 4 completed what runs 1–3 could not, at the lowest cost of the
   series.
2. The brake armed cleanly in production and stayed silent on a
   legitimate heavy run — no false fire at these ceilings (the
   calibration evidence said p99 headroom was wide; this is one more
   confirming sample).
3. The ending is still recorded as bare `stuck` — the run's stop was
   a preset cost cap with a finished deliverable sitting in
   artifacts/. This is precisely the out-of-budget /
   done-vs-stopped conflation the chunk-9 #4 stop-verdict split
   (in flight, same day) exists to fix; this run would have carried
   `stop_verdict=out-of-budget` with the cap in evidence.

## Side-finding

`metrics.spend_for_loops` and the run cost meter disagree ($1.74 vs
$2.49): the meter accrues closure/verification and killed-call
estimates that aren't step-attributed. Known shape, not new — but
worth remembering when comparing "cost" across records.
