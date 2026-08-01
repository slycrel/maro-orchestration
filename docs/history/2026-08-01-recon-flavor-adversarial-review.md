---
status: record
---

# Adversarial review — recon flavor runtime slice (68ed793)

3 Codex lenses (Skeptic / Architect / Minimalist) via `codex exec`,
read-only, vs commit 68ed793. Per house discipline every claim was
verified against the tree before acting. Verdict as-reviewed: **REJECT**
(all three said "do not ship") → remediated same session. 7 deduplicated
findings; 7/7 code claims verified real at the cited lines, 0
hallucinated (fifteenth clean round) — though two findings' *impact*
claims were overstated (F2, F5 below), which is a different failure mode
than fabrication and worth tracking separately.

## Accepted → fixed

**F1 (Architect+Minimalist, HIGH) — emission gaps in two planner
lanes.** Wide/deep goals take the staged-pass lane, which replaces the
assembled system prompt with `_STAGED_PASS_SYSTEM` — RECON_FLAVOR_RULES
never reached it, so the goals with the most survey-shaped work were the
ones never taught the tag. The multi-plan composer and the step-ceiling
corrective re-ask both REWRITE steps with fresh prompts and no
instruction to keep inline tags — they could silently strip
[recon:]/[after:N]/[boundary] from every plan they touched (the tag-drop
half was latent for boundary/after tags before this slice too). Fix:
`_recon_emission_on` computed once, staged lane appends the rules under
the same killswitch; composer + ceiling prompts get an explicit
keep-tags-attached-verbatim instruction (unconditional — it protects
whatever tags the candidates carry). Pins: staged-lane teaching,
compose-prompt instruction.

**F3 (Architect+Skeptic, HIGH) — `_split_exec_analyze` destroys the
tag.** The exec+analyze shaper rebuilds step text ("Run X and save
output…" / "Read the captured output and…" with 120-char truncation) —
a tagged compound recon step became two untagged commit steps: no
map-edit contract, deliverable verification question. The boundary tag
already had an explicit skip in `_shape_steps` for exactly this reason;
recon steps now get the same skip (they are information work — the
split exists for deliverable compound steps). Because `_shape_steps` is
the single invariant gate, one guard covers initial-plan, inject,
replan, and interrupt lanes. Pins: tagged compound step passes through
unsplit; the same text untagged still splits.

**F4 (Skeptic+Minimalist+Architect, HIGH) — five early returns
unstamped.** await-event done/timeout (before detection), HITL
constraint block (before detection), and both adapter-death handlers
(after detection but before the final seam) returned outcomes with no
flavor stamp — "every outcome shape is stamped" was false as landed.
Fix: detection hoisted to function entry, one `_stamp_flavor` helper,
every return path routes through it (the final seam now uses the same
helper). Pins: constraint-blocked and adapter-error recon outcomes
stamped.

## Rejected, with rationale

**F2 (all three, HIGH) — "add flavor/recon_decision fields to
StepOutcome; downstream cannot observe the stamp."** The code claim is
true (no typed field; the dict stamp dies at `step_from_decompose`) but
the impact claim is false: `StepOutcome.text` carries the tag into every
durable record (manifests, checkpoints, loop JSON), `parse_dependencies`
strips only `[after:N]` so clean_steps keep the recon tag, and flavor is
a pure function of text (`planner.step_flavor`) importable by any
reader. That is the design: the tag IS the schema — a parallel typed
field is a dual source of truth that can drift from the text it
duplicates. What WAS wrong was our own wording ("outcome rows now carry
the fields" in BACKLOG / "every downstream reader" framing) — sharpened
to name the text as the durable carrier and the dict stamp as
in-process convenience. New pin: tag survives parse_dependencies into
the clean_steps → StepOutcome.text path.

**F5 (Skeptic, MEDIUM) — parallel/DAG lanes never verify recon steps.**
True observation, wrong attribution: the parallel lane skips Ralph
verification for ALL steps ("requires session-level state" —
loop_parallel.py, pre-existing). Recon inherits the lane's existing gap;
a recon-specific patch would verify recon steps in a lane that verifies
nothing else. Named as a BACKLOG edge riding whatever fixes
parallel-lane verification generally.

**F6 (Minimalist, MEDIUM) — factory_thin bypasses the contract.**
factory_thin/mode:thin are adjudicated instruments (2026-07-21 factory
adjudication: kept as instruments, not mainline), outside the main-loop
contract this slice targets. No change.

**F7 (Minimalist, LOW) — delete strip_recon_tag (no caller).** Already a
named deliberate edge in BACKLOG at ship time; it is the parser's
3-line inverse (strip_boundary_tag precedent) with display surfaces as
the named consumer. Keep.

## Outcome

Fix commit: planner (staged-lane teaching + two keep-tags prompt
instructions), step_exec (entry detection + `_stamp_flavor` on every
return), loop_planning (shaper skip), 6 new pins (22 total in
tests/test_step_flavor.py), BACKLOG edge rewrites, record-doc addendum.
Full suite green before landing.
