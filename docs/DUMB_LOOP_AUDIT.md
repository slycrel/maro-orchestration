# Dumb-Loop Audit — pipeline decision-point inventory (static half)

**Status: static half done 2026-06-11. Data half gated on live shadow volume.**

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
   2026-06-11 there is ~1 live event — explicitly not enough; no cutover
   conversation until volume exists.

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

## Known gaps (carried to BACKLOG when actionable)

- No calibration tracking for director escalation confidence (≥5 gate).
- No outcome correlation on dispatch-guard trips (stranded vs unrecoverable).
- 6h dispatch lease can double-dispatch a genuinely long run.
- `_check_outcome()` exit-code classification may misread silent failures.
