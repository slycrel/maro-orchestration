---
status: record
name: 2026-08-21-caps-sweep-review
description: Adversarial review round on the caps-cleanup chunk (e484f5e4) — 4 lenses on sonnet-medium fallback, verification ledger, fixes applied same session.
---

# Caps-sweep adversarial review — 2026-08-21

Chunk under review: `e484f5e4` (override-discipline tripwire + truncation-audit
tranche 2). Reviewers: Skeptic, Architect, Minimalist, Expert QA — all four
returned clean artifacts. Round dir: `/tmp/adversarial-review.8SGu9l`.

## Verdict: CONTESTED → fixed to green (SAME-MODEL FALLBACK: sonnet-medium)

An unusually real round — verify-before-fix confirmed nearly every code claim,
and one MEDIUM turned out to be a latent runtime crash the reviewer had
under-diagnosed.

## Verification Ledger

- **VERIFIED (HIGH, Skeptic#1/Architect#2): tripwire blind to positional
  `clip(text, N)` literals.** By construction — `_scan()` matches only
  keyword args in `BUDGET_KWARGS`; the sweep's own fixes are positional
  `clip()` calls. Resolution: documented as a NAMED v1 scope edge in the
  test docstring (alongside count caps and name-bound evasion) + BACKLOG
  follow-up tranche. Policing the value means adjudicating ~150 existing
  clip sites — its own chunk, not a patch.
- **VERIFIED (HIGH, Min#1/Arch#3/QA#2, Skeptic#7): negative control never
  called `_scan()`.** Confirmed — the v1 test hand-copied the predicate.
  FIXED: `_scan(src_root)` now takes a root; the control plants overrides
  in a temp tree (including a subpackage) and asserts the real scanner's
  exact keys, plus a signature-default non-match.
- **VERIFIED (HIGH Arch#1 / MED Skeptic#4): `probe_command` went from
  cut-at-300 to unbounded** into the append-forever captain's log, on an
  LLM-emitted field. FIXED: marked `clip(cmd, 2000)` — replay handle
  survives, runaway bounded.
- **VERIFIED (MED→worse, Min#3): `max_length` kwarg family unscanned** —
  and the four `harness_optimizer.py` sites were latent **TypeErrors**
  (`safe_str` takes `max_len`, keyword-only; the loop sits outside the
  try). FIXED: kwargs corrected to `max_len`, `max_length` added to
  `BUDGET_KWARGS`, four registry rows with rationale noting the DOA bug.
- **VERIFIED (HIGH Min#2 / LOW-MED Skeptic#5): navigator signals carried a
  second, [:80]-starved copy of block_reason** beside the full parameter.
  FIXED: duplicate dropped (callee already receives the value); inventory
  row deleted. The callee's own [:300] stays — ledgered debt, and the
  shadow lane is mid-experiment (changing evidence width would corrupt the
  arms).
- **VERIFIED (MED, Arch#5): inspector comment cited the step-result
  distribution but the field is upstream-bounded at VERDICT_PROSE_CAP
  (2000)** via closure_verify. FIXED: comment rewritten — 4000 is a
  generous ceiling above the upstream bound, not a fresh measurement.
- **VERIFIED (MED, all four): no boundary tests on the new cap values.**
  FIXED: `tests/test_caps_sweep.py` — behavioral pins for planner operator
  docs, the retry hint, and claim-probe receipts (typical-length passes
  whole + runaway is bounded-and-marked), source pins for the buried sites.
- **VERIFIED (HIGH, QA#1): removed overrides consolidate onto unmeasured
  defaults** (memory_bridge 1200, playbook 800 — the latter's own module
  notes the seed overflowed 800; live playbook ~2.6k chars). Resolution:
  single-owner budget notes at both defaults + BACKLOG measurement
  follow-up. Deliberately NOT raised blind — changing injection volume
  without a distribution pass is the disease this sweep treats.
- **VERIFIED (MED, Skeptic#2): `DEFAULT_ENTRY_CAP` and `_REVIEW_STEP_CUT`
  both encode 4000 from the same distribution.** Resolution: cross-linked
  provenance comments; deliberately NOT merged (different surfaces — gate
  evidence window vs generic entry budget).
- **VERIFIED (LOW, Skeptic#6): scanner glob non-recursive.** FIXED: rglob,
  and the negative control now proves subpackage coverage.
- **VERIFIED (LOW, Min#5): claim-probe comment attributed the 13%
  saturation to the 400 capture cut; it was measured at the 300 emit
  re-cut.** FIXED: wording corrected ("true over-400 rate unknowable").
- **VERIFIED (LOW, Arch#7): bare `goal[:200]`/`goal[:100]` siblings in
  the fixed prompts.** FIXED: marked clips at the same widths.
- **VERIFIED (LOW, QA#5): `probe_output_preview` is written-only.** FIXED:
  comment at the write site.

## Accepted residuals (recorded, not fixed)

- **Forged-marker idempotency bypass on user-authored docs (Skeptic#8,
  QA#4):** `clip()`'s marker-shape guard now sees raw operator files; a
  hand-written doc ending in a marker-shaped suffix near the boundary
  would pass unclipped. Narrow, deliberate anti-double-clip design; noted
  here as the class-of-input change. Revisit if forged-marker provenance
  ever matters (same residual family as the audit's typed-truncation-
  metadata deferral).
- **Fingerprint [:200] stay is adjudicated by assertion, not data
  (Min#6):** the head-carries-identity claim is plausible but unmeasured;
  tagged as such in place. A measurement (tail-divergent failure pairs)
  is cheap if convergence detection ever misbehaves.
- **Architect#8:** observation, no defect (asymmetric risk across the
  three redundant-override removals) — the single-owner notes above are
  the response.

## Ledger falsifier check (r1)

No UNSETTLED entries this round — every claim was settleable by reading the
tree and running the scanner/tests locally, which is the honest state for a
review whose subject is mostly test infrastructure and comment claims. The
probes were runnable and run.

---

# Round 2 — fix diff (`e484f5e4..e88beca0`), Skeptic + Architect

Round dir: `/tmp/adversarial-review.5JwlTC`. Verdict: CONTESTED → fixed
(SAME-MODEL FALLBACK: sonnet-medium). Both HIGHs verified real.

## Verification Ledger (r2)

- **VERIFIED (HIGH, Skeptic): the r1 navigator "fix" removed a dead key,
  not the starved surface** — `navigator_shadow.py:511` bare-sliced the
  text the navigator actually judges (`WorkReport.summary`) at [:300],
  unmarked, cutting the measured p99 tail. FIXED: marked `clip(…, 600)`
  (= measured p99, matching same-commit siblings); comment notes pairs
  recorded before 2026-08-21 saw the narrower input, so lane adjudication
  can segment; inventory row deleted (99 rows).
- **VERIFIED (HIGH Architect / MED Skeptic, same finding): the r1
  inspector comment's upstream-bound story was false for 2 of 3 lanes.**
  Traced: handle.py NOW lane → VERDICT_PROSE_CAP 2000; loop_finalize
  agenda lane → STORE_TOTAL_BUDGET 4000 (no 2000 net — the majority
  path); evolver verify lane → one uncapped stdout line. FIXED: comment
  rewritten per-lane with an explicit do-not-tighten warning. (The r1
  comment repeated the exact defect class r1 had flagged — noted.)
- **VERIFIED (MED, Architect): `claim_preview[:200]`/`summary[:120]`
  siblings in the same event dict left bare with no exemption note.**
  FIXED: exemption comment (claim is short-by-prompt-contract, preview-
  named) + per-event total note (~4.4KB worst-case row, accepted).
- **VERIFIED (LOW, Skeptic): playbook docstring example restated the
  default (`inject_playbook(max_chars=800)`).** FIXED: kwarg dropped.

## Accepted residuals (r2)

- clean.py fixture assertion is non-discriminating (scanner never visits
  signature defaults) — kept as documentation-by-test; the top.py/deep.py
  halves carry the must-detect load (Skeptic LOW).
- Real-tree `src/maro_assets/` coverage proven only by the synthetic
  rglob fixture, not against the live tree (Architect LOW) — accepted;
  the fixture pins the traversal semantics.
- Probe-receipt tests depend on a real shell + `cat` (Skeptic LOW) —
  accepted; this box and CI are Linux, and the shell path IS the
  production path being pinned.
- Architect confirmed the single-owner notes at memory_bridge/playbook
  are accurate (positive verification, no action).

Convergence: r2 highs were both residue of r1's own fixes; no new defect
class. Lows-only expected next round — stopping at r2 per the
review-to-fixpoint practice (converges to lows by round 3-4; this arc's
remaining items are BACKLOG'd tranches, not review residue).
