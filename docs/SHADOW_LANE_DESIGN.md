---
status: living
---

# Shadow lane — the lane-honesty standing test (champion–challenger)

**Decreed 2026-08-14** (GOAL_BRAIN Decisions; journal e07161fe). Jeremy's
framing, quoted: *"the test I want to (continually) try to prove is that
AGENDA lanes are genuinely worth it; and that as we continue over time, the
NOW lane shouldn't get smaller and smaller, necessarily, we just get better
at asking the right questions in either way."*

Lane-fit, not lane-supremacy. The star skill's identity is the *measurement
instrument* — our orchestration codified as a prompt, keeping us honest
against the bitter lesson. The runtime star-port architecture question
(BACKLOG "NOW retry rung" arm (c)) is separate and stays parked; this lane
generates the evidence that would settle it either way.

## Measurement model (three arms)

Every eligible primary run can get ONE shadow — a strictly isolated
challenger re-run of the same goal, arm randomized:

| Arm | What it is | The gap it measures |
|---|---|---|
| harness (champion) | the primary run itself — maro's machinery + accrued learning | — |
| `star` | headless frontier subprocess carrying the star SKILL.md contract (orchestration-as-prompt) | harness > star ⇒ the machinery/learning earn their keep beyond the pattern |
| `plain` | headless frontier subprocess, bare goal, no orchestration teaching | star > plain ⇒ the orchestration pattern itself adds value; **plain ≥ harness ⇒ bitter-lesson red alert** |

Randomizing the arm per shadow keeps cost at one challenger per run while
both comparison corpuses accumulate passively. "Plain might be sometimes
better, sometimes worse than star, and we don't need to know today"
(Jeremy) — the answer emerges when n is big enough, no dedicated
experiment.

## Design invariants

1. **Decoupled**: the shadow fires from a post-run sweep, never inside the
   primary run's process. The primary's latency, cost accounting, and
   verdict are untouchable.
2. **Black box**: the challenger gets the goal text ONLY. No maro memory,
   no lessons, no workspace artifacts, fresh scratch cwd. The learning
   asymmetry is deliberate — accrued learning is part of what's being
   tested.
3. **Isolation both directions**: shadow runs are stamped
   `measurement_class=shadow`; no learning path may ingest them (outcomes,
   lessons, skill stats, evolver, knowledge). Enforced by test, not
   convention.
4. **Side-effect guard (hard)**: a shadow *re-executes a goal*. Only
   read/research-shaped goals are eligible. Anything that could send,
   commit, spend, or mutate outside its scratch dir is ineligible in v1.
   Sandboxing is an upgrade edge, not a v1 promise.
5. **Output beside the primary**: `<run-dir>/shadow/<arm>/RESULT.md` + a
   ledger row — mirrors the re-run surface so viz can render the pair.
6. **Serial + throttled**: one shadow at a time (box rule), config sample
   rate + daily cap. Subscription tokens make the dollar cost ~0; box
   minutes and rate-limit pressure are the real budget.
7. **Version-pinned instrument**: each shadow row stamps the star skill
   version (and prompt hash) it ran with — the skill evolves, results must
   stay interpretable.
8. **Batch adjudication**: no per-run judging. A periodic cross-model pass
   compares accumulated pairs (union/miss scoring, the head-to-head
   discipline from docs/history/2026-08-13-star-vs-harness-comparison.md)
   and writes comparison verdicts.

## Pre-registered adjudication (the test that can fail)

Discipline copied from the star skill's own alpha gate — no silent
half-death:

- **Usage expectation**: shadows accumulate on organic eligible runs; first
  adjudication at ~10 completed pairs (or 30 days, whichever first).
- **Keep signal**: the corpus produces at least one lane-fit or
  bitter-lesson finding the n=1 head-to-heads didn't already establish —
  e.g. a measured harness>star gap attributable to accrued learning, a
  goal-shape where plain beats the harness, or a NOW-lane
  "one-shot-was-enough" rate.
- **Kill signal**: two consecutive adjudications produce nothing new, or
  the lane's box-time/rate-limit pressure visibly degrades primary runs.
- Verdict lands in GOAL_BRAIN Decisions either way.

## Prediction on record (2026-08-14, pre-build)

Registered before the first shadow fires, per the design-review corollary
(demand named falsifiers):

- On research-shaped AGENDA goals, star ≈ harness on answer quality with
  star cheaper — consistent with the 08-13 head-to-head; the harness's
  edge, if real, should show up as *coverage* (its wider step fan) and as
  *learning* (mature-workspace recall the black-box challenger lacks).
