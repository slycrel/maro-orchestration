---
status: record
---

# Adversarial review — go-nuts stretch (2026-08-08)

Cross-model review (3 codex reviewers: Skeptic / Architect / Minimalist)
over the six stretch chunks: 26d3b58 (re-mint tombstones), aebf844
(inspector cadence), 42417df (node promotion age+content), a87348f
(match-tier telemetry), bc613de (skill pedigree), 97bb780 (world-facts
slice 1). Every finding verified against code before acting
(verify-before-fix; historical hallucination base rate ~30–50% — this
round ran unusually true: 9 of 12 confirmed).

## Intent

Whether the six chunks achieve their stated designs well — not whether
the designs are right (those were Jeremy's decided asks).

## Verdict: CONTESTED → fixes applied same-session

Three HIGHs were real and are fixed; two findings rejected as
misreadings of scope; one accepted as a documented limitation.

## Findings and outcomes

| # | Finding (lens) | Verdict | Outcome |
|---|---|---|---|
| A | `bool(parsed["valid"])` promotes on the string `"false"` through the fail-closed age path (Skeptic+Architect, HIGH) | CONFIRMED | Strict verdict parse: only JSON `true` / literal `"true"` approve |
| B | `--remint-pending` alone measures a decisively negative Δ and still clears probation (Architect, HIGH) | CONFIRMED | The strike-3 lane now applies both effect routes by definition ("sets the stamp whichever way it lands"); routes carry their own Δ-bar guards |
| C | Strike-3 event has no runtime consumer; CLI limit drops rows (Skeptic, HIGH) | REJECTED | Queue-by-event + deliberate CLI-driven spend is the decided design (no-silent-spend); `dropped` count is visible in the census |
| D | A watch row that tenure-promotes to LONG exits the MEDIUM-only selector — queued event unfulfillable (Architect, HIGH) | CONFIRMED | Selector scans MEDIUM+LONG; demote stamp and watch-clear are tier-aware; `_is_delta_demoted` joined the LONG injection filter |
| E | Anecdotal facts are an unchecked injection channel with "treat as known" authority (Skeptic, HIGH) | CONFIRMED | Deterministic `injection_guard` scan at declaration; flagged facts dropped fail-closed |
| F | Parallel paths don't checkpoint world facts (Skeptic+Architect, MED) | REJECTED as regression | Parallel fan-out/DAG runs have no checkpoint/resume machinery at all — pre-existing scope, now named in DEFAULTS + design doc |
| G | One corrupt checkpoint row (`int()` on junk) discards the whole restored ledger (both, MED) | CONFIRMED | Per-row try in `from_list` |
| H | Type-corrupt cadence counter wedges the inspector lane permanently (Skeptic, LOW) | CONFIRMED | Field-level guards; corrupt fields self-heal to 0 |
| I | Disabled cadence still counts + creates the state file; enabling later fires immediately (Minimalist, MED) | CONFIRMED | `cadence <= 0` short-circuits before the tick |
| J | `world_facts.enabled=false` gates capture but not render of checkpoint-restored facts (Minimalist, MED) | CONFIRMED | Kill switch gates the render site too |
| K | Ten judged-invalid oldest candidates starve the whole 433-node promotion backlog forever (Minimalist, HIGH) | CONFIRMED | Terminal-rejection stamp (`promotion_rejected_*`); re-enters only when `times_applied` grows past the stamp |
| L | Pack import doesn't normalize/type-check foreign domain/tags — `"tags": "Research"` becomes character tags (Architect, LOW) | CONFIRMED | Boundary normalization at pack import + the same list guard in `dict_to_skill` |

## What went well (all three reviewers)

Match-tier telemetry wiring, the inspector's locked RMW cadence counter
structure, and the archive-as-tombstone-store design drew no structural
findings. The Architect explicitly cleared the manifest wiring and
cadence locking.

## Lead judgment notes

- C's rejection is scope, not dismissal: if the strike-3 event
  accumulates unactioned in practice, wiring a consumer (heartbeat
  lane) is the follow-up — evidence-gated, not pre-built.
- F stays a named limitation until parallel runs grow checkpoint
  machinery of their own; bolting fact-carry onto a lane with no resume
  would be dead code.
