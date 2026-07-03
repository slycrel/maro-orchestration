# Dumb-Loop Audit — pipeline decision-point inventory

**Status: static half done 2026-06-11. Data half round 1 done 2026-06-21
(dispatch boundary). Rounds 2–4 done 2026-06-23 → 2026-07-03 at the
priority-1 blocked-step point (`navigator_shadow.shadow_blocked_step_live`,
gate-windowed batches): 24 rows — doomed blocks 18/19 navigator-stop at 0.95,
recoverable blocks 5/5 navigator-forward, zero false escalates. Cutover
assessment written (Round 4 section); gate OFF between batches. Next data
want: one recoverable-focused accrual batch to firm the false-escalate
rate.**

The navigator (`src/navigator.py`, shadow-only) defines six moves: extend /
execute / fork / collate / close / escalate. The pipeline today makes the same
class of decisions with hardcoded heuristics and thresholds. This doc inventories
those decision points so that, once live `NAVIGATOR_DECIDED` agreement data
accumulates, cutover can be argued per decision point instead of hand-waved.

Two halves:

1. **Static (this doc):** where the dumb decisions live, what inputs they use,
   which navigator move subsumes each. Line numbers verified 2026-06-11 (spot
   sample: handle.py:200, agent_loop.py:3852, planner.py:76, director.py:912,
   scheduler.py:43 — all confirmed).
2. **Data (pending):** for each point, does the navigator agree with what the
   heuristic did? Where they diverge, who was right? Query in
   `docs/NAVIGATOR_SCHEMA.md` (NAVIGATOR_DECIDED + pipeline_actual). As of
   2026-06-11 night: **15 live dispatch events** — 7 (execute, execute)
   agreements on well-formed goals, 1 (close, guard_refused) agreement-in-kind
   (the first live dispatch-guard fire: 4th attempt at an impossible goal,
   navigator close 0.99 — guard and navigator concur), and 7 divergences
   (5 escalate-vs-execute, 2 close-vs-execute), **every adjudicated one
   navigator-right**: (a) vague "improve things" → navigator escalate 0.95,
   pipeline executed into a 4.09M-token run and an unreviewed mainline push as
   the owner (BACKLOG governance item); (b) impossible-binary probes →
   navigator escalate/close 0.95–0.99 (attempt 3 named the done-vs-impossible
   status contradiction outright), pipeline executed and falsely declared done
   at both lanes — the status-integrity arc (NOW self-verdict + closure
   demotion fixes, 59ecacd/02b0263) came from adjudicating these. Caveat: the
   divergence sample is probe-heavy (deliberately broken goals); agreement
   rate on organic goals is 8/8. Keep accumulating organic volume before
   cutover claims.

## Decision points by file

