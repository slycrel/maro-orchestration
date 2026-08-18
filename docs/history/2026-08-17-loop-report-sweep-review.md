---
status: record
---

# Adversarial review — loop_report torn-store honesty + truth sweep (2026-08-17)

Round 1 on the landed range `80d1b6df..97a21931` (torn-store fixes
`5cf6f60f`, survivor closures, 37-mutation spec, records). **Lane
note:** codex usage-capped until ~2026-08-19, so all five seats ran the
sanctioned same-model sonnet-medium fallback (`claude -p --model sonnet
--effort medium`), serialized per the box-parallelism rule. Roster:
Skeptic, Architect, Minimalist, Expert QA, Experimentalist (standing
trigger: the chunk ships numbers). All five disclosed static-trace-only
(no pytest in their sandbox).

## Verdict: 5/5 PASS round 1 → 2 MEDIUMs verified real, fixed same session

### Accepted MEDIUM — Architect: the index row silently laundered a torn run_card

The Outcome panel announces a torn `run_card.json` honestly, but
`_gather_run_summaries` — reading the *same file* for the *same
purpose* one page over — degraded to `card = {}` + a log-only warning.
The row fell back to raw process status ("done") with no page-visible
signal: exactly probe #4's failure ("a curated run rendered as
not-yet-curated"), reproduced on the cross-run index. The miss wasn't
unconscious — my own test asserted the silent-to-the-page behavior —
but it contradicted the chunk's operator-visible bar, and the record's
prose blurred metadata-unreadable (badged) with card-unreadable
(unbadged) on the same row. **Fix:** the row now carries a visible
overcap marker ("run_card.json exists but could not be read — this run
WAS curated…"), mirroring the step-flags glyph pattern; mutation
`index :: torn run_card marker never renders` pins it.

### Accepted MEDIUM — Expert QA: a vacuous assertion in the torn-card test

`test_index_row_falls_back_when_run_card_torn` asserted bare
`"done" in content` — satisfied unconditionally by the page's
`.badge-done` CSS and the status legend, so the status-fallback claim
pinned nothing. Verified: reverting the `meta.get("status")` fallback
passed the old test. The *neighboring* test had learned this exact
lesson (`>unreadable</span>`, commit `c81ff9b3`) in the same session —
the pattern wasn't reapplied one test down. **Fix:** pins
`data-status="done"` (row attribute, per-row only) + the marker text;
new mutation `index :: row status loses the process-metadata fallback`
confirms the assertion now bites (DETECTED). Spec 37 → 39, all
accounted.

### Accepted (doc fix) — Experimentalist: 25/37 drift in the README ledger

All shipped numbers re-derived independently (37 spec entries, census
95/87/12 hand-summed, zero remaining loop_report census entries) and
matched across record/BACKLOG/GOAL_BRAIN — except
`tests/mutation/README.md`'s ledger row said "25/37 first pass" where
the runner's log says 27. Real in-tree drift; corrected. (Skeptic
independently flagged the same 25/37 in a commit message — immutable
post-land, noted here instead.)

## Deferred / accepted-as-is, with reasons

- **Architect LOW + Minimalist (convergent): the
  `loads_clean(store_text(...))` + dict-check + warn-degrade idiom is
  duplicated 8× in loop_report.py** — past CODING_NOTES'
  4-wants-extraction threshold, and `jsonl_utils` has no single-object
  analog of `read_jsonl_announced`. Real, deliberate defer: extraction
  touches 16 call sites + tests across the doctrine's consumers and
  belongs in its own chunk, not a post-review patch (don't-refactor-
  mid-feature). Filed in BACKLOG under the mutation-sweep item.
- **Expert QA LOW: `run_curation.surface_step_flags` reads the same
  `captains_log_slice.jsonl` with the exact strict-read +
  `except OSError` pattern this chunk fixed** in `_too_broad_events`.
  On the census already, and a torn slice surfaces as a curator failure
  entry rather than silence — but it is the same family-B defect on
  the same file, so it's named the next tier-3 stop (BACKLOG updated).
- **Skeptic LOW: `_step_artifact_names` silently returns `[]` on a
  torn call record** (out of the census scanner's stated scope).
  Accepted: the loss is step *attribution* only — the artifact itself
  stays visible on the Outcome panel by R2-6 design — and the calls
  table renders its own "unreadable record" row for the same file.
- **Skeptic LOW: record's "all reads route through…" overclaims** —
  several single-object reads degrade safely but aren't byte-*tolerant*
  (they refuse-and-announce, which is the design). The record's Fixes
  paragraph already distinguishes this; no edit.
- **Architect confirmed the labeled accepts** (finalizing-badge strict
  metadata read: blast radius one badge, self-correcting; NOW loss-note
  fixture: helper pinned via the loop report).

## Health signal

Convergence in one round, no HIGHs, and both accepted MEDIUMs were
*consistency* misses (a doctrine applied at one seam and not its
sibling; a lesson learned in one test and not reapplied one test down)
rather than unprobed claims — step 3 held. The Experimentalist seat
earned its standing trigger again: the one number nobody re-derived
was the one that drifted.
