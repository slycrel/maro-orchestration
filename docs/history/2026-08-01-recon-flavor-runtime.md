---
status: record
---

# Chunk-9 #2 — recon flavor graduated to the runtime

Per the delegated build order (Jeremy 2026-07-31, piped 8c7f5068: the §12
order stands, "#2 recon flavor, exercised in `star` first", executed as
honest-good-enough slices with named upgrade edges; continued 2026-08-01,
"continue for the build order work"). The star half shipped 2026-07-27
(contract + two live exercises — the stop-path survey's 2 recon
delegations at 11/11 spot-verifications, and the cap-stuck exercise);
this slice is the src/ graduation the "exercised in star first"
sequencing was building toward.

## What the star exercises proved (the graduation evidence)

- The commit/recon distinction is real work-typing, not taxonomy: recon
  returns were judged on map change and the judgment caught value the
  deliverable question can't ask for (the achieved-but-stuck find came
  out of a recon delegation).
- The VOI gate ("name the pending decision or don't run the recon") is
  statable as prompting and was honored in both exercises.
- The map-edit return type (resolved/surfaced unknowns, edges naming
  what settles them, cost estimates) is what makes recon verifiable —
  §12's point 4, exercised live.

Meanwhile the runtime was already generating recon-shaped work untyped:
DECOMPOSE's own "SURVEY FIRST" guidance produces survey steps that then
get verified with the deliverable question — the exact dishonesty the
flavor exists to fix.

## What shipped

**The tag is the schema.** Steps are plain strings end-to-end, and the
established convention for step attributes is inline tags
(`[after:N]`, `[boundary]`). The flavor rides the same way:

    Survey src/ loaders [recon: decides which loader the refactor targets]

self-describing through manifests, checkpoint resume, splits,
injections, and display — zero side-channel plumbing, no schema
migration.

- **planner.py** — `step_flavor(step) -> (flavor, voi)` +
  `strip_recon_tag` beside the boundary-tag helpers; `RECON_FLAVOR_RULES`
  teaches the tag + the VOI rule ("if you cannot say which later choice
  the answer would change, cut the step or fold it in") in decompose,
  gated on `planner.recon_flavor` (default ON).
- **step_exec.py** — tagged steps get `_RECON_STEP_EXTRA` in the
  execution prompt (both the full user_msg and the session-reuse delta):
  the map-edit return contract, the named VOI decision (or an explicit
  "none named" when the tag is bare), and "do NOT do the downstream work
  here." Every outcome shape (done/blocked/error) is stamped
  `flavor: recon` + `recon_decision` — a recon step that got stuck is
  still a recon step to every downstream reader.
- **verification_agent.py** — `_VERIFY_RECON_STEP_SYSTEM` swaps in when
  the step text carries the tag: PASS requires at least one concrete map
  edit; narration that changes nothing, or claims naming no way to check
  them, is RETRY ("hallucination-shaped: say so"). An honest negative
  ("X is not configured; the edge is closed") explicitly PASSES — recon
  that closes an edge is a real map edit. Both validation-ladder tiers
  (hosted-free and paid) route through VerificationAgent, so one
  detection point covers every tier by construction. Verdict semantics,
  confidence thresholds, and the §13e judged/fail-open contract are
  unchanged — only the question differs.

**Killswitch posture** (chunk-6 precedent: kill the boost, never the
measurement): `planner.recon_flavor` OFF stops EMISSION — the planner is
never taught the tag, so no new recon steps appear. Detection at every
consumer stays unconditional: a marker already in flight (or arriving
from an injected step or hand-written goal) keeps the honest treatment.
The one-way-door alternative — gating detection — would flip in-flight
recon steps to the deliverable question mid-run.

**Bare `[recon]` keeps its flavor** (VOI missing, voi = ""). Demoting it
to commit would verify an information-buying step with the
deliverable-progress question — the exact wrong question this slice
exists to remove. VOI-missing is visible instead: the executor is told
"none named — surface what you judge decision-relevant" and the outcome
row carries `flavor` without `recon_decision`, so the denominator for a
future hard-gate decision accrues from live runs.

## Deliberate cuts (BACKLOG "Recon-flavor upgrade edges")

No map/landmark store (§12 nudge 4 stands — the schema emerges by
subtraction or not at all); no structured `map_edits` complete_step
field until a structured consumer exists; no VOI hard-gate; no probe
execution at verify time (claim_probe wiring is its own slice); no
blocked_on/§14 graduation (the 07-27 record's own routing: wait for the
corpus); no recon-aware readout yet (outcome rows now carry the fields).

## Verification

16 new pins (tests/test_step_flavor.py): parsing (VOI/bare/commit/
coexistence with [after:N]), emission gate both ways + detection survives
the killswitch, execution contract + VOI in both prompt lanes, stamp on done
and blocked shapes, verify-question swap both ways, RETRY semantics and
§13e fail-open unchanged on recon steps. Adjacent suites green
(test_planner, test_step_exec, test_verification_agent,
test_judged_markers, test_defaults_doc — the new key resolves a reader
in the census). Full suite via scripts/test-safe.sh before landing.

One import fix riding along: `import re` moved to planner's top import
block (was mid-file at :488, below the new module-level tag regex).

## Post-land adversarial review (same day)

3 Codex lenses vs 68ed793 — verdict as-reviewed "do not ship", remediated
same session. Accepted + fixed: staged-pass lane (wide/deep goals) was
never taught the tag and the multi-plan composer / step-ceiling re-ask
could silently drop inline tags when rewriting steps; five early-return
paths in execute_step (await ×2, constraint block, adapter death ×2)
returned unstamped outcomes — detection now happens at function entry
and every return routes through one `_stamp_flavor` helper;
`_split_exec_analyze` rebuilt step text and destroyed the tag — recon
steps now skip the shaper exactly like boundary steps. Rejected with
rationale: typed StepOutcome flavor fields (the tag rides
StepOutcome.text into every durable record — flavor is derivable by
construction, and a parallel typed field is a dual source of truth);
parallel-lane verification skip (pre-existing property for ALL steps,
now a named edge); factory_thin bypass (adjudicated instrument, not
mainline); strip_recon_tag deletion (already a named edge). The
"outcome rows carry the fields" phrasing above is sharpened in BACKLOG:
the durable carrier is the step TEXT; the dict stamp is in-process
convenience. 6 new pins (22 total). Full record:
docs/history/2026-08-01-recon-flavor-adversarial-review.md.
