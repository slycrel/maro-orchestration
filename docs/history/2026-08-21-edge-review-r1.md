---
status: record
name: edge-review-r1
description: Adversarial review r1 of the edge-traversal chunk (fcff27be) — REJECT on two verified HIGHs (self-corrupting A/B denominator, forged-weight wedge), fixed same session; corrected set-based replay readout 2/120.
---

# Adversarial review r1 — edge-traversal chunk (fcff27be)

Four sonnet-medium lenses (Skeptic, Architect, Minimalist, Expert QA);
codex usage-capped until 2026-08-27, so this round ran the skill's
sanctioned same-model fallback.

## Intent

Make the knowledge graph's edge layer real: a deterministic first-party
edge writer (`derive_coderivation_edges`, co-derivation from outcome
provenance), a flag-gated one-hop recall expansion with A/B
observability (`KNOWLEDGE_EDGE_EXPANSION`), dead-writer removal, and a
live backfill — default recall behaviour unchanged with the flag off.

## Verdict: REJECT (SAME-MODEL FALLBACK: sonnet-medium)

Two VERIFIED HIGHs, both in the new code. Fixed same session (commit
following this doc).

## Verification Ledger

**Architect H1 — the A/B denominator was self-corrupting — VERIFIED.**
`_expand_scored_via_edges` boosted any neighbour whose damped score
(`seed * weight * 0.5`) beat its own, with no check against the rendered
top-k cutoff: a neighbour already inside the slice got `via_edge_from`
stamped, `[linked]` rendered, and `KNOWLEDGE_EDGE_EXPANSION` fired —
claiming "injection actually differed from text-only ranking" when the
rendered node SET was identical (only order/decoration moved). Traced:
the boost condition `candidate > own.get(nb_id, 0.0)` had no baseline
membership term; `inject_knowledge_for_goal` evented on any rendered
stamp. Common at live scale (82 ACTIVE nodes, top-5 slices, 430 edges).
Corollary confirmed (Architect finding 6): the shipped "4/120 recalls
changed" replay compared ordered id LISTS — re-measured with set
comparison after the fix, the honest figure is **2/120 (1.7%)**; two of
the four were pure reorderings.

**Expert QA H1 — one malformed `weight` row silently killed both halves
forever — VERIFIED.** `load_knowledge_edges` constructed the dataclass
with no numeric validation (its guard only caught shape drift), so a
string/None/NaN weight loaded fine and then raised TypeError inside
`max(existing.get(key, 0.0), e.weight)` — in the writer (swallowed at
`log.debug` by `run_skill_maintenance`, invisible forever) and in the
reader (bare `except → return scored`, no log at all). Same
string-typed-numeric class that wedged the lessons store 2026-08-16 —
proven hazard, not hypothetical.

## Fixes applied (this round)

- **Set-semantics expansion** — expansion now acts only when it changes
  MEMBERSHIP of the rendered top-k. Set unchanged → text-only ranking
  returned verbatim (ON arm renders exactly what OFF would); set changed
  → only genuine new entrants get `via_edge_from`. Pinned by
  `TestExpansionSetSemantics` (reorder-only identity, new-entrant-only
  stamping, boosted-but-already-rendered unmarked).
- **Weight coercion at the loader boundary** — `load_knowledge_edges`
  coerces `weight` to float per row; non-numeric/NaN rows are
  skip-and-counted through the existing drift warning. Forged-row
  fixtures prove writer and reader survive (`TestForgedWeightRows`).