- On NOW goals, plain ≈ harness (the NOW lane is already nearly a plain
  prompt) — a large plain>harness gap there would indict NOW's prompt
  scaffolding, not orchestration.
- The bitter-lesson alert (plain ≥ harness on AGENDA-shaped goals) is
  NOT expected; if it fires, that's the most important finding this lane
  can produce and goes straight to Jeremy.

## Architecture (v1)

```
primary run finishes (any lane)
        │
        ▼  (post-run sweep, decoupled — cadence job)
eligibility gate ──not eligible──▶ stamped skip reason, no shadow
        │ eligible (read/research-shaped, organic, not already shadowed,
        │           under daily cap, sample-rate pass)
        ▼
arm pick (random: star | plain), star version pinned
        ▼
challenger: headless subprocess in fresh scratch cwd
  - star arm: star SKILL.md contract inlined as system prompt
  - plain arm: bare goal
  - no maro env/memory; wall-clock + token capture; hard timeout
        ▼
<primary-run-dir>/shadow/<arm>/RESULT.md (+ meta.json)
memory/shadow_ledger.jsonl row (arm, versions, cost, wall, status)
        ▼  (later, batched)
adjudication pass over pairs → comparison verdicts → GOAL_BRAIN
```

Module: `src/shadow_lane.py` (sweep + eligibility + runner + ledger), CLI
`python3 -m shadow_lane sweep|status`. Cadence wiring into the existing
post-run/heartbeat sweep family. Config namespace `shadow.*` (default OFF;
ON on this box), registered in docs/DEFAULTS.md.

### Seam map (recon 2026-08-14)

- **Isolation is by construction, not by stamp.** The challenger is a bare
  headless subprocess — never a maro run: no `handle()`, no run dir of its
  own, no `record_outcome`, no lesson/skill/evolver paths. Recon confirmed
  no consumer filters on `measurement_class` today (only `dry_run` is an
  enforced exclusion), so stamp-based exclusion would have been per-consumer
  whack-a-mole; structural absence is strictly stronger. No
  `MEASUREMENT_CLASSES` change in v1. Enforced by a pin test: shadow-lane
  writes are confined to `<run-dir>/shadow/` + `memory/shadow_ledger.jsonl`,
  and no learning module reads either.
- **Run discovery**: scan `runs.runs_root()` dirs' `metadata.json` —
  fields: `prompt` (the goal text), `lane`, `status` (free string; `done`
  is the eligible terminal), `ended_at`, `measurement_class`, `dry_run`,
  `goal_achieved`. `run_curation.list_runs()` exists but synthesizes; the
  sweep reads metadata directly.
- **Eligibility gate**: `workers.infer_worker_type(goal) == "research"`
  (existing research-shaped gate precedent, cf. `quality_gate.cross_ref_research`)
  AND `constraint.classify_action_tier(goal) == ACTION_TIER_READ`
  (belt-and-braces; READ is its unmatched default — applied to goal text,
  a conservative composite with the worker-type gate).
- **Challenger invocation**: own command construction (NOT
  `ClaudeSubprocessAdapter` — maro's adapter disallows WebFetch/WebSearch
  because the harness has its own web verbs; the challenger keeps the stock
  toolset, which IS the bitter-lesson contender). Reuses
  `llm._run_subprocess_safe` (process-group kill, wall + liveness timeouts).
  Trust level: same `--dangerously-skip-permissions` class as maro's own
  executor subprocesses on this box, bounded by the eligibility gate +
  fresh scratch cwd (`benchmark_isolation` precedent: fail-closed
  `exist_ok=False` reservation).
- **Cadence**: CLI-first (`python3 -m shadow_lane sweep`) + a tick-gated
  heartbeat job following the backlog-drain thread pattern (config-gated,
  default off; idle-window gating via `SlowUpdateScheduler` comes free).
- **Config**: `shadow.*` keys registered in docs/DEFAULTS.md (census test
  `tests/test_defaults_doc.py` enforces both directions).
- **Adjacent precedent, distinct**: `navigator_shadow.py` replays
  *decisions* only (never re-executes); `rerun_identity.py` is intake-time
  prior-art briefing. Neither re-runs goals; this lane is the first thing
  that does — hence the hard eligibility gate.

## Open questions / upgrade edges

- Sandboxed eligibility expansion (worktree/container) so build-shaped
  goals can be shadowed safely — evidence-gated on v1 actually producing
  findings.
- Whether NOW shadows should also run the *harness* AGENDA arm ("would the
  machinery have done better?") — v1 keeps arms to star|plain to bound
  cost; revisit at first adjudication.
- Viz rendering of primary/shadow pairs — after the first real rows exist.
