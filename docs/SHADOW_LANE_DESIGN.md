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
| `go` (2026-09-06, own track) | the Go successor engine (`maro-go now\|agenda`) on its own persistent workspace, scratch work dir, every mutating/network tool denied by tool policy | harness vs go ⇒ does the successor carry the machinery's value on the live stream; go ≥ harness at lower cost ⇒ the successor is ready to be the champion |

Randomizing the arm per shadow keeps cost at one challenger per run while
both comparison corpuses accumulate passively. "Plain might be sometimes
better, sometimes worse than star, and we don't need to know today"
(Jeremy) — the answer emerges when n is big enough, no dedicated
experiment.

### The Go track (2026-09-06)

The `go` arm is not a third pick in the star|plain randomization — it is
its own track inside the same sweep (same lock, so serial stays a system
invariant): own switch (`shadow.go.enabled`, double opt-in with
`shadow.enabled`), own claim dir (`<run-dir>/shadow-go/`, a SIBLING of
`shadow/` because the star|plain track claims by the existence of
`shadow/`), own daily cap counted from its own `arm: "go"` rows, own
eligibility. Why own track: the live ledger held ONE star|plain row in
three weeks (tight read-tier gate × sparse stream); the successor's
challenger evidence cannot wait on a slot it would compete for, and a
run may honestly carry both a star|plain shadow and a Go shadow.

