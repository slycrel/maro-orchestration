---
status: record
---

# Adversarial review — §9.3 structural declare-blocked (0965a7c)

Post-land review per the arc's per-chunk discipline. 3 Codex lenses
(Skeptic / Architect / Minimalist) via `codex exec` over the full chunk
diff (13 files, 488 insertions). Findings verified against the tree
before acting (feedback_verify_before_fix): **2/2 distinct findings
verified real, 0 hallucinated** — eleventh clean round.

## Intent

Extend the Phase 62 convergence seam one level up: a closure restart
that fails the identical checks as its parent attempt is a plan-level
stall — declare a typed stop (`thesis-refuted`, structural evidence)
instead of pretending done or restarting again. Fail open everywhere;
zero new LLM calls.

## Verdict: CONTESTED → remediated same session

High-severity three-lens consensus on one finding; both findings
accepted and fixed.

## Findings

1. **[high — three-lens consensus: Skeptic high, Architect + Minimalist
   medium]** Declare-blocked could stamp `stop_verdict="thesis-refuted"`
   while the run still returned `status="done"`. The consume branch
   stamped through the first-write-wins rail but never touched status;
   the generic confidence demotion (handle.py, "Status honesty" block)
   gates at `confidence >= 0.7` while restart-worthy starts at 0.6 — so
   any verdict in **[0.6, 0.7)** produced a run that was simultaneously
   "done" and structurally declared blocked. The new handle test used
   conf 0.85 and never asserted returned status, so the band was
   untested. **Verified**: the 0.7 gate is exactly as claimed
   (handle.py:2144-2148 pre-fix).
   - Lens: all three. Principle: status honesty / verified-done beats
     reported-done (the 2026-06-11 live find the demotion block cites).
   - Fix: the consume branch now demotes `status="done"` →
     `"incomplete"` (and sets `stuck_reason` if unset) before stamping.
     Rationale: the declare-blocked evidence is deterministic — the
     same hard-failed checks across BOTH attempts (restart-worthy
     requires ≥1 hard fail, 0 inconclusive) — which is stronger than
     the LLM-confidence bar the generic demotion guards with. A run
     that hard-failed identical checks twice is not done at any
     confidence. Non-done statuses (stuck, failed) are already honest
     and are left alone. Coherent with the stop-verdicts decree
     (verdicts ride beside status; the status change comes from the
     existing demotion rail's semantics, not from the verdict itself).
   - Test: `test_restart_boundary_consumes_declare_blocked` now runs at
     conf 0.65 — inside the flagged band, below the generic bar — and
     asserts `status == "incomplete"`.

2. **[medium — Architect solo]** Command-identity fingerprinting was too
   coarse in the false-STALL direction: a broad command (`pytest -q`,
   `npm test`) failing on test A pre-restart and test B post-restart
   produced the SAME fingerprint → declare-blocked after a genuinely
   productive restart. Failure identity (stdout/stderr, available on
   every check-result row) was discarded; tests pinned only narrow
   single-purpose commands. **Verified**: rows carry
   `exit_code`/`stdout[:500]`/`stderr[:300]` and the population site
   used `command` only.
   - Lens: Architect. Principle: the seam's own design —
     `loop_blocked._error_fingerprint` hashes reason|result *content*,
     not the probe name; the twin should too. A false stall is the
     fail-closed direction the design explicitly promised to avoid.
   - Fix: `_failed_check_signature()` — fingerprint material is now
     `command => exit N: <bounded output slice>` (stderr+stdout,
     whitespace-normalized, 200 chars; `closure_fingerprint`'s
     per-entry cap raised to 500 to hold the full signature).
     Nondeterministic output (timestamps, tmp paths) can only make
     fingerprints DIFFER, which fails open to a normal restart — the
     safe direction. Evidence text now shows failure content, not just
     the command. This narrows the BACKLOG "fingerprint coarsening"
     item to its artifact-level half (command → target artifact), which
     stays evidence-gated.
   - Tests: `TestFailedCheckSignature` (same command + different output
     → different fingerprints; signature shape; no-output form);
     `test_failed_checks_records_hard_fails_only` updated to the
     signature form.

## What Went Well

All three reviewers independently verified the core plumbing clean: the
fail-open guards (no baseline / empty fingerprint / differing
fingerprints → exact pre-chunk behavior), the `outcome == "fail"`-only
population (inconclusive stays verifier-failure, not goal evidence),
and the prior-verdict baseline pass-through at the re-verify site. No
reviewer challenged the design direction (structural stall over budget
exhaustion) or the recommend/dispose contract.

## Lead Judgment

- Finding 1: **accept.** Initially defensible as the documented
  verdict-beside-status design, but the reviewers are right that this
  specific verdict class carries deterministic evidence the generic
  demotion bar was never designed to gate — leaving "done" standing in
  the [0.6, 0.7) band is exactly the reported-done-over-verified-done
  failure the demotion block exists to prevent.
- Finding 2: **accept.** I had BACKLOG'd coarsening as evidence-gated,
  but that entry was about *loosening* (command → artifact) to catch
  regenerated checks. The Architect's finding is the opposite defect —
  false positives — and fixing it costs one helper, needs no new
  evidence, and makes the fingerprint a truer twin of
  `_error_fingerprint`. Waiting on evidence to *avoid declaring a
  false stall* would have inverted the fail-open promise.
