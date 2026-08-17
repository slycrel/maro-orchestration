---
status: record
---

# Adversarial review — memory_ledger closure chunk (2026-08-17)

Round 1 on the landed range `a4bf9b10..f9dd28c7` (loader conversion to
`_read_store`/`_rows_as`, six REVIEWED stampers, qualified census keys,
`stamp_preserve.json`). **Lane note:** codex was usage-capped until
2026-08-19, so all five reviewers ran on the same-model sonnet-medium
fallback (`claude -p --model sonnet --effort medium`) — the sanctioned
degraded lane, disclosed per skill policy. Roster: Skeptic, Architect,
Minimalist, Expert QA (Large size), plus the Experimentalist (standing
trigger: the chunk ships a number).

## Verdict: REJECT round 1 → fixed same session → re-reviewed round 2

### The consensus HIGH (Architect + Expert QA + Experimentalist, confirmed by probe)

All six REVIEWED stampers read the whole store with
`path.read_text(encoding="utf-8")` — strict decode — guarded by
`except FileNotFoundError` / `except OSError`. `UnicodeDecodeError` is a
`ValueError`, so one non-UTF-8 byte anywhere in `outcomes.jsonl` made
every stamper raise. Probe: six stampers, six raises. Most call sites
swallow at `except Exception … log.debug`, so one crash-torn append
silently disabled verdict/lesson/supersede stamping on that store until
manual repair — a strictly worse instance of the silent-loss shape the
chunk existed to close, in the very functions the chunk marked REVIEWED.
The pinning tests shared the blind spot: `_TORN`/`_TORN_TAIL` were valid
UTF-8 (truncated JSON), so they exercised the failure the stampers
already handled and never the one they didn't.

**Fix (root cause, commit b061c324):** `locked_rmw` decodes and
`atomic_write` encodes with `surrogateescape`, so undecodable bytes
round-trip verbatim through every rmw in the tree (16 modules); the four
inline stamper reads route through `_store_text` with the same contract.
Probed after: zero raises, stamps land past raw torn bytes, bytes
preserved byte-identical. Fixture gains the third torn shape; three
revert mutants pin the layer.

### Other accepted findings

- **Skeptic (HIGH as filed, MEDIUM in effect):** the vacuity guard's
  `assert field in stamped` was itself vacuous for
  `annotate_outcome_lessons` — the seed row pre-carries `"lessons": []`.
  Fixed: assert the VALUE differs from the seed.
- **Skeptic + Architect (MEDIUM):** the census module key was the
  basename, so two same-named files in different packages shared one
  allowance — the function-half collision, one axis over. Fixed:
  relative-path keys + can-fail fixture + basename-fallback mutant. No
  live collision existed (verified).
- **Minimalist (MEDIUM):** family B had three members, not two —
  pre-fix `load_step_traces` had the identical `except OSError` shape.
  Docstring corrected; the loader itself was already fixed by the shared
  helper.
- **Minimalist + Expert QA (MEDIUM/LOW):** `stamp_preserve.json` covered
  the rebuild mutant on 4/6 stampers and vacuity on 1/6. Extended to
  23 mutations, 23/23.
- **Minimalist (LOW, direction adjusted):** `_rows_as`'s broad catch can
  relabel a code defect as "schema drift". Kept broad (one weird row
  must never abort the load — the module's doctrine) but the warning now
  names the exception class, so `KeyError` every row reads as what it is.

### Rejected

- **Architect (LOW):** `load_task_ledger` filtering after `_rows_as`
  means drift warnings fire for rows outside the queried loop_id.
  Rejected as by-design: the store is torn regardless of the query, and
  announcing that is the arc's thesis. Cost is negligible (same O(n)
  parse as before).
- **Skeptic (LOW):** "9/15 vs 16 mutations is inconsistent" — both
  numbers are right (a 16th was added between passes); README now says
  so explicitly.

### What went well

Every lens independently cleared the qualified-name walk itself
(`_child_scopes` / `visit` composition) — hand-traced against both
collision families, no evasion found beyond the basename axis. The
loader conversion and `SkipReport` plumbing drew zero correctness
findings. Reviewer precision held the 0-hallucination streak: every
code claim in every finding verified true against the tree (the one
inflated severity was an impact claim, not a code claim).

### Lessons

1. **A fixture family is the suite's vocabulary** — a failure shape
   missing from the fixtures is one the suite cannot speak about, no
   matter how adversarial the assertions over the shapes it has. (Third
   depth of the same lesson this chunk already hit twice.)
2. **"Safe by construction" claims have a construction site.** The
   stampers' rejoin really was safe — against the failure that happens
   after the read. The claim silently assumed the read.
3. The sonnet-medium fallback lane produced a REJECT-grade round: 3/5
   lenses converged on a probe-confirmed HIGH with zero hallucinated
   code claims. The lane is degraded, not decorative.

## Round 2 (same lane, 3 reviewers: Skeptic, Architect, Expert QA — pointed at the fix layer)

Both real findings survived verification; both fixed in `0b67dcaf`.

- **All three lenses — the surrogateescape encoder was scoped at the
  wrong altitude.** Making it `atomic_write`'s default converted "fail
  loud" into "fail silent" for the ~19 modules that call it directly
  with self-built content: a lone surrogate there is an upstream bug,
  and the strict encoder's UnicodeEncodeError at the write site was the
  only signal naming it. Fixed: strict default, explicit `errors=`
  opt-in; `locked_rmw` pairs internally (its read makes the surrogates,
  so its write must round-trip them); the four inline stamper writes
  opt in to match `_store_text`.
- **Architect + Expert QA (independent HIGHs) — the launder path.** A
  byte-torn line can be structurally valid JSON (`{"goal": "\udcff"}`
  parses — probed), so any rewrite that parses rows re-serializes the
  surrogate as a clean ASCII escape: the undecodable count that
  announced the corruption goes silent and garbage persists as
  legitimate content. A lie, not a loss. Fixed with `_loads_clean` — a
  json.loads that refuses surrogate-carrying lines by raising
  JSONDecodeError — applied at all 13 per-row scan/rewrite sites, so
  every existing unparseable branch preserves the raw line verbatim.
  The census gained `_PARSE_WRAPPERS` in the same diff; without it the
  wrapper would have removed all six REVIEWED sites from the census.
- **Rejected:** first-err ordering in `_rows_as` (first exception +
  class name is sufficient signal); the "target-row escape
  normalization" residual (mooted — tainted targets are now skipped,
  so no rewrite ever re-serializes one).

Spec 23 → 27 mutations, 27/27. Suite 9415 green. Round 3 not run: both
round-2 findings were about the round-1 fix, the round-2 fixes are
narrower than round 1's, and every new surface carries its own
must-detect mutants — the fixpoint criterion (converging to lows) is
met.

### Round-2 lesson

The round-1 fix created the round-2 bug in both cases: the encoder
default was collateral of fixing the read, and the launder path only
became REACHABLE once surrogateescape stopped the crash that used to
sit in front of it. **A fix that widens what survives the read widens
what downstream code must be honest about.** Watch-list probe #1 (
"attack the previous round's fixes first") is the whole reason round 2
was pointed there.
