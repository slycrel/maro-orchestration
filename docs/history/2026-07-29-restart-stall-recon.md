---
status: record
---

# Star exercise — restart-stall recon (§9.3 build input)

Third star use (post-KEEP adjudication); first with the Map Δ column and
the structural-stall trigger in the contract (added this session, §9.3
star half). Purpose: ground the runtime declare-blocked build in live
data before landing it.

## Invocation contract

- **Goal**: determine whether identical-failure closure restarts occur
  in live run data — did restarted attempts fail the same checks as the
  attempt they restarted from?
- **Done-means**: counts of runs scanned / closure restarts found /
  pairs with recoverable failed-check identities, plus a same/different
  call per pair — every claim path-backed, at least one spot-verified
  by the master.
- **Cuts**: read-only over `~/.maro/workspace/`; no repo test fixtures;
  no worth-it verdict on the runtime feature (already greenlit).
- **Budget**: 4 delegations.

## Run ledger

| # | Task (outcome) | Flavor | Criteria stated? | Verdict | Map Δ | Surprise |
|---|----------------|--------|------------------|---------|-------|----------|
| 1 | Inventory closure restarts + per-check recoverability in live data | recon (VOI: would command-identity fingerprinting ever fire live; what can evidence text name) | yes (counts + quoted-line proof per claim + persistence map + honesty section) | accept (spot-verified pair 9 end-to-end: LOOP_CREATED line, parent 4/5 with one exit-1, child 5/5 with reworded check) | LARGE: 10 restart landmarks, full persistence map, regeneration finding, 0 depth≥2 | Checks are REGENERATED per attempt — no failing command recurred verbatim in either recoverable pair; and run metadata.json carries no restart lineage at all |

## Result block

- **Deliverables**: this record; corrections folded into the §9.3 ship
  record (COMPOUND_THINKING_DESIGN 2026-07-29 addendum) and the BACKLOG
  cut list (main-gate join material corrected to captain's-log
  LOOP_CREATED; fingerprint-coarsening extension added).
- **Done-means verdict**: PASS at 1/4 delegations — (a) 728 run dirs +
  both captains logs + 1,442 outcome rows scanned; (b) 10 closure
  restarts, each with a quoted proving line; (c) 2 fully recoverable
  pairs (+1 partial via prose); (d) different commands in all
  recoverable pairs. Master spot-verified pair 9 (dc311ea6→86511522)
  against `runs/fd483efb-stout-ember/build/calls/` — exit-code arrays
  and the reworded constraint check match the recon's claims exactly.
- **Stop verdict**: none — done.
- **Residuals**: 7/10 pairs have no persisted child-side checks anywhere
  (calls capture post-dates them); captain's-log commands truncate at
  200 chars; n=2 is too thin to conclude "command identity never
  recurs" — the declare-blocked log line is the ongoing readout.
- **Cost**: 1 delegation of 4.
- **Findings**:
  - **Load-bearing for §9.3**: closure checks are LLM-regenerated per
    attempt; command-identity fingerprinting would not have fired on
    either recoverable historical pair. v1 ships anyway (fail-open,
    zero cost, deterministic canonical checks recur in other classes);
    coarsening is evidence-gated (BACKLOG).
  - Run metadata.json has NO restart lineage (no loop_reason /
    parent_loop_id / continuation_depth in any of 728 dirs) — restart
    ancestry lives only in captain's-log LOOP_CREATED events.
  - Side-find (not this chunk's business): in pair 9, both attempts'
    raw closure LLM responses said `complete: true` and were overridden
    to `complete=False` by the post-review judge — the judge-override
    pattern is doing real work on live runs.
  - Trigger mechanics: the Map Δ column was exercised; the stall
    trigger did not fire (single row, large Δ) — first genuine firing
    still unobserved, consistent with its purpose (rare by design).