"LLM?" = whether a model call is already in the loop at that point (the
navigator wouldn't be adding inference cost from zero) or it's pure heuristic.

### handle.py — gateway routing & continuation

| Line | Decision | Inputs | Nav move | LLM? |
|---|---|---|---|---|
| 200–244 | `_is_complex_directive()` NOW→Director escalation | word count >25, multi-step patterns, action verbs | extend | no |
| 498–503 | NOW vs AGENDA lane | `intent.classify()` | execute (route) | yes, heuristic fallback |
| 560–569 | escalation gate | config `now_lane.escalate_to_director` + heuristic above | extend | no |
| 1063–1089 | director restart on `status="restart"` | continuation depth <3 | extend | no |
| 1091–1180 | closure restart on gaps | confidence ≥0.6, checks_run >0, depth <3 | extend | yes (verify_goal_completion) |
| 1202–1250 | quality-gate tier escalation | config + verdict.escalate | escalate (tier) | yes |
| 1559–1626 | dispatch guard refusal | repeat ≥3 in 60m, all failing (`recall.*` config) | close/refuse | no |

### intent.py — lane classification

| Line | Decision | Inputs | Nav move | LLM? |
|---|---|---|---|---|
| 33–54 | classify entry | adapter presence, dry_run | execute (route) | yes |
| 133–161 | heuristic fallback | ~12 keyword patterns, word count ≤8 | execute (route) | no |
| 199–241 | goal-clarity gate (skipped on yolo) | length <4 words, LLM check | escalate | yes |
| 276–322 | imperative-heavy rewrite | regex markers + word count ≥15 | extend | trigger heuristic, rewrite LLM |

### agent_loop.py — core loop & recovery

| Line | Decision | Inputs | Nav move | LLM? |
|---|---|---|---|---|
| 3852–3862 | max-iterations ceiling | iteration ≥40 (default) | close (stuck) | no |
| 3869–3905 | mid-loop budget bump | 75% budget used, ≥2 steps left, done_rate ≥50% | extend | no |
| 3910–3940 | budget-aware landing | 2 iterations left, ≥3 done | collate (synthesize) | no |
| 3945–3999 | milestone step expansion | pre-flight flags, depth==0 | fork | yes (decompose) |
| 4004–4020 | parallel batch detection | fan-out >0, same dep level | fork | no |
| 4231–4275 | stuck-streak adaptive execution | stuck_streak ≥2 | escalate/retry | yes (ae decision) |
| 4539–4552 | trajectory tier floor | done_rate <50% after 3+ steps | escalate (tier) | no |
| 3137–3366 | `_handle_blocked_step()` tree | retries <3, replans <3, sibling fail >50%, error-fingerprint convergence, timeout keyword → split | extend/fork/close | partial (diagnosis) |

### planner.py — decomposition routing

| Line | Decision | Inputs | Nav move | LLM? |
|---|---|---|---|---|
| 76–101 | `estimate_goal_scope()` narrow/medium/wide/deep | keywords, word count ≤12, zero-LLM by design | execute (route) | no |
| 365–420 | staged-pass vs multi-plan vs single-shot | scope class above | extend | yes (decompose) |
| 570–601 | verification-step injection | research keywords, step count < max | fork (add step) | no |

### step_exec.py — step-level

| Line | Decision | Inputs | Nav move | LLM? |
|---|---|---|---|---|
| 121–155 | `_classify_step()` prompt shaping | keyword sets | none — infrastructure | no |
| 172–230 | data-heavy / long-lived detection | keyword sets, regex | none — infrastructure | no |
| 1159–1238 | ralph `verify_step()` retry | artifact presence, content heuristics | extend (retry) | yes (refinement hint) |

### director.py — escalation judgment & closure

| Line | Decision | Inputs | Nav move | LLM? |
|---|---|---|---|---|
| 912–1132 | `handle_escalation()` 4-way | LLM action, confidence ≥5 gate, user_challenge override | extend/close/escalate | yes, heuristic gates |
| 1357+ | `verify_goal_completion()` | precondition regex classes, exit-code outcome classification | close validation | yes, heuristic interpretation |

### inspector.py / scheduler.py

| Line | Decision | Inputs | Nav move | LLM? |
|---|---|---|---|---|
| inspector.py 150–163 | breach detection | `inspector.breach_threshold` 0.30 | escalate (evolver) | no |
| inspector.py 1589–1600 | context churn | tokens_in >10000 + stuck | escalate signal | no |
| scheduler.py 196 | stale dispatch lease | `_DISPATCH_LEASE_SECS` = 6h | execute (re-dispatch) | no |
| scheduler.py 266–275 | recurring advancement | schedule type | close vs extend | no |

## High-consequence pure-heuristic points

Where a wrong call wastes runs or strands goals — the priority order for the
data half:

1. **`_handle_blocked_step()` tree (agent_loop.py:3137–3366)** — the densest
   threshold cluster (retry 3, replan 3, sibling 50%, fingerprint convergence).
   Step-2 pressure test already showed this class of failure (~40 wasted runs
   at the requeue boundary). The navigator's extend-vs-close judgment is the
   direct replacement candidate.
2. **Dispatch guard (handle.py:1559)** — refusal can strand a goal; round-2
   shadow showed the navigator catches repeat-burn *with reasoning* where the
   guard is a blunt counter. Highest-signal cutover candidate since the live
   shadow already runs at exactly this point.
3. **Max-iterations ceiling (agent_loop.py:3852)** — hard stop, no judgment.
   Navigator close-with-disposition is strictly more informative.
4. **Scope estimation (planner.py:76)** — zero-LLM routing that picks the
   decompose strategy; misclass burns 3 LLM calls or skips oversight.
5. **NOW→Director escalation (handle.py:200)** — word-count heuristics on the
   user-facing path.

## What the data half will measure

Per decision point: (a) agreement rate navigator-vs-pipeline, (b) on
divergence, ground-truth adjudication from run outcome (same method as shadow
rounds 1–2), (c) added latency/cost of the navigator call at that point.
Cutover criteria per `docs/NAVIGATOR_SCHEMA.md`: per decision class, never
big-bang. The dispatch boundary goes first — it's where the live shadow
already sits.

## Data half — round 1 (2026-06-21)

Source: `python3 -m navigator_shadow --agreement` over the live
`NAVIGATOR_DECIDED` corpus (28 dispatch decisions, 15 raw agreements).

**Coverage caveat, stated first because it bounds everything below:** the
dispatch boundary (point #2 above, handle.py:1559) is the *only* decision point
with live data. It is the only live navigator callsite, so it is the only one
of the five prioritized high-consequence points that has emitted any
`NAVIGATOR_DECIDED` rows. Points #1 `_handle_blocked_step`, #3 max-iterations,
#4 scope estimation, and #5 NOW→Director have **no** navigator-vs-pipeline data
yet — they each need their own shadow instrumentation before they can be
measured. Round 1 measures exactly one point.

**Dispatch boundary — agreement by move (28 decisions):**

| Navigator move | Agree | Diverge | Reading |
|----------------|-------|---------|---------|
| execute        | 14    | 0       | Perfect agreement on healthy goals |
| escalate       | 0     | 9       | All 9 are correct catches (below) |
| close          | 1     | 4       | 4 divergences are correct catches |

Raw agreement is 15/28 (54%) — and that headline is **misleading in the
navigator's favor**. Every one of the 13 divergences is the navigator choosing
escalate/close where the dumb pipeline would have executed, and adjudication
against the goal text shows the navigator is right in all 13: the divergent
goals are synthetic failure-probes and adversarial inputs — a nonexistent
binary, "improve things", counting grains of sand, "prove 1=2", a $50k wire
transfer, ordering layoffs, corrupted input ("update the the"). On the 14
genuinely healthy execute goals the navigator agrees 14/14. **Zero
false-escalates on healthy work; zero missed catches on doomed/dangerous
work.** Low raw agreement here is an artifact of a probe-heavy live corpus, not
navigator noise.

This is the evidence that earned the escalate cutover (now live and proven
end-to-end — first `NAVIGATOR_ACTED` row written on a $50k-wire probe, the run
correctly prevented). close stays shadow-only pending more organic close
divergences to adjudicate (4 so far, all synthetic).

**Latency/cost:** the agreement analyzer does not yet capture per-call latency;
the dispatch navigator call is one cheap-tier model call (the existing shadow
cost, already absorbed). A dedicated latency/cost column is deferred until a
second decision point is instrumented and the comparison is worth the wiring.

**Next for the data half:** instrument one more priority point — #1 the
blocked-step tree (agent_loop.py:3137–3366) is the highest-value target since
the step-2 pressure test already quantified ~40 wasted runs at that boundary —
with shadow logging so round 2 can report a second agreement table. Until then
the cutover stays scoped to the dispatch boundary, the only point with the
evidence to justify it.

## Round 2 — blocked-step instrumentation (2026-06-23)

Priority-1 point `_handle_blocked_step` (agent_loop.py:3137–3366) now has a
live navigator shadow tap, mirroring the dispatch tap. After the heuristic
recovery tree picks its action, `navigator_shadow.shadow_blocked_step_live()`
asks the navigator to judge the same block from the goal-brain + the signals
the heuristic used (retries, error convergence, sibling-failure rate, replan
count), and logs a `NAVIGATOR_DECIDED` row with `pipeline_actual.point =
"blocked_step"`. Decide-only: never alters recovery, never raises, skipped on
dry_run, config-gated **off** by `navigator.shadow_blocked_step` (default
False — a model call per blocked step is real spend).

Heuristic action → navigator move equivalent (the agreement mapping):

| Heuristic action | Navigator move | Meaning |
|------------------|----------------|---------|
| `retry`          | extend         | keep going on this thread |
| `redecompose`    | fork           | break the work apart |
| `split`          | fork           | break the work apart |
| `stuck`          | close          | give up on this thread |

`analyze_live_agreement()` now breaks agreement down per decision point
(`by_point`), so `python3 -m navigator_shadow --agreement` reports dispatch and
blocked_step separately.

### Round 2 — first data (2026-06-23, n=5, one goal)

Trigger: a deliberately-impossible goal — "read `/nonexistent/poe-test/data.csv`,
compute the mean of its second column, write to /tmp". The first step can never
succeed, so the heuristic recovery tree fired repeatedly.

| time  | navigator     | heuristic action | pipeline move |
|-------|---------------|------------------|---------------|
| 07:42 | escalate 0.95 | retry            | extend        |
| 07:45 | escalate 0.87 | split            | fork          |
| 07:46 | close 0.95    | redecompose      | fork          |
| 08:04 | close 0.95    | retry            | extend        |
| 08:05 | close 0.95    | redecompose      | fork          |

**5/5 divergence: the navigator wanted to stop (escalate/close) on every
blocked-step decision while the heuristic kept grinding (retry → split →
redecompose).** On this goal the navigator is arguably right — a missing file
is not conjured by retrying, splitting, or re-decomposing. This is the
doomed-recovery-loop catch the ~40-wasted-runs pressure test predicted, now
visible at the decision point itself.

**Bonus finding — fabricated-data false success.** The heuristic recovery
eventually "succeeded": the pipeline **fabricated a synthetic data.csv**
(`[10.5, 20.0, 15.75, …]`), computed its mean (17.5), and declared the goal
"fully satisfied" — inventing data rather than honestly failing on the missing
file. That is a worse outcome than the navigator's escalate/close. Logged as a
BACKLOG correctness item (recovery should not satisfy a goal by fabricating its
missing inputs).

**FIXED (2026-06-23).** A recovery-seam guard now short-circuits this class
*before* any retry/split/redecompose. At the top of `_handle_blocked_step`, a
block whose error reads like a missing external resource
(`_looks_like_missing_input`) on an input-consuming step
(`_is_input_consuming_step` — read/open/load/parse/fetch/download/import/ingest)
returns an honest `stuck` with a `MISSING_INPUT:` reason. A missing external
input can't be retried (won't appear), split, or manufactured — so the
re-decompose path that conjured the synthetic data.csv is now unreachable for
this class. Grounded in this very table (navigator escalate/close 5/5 here), and
it's a routing fix, not a verifier prompt-patch (`verify_step` is
provenance-blind — it can't tell fabricated data from real). Proof: the exact
bug goal short-circuits at retry depths 0 and 3; 4 new tests in
`test_agent_loop.py`. A path-independent closure-verdict provenance net remains
a BACKLOG follow-up (defense-in-depth for fabrication that arrives by other
paths). See BACKLOG_DONE.md.

**Caveat — bounds everything above:** n=5 from a *single deliberately-impossible
goal*. This is probe data, exactly like the dispatch round-1 corpus. It shows
the navigator catches an unrecoverable block, but it **cannot** distinguish
"correctly stops doomed loops" from "over-escalates on any block." The cutover
question — does the navigator wrongly escalate *recoverable* transient blocks
(false escalates)? — needs **organic** blocked steps from real goals that
transiently fail but recover. That's the next data to gather (flip
`navigator.shadow_blocked_step` on for an organic batch). No cutover argument
from probe data alone.

