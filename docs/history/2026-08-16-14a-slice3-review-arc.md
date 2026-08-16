---
status: record
---

# §14a slice 3 review arc — r1 through r4

Point-in-time record of the cross-model adversarial review that followed
§14a slice 3 (`419104a`, `91c4d29`). Written at r4 because the arc ran
long and three of its findings were self-inflicted — a fix round
introducing the next round's bug, twice — which is the part worth
keeping.

**Commits:** slice `419104a` + `91c4d29` → r1 `c90b3a6` → r2 `6a5ffdb`
→ r3 `38dd3d7` → r4 (this round).

## The shape of it

| Round | Lenses | Fixes | What it was mostly about |
|---|---|---|---|
| r1 | 3 | 6 | Cross-slot recovery, exemplar anchoring, census honesty branches |
| r2 | 4 | 8 | Forged store rows: type-screening, malformed reporting |
| r3 | 4 | 19 | **r2's own fix crashed the mint path**; two pre-existing store bugs; the coverage instrument |
| r4 | 4 | 14 | **r3's own fix made rows immortal**; the imported gate; the false 290-row claim |
| r4b | — | 9 | Broad-sweep coverage gaps; tests only, no production change |
| r5 | 1 | 10 | **The decree's own tripwire didn't cover the ranker**; three more vocabulary mirrors |

The rounds did not converge to QUIET. They converged to *smaller blast
radius*: r1–r2 were about the feature, r3–r4 were about the store the
feature writes to, and the r4 findings are all latent rather than live.

## The two self-inflicted regressions

Both had the same shape: **a fix that was correct about the problem and
wrong about the blast radius.**

1. **r2 → r3.** r2 hardened the census reader with `_clean_scope`
   (type-screen before vocabulary, because `["world"] in frozenset`
   raises). In the same commit it wrote a *bare* `row.scope not in
   _LESSON_SCOPES` on the reinforce path — the exact pattern the helper
   existed to prevent, one file over. Result: one corrupt row
   permanently blackholed every future mint that dedup-matched its text,
   with no log line, because both mint call sites swallow the exception
   as a bare counter bump. Pre-r2, that case was benign.
   *Root cause was a seam:* `knowledge_web` owned the vocabulary,
   `camera_readout` owned the only type-safe reader of it, and three
   sites hand-rolled the check. Fixed by hoisting `coerce_scope` next to
   `_LESSON_SCOPES` and routing every site through it.

2. **r3 → r4.** r3 stopped rewrites from silently deleting unparseable
   store rows — correct, and required by the store's own "decay trust,
   never data" decree. It did so by carrying those rows back into the
   live file, which made them **immortal**: no code path could remove
   one, not `forget_lesson`, not GC, not a `lambda L: []` mutate. Worse,
   a pre-schema shadow row sharing a `lesson_id` with a forgotten lesson
   would come back to life the moment a future version could parse it —
   inverting `forget_lesson`'s "forgetting is final".
   *Fix:* move them OUT to a `lessons.jsonl.unparseable` sidecar, which
   is the same verb `_archive_lessons` already used. Preservation was
   never the hard part; **where** to preserve was.

## Pre-existing bugs the arc surfaced

Neither is about scope. Both were found by walking the blast radius of a
scope fix, and both are the same pattern — *the reader hides the
corruption and the writer dies on it*:

- `load_tiered_lessons` sorted by score **outside** its per-row guard, so
  one row with `"score": "high"` raised on every raw load — and every
  read-modify-write loads raw, so reinforce, promote, GC, tombstone,
  canon and pack variant-union wedged at once (24 call sites). The
  ordinary read path silently dropped that row, so nothing pointed at it.
- `sessions_validated` had the identical wedge in `run_decay_cycle`'s
  promotion loop, with no enclosing try, and that row *parses cleanly* —
  so quarantine would never have caught it. Fixed generically off the
  dataclass annotations (`_coerce_int_fields`) rather than field by field.

## r5: the decree was guarded by two tests that could not fail

The r4 test-auditor's report arrived after r4/r4b had landed, written
against r3. Most of it was already closed; six items were not, and one
was the worst finding of the arc:

**`scope` is not a ranking input — and nothing could have told us
otherwise.** The decree had two guards, and a live
`sim *= 1.25 if lesson.scope == "method"` dropped into
`knowledge_web._tfidf_rank_scored` — the function that actually ranks
lessons — sailed past both:

- The **grep tripwire** scanned `portability.py`, `knowledge_lens.py`
  and `recall.py`. The ranker is in `knowledge_web.py`, which was not on
  the list and cannot simply be added: that module owns the vocabulary,
  so every line mentioning `scope` would be an offender. It now parses
  the file with `ast` and scans the ranking functions **by name**.
- The **behavioral pin** ranked two lessons with *different text* and
  then explained the score difference away in a comment. A test that
  explains away the only signal it could observe is not a pin. Replaced
  with an equality: the same lesson text, recorded three times into three
  workspaces as `method` / `world` / unstamped, must produce one score.

Both verified by mutation. The rest of r5 was the same vocabulary-mirror
problem one more time (the printed bucket table was a *third* hard-coded
`("method", "world", …)` after r4 fixed two), plus four unpinned lines:
load-time `float(tl.confidence)` (the string-score wedge's sibling, and
`"0.9"` parses so quarantine never sees it), quarantine on the
`_rewrite_tiered_lessons` path, and the printed `malformed` / `newest` /
`imported=` fields, all of which could be replaced by constants with a
green suite.

