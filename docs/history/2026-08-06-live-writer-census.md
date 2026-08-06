---
status: record
---

# Live-writer census — threshold gates vs their metric writers (2026-08-06)

**Method:** a gate is only as live as its input's writer. For every numeric
threshold gate in the self-improvement lanes, trace: comparison site → all
writers of the field it reads (file:line) → one call chain to a production
entry (heartbeat / agent_loop / handle / evolver) → positive evidence from
runtime data (`~/.maro/workspace/memory/*.jsonl`). Verdicts LIVE / SUSPECT /
DEAD. Motivated by the skills-promotion gate found dead 8 weeks (2026-08-06,
fixed in a0bae77): `Skill.use_count`'s only writer was removed 2026-07-29 and
the gate kept reading it — 0/376 skills promoted.

Two parallel read-only audit agents (skills/graduation lane;
knowledge/inspector/evolver/memory lane), every DEAD/SUSPECT claim re-verified
against code and data before acting (standing ~30–50% hallucination rate for
delegated findings — these all held up).

## Fixed in this commit (skills lane)

The a0bae77 promotion fix reached one of four `use_count` readers. The other
three were still dead, plus a live-fire hazard:

1. **`maybe_demote_skills` (skills.py:1353)** — read legacy-frozen
   `Skill.use_count`; an established skill with all uses in live SkillStats
   could never demote. Now gates on
   `max(use_count, SkillStats.total_uses)`, same shape as the promote fix.
   (Candidate set was doubly empty — 0/376 established — but promotion is
   un-dead as of a0bae77, so established skills are coming.)
2. **`skills_needing_rewrite` (skills.py:1425)** — same corpse, third site.
   Circuit-open skills could never reach the rewrite lane (uses leg
   structurally unsatisfiable). Same fix.
3. **`retire_losing_variants` parent leg (skills.py `parent_trials`)** —
   `parent.use_count >= MIN_VARIANT_USES` could never fill for post-07
   skills. Same fix.
4. **Frontier A/B self-variant bug** — `rewrite_skill` mutated the parent
   row in place and returned it, so `create_skill_variant`
   (skill_lifecycle.py frontier path) stamped every "challenger" as its own
   parent: all 6 live variants had `variant_of == their own id` and 0
   trials; the A/B never had two arms. Worse, had the retirement gate ever
   fired, "retiring the challenger" would have archived-and-deleted the
   parent itself. Fixes:
   - `rewrite_skill(..., in_place=False)` mints a distinct, unsaved
     challenger (fresh id, deep copy) for the frontier lane; the circuit
     lane keeps in-place semantics.
   - `create_skill_variant` raises ValueError on same-id stamping (callers
     catch non-fatally — loud failure over silent corruption).
   - `retire_losing_variants` heals existing self-referential rows (clears
     the corrupt `variant_of`, keeps the skill) — the 6 corrupt live rows
     drain on the next evolver cycle.

Pin tests: `test_skills.py` (demote + rewrite stats-uses pins),
`test_ab_variants.py` (self-variant rejection, heal, parent-trials-from-
stats), `test_evolver.py::TestRewriteSkillChallengerMint`.

## Confirmed LIVE (no action)

