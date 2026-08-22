---
status: record
name: edge-review-r3
description: Adversarial review r3 of the edge-review r2 fix commit (1bcbfad7) — CONTESTED trending to lows; sibling node-loader hardened, the 29/500 diagnosis receipted, the A/B gate honestly marked BLOCKED and queued for Jeremy.
---

# Adversarial review r3 — fix commit 1bcbfad7

Skeptic + Architect (Medium-size fix diff), sonnet-medium fallback as
r1/r2 (codex capped until 2026-08-27).

## Verdict: CONTESTED (SAME-MODEL FALLBACK: sonnet-medium)

No HIGH against the r2 fix layer itself — both r3 HIGHs are one hop out
(a sibling store; a prose-only number). The round is converging: r1/r2
HIGHs were in the new code, r3's are census/receipts findings. All
accepted findings fixed same session.

## Verification Ledger

**Skeptic HIGH — `load_knowledge_nodes` is the unhardened sibling —
VERIFIED (sibling-census class; anchoring loader itself out-of-range,
but the in-range work sits directly on its consumer).** A forged
`confidence: "high"` constructs fine and TypeErrors inside
`query_knowledge`'s `n.confidence >= min_confidence` filter; recall.py's
blanket swallow then blanks the ENTIRE knowledge block for every goal —
strictly worse blast radius than the edge-weight bug r2 fixed. Verified
by code read of the loader (no numeric validation) and the filter/scoring
lines. Census of the live store first: 0/1319 rows would be dropped by
the proposed guard — safe. **Fixed:** confidence (numeric, non-bool, in
[0,1]) and times_applied (numeric, non-bool, ≥0; `int(inf)` overflow
caught) validated at the loader boundary, skip-and-count through the
existing drift warning; fixtures + a one-forged-node-does-not-blank-recall
integration pin.

**Architect HIGH — the 29/500 query-level diagnosis was unreceipted —
VERIFIED.** The number appeared only in prose (BACKLOG, GOAL_BRAIN, r2
record); no committed code produced it — the same unreceipted-number
class r1/r2 were rejected for, one layer down. **Fixed:**
`--query-level` mode added to `scripts/replay_edge_expansion.py` (same
confidence filter, no char budget); re-run reproduces 29/500 (5.8%)
exactly.

**Architect MEDIUM — the A/B gate is structurally closed, not slow —
VERIFIED as a framing defect.** With the 600-char budget truncating
every entrant, the KNOWLEDGE_EDGE_EXPANSION denominator cannot accrue;
"pending" misdescribes a gate that cannot resolve without an
out-of-scope change. **Fixed:** BACKLOG item re-marked **BLOCKED** with
the three unblock paths named (budget decision / pool strengthening /
proxy metric), and a READING_QUEUE row queues the render-budget decision
for Jeremy.

**Skeptic MEDIUM — store_roundtrip.py `drops=""` now false for both
knowledge stores — VERIFIED.** The loaders now drop by design; the
diagnostic would have misreported the first real validation drop as a
loader bug. **Fixed:** both entries document their by-design drops,
matching the flat-lessons convention.

**Skeptic MEDIUM — the char-budget known-gap pin couldn't distinguish
"boost computed then truncated" from "boost never computed" — VERIFIED**
(asserted-the-object-not-the-flow). **Fixed:** intermediate query-level
assertion (sib present + `via_edge_from` stamped) before the render
assertions.

**Architect MEDIUM — no pin at the literal production budget —
VERIFIED.** **Fixed:** `TestProductionBudget` seeds ordinary-length
(~200-char-description) entries, proves the entrant enters the query
top-5 stamped, and asserts it never renders at `max_chars=600` (no
`[linked]`, no event) — the 0/500 mechanism as an executable pin.

**Architect LOW — inf fixtures ride stdlib-json `allow_nan` —
CONFIRMED-SAFE.** `_read_store` → `jsonl_utils.read_jsonl_announced` →
stdlib `json` with default parsing, so the fixtures exercise the real
ingestion path; noted in the test.

**Architect LOW — NaN missing from the new range-guard test's own
tuple — fixed** (added).

**Skeptic LOW — `relation` unvalidated — accepted with a comment:**
every consumer compares it against string constants, so junk is inert by
construction; the loader now says so explicitly.

**Skeptic LOW — lock-block "short" comment will age — no change:** the
deferral trigger (store 10×) is already recorded in BACKLOG from r1.

## Post-fix receipts

```
replayed 500 recent goals — production layer (min_confidence=0.3, max_chars=600); read-only
expansion changed membership on 0/500 recalls (0.0%)

replayed 500 recent goals — query-level (min_confidence=0.3, NO char budget); read-only
expansion changed membership on 29/500 recalls (5.8%)
```

Live-store safety checks before landing the guards: 0/2554 edge rows and
0/1319 node rows dropped by the new validation.
