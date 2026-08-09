---
status: record
---

# World-facts slices 2+3 — cross-model adversarial review (2026-08-09)

Three Codex lenses (skeptic / architect / minimalist) vs commit 04bc506
(finalize landing + planner FACT: emission), per the standing
land → review → verify-before-fix loop. 18 raw findings, deduped to 12;
**7 confirmed** (verified against code before fixing — the
verify-before-fix rule), 5 rejected/accepted-as-decided. Fix layer
landed same session.

## Verdict: CONTESTED → remediated

One 3/3-consensus HIGH (staged lane), one confirmed HIGH that gutted the
chunk's central safety claim (idempotency stamp), both fixed same
session.

## Confirmed and fixed

| # | Sev | Lens | Finding | Fix |
|---|---|---|---|---|
| 1 | HIGH | minimalist (sharpest), skeptic+architect (adjacent) | **Idempotency stamp compared `world_facts_landed == loop_id`, but `loop_init.py` mints a fresh `uuid4` loop_id on every init — including resume.** The stamp never matched on the exact demote→resume topology it was built for; the restored hypothesis WOULD have self-confirmed to the RULE_PROMOTE_CONFIRMATIONS=2 threshold. Verified: `ctx.loop_id = str(uuid.uuid4())[:8]` unconditional; resume restores ledger only. | Per-fact `world_facts_landed_keys` list in run metadata: landed keys skip, new-after-resume facts land, transiently-failed writes retry (this also resolved skeptic-4/architect-2's all-or-nothing-stamp finding structurally). Pinned with a real resume topology (new loop id) + a facts-after-resume pin. |
| 2 | HIGH | 3/3 consensus | **Staged-pass lane taught WORLD_FACT_RULES but never receives the injected-context extras** — the rules demand a fact the planner "can point to in the provided context" with no context provided; any staged FACT is a model prior wearing a grounding claim, then stamped evidence "planner-declared from injected context". | Teaching removed from the staged lane (subtract, not plumb — the staged lane's context-starvation is its own design issue); parse detection stays. Test flipped to pin NOT-taught. |
| 3 | MED | skeptic + architect | **Provenance sticky only for normalized-identical text** — "The API is rate-limited." re-declared planner-seeded "the API is rate-limited" as a fresh LANDABLE step row (trailing punctuation alone defeats the key). | `_planner_twin` guard in `observe()`: a same-kind lexical near-restatement (difflib ratio ≥ 0.85) of a planner row merges into it instead of minting a landable twin. Semantic paraphrases still slip — named residual (lexical instrument, honest label). |
| 4 | MED | minimalist | **hits inflated by same-step repetition** — one payload repeating a claim 3× outranked genuinely re-observed facts in the landing sort. | hits bumps only on a distinct step_idx. |
| 5 | MED | architect | **2-value source enum coerced unknown labels to "step"** — any future producer (container, dispatch) silently landable. | Unknown source strings preserved verbatim; landing flipped to an allowlist (`source == "step"`). Pinned. |
| 6 | LOW | skeptic | No-run-dir topology lands fail-open with no stamp read/write possible. | Accepted + documented (those topologies finalize once); named in the land_facts docstring. |
| 7 | LOW | architect | `seed_planner_facts` evidence string claimed "from injected context" for the staged lane too. | True only because of #2; resolved by #2 (remaining taught lanes all carry the extras). |

## Rejected / accepted-as-decided (with rationale)

- **factory_thin `_parse_steps` doesn't strip FACT lines** (skeptic,
  HIGH-claimed): real by construction, rejected as ship-blocking —
  factory_thin uses its own `FACTORY_DECOMPOSE` prompt and is never
  taught the protocol; it is the adjudicated separate instrument (same
  rejection as the 2026-08-01 review round). Recorded as a named
  residual in the design doc.
- **Concurrent finalizes can double-mint candidate nodes / lose
  confidence bumps** (skeptic, HIGH-claimed): verified the mechanism,
  but it is the bridge's pre-existing snapshot-upsert pattern
  (`outcome_to_knowledge` has landed this way since K4) — a store-
  serialization class, not introduced by this chunk. Own-the-file says
  name it, not bolt a lock onto one caller: candidate nodes are
  born-invisible and promotion-gated, so the blast radius is a
  duplicate candidate. Named here; the store-concurrency work is its
  own item.
- **Multi-plan collects FACTs from rejected candidates** (minimalist,
  MED): DECIDED-keep. Facts claim grounding in the SHARED injected
  context, identical across candidates — plan selection judges plan
  quality, not fact validity; the sampled-hallucination risk is the
  same in every taught lane and is bounded by the injection scan, the
  advisory-only render, and the planner-never-lands guard.
- **World-fact hypotheses can confirm lesson-derived hypotheses**
  (skeptic, MED): verified the mechanism (exact-text match crosses
  domains; 0.85 similarity requires same domain, and world-fact domain
  is project-slug vs task_type elsewhere, which SUPPRESSES cross-lane
  similarity). Exact-text collision between an LLM-minted lesson and a
  worker's guess is the only path — accepted as a low-probability edge;
  contradiction-check-at-promotion and refight are the backstops.
- **WORLD_FACTS_LANDED is write-only telemetry** (minimalist, LOW):
  rejected — captain's log as decided audit surface; the identical
  finding (write-only remint event) was rejected in the 2026-08-08
  stretch round 2.
- **Parallel/DAG lane facts never land** (architect, HIGH-claimed):
  rejected as NEW — it is the named scope limit recorded in the design
  doc and slice-1 review before this chunk existed.
- **Candidate accretion / finalize cost growth, title-truncation
  conflation** (architect, LOW): accepted as known bridge-class edges,
  no change.

## Scoreboard

12 deduped findings, 7 confirmed (58% — above the 30–50% historical
confirmation band; the loop-id finding alone justified the round). The
fix layer is small enough to verify exhaustively — one round, no
fixpoint escalation needed. Pattern from the stretch holds: the
highest-value finding attacked the chunk's own safety mechanism, not
its feature surface.