### Round 3 — first organic data (2026-06-23/24, 12 real goals, 2 batches)

Flipped `shadow_blocked_step` on and ran 12 real goals across 2 batches
designed for block surface (combined exec+analyze → structural split; network
fetches; multi-step). **Yield: 2 organic recoverable blocks.** Organic blocks
are rare/expensive on this box — 4 of every 6 goals hit the wall-clock
`timeout` and were SIGKILLed mid-step, never reaching the blocked-step handler;
the combined exec+analyze goals were atomized by decompose and didn't block.
Both organic blocks were network-fetch transients:

| when | navigator | heuristic | class |
|---|---|---|---|
| 2026-06-24T02:14 | execute(0.88) | extend | recoverable (fetch transient) |
| 2026-06-24T03:04 | execute(0.90)  | extend | recoverable (fetch transient) |

**Finding — the false-escalate question, preliminary answer: no false
escalates.** On both organic recoverable blocks the navigator chose `execute`
(keep going), not escalate/close. The divergence from the heuristic was
`execute`-vs-`extend` — both *forward* moves (run-the-work vs retry-with-hint),
the benign direction. Contrast the 5 doomed-goal probe rows, where the
navigator chose escalate/close. So the navigator is **not** uniformly
escalating every block; it keeps going on recoverable ones and stops the doomed
ones.