Eligibility: a primary of EITHER lane passes on the basic checks alone
(done, not dry, organic, non-empty) — the engine runs with
`--deny-tools` naming every mutating/network tool
(`shadow_lane.GO_DENY_TOOLS`), so the goal text cannot act whatever its
shape; containment is structural (the engine refuses the tool), not the
star|plain preamble (instruction-level; `containment_preamble_version:
null` on Go rows so the batch judge partitions). Shipped first with the
star|plain read-tier gate on AGENDA; widened the same day (Jeremy: "if
we're going to shadow, let's do it right"). The cost of the width, on
record: a build-shaped goal runs in Go without write tools and fails
honestly — those pairs say nothing about engine quality, so every Go
row carries `primary_goal_shape` (worker type + action tier as the
star|plain gate would have classified it) and the adjudication
partitions on it. Any other lane is a terminal skip (`lane!=now|agenda`).

The engine keeps its OWN persistent workspace (`shadow.go.workspace`,
default `<workspace_root>/shadow-go`): its landscape (related-run
decisions) and lineage-scoped memory accrue across shadows the way the
primary's do — a fresh workspace per shadow would measure a memoryless
engine. It never reads this workspace (isolation invariant 3 holds by
construction: the Go journal is not a Python store, and the env scrub
unsets every `MARO_*` pointer before setting `MARO_GO_WORKSPACE`).

Readout: after the run, `maro-go runs show --json <handle>` (the run's
`Summary`: mission outcome/closure, landscape relation, every call with
its receipt — the landscape judge included — and the usage sum with
`cost_reported` honest about partial sums). Row fields: `go_handle`,
`go_outcome`, `go_closure`, `go_calls`, `go_landscape`, `cost_usd`
(None unless every call reported), `tokens_cached` (cache-read tokens,
so the diagnose-cost question below has its denominator),
`go_binary_sha256` (the version pin, the star arm's `prompt_sha256`
analogue), `tool_policy`; and `go_reason` / `go_needs_clarification` /
`go_question` — the engine's intake may decide the goal is not clear
enough to plan and ask a question instead of running (the first live
pair, 37d0e041, did: "what is 'the maro box'?"). A shadow asks nobody,
so the outcome is recorded as what it is (asked, not failed) and never
acted on; the adjudication partitions on it.

Operator-context parity (2026-09-06): the champion's planner injects the
operator docs (`user/GOALS.md`, `CONTEXT.md`, `SIGNALS.md`, workspace
overlay over repo template, `clip(…, 4000)` per doc as the breaker) into
its plan prompt; the Go challenger got none, which is what 37d0e041's
question was made of. The sweep now renders the same docs the same way
(`shadow_lane._operator_context`), writes them to
`<run-dir>/shadow-go/context.md`, and hands the file to the engine as
`--context <file>` — a RECORDED input on the Go side (a `context`
thought cited by the goal record; every intent, plan and NOW execute
request carries it and the fold re-derives the request from it, so a
journal that carried context verifies). Row fields: `context_docs` (which
docs were present), `context_sha256`, `context_chars`, `go_context` (the
engine's own hash of the thought it stored — the two hashes are of the
same bytes, so a mismatch is a transport defect). No docs → no flag, and
the row says so with an empty `context_docs`.

Reading the pairs (2026-09-06): `python3 -m shadow_lane pairs [--arm
go] [--json]` renders every ledger row as the pair the adjudication
reads — the primary's side (achieved, cost, wall, model), the
challenger's (outcome, asked-a-clarification + the question, cost, wall,
tokens incl. cached, landscape relation, context docs, binary pin), the
cost and wall ratios, and the challenger's result excerpt when the run
dir is still there — with a summary that partitions on arm, goal shape
and asked-vs-failed and gives median ratios. It does NOT claim answer
agreement: that is the batch judge's, at ~10 rows. The same view is the
viz's **Pairs tab** (`runs_root()/pairs.html`, `loop_report.
write_pairs_page`, allowlisted in `viz_server`), refreshed by the
runs-index write and by the sweep after every row it appends — the
challenger result is inlined because `shadow-go/` is not a servable
subtree. Rows written before the clarification fields existed read as
"not asked"; nothing is backfilled (the engine's reason was not on the
row, and the ledger is append-only).

Pre-registered questions for the Go track (adjudication at ~10 pairs,
the same bar as star|plain): (1) delivered-answer agreement with the
primary on NOW pairs; (2) cost and wall per pair (the 2026-09-05
comparison predicts Go at ~1/5 the cost and ~2/3 the wall); (3) landscape
relation on follow-ups — does the engine relate a run the primary
related. Prediction on record: Go ≥ harness on NOW answers at lower cost;
Go < harness on AGENDA depth until its workspace has accrued lessons.

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
4. **Side-effect guard (best-effort textual gate + named residual)**: a
   shadow *re-executes a goal*. Only read/research-shaped goals are
   eligible (worker-type AND action-tier classifiers, both keyword/regex
   over goal text — a textual gate, honestly labeled: an implicit or
   fetched instruction inside a research-looking goal can pass it). The
   residual is bounded, not eliminated: the challenger runs in the same
   trust class as maro's own executor subprocesses on this box
   (skip-permissions, ambient HOME), with the maro workspace env pointers
   scrubbed from its environment and a fresh scratch cwd. True
   sandboxing (container/worktree) is the upgrade edge that would make
   the guard hard; it is evidence-gated, not a v1 promise.
5. **Output beside the primary**: `<run-dir>/shadow/<arm>/RESULT.md` + a
   ledger row — mirrors the re-run surface so viz can render the pair.
   The challenger's scratch cwd also lives inside this boundary
   (`<run-dir>/shadow/<arm>/scratch/`) — one inspectable unit per shadow,
   no write surface outside the run dir + ledger.
6. **Serial + throttled**: one shadow at a time (box rule), config sample
   rate + daily cap. Subscription tokens make the dollar cost ~0; box
   minutes and rate-limit pressure are the real budget.
7. **Version-pinned instrument**: each shadow row stamps the star skill
   version (and prompt hash) it ran with — the skill evolves, results must
   stay interpretable.
8. **Batch adjudication**: no per-run judging. A periodic cross-model pass
   compares accumulated pairs (union/miss scoring, the head-to-head
   discipline from docs/history/2026-08-13-star-vs-harness-comparison.md)
   and writes comparison verdicts. The adjudication *tooling* deliberately
   ships with the first batch, not with v1 — building the judge before ten
   pairs exist would be judging hypothetical data. The ledger rows carry
   the fields it will need (challenger cost/wall/model + primary
   cost/wall/model, star version + prompt sha, CLI version).

**Live-fire finding (2026-08-14, first fire be7c618a/star):** the named
textual-gate residual fired immediately, and taught two things. (1) A
READ-tier-classified goal can still *instruct writing* ("write the audit
as a cited report") — the challenger wrote its report into the live
project directory, overwriting the primary run's own AUDIT.md
deliverable (recovered from the run dir's `artifact/` copy; challenger
version preserved in the arm dir). (2) The challenger *found the
primary's prior answer* in that directory and verified-instead-of-redid
— arms are not independent when primary deliverables are
workspace-visible. Fix shipped same day: a versioned containment
preamble prepended to the stdin prompt for BOTH arms (symmetric, so it
cancels out of arm comparisons) — write-only-in-cwd + do-your-own-work.
Instruction-level, same best-effort class as the gate itself; true
independence needs snapshot isolation (upgrade edge below). Batch judge
must still check RESULT.md for prior-answer reuse on
pre-preamble rows and treat `containment_preamble_version` as a
partition key.

Accepted residuals (named, reviewed 2026-08-14): the heartbeat's
idle-window gate is checked at challenger *launch*, not held for its
lifetime — a primary run starting mid-challenger shares the box with it
(bounded by serial + daily cap + timeout; revisit if primaries visibly
degrade). The daily cap counts ledger rows, so a challenger whose ledger
append failed (fallback row in its arm dir) escapes the count until
reconciled — bounded by the same cap's small value.

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
post-run/heartbeat sweep family (on this box, 2026-09-06: a crontab
entry every 10 minutes — no heartbeat process runs here, so the
heartbeat tick was never a live cadence). Config namespace `shadow.*` (default OFF;
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
- ~~Whether a NOW shadow should also run another engine~~ — the Go track
  (2026-09-06) is exactly that: the successor as challenger, on its own
  track so it never competes with star|plain for the run's slot.
- Whether NOW shadows should also run the *harness* AGENDA arm ("would the
  machinery have done better?") — v1 keeps arms to star|plain to bound
  cost; revisit at first adjudication.
- Viz rendering of primary/shadow pairs — after the first real rows exist.
- Snapshot isolation: run challengers against a pre-primary snapshot of
  the workspace paths the goal names, so primary deliverables can't leak
  into challenger input (the live-fire independence finding). Expensive;
  evidence-gated on the containment preamble proving insufficient.

## Review record (build arc, 2026-08-14)

Review-to-fixpoint per the standing workflow decree. Cross-model
adversarial reviews; every accepted finding verified against the tree
before fixing (verify-before-fix).

| Round | Reviewers | Findings accepted | Commit |
|---|---|---|---|
| r1 | codex ×3 (skeptic/architect/minimalist) | 9 | 2122275 |
| r2 | codex ×2 | 5 (incl. MARO_ORCH_ROOT/MEMORY_DIR scrub gaps, ts-after-lock cap fix) | fceb977 |
| r3 | codex ×1 | 4 (incl. HIGH: wildcard scrub had unset MARO_WORKER_RUN, bypassing the pre-push guard — force-set "1") | 8e5a9e3 |
| r4 | grok-4.3 ×1 (codex usage-capped until 08-19) | 0 — clean, fixpoint | — |

Convergence signature 9 → 5 → 4 → 0, defects migrating into the fixes'
own edges by r3 — the expected shape (feedback_review_to_fixpoint).
Suite green after each round (8629 passed / 1 platform skip at r3).
