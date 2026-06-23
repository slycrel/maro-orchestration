# Dumb-Loop Audit — pipeline decision-point inventory

**Status: static half done 2026-06-11. Data half round 1 done 2026-06-21 —
dispatch boundary only (the single live shadow point). Round 2 instrumentation
wired 2026-06-23 — priority-1 point `_handle_blocked_step` now has a live
navigator shadow tap (`navigator_shadow.shadow_blocked_step_live`), config-gated
off; awaiting data. See "Data half — round 1" and "Round 2 — blocked-step
instrumentation" below.**

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
blocked_step separately. **Awaiting data:** enable `navigator.shadow_blocked_step`
in `~/.poe/workspace/config.yml` for a batch of real runs that hit blocked
steps, then read the table. The cutover question this answers: does the
navigator's extend-vs-fork-vs-close judgment match the threshold cluster
(retry 3 / replan 2 / sibling 50% / convergence), and on divergence, who was
right by run outcome?

## Known gaps (carried to BACKLOG when actionable)

- No calibration tracking for director escalation confidence (≥5 gate).
- No outcome correlation on dispatch-guard trips (stranded vs unrecoverable).
- 6h dispatch lease can double-dispatch a genuinely long run.
- `_check_outcome()` exit-code classification may misread silent failures.