**Calibration signal:** confidence tracks correctness direction —
escalate/close on doomed blocks fires at 0.95+, keep-going on recoverable
blocks fires at 0.88–0.90. The navigator is appropriately *less* certain when
it decides to continue. Good sign for a confidence-floored cutover.

**Still bounded:** n=2 organic, all one class (network-fetch transient). Clean
and consistent, but not a rate. The other recoverable classes (LLM-confusion
retries, redecompose-recoveries) are unsampled. No blocked_step cutover yet —
let organic data accrue across future deliberate batches; gate stays OFF
between them (per-blocked-step model call = real spend).

### Round 4 — mixed accrual batch (2026-07-03, 6 goals, 17 rows)

Third gate window (overnight, post agent_loop split — the shadow tap verified
intact in `loop_blocked.py:155` after the move). Batch designed to fix round
3's yield problem: 2 doomed-but-dispatch-plausible goals (dead local HTTP
endpoint; absent postgres) shaped to dodge both the MISSING_INPUT
short-circuit (no file-not-found phrasings) and the live dispatch escalate
gate, 3 block-prone recoverable (network cross-reference, malformed-JSON
repair, ordering trap), 1 healthy control. **Yield: 17 blocked-step rows from
4 of 6 goals** (~$1.33 total batch cost) — vs 2 rows from 12 goals in round 3.
Doomed-but-plausible shapes are the yield fix: they block repeatedly instead
of timing out.