Also fixed: `test_an_unreadable_store_is_named_not_swallowed` passed with
the error text replaced by a constant, because its two split assertions
were both satisfied by an unrelated WARNING printed in the same call —
the exact defect that had been fixed 120 lines above it and not applied
here. One joined assertion now. And `TestStampCoverage._write` built
`recorded_at` as `2026-08-1{i}`, which yields `2026-08-110T…` at i≥10 and
had quietly blocked any fixture past ten rows — including r4b's 60-row
paging test, which was therefore asserting over malformed timestamps.

**What r5 says about the arc:** the rounds stopped finding bugs in the
feature at r3. r4 found bugs in the store, r4b and r5 found bugs in the
*tests* — and the r5 items are the most valuable of the three, because a
guard that cannot fail is worse than no guard: it is a standing claim
that the decree is enforced.

## Claims that did not survive

Recorded because the house rule is to demand named falsifiers, and these
were mine:

- **"0 malformed on the live store" (r2).** True only among the truthy
  shapes: `or ""` upstream flattened `False`/`0`/`[]`/`{}` before the
  screen ran.
- **"A real backup file has 290 rows that all fail today's dataclass, so
  one mint would have erased them" (r3).** Wrong. That file is a
  snapshot of the FLAT store, key-for-key identical to today's live flat
  store; it fails `TieredLesson` because it is a *different dataclass*,
  not because of drift, and the tiered lane never reads or rewrites it.
  The fix is still right — but on decree grounds, not on that evidence.
  The honest supporting fact is narrower and better: the flat store has
  preserved unparseable lines for ages (`memory_ledger`), and the tiered
  store did not.
- **"r2's live-fire verified the slice."** It verified the *reader*. The
  writer had never fired in production — which is exactly why r3 added
  the store-wide coverage line, and why it read 0/195.

## Method notes

- **Mutation testing earned its place.** r2 claimed 8/8 detected; 4 of
  the first 7 were NOT detected on the first pass and the tests had to be
  strengthened. r3's audit then showed one of r2's "8 mutation-tested
  fixes" had no test at all. r4's first pass was 6 detected of 15.
  Assume the harness is lying until each mutation has actually been run.
- **The r4 misses were all the same failure: a test that passes for a
  reason other than the one in its docstring.** Not missing tests —
  *vacuous* ones, each written alongside its fix and each looking right.
  - `test_an_uncited_imported_row_does_not_void_the_verdict` cited its
    imported row **zero** times, so the census built no row for it and
    the gate under test was never reached. Fixed by adding a row cited
    four times from runs carrying no verdict — present in the bucket,
    contributing nothing to the pooled figure, which is the case the
    `imported_fv` gate actually exists for.
  - `test_an_unreadable_store_reports_an_error` chmod-000'd the file, so
    the *line-count* read raised and the outer `except` set `error`
    before the loader-disagrees branch was consulted. The branch needs
    bytes that read fine and a loader that returns `[]`.
  - Both quarantine tests drove the helper through `record_tiered_lesson`,
    where the rewrite that *follows* quarantine rebuilds the live file
    from the parsed list anyway — masking both "never shrinks" and
    "sidecar opened `w`". The helper's own contract needed a direct test.
  The pattern to watch: **a test whose subject is reached through a
  caller that would produce the same observable on its own.**
- **A broad sweep found what a targeted harness cannot.** The r4
  test-auditor ran ~12 mutations chosen by reading the code rather than
  by reading the fix list, and every one survived. They were not fix
  regressions — they were places the fixes had never been *aimed*: the
  coverage census scanned one tier of two and would silently page at
  the loader's default 50 (the live store is 195 rows); `stamped` summed
  a vocabulary that no fixture ever exercised past `method`; the
  `imported` flag was read off store and archive rows by two lines that
  every test monkeypatched away; two WARNINGs that are the only trace a
  corrupt row ever existed could be deleted silently; and pack's
  `lesson_type` screen kept its `isinstance` half but not its vocabulary
  half, because every junk fixture was a non-string. All nine surviving
  mutants that still had an r4 anchor are now must-detect. **The lesson
  is about harness design:** a mutation list derived from the diff tests
  whether the fixes are pinned; a list derived from the file tests
  whether the *behavior* is. Only the second one found these.
- **One mutation was equivalent and is recorded as such.** The coverage
  scope counter's `out.get(scope, 0) + 1` cannot fail today — the seed
  loop pre-creates every vocabulary key and `camera_readout` imports the
  vocabulary by name, so the two can only disagree under a monkeypatch
  that production cannot produce. Kept as defense against a future seam;
  not claimed as tested. Contorting a test to kill an equivalent mutant
  is how a suite starts testing its own mocks.
- **Reviewers must not mutate a shared checkout.** In r3, a lens doing
  in-tree mutation testing collided with two other live sessions and made
  a sibling lens's results unreliable. From r4 on, review prompts say:
  extract `git archive <sha>` into scratch and mutate there.
- **Verify-before-fix held at roughly its usual rate.** Findings that
  reproduced were acted on; several plausible-sounding ones did not
  survive a probe, and two rounds' worth of "checked and clean" lists are
  in the round reports.
- **Four lenses beat three.** The skeptic, test-auditor, Expert QA and
  design-coherence lenses each owned findings the others missed, and the
  two biggest items in r3 and r4 both came from Expert QA (sad paths /
  operational reality).
