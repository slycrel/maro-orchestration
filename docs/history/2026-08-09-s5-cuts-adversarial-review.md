---
status: record
---

# Adversarial review — §5 cuts (lesson refight + trace scoring)

**Date:** 2026-08-09
**Scope:** commits `ea83edb` (§5 cut A — lesson un-contest/refight verb) and
`27512e4` (§5 cut B — evolver/thinkback trace scoring), diff `d7360b3..27512e4`
over `src/` + `tests/`.
**Reviewers:** 3 Codex lenses (`codex exec` — cross-model per house rule):
skeptic, architect, minimalist. Raw output: `/tmp/adversarial-review.n9C4Ye/`
(session-scoped; findings reproduced below).
**Protocol:** verify-before-fix — every code claim checked against the tree
before any fix (historical hallucination rate ~30–50%).

## Verdict: CONTESTED → fixed same day

Six confirmed defect classes, all fixed in the follow-up commit (this doc
rides it). Three findings rejected or accepted-as-residual after
verification.

## Confirmed (fixed)

| # | Finding | Lens | Verified how | Fix |
|---|---------|------|--------------|-----|
| F1 | **Stale-verdict race:** `refight_lesson._apply` only checked "still contested" — a verdict rendered against the OLD contest stamp could resolve a NEWER contest (resolved-then-re-contested between LLM call and lock). 3/3 reviewer consensus. | all three | Read `_apply` guard; confirmed no stamp comparison | Verdict is stamp-bound: `_same_contest` matches `contested_at + source` before any mutation; mismatch = no-op, returns None |
| F2 | **Revise destroys the original + bypasses the injection scan:** the pre-revise text was overwritten with no archive copy (for a tiered-only row that copy is the ONLY full text), and the LLM-authored replacement text skipped `_lesson_looks_adversarial` — the refight prompt carries external content (contest reasons, captain's-log failure summaries) | skeptic + architect | Read revise branch; confirmed no `_archive_lessons` call, no scan | Pre-revise row archived `reason="refight_revise"` before overwrite; new text scanned — adversarial pattern = unusable verdict (stays contested, evidence consumed) |
| F3 | **Keep's flat clear was blind and silent:** `uncontest_flat_lesson` cleared whatever stamp the flat row carried (dual-written stores diverge — a newer flat contest could be erased) and its return value was discarded | skeptic + architect | Read keep branch + helper; confirmed unconditional pop + ignored result | Helper gained `expected_stamp` (matched on `contested_at + source`; None = operator escape); result captured, miss logged as warning, `flat_cleared` recorded in the LESSON_REFOUGHT event |
| F4 | **Evidence gate never consumed → unbounded ambient spend:** `run_skill_maintenance` runs on EVERY loop finalize (`loop_finalize.py:814`); a contested row with ≥1 post-contest re-sighting whose refight kept returning unusable verdicts would be re-billed 3 LLM calls per completed run, forever | skeptic (spend), architect (mechanism) | Traced maintenance call site; confirmed scan admits on raw `_reinforced_since_contest ≥ 1` with no attempt marker | Unusable verdicts stamp `contested["refight_attempted_at"] = times_reinforced` (stamp-bound, under lock); `contested_lessons(new_evidence_only=True)` requires sightings BEYOND the last judged level. CLI verb uses the default scan — operators can always re-fight explicitly |
| F6 | **Killswitch can't kill on quoted YAML:** `effect_promotion_enabled` / `effect_demotion_enabled` used bare `bool(cfg)` — a quoted `"false"` is a truthy string. Pre-existing on the demotion side, but we own the file and the chunk-5a F1 normalization rule is standing | minimalist | Read both helpers; compared against `_novelty_term_enabled` | Both normalized (`"false"/"0"/"no"/"off"` → False), mirroring `_novelty_term_enabled` |
| F8 | **Δ-confirm text TOCTOU:** `confirm_lesson_by_delta` verified provisional/quarantine/contested in-lock but not TEXT — a Δ measured against one wording could confirm a concurrently refight-revised row with NEW text (which re-entered provisional precisely because it must re-earn its record) | skeptic | Read `_confirm`; confirmed no text check | `expected_lesson` param compared under lock; `run_effect_route` passes the measured row's text. Empty = legacy behavior |

## Rejected / residual (verified, not fixed)

| # | Finding | Disposition |
|---|---------|-------------|
| F5 | LONG-tier dedup re-sighting feeds `observe_pattern` even for provisional/thinkback rows | **Pre-existing residual**, not introduced by these chunks — the provisional-re-record class predates cut B. Noted; not in scope |
| F9 | `minted_by` lost when a thinkback mint dedups into an existing row | **Working as intended** — dedup means the lesson already existed with its own provenance; the trace didn't mint it |
| skeptic-3 | Cross-process duplicate refight spend (two loops finalizing at once both scan before either applies) | **Mostly bounded by the F4 fix** (each unusable attempt consumes the evidence; usable verdicts resolve the row). A per-row lease would close the residual double-spend window; deferred — worst case is one duplicated call per collision, self-limiting |

## What went well (reviewer consensus)

- LLM calls outside store locks, verdicts applied under lock against a fresh
  reload — the 2026-08-04 stale-copy discipline held everywhere.
- `refight_retire` correctly excluded from graveyard resurrection (the
  user_forget precedent applied without being asked).
- Trace mints entering provisional (not full-citizen flat writes) was judged
  the right structural half of the LeAct acceptance filter by all three
  lenses.

## Pins added

- `tests/test_lesson_refight.py`: TestStampIdentity (F1),
  TestEvidenceConsumption ×4 (F4, incl. two-cycle maintenance no-respend +
  operator-scan escape), TestReviseRetention ×2 (F2 archive + adversarial
  text), TestFlatStampBinding ×3 (F3 mismatch/newer-contest/event).
- `tests/test_trace_scoring.py`: TestConfirmTextBinding ×2 (F8),
  TestKillswitchNormalization (F6, both switches).
