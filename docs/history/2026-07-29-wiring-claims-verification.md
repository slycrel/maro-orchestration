---
status: record
---

# Wiring-inventory surprise claims — verification docket (2026-07-29)

**What this is.** The 2026-07-21 wiring inventory
(`2026-07-21-wiring-inventory.md`) flagged 8 "surprises" as
agent-reported and UNVERIFIED. This is the verify-before-fix pass:
every claim re-checked against today's tree (post swarm-review arc,
post 2026-07-29 landings) by the adjudicating session. **Report-only
by adversary-trio verdict** — no wiring, no deleting; each row names
the smallest consumer-first next move for whoever picks it up.

Line numbers below are current as of this pass (the inventory's have
drifted).

**Score: 7/8 CONFIRMED, 1/8 mischaracterized** (right in practice,
wrong in mechanism). The survey agent's hit rate was far better than
the historical 30–78% reviewer-hallucination band — worth noting for
calibration: read-only wiring surveys with caller-chain evidence seem
to be a reliability class above adversarial review findings.

| # | Claim | Verdict | Evidence (today's tree) | Smallest next move |
|---|---|---|---|---|
| 1 | `task_ledger.jsonl` WRITE-ORPHAN | **CONFIRMED** | Writer live: loop_execute.py:1377. `load_task_ledger` (memory_ledger.py:298): only the def, a memory.py:59 re-export, and unit tests. Zero runtime callers. | Decide wire-or-stop-writing. Cheapest real consumer: per-step duty data in `discretion_readout`. Retention decree bars deleting the accumulated file either way. |
| 2 | Outcome-compression pipeline DEAD | **CONFIRMED at runtime** | `compress_old_outcomes` / `load_compressed_batches` / `load_outcomes_with_context`: only memory.py:66-68 re-exports + test_memory.py:527-705. Zero runtime callers; `compressed_outcomes.jsonl` never created on this box. Nuance: NOT untested — it's the "a test exists proves nothing" precedent (the impact-scanner lesson) in its purest form: a fully unit-tested subsystem no runtime path reaches. | If wanted: ride `maybe_consolidate` at evolver cadence (the chunk-2 curation precedent — archive-before-write). If not: retire the ~250 LOC. Either is a design call; `outcomes.jsonl` grows unbounded meanwhile (1431+ rows). |
| 3 | `knowledge_edges.jsonl` dead both ends | **CONFIRMED** | Writer gate: `record_skill_knowledge_edge` fires only under `if skills_used:` (knowledge_bridge.py:397-400); both live call sites — memory.py:717 and :881 — call `outcome_to_knowledge(outcome, adapter=..., dry_run=False)` with no `skills_used`. `build_wiki_link_edges` (knowledge_web.py:1805): zero callers. `load_knowledge_edges`: zero callers. | Either thread `skills_used` from the outcome at the two memory.py sites (data availability unverified) with a reader in the same chunk, or declare the edge graph scaffolding and record that. Consumer-first: no writer fix without a reader. |
| 4 | `times_applied += 1` in-memory only | **CONFIRMED** | knowledge_web.py:1787 (`inject_knowledge_for_goal`, comment "Track application"): increments the loaded `KnowledgeNode` object; function returns a string, nothing writes nodes back. Usage evidence discarded every call. Same starvation family as canon (`_increment_times_applied` for tiered lessons DOES persist — the node path just never got the equivalent). | Persist the increment (small, bounded write). Matters because any future ACTIVE-node promotion/decay keyed on `times_applied` reads zeros. |
| 5 | Persona template memory seam dormant | **CONFIRMED + worse** | Two independent kills: (a) persona.py:412 `from intent import classify_intent` — function does not exist in intent.py → ImportError → except path → `task_type` permanently `"general"` (found during the ClassifyResult chunk, 2026-07-29); (b) inventory's original finding stands — no persona file in repo or workspace contains `{{ standing_rules }}` / `{{ recent_lessons }}`. | Fix (a) is one line once someone decides what task_type means post-ClassifyResult (classify() returns lane, not task_type). (b) is a content decision — a template var nobody uses is dormant by choice until a persona wants it. |
| 6 | `hypotheses.jsonl` no runtime injection reader | **CONFIRMED** | External readers: pack.py (CLI import/export) only. Other grep hits are prose (workers.py:64 prompt text) and docstrings (memory_backends.py:19, file_lock.py:4). Internal confirm→promote loop inside `observe_pattern` intact — hypotheses still graduate to standing rules; they're just invisible to recall until then. | Working as designed unless someone decides pre-graduation hypotheses should inject. Design call, not a bug. |
| 7 | Verification-calibration cluster dead | **CONFIRMED, extended** | One copy (knowledge_lens.py:1264/1349/1383), re-exported by memory.py:99-100. Zero runtime callers of the writer OR either intended consumer. Only tests exercise them — including test_phase61_integration.py, which means **Phase 60's "DONE" shipped symbols + tests and never wired the runtime consumer** (the corpus fixture even recorded "inspector.py wiring — not confirmed" at the time). Workspace corroborates: 58 rows stale since Apr 11. | Wire `record_verification` at the closure/quality-gate verdict seams (the natural writers) with `calibrated_alignment_threshold` consumed where alignment is thresholded — or retire the cluster. Era-09 "urgent by its own argument" family. |
| 8 | `RULE_GRADUATED` second phantom event | **MISCHARACTERIZED — right in practice** | Not a phantom like old `STANDING_RULE_CONTRADICTED` (which had NO emitter): the emitter exists and works — rules.py:271-273 inside `graduate_skill_to_rule` ← knowledge.py:355-356 (`maro-knowledge graduate` CLI). But zero live firings ever (no rules.jsonl, no captains-log rows), so recall.py's `_LOOP_ACTIONABLE_EVENTS` does list an event that has never appeared in the live log. | Inventory row wording corrected here (record-level fix only). Liveness arrives if/when graduation actually fires — nothing to build. |

## Rows overtaken since the inventory (currency notes, not part of the docket)

The swarm-review arc repaired several non-surprise rows after 2026-07-21:
row 14 `decisions.jsonl` now has three live writers (chunk 3); row 19
`contradict_pattern` has the chunk-4 adjudicator writer and the refight
path is reachable; rows 2/7's recall-reads-legacy finding was rewired to
tiered-first (chunk 6); row 17's playbook horizon bug is fixed (chunk 2).
The inventory table is a point-in-time record — read it with this docket
beside it.
