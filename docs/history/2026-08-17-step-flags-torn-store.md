---
status: record
---

# 2026-08-17 — step-flags curation lane: torn-store honesty (tier-3, stop 2)

Follow-on named by the loop_report chunk's adversarial r1 (Expert QA):
`run_curation.surface_step_flags` strict-reads the **same**
`captains_log_slice.jsonl` the loop_report chunk had just made
byte-tolerant. Probe-first per HOUSE_STYLE step 3; all three probes ran
against an isolated `MARO_WORKSPACE` with the resolved `runs_root`
asserted before any write (2026-08-16 live-ledger rule).

## Probes (all three confirmed live before any edit)

1. **Curator crash / silent skip.** One crash-torn byte →
   `read_text` raised `UnicodeDecodeError` past `except OSError`. In
   the curation pipeline the curator lands in `outcome["failed"]`
   (announced-ish); in the `refresh_step_flags` backfill lane the outer
   `except Exception: continue` swallowed it silently. Either way,
   flags recoverable from the healthy lines were lost.
2. **DESTRUCTIVE — the worst find of the day.** `refresh_step_flags._merge`
   parsed a torn `run_card.json` as `{}` and rewrote the ENTIRE card as
   a step_flags-only stub: a 51-byte curated card (`status: success`,
   `goal_achieved: true`) became a 193-byte stub containing nothing but
   the flags. A maintenance backfill erasing the curation verdict it
   exists to annotate — and `refresh_step_flags(None)` walks **every
   run**, so one pass over a store with one torn card meant one
   destroyed card per pass.
3. **Launder.** A byte-tainted but structurally VALID card parsed under
   plain `json.loads` (locked_rmw's surrogateescape decode carries the
   taint as surrogates) and re-dumped as clean `\udcXX` escapes —
   corruption persisting as legitimate content.

## Fixes (this commit)

- `surface_step_flags`: `read_jsonl_tail_counted` + lower-bound
  WARNING; the card's `step_flags` gains a `slice_loss` note, and a
  lossy read **always writes the key** — the docstring's "absent =
  nothing fired" contract is only truthful when every line read clean.
  (One existing fixture expectation updated: its `not json` line is
  announced loss now, not silent tolerance.)
- `_merge`: `loads_clean` + isinstance guard; unreadable/tainted cards
  are **left byte-identical** with a WARNING (preserve-don't-destroy,
  same doctrine as rules/background). `loads_clean` refuses the
  launder shape by construction.
- Census: `surface_step_flags` entry cleared — live count
  **94 unreviewed sites / 86 functions + 12 reviewed**.

## Sweep — `tests/mutation/step_flags_truth.json`, 8 mutations

8/8 DETECTED first pass. Honest label: **co-written with its fixes**
(the weak green) — its value is as a ratchet against reverts, not as a
measurement of pre-existing coverage. Anchors follow the
substring-collision lesson from the loop_report sweep (the two
`if skip:` sites at different depths are disambiguated by trailing
context).

## Uncovered, recorded not hidden

- `refresh_step_flags`'s outer `except Exception: continue` around the
  curator call still swallows non-decode errors silently (census scope
  says it's not counted there); after this fix the torn-slice path no
  longer reaches it.
- The `slice_loss` note reaches the card but no HTML surface renders it
  yet; loop_report's index reads only `too_broad` length. Queryable
  (`jq`-able) today, visible later if the flags panel grows.
