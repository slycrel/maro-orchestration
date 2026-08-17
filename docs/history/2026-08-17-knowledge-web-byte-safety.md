---
status: record
---

# knowledge_web byte-safety — the tier-destruction fix (2026-08-17)

Third surface of the silent-drop arc (after the tier-2 census and the
memory_ledger stamper chunk). Same three bug families, but this module
held the worst instance found anywhere in the arc.

## The headline probe

One crash-torn byte (a single `\xff` from an interrupted append) in
`medium/lessons.jsonl`, then any reinforcement / GC / promotion /
refight — all of which run through `_mutate_tiered_lessons`:

```
before: 573 bytes, 4 lines (3 healthy lessons + 1 torn)
after identity mutate: 0 bytes, 0 lines
sidecar exists: False
```

**The entire lesson tier was destroyed, silently, with no sidecar.**
Chain: `load_tiered_lessons`' strict whole-file `read_text` raised
`UnicodeDecodeError`, the outer `except Exception: pass` swallowed it
(the read already returned 0 of 3 healthy rows — family A), and
`_quarantine_unparseable` was blind the same way (`except Exception:
return 0`), so `_mutate_tiered_lessons` rebuilt the store from an empty
list and `atomic_write` made the wipe durable. The quarantine-sidecar
machinery built in the r3/r4 scope arc never got a chance to run —
it only handled rows that *parse* wrong, not bytes that *decode* wrong.

Consolidation runs at most daily on every `handle()` exit and heartbeat
tick, so in production the window from torn byte to wiped tier was at
most a day.

Also probed before fixing (same fixture):

- `load_knowledge_nodes` and `_bump_node_times_applied` **raised
  UnicodeDecodeError into their callers** (family B — a ValueError,
  invisible to the `except OSError` guards).
- `_load_archived_lessons`, `_remint_watch_stamp`, `_load_canon_stats`
  returned empty/None silently (family A) — the remint case silently
  erases the Δ-gate strike lineage.

## The fix

The four helpers proven in memory_ledger earlier the same day —
announced read, drift-counting row builder, surrogateescape whole-store
text, taint-refusing parse — were generalized into `jsonl_utils` as
`read_jsonl_announced` / `read_rows_as` / `store_text` / `loads_clean`;
memory_ledger imports them under its original private names (tests and
call sites unchanged; seven `stamp_preserve.json` mutations re-filed to
`src/jsonl_utils.py`, re-run 27/27).

knowledge_web conversions:

- **Pure loaders** (`load_tiered_lessons`, `_load_archived_lessons`,
  `_remint_watch_stamp`, `_load_canon_stats`, `load_knowledge_nodes`,
  `load_knowledge_edges`): byte-level announced reads; one torn byte
  costs one row and is named in a WARNING with the store path; schema
  drift counted separately from corruption.
- **`_quarantine_unparseable`**: byte-safe round trip (`store_text` +
  `loads_clean`), sidecar append and live rewrite both opt into
  surrogateescape, so a torn line lands in the sidecar as its ORIGINAL
  BYTES. It now returns the lines it could NOT move (sidecar write
  failure), and both rewrite paths append them verbatim to whatever
  they rebuild — the failure log line already promised "leaving them in
  place rather than risk losing them", but the callers deleted them
  anyway. An unreadable store (OSError) now aborts the rewrite instead
  of proceeding to an empty write.
- **Rewrite paths** (`_bump_node_times_applied`,
  `promote_knowledge_candidates`): byte-safe read + `loads_clean` so a
  tainted-but-structurally-valid row is never laundered by the
  `json.dumps` re-serialize, surrogateescape writes. Both are REVIEWED
  in the census (`_NODE_STAMPER` reason) and pinned by
  `TestNodeRewritesPreserveWhatTheyCannotParse`.

Census: 8 knowledge_web entries cleared from `UNREVIEWED_SILENT_DROPS`
(121 → 113 unreviewed, 8 reviewed), `loads_clean` added to
`_PARSE_WRAPPERS`.

## Verification

- 14 new tests (`TestTheTierSurvivesATornByte`,
  `TestNodeRewritesPreserveWhatTheyCannotParse`,
  `TestTheKnowledgeLoadersAnnounceTheirLoss`), including the
  destruction repro, the launder pin, the stranded-rows fallback, and
  the unreadable-store abort.
