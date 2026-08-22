---
status: record
name: edge-review-r2
description: Adversarial review r2 of the edge-review fix commit (1749b79c) — REJECT again (receipt measured the wrong layer; loader left endpoint ids unvalidated); fixed same session, and the corrected production-layer replay reads 0/500 — expansion is currently rendered-inert under the 600-char budget.
---

# Adversarial review r2 — fix commit 1749b79c

Same four sonnet-medium lenses as r1 (codex capped until 2026-08-27;
same-model fallback per the skill).

## Verdict: REJECT (SAME-MODEL FALLBACK: sonnet-medium)

Two VERIFIED HIGHs in the r1 fix layer — exactly where the watch-list
says round-2 HIGHs live. Fixed same session.

## Verification Ledger

**Consensus HIGH (Skeptic/Architect/Minimalist; QA as MEDIUM) — the
"corrected" 2/120 receipt measured the wrong layer — VERIFIED.**
`scripts/replay_edge_expansion.py` compared raw
`query_knowledge(expand_edges=...)` sets at `min_confidence=0.0` with no
char budget. The literal production path (`recall.py:865`) is
`inject_knowledge_for_goal(goal, max_chars=600)` →
`query_knowledge(..., min_confidence=0.3)` → char-budget truncation, and
`KNOWLEDGE_EDGE_EXPANSION` fires only on nodes that survive the
truncation. Verified by direct read of both call sites. The receipt was
not a receipt for the surface it was cited to justify.

**Expert QA HIGH — loader validates `weight` but not
`source_id`/`target_id` — VERIFIED.** A `null`/non-string endpoint id
constructed fine and then raised
`TypeError: '<' not supported between instances of 'NoneType' and 'str'`
inside `tuple(sorted((e.source_id, e.target_id)))` in the writer's
snapshot — silently aborting every maintenance sweep at `log.debug`
(with the drift WARNING never firing, since the row loads "cleanly") and
crashing the CLI with a raw traceback. Reproduced the `sorted()`
TypeError directly. Same wedge class the round claimed to close.

**Three-lens MEDIUM — inf / out-of-range weight passes the NaN-only
guard — VERIFIED.** `float("inf") != float("inf")` is False, so `"inf"`
loaded as a legitimate edge: it permanently freezes the max-wins
idempotency for its pair (nothing exceeds `inf`) and makes
`candidate = seed × inf × 0.5` dominate every boost comparison, with
zero drift signal. Reproduced the float behaviour directly.

## Fixes applied

- **Loader boundary now validates the whole row** — endpoint ids must be
  non-empty strings; weight must be numeric, non-bool, and within
  `[0.0, 1.0]` (a bound check is False for NaN and ±inf, so one guard
  covers all non-finite shapes). Everything else is skip-and-counted
  through the existing drift warning. Fixtures: null/list/empty ids,
  ±inf, 1e9, −50, 1.5, bools; writer survival with a null-id row.
- **`_render_knowledge_entries` extracted** — the pure render decision
  (char budget, markers, `expanded_rendered`) is now one function shared
  by `inject_knowledge_for_goal` and the replay script, so the receipt
  measures the literal layer that fires the event, drift-proof by
  construction.
- **Replay script replays the production shape** —
  `query_knowledge(max_results=5, min_confidence=0.3)` + the shared
  render helper at `max_chars=600`, comparing RENDERED id sets.
- **Char-budget known-gap pin** — a boosted entrant that changes
  query-level membership but is truncated by the budget renders no
  `[linked]` and fires no event. The undercount is the safe direction
  for the denominator (events only claim renders that happened); the pin
  documents the accepted residual per the known-gap convention.
- **Stats built inside the lock span; dry-run lock behaviour
  commented** (Skeptic L3, three-lens LOW).

## Rejected / no-change

- **Flag junk-value warning (Minimalist L4)** — `_edge_expansion_enabled`
  runs per query; a per-call WARNING for a junk config value is log
  spam. Strictness is pinned; declined.
- **Receipt goal-id truncation (Skeptic L4)** — cosmetic, flagged by the
  lens itself as not-for-fixing.

## The corrected readout — and what it actually says

```
replayed 500 recent goals (rendered-set comparison at the production
layer: min_confidence=0.3, max_chars=600; read-only)
expansion changed rendered membership on 0/500 recalls (0.0%)
```

Diagnosis (isolating the constraints): at `min_confidence=0.3` with no
char budget, query-level membership changes on **29/500 (5.8%)** — the
600-char render budget then truncates every one of them before render.
The damped boost (`≤ 0.45 × seed`) sorts entrants toward the back of
the top-5, and 600 chars renders only the first ~2–4 entries.

So the honest current state: **edge expansion is live but
rendered-inert on this box's traffic.** The r1-era figures (4/120,
then 2/120) were artifacts of successively laxer measurement layers.
The A/B denominator will read zero until either candidate promotion
improves the seed/neighbour pool or the render budget grows — the
latter is a recall-wide scope decision (Jeremy's), not a review fix.
No silent tuning of `max_chars` was done.
