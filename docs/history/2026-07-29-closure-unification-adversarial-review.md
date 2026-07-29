---
status: record
---

# Adversarial review — closure-check unification (d9f9968)

Post-land review per arc discipline: 3 Codex lenses (`codex exec`,
opposite model family) — Skeptic, Architect, Minimalist — against the
landed diff + tree. Eleventh round of the discipline.

## Intent

Unify the post-loop closure check into the adaptive-execution decision
seam (census #30) without destroying accreted verdict-integrity
behavior: `director.evaluate_closure()` deterministically maps the
untouched `verify_goal_completion` verdict into the shared
`DirectorDecision` vocabulary; `ClosureVerdict` retires as decision
interface, survives as evidence record; four call sites route through
the choke point; policy stays caller-side.

## Verdict: PASS (with one fix landed)

No high-severity findings. One of three findings accepted and fixed
same session; zero hallucinated claims (all three cited real code
accurately — the two rejections are judgment disagreements, not
fabrications).

## Findings

1. **[medium] ACCEPTED — migration is test-invisible to regression**
   (Architect). The closure suites monkeypatch
   `director.verify_goal_completion`, so a call site regressing from
   `evaluate_closure` back to the raw pipeline would keep passing every
   existing test while silently bypassing the decision layer — and any
   future closure policy landing there (§9.3 declare-blocked,
   gate-reads-scope). Lens: Architect. Principle:
   migrate-callers-then-delete-legacy-apis / boundary-discipline.
   **Fix:** AST census tripwire
   `test_no_production_caller_bypasses_the_decision_layer` — no
   production module outside closure_verify.py/director.py may CALL
   `verify_goal_completion`. Mutation-proven (a simulated cli.py
   regression is caught at its call line).

2. **[medium] REJECTED — decision action "not a reliable orchestration
   fact" across lanes** (Architect). A conf-0.65 restart-worthy verdict
   maps to `action="restart"` while the CLI lane ignores the action and
   demotes only at ≥0.7, so `maro run` can exit done while the seam
   said restart. Factually accurate — and it is the documented,
   pre-existing design: the CLI lane is honesty-only parity with NO
   restart machinery (BACKLOG #18, shipped long before this chunk), the
   0.6-restart vs 0.7-demote thresholds predate the chunk and guard
   different facts (a restart costs a full loop → positive-evidence
   bar; demotion is recording honesty → higher-confidence bar, no
   check-failure requirement), and "director recommends, caller
   disposes" is stated in both docstrings. Behavior is bit-identical to
   pre-chunk. The finding restates the deliberate policy split as an
   inconsistency.

3. **[medium] REJECTED — handle liveness pin "blesses" an impossible
   decision/evidence pair** (Minimalist). The pin constructs
   `action="restart"` riding a narrative-only verdict to prove handle
   consumes the action; reviewer argues the better test is at
   `evaluate_closure` and that handle lost its evidence sanity check.
   The suggested seam tests ALREADY exist
   (`test_narrative_only_gaps_map_to_continue`,
   `test_low_confidence_maps_to_continue`,
   `test_inconclusive_checks_map_to_continue`,
   `test_unjudged_verdict_maps_to_continue` — the invariant is pinned
   where it lives), and the handle pin deliberately tests consumption
   topology, not verdict semantics: seam invariant + consumption
   liveness are complementary, and re-deriving evidence checks in
   handle would recreate exactly the duplicated gate this chunk
   removed.

Skeptic returned **no findings** after verifying the mapping predicate
against the prior inline gate, the evidence attachment, and the
monkeypatch-seam liveness.

## What Went Well

- The mapping predicate transfer (handle's inline gate →
  `evaluate_closure`) verified exact by the Skeptic — no semantic
  drift in the port.
- The monkeypatch pass-through design (existing suite exercises the
  new layer unchanged) held up under adversarial reading; the
  Architect's finding is about future regression, not present
  correctness.
- All three reviewers cited real lines — zero hallucinated claims this
  round (historic rate 30–78%).

## Lead Judgment

Accept #1 (cheap, mechanical, house-pattern census tripwire; the exact
failure mode it pins is the one this repo has been burned by — silent
seam bypass surviving green tests). Reject #2 (documented design, zero
behavior delta; unifying the thresholds would be a real policy change
needing its own justification, not a review fix). Reject #3 (the
proposed alternative already exists; the pin's construction is the
point, not an oversight).

Sandbox note: neither reviewer could run pytest (read-only sandbox, no
temp dir) — noted per skill discipline; the suite was run green by the
lead before and after the fix.