- `tests/mutation/knowledge_web_preserve.json`: 18 file-derived
  must-detect mutations, 18/18 DETECTED first pass. Anchors verified to
  bind exactly once programmatically before the sweep.
- `stamp_preserve.json` re-run after the helper move: 27/27.
- Full suite: 9428 passed, 1 expected skip (one pre-existing test in
  `test_lesson_scope.py` updated to the new `_quarantine_unparseable`
  return contract — stranded lines, not a count).

## Adversarial rounds (same-day, sonnet-medium fallback lane — codex capped)

**Round 1** (5 lenses: Skeptic, Architect, Minimalist, Expert QA,
Experimentalist on `511a87d7..a0b60a4a`): **REJECT.** All five returned
clean; zero hallucinated findings. Accepted four:

1. (4-lens consensus HIGH) `_quarantine_unparseable` held the sidecar
   append and live shrink in one `try/except` — append-success +
   shrink-failure returned already-durable rows as "stranded", the
   caller carried them back into the live file, and every later pass
   re-appended them to the append-only sidecar, unboundedly. Fixed:
   multiset-diff idempotent append (`Counter`, so two identical torn
   live lines stay two records), split try blocks, and "stranded"
   strictly meaning "the sidecar does NOT hold it" — a failed shrink
   after a durable append returns `[]` and the caller's rebuild does
   the shrink. Documented trade: byte-identical corruption recurring
   after a COMPLETED quarantine of its twin shrinks without a second
   sidecar copy.
2. (Skeptic HIGH) `unparseable_sidecar_count` strict-read the sidecar —
   which by design holds raw bytes — reporting **0 exactly when
   quarantine fired**. Byte-safe now.
3. (QA HIGH) `load_tiered_lessons` swallowed OSError to `[]` on the
   rmw path — empty rebuild, the destruction shape one function
   downstream. Aborts now.
4. (Experimentalist MEDIUM, undercounted) `scope_14a.json` had stale
   anchors against the rewritten code — reviewer found 4, re-check
   found **6**; all re-filed, sweep re-run 35/35.

Rejected: converting node/edge loaders onto `read_rows_as`
(dict-default vs dataclass-default filter semantics — a behavior risk
for zero functional gain).

The initial sweep of the fixes surfaced its own gap: the dedup-removal
mutant SURVIVED because the first partial-failure test never re-runs
quarantine over a still-dirty live file. A harsher both-writes-fail
convergence test killed it — the sweep auditing its own killer tests.

**Round 2** (3 lenses on the fix layer): two accepted.

1. (Skeptic+Architect; QA explicitly dissented — the dissent was chased
   and the majority was right) The `raw=True` OSError raise overloaded
   two meanings: `raw` means "skip decay math" and five pure-read/append
   callers pass it (`contested_lessons`, `resurrect_archived_lesson`,
   decay-cycle LONG reads, the delta_replay CLI chain, CLI refight) —
   all inherited crash semantics they never asked for. The abort now
   rides its own `for_rewrite` flag, passed only by the two locked
   rebuild paths.
2. (3-lens consensus) The new sidecar dedup read caught only
   `FileNotFoundError`; any other OSError crashed the lock-held rewrite
   unclassified. Now degrades the dedup, not the quarantine (warn +
   empty diff; worst case one duplicate sidecar row, the documented
   trade).

**Round 3 declined, reasons on the record:** both round-2 findings were
about round-1 fixes, and the round-2 fixes are strictly narrower — the
`for_rewrite` flag *restores* the pre-chunk degradation for pure readers
(no new semantics anywhere), and the OSError catch is a pure widening
with a warning. Both are pinned by tests and mutants (spec at 23/23).

Final state: suite 9434 passed / 1 expected skip; sweeps 23/23 (kw),
35/35 (scope_14a), 27/27 (stamp_preserve).

## Lesson

A safety mechanism written against one corruption model (rows that
parse wrong) can itself be the destruction path under the neighboring
model (bytes that decode wrong): the quarantine's own blind read is
what turned one torn byte into a wiped tier. When adding a guard,
probe it under every failure shape the store can actually take — the
guard is just more code that reads the file.
