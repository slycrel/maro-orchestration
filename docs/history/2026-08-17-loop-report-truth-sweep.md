---
status: record
---

# 2026-08-17 — loop_report torn-store honesty + truth-surface mutation sweep (tier-3 opener)

The file-derived mutation sweep's first **tier-3 (operator-facing
output)** surface, chosen as the double-payoff target: `loop_report.py`
is the run report + cross-run index an operator actually reads, and it
carried 6 unreviewed silent-drop census sites (the tier-2 tripwire's
opportunistic burn-down contract). Method: HOUSE_STYLE step 3 —
**probe live, fix, pin, then derive the mutation list from the file.**

## Probes (all six confirmed before any edit)

1. **Report-killer.** One crash-torn byte in
   `captains_log_slice.jsonl` → `_too_broad_events`' strict whole-file
   `read_text` raised `UnicodeDecodeError` past `except OSError` → the
   ENTIRE run report was never written (`write_run_report` returned
   None). The file that sits beside every report by design was the
   report's own single point of failure. Same family-B shape as the
   memory_ledger/knowledge_web finds, one subsystem over.
2. **Vanishing run.** A torn `metadata.json` made the run disappear
   from the cross-run index — indistinguishable from never having run,
   on the one page that claims to list every run.
3. **Wrong-source fallback.** A torn slice read as *missing*, silently
   rerouting Decision points / Run activity to the rotated ~1000-entry
   global tail.
4. **Curation erased.** A torn `run_card.json` silently removed the
   Outcome panel — a curated run rendered as not-yet-curated.
5. **Skills un-claimed.** One torn manifest line voided the whole
   Environment panel.
6. **Unclassifiable dir.** Backfill's NOW-detection swallowed an
   unreadable metadata.json without a word.

## Fixes (landed `5cf6f60f`)

All reads route through `jsonl_utils` (announced, byte-tolerant,
per-line) or `loads_clean(store_text(...))` for single-file JSON;
every degrade is operator-visible: a slice-loss integrity note on the
report page, a degraded index row with an explicit `unreadable` badge
and on-disk pointer (report links still resolve from `build/`), an
honest Outcome panel for a torn card, per-section honesty lines in
Environment (missing stays silent by design — drift-reads-as-absent
applies to *unreadable*, not *absent-by-age*), and WARNINGs at every
seam. Census: all 6 loop_report `UNREVIEWED_SILENT_DROPS` entries
cleared — live count now **95 unreviewed sites across 87 functions +
12 reviewed** (was 101 sites; the landing commit's "121 → 115" and the
BACKLOG's "92 unreviewed" were both stale snapshots — counted live via
import for this record).

**The chunk's own tests caught a second-order launder** before the
sweep did: `store_text` + plain `json.loads` *parses* byte-tainted
JSON (surrogates ride through as legitimate strings), and
`_atomic_write_text`'s strict encoder then crashed with `surrogates
not allowed` — recreating the report-killer one seam later, with the
taint laundered into the HTML on the way. All eight single-file JSON
reads use `loads_clean`, so taint fails the parse and takes the honest
degrade path. This is the `loads_clean` doctrine's first consumer
outside the store-rewrite lane.

## Sweep — `tests/mutation/loop_report_truth.json`, 37 mutations

**First pass 27/37**: 2 anchor SKIPs, 8 SURVIVED, 27 DETECTED. The
detected half was mostly the month-old surface (freeze sentinel ×3,
finalizing badge + refresh, running→interrupted coercion, R2-5/R2-6
reverts, debounce both directions, date-bound normalization, audience
overreach) — review-hardened features holding, consistent with the
arc's standing lesson.

**All 8 survivors were real holes, zero equivalents**, and they cluster
exactly where the tier-3 thesis said: operator-facing printed fields
and index plumbing nothing asserted —

- the **unreadable call record** row (a torn record could be silently
  skipped, making the calls table under-count paid calls);
- **mixed ended_ts** no longer forcing approximate mode (`any` → `all`
  survived — the only pinned fixture had ALL timestamps missing);
- **markers slotting into fabricated approximate windows**;
- **card-over-metadata status preference** on the index row;
- the **ledger-newest fallback href** (the sort-key negation was
  observable only on the reports[0] fallback path, which nothing
  exercised);
- **result-link `build/` containment** (a card `result_path` outside
  build/ became a row link);
- **cross-loop totals accumulation** (`+=` → `=` survived: every
  fixture had one loop);
- the **index run count**, replaceable by a constant.

Closed with 7 new tests + 1 assertion. Second pass 36/37: the re-anchored
launder mutants ran, and the Outcome-panel one SURVIVED — my only
torn-card fixture was structurally INVALID JSON, which fails either
parser, so the launder was unobservable (the tainted-but-valid-bystander
lesson from the evolver_store sweep, reproduced in my own fixture despite
having applied it to the metadata fixtures the same session). Added the
byte-tainted-but-valid card shape; final: **37/37 accounted for, 0
survivors, 0 equivalents.**

## Runner lesson worth keeping

**Anchors are substring matches, so a shorter-indented line is a
substring of the same line at deeper indentation.**
`"        card = loads_clean(store_text(card_path))"` (8-space) matched
both its intended site and a 16-space sibling, and the runner correctly
SKIPped. Re-anchor with a preceding line rather than trusting
indentation to disambiguate.

Also self-caught before the sweep: the new torn-metadata test asserted
the bare word `"unreadable"`, which the degraded row's *goal
placeholder* also contains — the two-guards-one-test shape inside a
freshly written test. Pinned `>unreadable</span>` (the badge)
specifically.

## Honest labeling

The byte-safety third of the spec is co-written with its fixes (the
weak green, per the README's `jsonl_utils` note); the truth-surface
two-thirds audits guards written 2026-07-08..08-15 by earlier sessions,
and its 8-survivor yield is a measurement. Uncovered, recorded not
hidden: the NOW template's loss-note line has no NOW-specific torn
fixture (the helper itself is pinned via the loop report), and
`_render_report_html`'s run-status read for the finalizing badge still
strict-reads metadata (torn → badge shows on a done loop; mild,
self-correcting on the post-curation re-render).
