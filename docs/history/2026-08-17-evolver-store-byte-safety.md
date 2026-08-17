---
status: record
---

# evolver_store byte-safety — the suggestions ledger (2026-08-17)

Fourth and last named surface of silent-drop slice 3 (after the tier-2
census, memory_ledger, and knowledge_web). Eight census sites, all
three bug families present, one revived old bug.

## The probes (all live, pre-fix, MARO_WORKSPACE=tmp)

One crash-torn byte (`\xff` mid-line) appended to `suggestions.jsonl`:

- `load_suggestions()` → `[]` — the healthy rows vanished (family A:
  strict whole-file/whole-scan read swallowed by a broad except).
- `get_suggestion("S1")` → `None`. This is the sharp one: the V2
  auto-revert authority guard re-reads exactly this row **just before
  an irreversible revert** so the decision isn't made off a stale
  snapshot. A torn byte turned "re-confirm authority" into "row
  absent".
- `suggestion_is_applied` / `apply_suggestion` / `revert_suggestion` →
  raised `UnicodeDecodeError` straight into callers (family B:
  unguarded strict read).
- `_save_suggestions`' dedup scan went blind, so re-saving an identical
  suggestion appended a second copy — **the 81-duplicate calibration
  bug this dedup was built to end, resurrected by one byte** (probed:
  2 copies after one torn byte + one re-save).

## The fix

Same treatment as memory_ledger/knowledge_web, via the shared
`jsonl_utils` helpers:

- Six loaders converted to announced reads (`_rows_as` /
  `_read_store`): healthy rows survive a torn line, the loss is
  WARNING'd with the store name, corruption counted apart from schema
  drift. `get_suggestion` additionally warns when a row is JSON but
  not loadable as a Suggestion and treats it as absent.
- The three keyed rewrites — `apply_suggestion`'s `_merge`,
  `revert_suggestion`'s `_drop_constraint` and `_mark_reverted` — parse
  each line with `loads_clean`, which refuses byte-tainted lines. A
  tainted line therefore never key-matches and falls into the
  preserve branch, re-emitted verbatim (family C). `_mark_reverted` is
  the launder exposure: it re-dumps **every** row it parses, so under
  plain `json.loads` a tainted-but-valid row would come back as clean
  `\udcff` escapes and the corruption signal would be erased forever.

No structural change to the rewrite semantics: these were already
preserve-what-you-can't-parse merges under `locked_rmw` (which is why
the two census entries are REVIEWED-with-reason rather than converted —
the silence is correct once the parser refuses taint).

## Receipts

- `tests/test_evolver_store.py` — 8 tests, all three families, written
  against the live probes before the spec.
- `tests/mutation/evolver_store_preserve.json` — 12 file-derived
  must-detect mutations, anchors verified to bind exactly once,
  **12/12 first pass**. One mutant was caught *before* the sweep by
  dry-reading the spec against the tests: the `_mark_reverted` launder
  mutant survives any fixture whose only bad line is torn (both parsers
  reject it), so the revert fixture gained a tainted-but-VALID
  bystander row first.
- Census: 8 `evolver_store.py` sites cleared — 6 removed, 2 moved to
  REVIEWED under `_SUGGESTION_STAMPER`. Baseline now 95 unreviewed +
  10 reviewed.
- Full suite 9442 passed, 1 expected skip.

## Lesson

Mutation specs can be dry-run against the tests before the sweep: for
each mutant, ask "which single assertion dies?" If the answer needs a
fixture shape no test has (here: tainted-but-valid, not just torn),
the sweep would have found it — but finding it on paper costs one
minute and no round trip. The distinction that mattered: **torn lines
test the read path; tainted-but-valid lines test the launder path.**
A fixture with only torn bytes cannot falsify a launder guard.