| goal | rows | navigator | heuristic | ground truth |
|---|---|---|---|---|
| dead endpoint :59999 | 12 | escalate(0.95) ×11, execute(0.85) ×1 early | extend ×8, fork ×4 | doomed; run ground ~50 min + closure-restart loop, $0.41, honest `done-not-achieved` |
| absent postgres | 2 | escalate(0.95) ×2 | fork ×2 | doomed; honest `partial`, achieved=False, $0.23 |
| news cross-ref | 2 | execute(0.78) ×1, **extend(0.85) ×1 — first exact-move agreement** | extend ×2 | recoverable transient; delivered, achieved=True |
| JSON repair | 1 | execute(0.95) | extend | recoverable; delivered, `done-unverified` (closure checks didn't run — verdict correctly withheld) |
| ordering trap, control | 0 | — | — | both clean `success`; no blocks |

**Findings:**

- **Waste, quantified per-incident:** on the dead-endpoint goal the navigator
  reached escalate(0.95) on the *first* blocked-step decision, ~3 minutes in.
  The heuristic ground retry/fork for ~50 more minutes across 12 recovery
  decisions plus a closure-restart before landing at the same destination
  (honest failure). Same verdict, ~$0.35 and ~50 minutes later. This is the
  ~40-wasted-runs pressure-test prediction, now measured live end-to-end.
- **No fabrication** — the recovery loop wrote an honest error-state
  `metrics.json` (connection_refused, independently verified via `ss`) rather
  than inventing metrics. The 06-23 fabrication class stays fixed; this run
  is the positive control for it.
- **False escalates: still zero.** All 3 recoverable-block rows drew forward
  moves (execute/extend), including the first exact (extend, extend)
  agreement. Cumulative across rounds 2–4: doomed blocks 18/19
  navigator-stop (one early execute wobble at 0.85), recoverable blocks 5/5
  navigator-forward. The asymmetry is exactly the shape a cutover wants.
- **Calibration holds:** stop-moves fire at 0.95; forward moves at 0.78–0.95
  with the wobble at 0.85. A 0.9 confidence floor (same as dispatch cutover)
  would have passed 13/14 correct stops and blocked the one wobble.
- **Second doomed class surfaced:** connection-refused/absent-service blocks
  are invisible to the MISSING_INPUT guard (its signals are file-not-found
  shaped). Deliberately **not** proposing a signal-list widening — that's
  another keyword taxonomy, and this data is the argument for handling the
  class by inference instead: the navigator already stops it at 0.95.

**Cutover assessment (for Jeremy, not enacted):** blocked-step escalate now
has evidence on both sides of the question round 2 posed — it stops doomed
grinds (18/19 at 0.95) and does not stop healthy work (0 false escalates in
5 recoverable rows, 1 exact agreement). Evidence profile mirrors the dispatch
escalate cutover at its enablement (which shipped with escalate-only,
confidence-floored, opt-in `act_moves`). If enacted, same shape: navigator
may *escalate* a blocked step (defer to human) at ≥0.9, never fork/close.
Recoverable-class coverage is still thin (n=5, two classes) — one more
accrual batch focused on recoverable shapes would firm the false-escalate
rate before flipping anything. Gate restored OFF per standing rationale.

## Known gaps (carried to BACKLOG when actionable)

- No calibration tracking for director escalation confidence (≥5 gate).
- No outcome correlation on dispatch-guard trips (stranded vs unrecoverable).
- 6h dispatch lease can double-dispatch a genuinely long run.
- `_check_outcome()` exit-code classification may misread silent failures.
