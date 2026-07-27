---
status: record
---

# Chunk-9 #2 — recon flavor + §14 diagnosis in star, exercised live

Per the decided build order (#4 stop-verdict split first, then #2
exercised in `star` as pure prompting; graduation to src/ waits on
crystallization-pressure findings). The recon-flavor half of the star
contract shipped in chunk-9a (commit/recon + VOI gate + recon JUDGE
question); this chunk adds the §14 diagnosis boundary and exercises the
contract on a real scoped task.

## Contract changes (`.claude/skills/star/SKILL.md`)

- **Failure contract in TASTE**: every delegated task now states that a
  sub-agent that cannot complete returns a typed `blocked_on` block,
  never a shrug.
- **New section "Diagnosis at the failure boundary (§14)"**: the
  diagnosis question ("what would have to be different for this pass to
  succeed?"), the typed cause set (missing-capability /
  missing-information / wrong-approach / terrain / transient) with its
  routing table, falsifiability as the excuse-killer (`blocked_on`
  must name the specific missing thing AND the experiment proving it's
  missing — `settled_by_command` applied to the blockage itself), and
  Jeremy's ownership split verbatim in force: **the step DIAGNOSES and
  RECOMMENDS; the master DECIDES and ROUTES.** Steps may substantiate
  within already-granted scope (the `which <tool>` class); the
  experiment-as-plan-move, side-quest spawn, and revisit are map
  operations (dedup only exists at the map; the master may route away
  regardless).
- **Routing as a taste act**: a `blocked_on` return gets its own ledger
  row for the master's routing decision (micro-test recon / side-quest /
  reroute / typed stop); same-cause blockages dedup to ONE decision.
- **Ledger**: blocked tasks record `blocked:<cause>` in the Verdict
  column.

## The exercise (star use #2, counts toward alpha adjudication)

**Goal**: answer BACKLOG's cap-stuck REMAINING question with live
evidence from `~/.maro/workspace/runs/`.
**Done-means**: `classify_outcome` executed on the tire-run specimens
with output quoted; rebucket verdict grounded in ≥1 real run per
claimed pattern. **Cuts**: read-only over run data; no src/ changes
in-run; no new instrumentation. **Budget**: 4 delegations.

### Run ledger

| # | Task (outcome) | Flavor | Criteria stated? | Verdict | Surprise |
|---|----------------|--------|------------------|---------|----------|
| 1 | Survey cap-shaped endings in the live store; executed classify_outcome on specimens + cap-stuck family | recon (VOI: decides build/defer/close on the cap-stuck rebucket) | yes (table shape, executed-command requirement, quote-don't-guess, typed failure contract) | accept — both load-bearing edges spot-verified by the master (re-ran classify_outcome on 692bd96f; read the branch ordering; re-grepped the store) | goal_achieved:TRUE runs classifying as "failed" — an unnamed conflation, not the one we went looking for |
| 2 | Routing decision (master taste, no delegation): record findings, defer the vocabulary change | — | — | recorded | none |

### Result block

- **Deliverables**: this record; BACKLOG "Success accounting" item
  updated with live numbers; new BACKLOG item "Achieved-but-stuck
  classifies as failed".
- **Done-means verdict**: PASS — classify_outcome executed on both
  specimens (`1bfd0894-noble-marsh`, `b52b7731-swift-echo` → both
  `success_class="failed"`, `stop_verdict` ABSENT, verbatim output in
  the recon return and re-verified by the master); every claimed
  pattern has named real runs.
- **Findings** (the map edits):
  1. **Cap-stuck family = 9 of 726 runs** (all
     `cost_budget=$2.00 + slush=$0.40 exceeded` endings; status
     distribution of the store: done 310, stuck 166, error 132,
     stranded 57, incomplete 36, clarification_needed 24). Two more
     cap-shaped runs end stranded/done. All classify `failed` today.
  2. **Zero stamped stop_verdicts in the store** — every run predates
     the fc93dfa rail (landed today). Forward runs will carry
     `out-of-budget`; a LEGACY rebucket would need a
     stuck_reason-derived fallback, and the cap evidence lives in
     `build/loop-*-log.json`, not metadata.json — classify_outcome
     would need a log read it doesn't do today.
  3. **NEW (the surprise): achieved-but-stuck → "failed".**
     `classify_outcome` consults `goal_achieved is True` only inside
     `_SUCCESS_STATUSES`; two live runs (`692bd96f-brisk-lichen`,
     `d9f01e13-golden-birch`) carry judged `goal_achieved: true` with
     status "stuck" and classify as `failed`. A judged SUCCESS counted
     as failure evidence — the inverse of done≠achieved, unnamed by the
     stop-path survey. BACKLOG'd with fix shape + consumer-census
     requirement.
- **Routing decision** (master taste, on the record): DEFER the
  success_class vocabulary change — forward runs are already
  verdict-visible (chunk-9 #4 consumers), the legacy family is 1.2% of
  the store, and a class rebucket ripples into
  learnability/notify/evolver consumers that deserve the same census
  discipline the "interrupted" class got. The two BACKLOG items carry
  the evidence; reopen when a consumer actually misreads a forward run.
- **Residuals**: the `blocked_on` machinery itself was NOT exercised
  live (no delegation failed this run) — it fires opportunistically on
  a future blocked star task; §14's contract is prompting-only until
  then. Cost: 1 delegation of 4.
- **Adjudication note**: keep-signal met for this use — the
  achieved-but-stuck find is a design insight the normal flow missed
  (the chunk-9 #4 review, three lenses deep, didn't name it either).

## Graduation pressure (what this suggests for src/, not built)

The diagnosis question maps onto the existing `_BlockDecision` seam
(loop_blocked already types metacognitive_reason + stop_verdict at
terminal decisions) — a `blocked_on.cause` field there would be the
src/ landing spot if star keeps proving the shape. No crystallization
pressure yet: one exercise, zero live blocked_on returns. Wait for the
corpus.