- **Guarded expansion body + logged degradation** (QA M3) — the whole
  `_expand_scored_via_edges` body is wrapped; failures log at WARNING
  before degrading to text-only, so a broken expansion can't masquerade
  as "expansion rarely helps" (recall.py's blanket swallow sits above).
- **Spanning lock on read-decide-append** (Skeptic L2/Architect L5) —
  `derive_coderivation_edges` takes `locked_write` on the edges file
  across snapshot + decide + append (reentrant with the inner
  `locked_append`), closing the duplicate-row race between heartbeat
  maintenance, finalize tails, and the CLI.
- **Flag coercion parity** (Skeptic M1) — `_edge_expansion_enabled` now
  accepts numeric `1`/`1.0` as ON (the old `val is True` read YAML `1`
  as disabled); other non-bool junk stays OFF (strict for an OFF-default
  behaviour-changing flag). Pinned incl. negative controls.
- **Maintenance-cadence integration tests** (QA M2) —
  `TestMaintenanceWiring` mirrors `test_skill_maintenance_wires_node_promotion`:
  wired count, flag-off skip, exception containment.
- **CLI coverage** (Architect 4) — `derive-edges` and `--dry-run` tested
  through `knowledge.main`.
- **Superseded nodes excluded from the sweep** (Skeptic L5) — the
  writer now filters to active+candidate; no new edges for retired
  content.
- **Dead sibling removed** (Minimalist M1) — `build_wiki_link_edges`,
  `extract_wiki_links`, `_WIKI_LINK_RE` and their tests deleted: zero
  production callers since inception, same class as
  `record_skill_knowledge_edge` (removed in fcff27be), and BACKLOG's own
  fix direction had already rejected the regex-only mechanism.
- **Receipts** (Minimalist M2/Skeptic L3) —
  `scripts/replay_edge_expansion.py` committed (read-only, set-based
  comparison); output below. BACKLOG numbers corrected to the set-based
  figure.
- **Relation comment** (Minimalist L6) — `co_derived` added to the
  `KnowledgeEdge.relation` doc comment.

## Rejected / no-change, with rationale

- **Centrality size-normalizer (all four lenses, MEDIUM)** — real debt,
  deliberately not fixed here: the percentile-of-lines fix was measured
  during the chunk and made the artifact worse (agent_loop rank 24→32),
  and changing the runtime metric churns `find_files_for_goal` behaviour
  mid-feature. The threshold widening is recorded in the test comment
  with the failed experiment; metric revisit noted in BACKLOG. The test
  remains fallible (a genuine decentralization past top-18% still
  fails).
- **Adjacency cache / incremental sweep watermark (Architect M2,
  Minimalist L3)** — premature at 572 fp nodes / ~2.6k edge rows
  (single-digit ms); noted as the named upgrade edge if either store
  10×s.
- **Event-after-loop partial-batch undercount (QA L5)** — accepted
  as-is: append-only store is self-healing; the next sweep converges and
  events.
- **`log.debug` on maintenance failure (QA L6)** — kept for consistency
  with every sibling block in `run_skill_maintenance`; the operator
  signal is now the loader's WARNING drift line (the root cause QA H1
  fix removes the realistic silent-failure path).
- **times_applied feedback loop (Skeptic L7)** — semantics hold
  ("times injected" — an edge-surfaced node IS injected), and the
  expansion pool is ACTIVE-only so no candidate-promotion laundering;
  noted as an A/B interpretation caveat in the BACKLOG entry.
- **Edge weight retraction/decay (Minimalist L4)** — no action until a
  source-retraction path exists; noted in the same BACKLOG entry.

## Replay receipt (post-fix, set comparison, read-only)

```
replayed 120 recent goals (set comparison, read-only)
expansion changed membership on 2/120 recalls (1.7%)
  ~ 'Where exactly is the nearest 24-hour pharmacy to 47 South Main Street, Ephraim, Utah, and '
    +['bfcf24d00d87']  -['a800462f41b5']
  ~ "Maro is getting better, slowly but surely. I have another for today, same ask—let's see if"
    +['cfe4e84f59ca']  -['ea0182ace1e9']
```

Conservative by construction: only 82 of 572 first-party nodes are
ACTIVE today; the denominator grows as candidates promote.

Reviewer artifacts: `/tmp/adversarial-review.4nxs4t/` (session-local,
not committed; findings preserved above).
