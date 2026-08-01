---
status: record
---

# Adversarial review — pack provenance-transport fix (4d9f995)

Post-land cross-model review of the 2026-08-01 pack transport health
pass (commit 4d9f995: provenance-gate stamp survives the maro-pack
border). Two Codex lenses (Skeptic + Architect — medium change, trust
boundary), prompts carrying intent + lens + brain principles
(boundary-discipline, make-operations-idempotent,
verification-before-completion, fix-root-causes) + the full diff.
`reviewer_cli=codex`; outputs at /tmp/adversarial-review.Eqyc8W/
(box-local).

## Intent

Make the lesson-provenance quarantine (`minted_from="prompt"`) survive
export→import so transport cannot launder a quarantined lesson into an
injectable one on the receiving box.

## Verdict: PASS with fixes

The laundering hole itself was closed correctly; the reviewers found the
border still trusted the stamp's *value* and two robustness regressions.
6 unique findings (2 lens-duplicates deduped), **6/6 verified real
against the tree, 0 hallucinated** — the clean streak holds. 4 fixed
same-session, 2 accepted as named residuals.

## Findings

1. **[high, FIXED]** Noncanonical/untrusted `minted_from` values passed
   through verbatim (`"Prompt "`, `"outcome"` claimed by a foreign
   exporter, garbage) — the retrieval quarantine matches the exact
   string `"prompt"`, so a carried noncanonical value silently passed
   every injection surface. Both lenses, independently. Fix: stamp
   normalized to the known enum; local Tier-0 classifier now runs on
   EVERY row; conservative union (either side saying "prompt"
   quarantines). Lens: Skeptic #1 + Architect #1. Principle:
   boundary-discipline.
2. **[high, RESIDUAL — accepted, grounded]** Re-import cannot repair
   rows already laundered by the pre-fix importer (`skipped_identical`
   fires before stamping). Grounded against reality: imports.jsonl
   shows the only real imports ever run are 2026-07-09
   workspace_import machine-merges predating maro-pack — no box holds
   laundered rows. Manual repair path exists
   (`set_lesson_minted_from`). Documented in the design-doc addendum
   rather than building speculative reconciliation machinery. Lens:
   Skeptic #2.
3. **[medium, FIXED]** Untyped pack JSON (non-string `source_goal`)
   made the classifier raise `TypeError`, caught into a silent clean
   import — verified live. Fix: inputs coerced to `str` before
   classification. Lens: Skeptic #3.
4. **[medium, FIXED]** `_quarantined_row` crashed the whole export on
   valid-JSON non-object rows (`null`, `[]`) — only `JSONDecodeError`
   was caught; a regression vs the pre-change scrubber, and another
   instance of DEV_PATTERNS judgement check #7 (missing != empty !=
   value; non-dict payloads). Verified live. Fix: `isinstance(obj,
   dict)` guard; non-object rows ship scrubbed as before. Lens:
   Skeptic #4.
5. **[low/medium, FIXED]** All-rows-quarantined emptied the artifact
   AND its `quarantined_rows_skipped` count (early return) — a
   reviewer couldn't tell "no lessons existed" from "all withheld".
   Fix: zero-row manifest entry with the count. Lens: Skeptic #5 +
   Architect #3.
6. **[high per reviewer, RESIDUAL — accepted]** Stores keep
   `source_goal[:120]` while mint-time classification saw the full
   goal, so a pre-gate row whose only signal is the scaffolding-echo
   rule can arrive unclassified — evidence destroyed at origin, not
   fixable at the border. The reviewer's "quarantine all unknown"
   proposal rejected as over-broad (it would quarantine every pre-gate
   corpus wholesale — the live 145-lesson pack would arrive useless).
   Two of three classifier signals fire on lesson text alone.
   Documented residual. Lens: Architect #2.

## What Went Well

- The core two-halves design (export filters + import re-classifies)
  survived both lenses — neither found a path around it, only around
  the stamp's *value* handling.
- Per-row fault isolation, the audit trail, and the trust-demotion
  frame were called out as correct and consistent with §3.
- Test harness (`_add_artifact` re-seal fixture) let every attack
  scenario be pinned without weakening seal checks.

## Lead Judgment

Accept 1, 3, 4, 5 (fixed + pinned: 5 new tests in
TestProvenanceTransport). Accept 2 and 6 as documented residuals — both
are real but their proposed fixes build machinery for boxes/evidence
that don't exist; the design doc now names them with triggers (a real
pre-fix import surfacing → manual sweep; scaffolding-echo-only pre-gate
rows → accepted, decay handles). The finding-2 grounding (imports.jsonl
empirical check) is the difference between "accepted residual" and
"unverified hope".
