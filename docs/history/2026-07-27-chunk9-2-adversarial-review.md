---
status: record
---

# Chunk-9 #2 adversarial review — recon/diagnosis star contract (10ea2a3)

Post-land review per the per-chunk discipline. Docs/skill-only chunk →
medium surface (the ~90 reviewable contract lines in
`.claude/skills/star/SKILL.md`) → 2 Codex lenses (Skeptic + Architect)
via `codex exec`, diff ea8d347..10ea2a3.

## Intent

Add design-doc §14 (diagnosis at the failure boundary) to the star
contract as pure prompting: typed `blocked_on` failure contract, the
diagnosis question, falsifiability as the excuse-killer, and the
decreed ownership split (step diagnoses/recommends, master
decides/routes).

## Verdict: CONTESTED → remediated same session

**6 deduped findings, 6/6 verified against the text, 0 hallucinated —
twelfth consecutive clean round.** All 6 accepted at least in part;
every fix is contract wording (the medium the chunk ships in).

## Findings

1. **[high] Dedup keyed on the cause enum, not the named missing
   thing** — both lenses independently. "Same cause → ONE routing
   decision" collapses "missing GitHub access" and "missing DB
   credentials" (both missing-capability) into one side-quest; the
   design doc's rationale is "same missing *capability*" — the
   specific thing. **FIXED**: dedup now keys on
   `what_would_be_different`, with that exact counter-example inline.
2. **[high] Rejected/inconclusive completions bypassed the diagnosis
   question** (Skeptic) — a partial return with no `blocked_on` could
   be marked inconclusive and re-probed with no typed cause, the exact
   "failed pass becomes a verdict without a causal hypothesis" smell
   §14 names. **FIXED**: the diagnosis question now applies at every
   failure — on reject/inconclusive the master types its own cause
   hypothesis in the routing row before re-delegating or abandoning.
3. **[high] The substantiate-within-scope line was undefined**
   (Skeptic) — nothing separated substantiation from running the
   discriminating experiment. **FIXED**: the line IS the
   already-granted scope/budget; a probe that resolves the blockage
   means keep working; beyond scope the probe returns unrun as
   `proposed_experiment`; and the step never routes on a probe result
   either way.
4. **[medium] `blocked:<cause>` in the Verdict column collided with
   tri-state judgement and the two-consecutive-rejects rule**
   (Architect). **FIXED**: only a `blocked_on` that survives judgement
   records `blocked:<cause>` (an accepted observation — does not count
   toward two-rejects); a malformed/excuse-shaped one is a
   reject-with-evidence and does count.
5. **[medium] The five-cause set had no home for cost-shaped
   blockers** (Skeptic) — feasible-but-exceeds-budget straddled the
   enum. **FIXED without touching Jeremy's cause set**: cost-shaped
   inability is declared not-a-blockage — it returns as a
   reachability-and-cost estimate feeding the master's
   reachable-but-not-worth-it judgment.
6. **[low] MILESTONES implied the blocked_on machinery was exercised
   live** (Skeptic) — the record doc was honest, the queue summary
   wasn't. **FIXED**: MILESTONES now states the machinery was not
   triggered (no delegation failed).

## What went well

- The ownership split itself, the falsifiability rule, and the routing
  table survived both lenses unchallenged — the §14 core translated
  faithfully; every finding was an edge-condition of the surrounding
  contract, not the decree's content.
- The exercise record's honesty (blocked_on "NOT exercised live")
  was itself used by the Skeptic as the standard the other docs were
  held to — the honesty discipline is load-bearing.

## Lead judgment

Accept all six; findings 1–3 are genuinely load-bearing (each names a
concrete path where the contract as written would have produced the
§14 failure it exists to prevent). Finding 5's fix deliberately
declines the reviewer's implicit invitation to extend the cause enum —
that set is Jeremy's §14 text; the boundary case is routed around it,
not into it. No fix required src/ — the chunk stays inside the star
charter (prompting only).