- **Circuit breaker** `CIRCUIT_OPEN_THRESHOLD` (skills.py) — writer on the
  step path (loop_post_step/loop_blocked → `update_skill_utility`), counter
  moving, open-circuit skills excluded from injection. (Its downstream
  rewrite payoff was dead — fix #2 above restores it.)
- **Graduation `min_count=3`** — diagnoses.jsonl 1,464 rows, gate fired
  2026-08-03. Note: the graduate→verify→demote back half has processed 0
  rows ever (all 8 graduation suggestions `applied=False`, human-apply by
  design) — inert-by-design, recorded, not a bug.
- **Knowledge tier promotion** `PROMOTE_MIN_SCORE`/`PROMOTE_MIN_SESSIONS` —
  fired 2026-08-02 (two long-tier arrivals). **GC** `GC_THRESHOLD` — 437
  GC'd across 24 decay cycles. **Canon** `CANON_APPLY_THRESHOLD` — 588
  canon_stats rows, 45 canon suggestions historically.
- **introspect `_BROAD_STEP_ELAPSED_MIN_TOKENS`** — token fields present on
  1220/1220 recent step_done events; classifier demonstrably running.
- **evolver core gates** (min_outcomes, confidence auto-apply,
  scan_step_costs, scan_quality_drift, post-apply verify) — all moving with
  2026-08-06 data.

## Recorded findings — need a decision or a design, not a quiet fix

1. **Phase-60 verification-calibration loop is the fourth dead lane** (both
   ends orphaned): `record_verification` (knowledge_lens.py:1281) has zero
   src/ callers — its docstring claims "called by inspector.check_alignment()"
   which doesn't exist; `calibrated_alignment_threshold`
   (knowledge_lens.py:1400) has zero src/ callers; memory.py re-exports
   both. verification_outcomes.jsonl: 58 rows frozen since 2026-04-12, tail
   rows test-shaped. Decision: wire it (inspector's `assess_goal_alignment`
   is the natural caller) or remove it. Same family as use_count /
   SKILL_REWRITE / promotion-gate.
2. **Inspector threshold cluster is unreachable in current config**:
   `_BREACH_THRESHOLD`, `_ESCALATION_MIN_HITS`,
   `_CONTEXT_CHURN_TOKEN_THRESHOLD` have live inputs, but `run_inspector`'s
   only production caller is the heartbeat tick lane — no daemon running AND
   `heartbeat.autonomy: false` (Jeremy's 2026-07-12 decree). Hard evidence:
   `inspection-log.jsonl` has never existed in the live workspace, so
   conductor/quality-gate/heartbeat friction readers get empty. Not a code
   bug — a decreed-off consumer. If inspector findings are wanted, they need
   a live lane (e.g. loop_finalize cadence like the evolver got); that's a
   design decision.
3. **`_REPHRASING_MIN_COUNT` is double-dead by construction**: no comparison
   site anywhere, and `SIGNAL_REPEATED_REPHRASE` has zero producers —
   declared, listed, described, never emitted. Either implement the detector
   or remove the constant+signal.
4. **Node-promotion gate will ~never fire as built**:
   `NODE_PROMOTE_MIN_APPLICATIONS=2` needs two dedup re-observations of the
   same candidate title (Jaccard ≥0.7); observed rate is 1 bump across 433
   candidates in 8 weeks (432/433 flat at times_applied=0). The pool dates
   to 2026-06-11 — what shipped 2026-08-02 was the sweep, not the pool.
   Plumbing verified live end-to-end; the threshold/matching design is what
   starves it.
5. **Calibration scan is a noise generator on frozen input**:
   calibration.jsonl's newest row is 2026-07-03 (tail rows are
   `esc-test-001` test rows in the live store), yet `scan_calibration_log`
   re-derives and re-saves the same findings every finalize — 81
   `calibration-*` suggestions since 2026-07-20. Underlying issue is
   systemic: `_save_suggestions` has no dedup, so all scans re-emit
   (cost-* 76, drift 28). Wants a dedup-on-save or scan-freshness design
   pass, not a point fix.
6. **Canon high-hit stats are permanently orphaned**: every lesson with ≥10
   canon hits (max 80) has since left the long store, and the gate requires
   store membership — current 7 long lessons max at 3 hits. Honest silence,
   but the hit history can never be acted on; worth folding into any canon
   rework.
7. **Synthesis + escalation gates are wired but starved/unread**:
   `synthesize_skill`'s gates fire on `had_no_matching_skill` — one attempt
   in 4 weeks with 376 skills in the pool. `needs_escalation` is set in
   production but consumed only by the manual CLI (`skill-stats
   --escalated`). Both honest, both effectively decorative today.
