---
status: record
---

# Wiring inventory — memory/knowledge stores and events (2026-07-21)

**Status: report-only.** This is chunk 1's wiring inventory from the
swarm-review plan (chunk-8 split: report early while repair budget
exists, enforce late). The **enforcement pin** — census checks that keep
this table true mechanically — lands after chunks 3-4 per the
pin-after-fix convention, alongside the store-registration convention it
needs.

**Provenance:** produced by a single read-only survey subagent
(2026-07-21). Rows marked KNOWN restate findings already independently
verified during the knowledge journey / battery adjudication. The 8
"surprises" and all other per-row claims are **agent-reported and not
yet verified by the adjudicating session** — per the verify-before-fix
rule (~30-78% of unverified reviewer claims historically wrong), verify
each claim against the tree before acting on it. BACKLOG entries for the
surprises carry the same flag.

**Vocabulary:** CLOSED-LOOP (live writer + live reader), WRITE-ORPHAN
(live writer, no reader), READ-ORPHAN (live reader, no writer), DEAD
(neither end live). "LIVE" = reachable from the runtime loop; evidence
is the caller chain in each row.

---

# Wiring inventory — memory/knowledge subsystem, `/home/clawd/claude/maro-orchestration`

All `file:line` refs below are under `/home/clawd/claude/maro-orchestration/src/` unless prefixed. Workspace = `/home/clawd/.maro/workspace/memory/`. "LIVE" = reachable from the runtime loop (handle.py / handle_queue.py → agent_loop → loop_planning/loop_execute/loop_post_step/loop_finalize, director.py worker dispatch, evolver via loop_finalize.py:624/661 + heartbeat.py:894/1219). KNOWN = in the parent's known-findings list, included with fresh evidence, not re-derived.

| # | Store / event (path constant) | Writer(s) + live chain | Reader(s) + live chain | Verdict |
|---|---|---|---|---|
| 1 | `outcomes.jsonl` (memory_ledger `memory_dir()`) | `record_outcome` memory_ledger.py:437 ← `reflect_and_record` memory.py:380 ← loop_finalize.py:562 (LIVE). Verdict stamp: `stamp_outcome_verdict` memory_ledger.py:570 ← handle.py:2040/2164/2353 (LIVE) | `load_outcomes` memory_ledger.py:980 ← evolver scans + `annotate_outcome_lessons`:696; `load_outcome_by_loop_id`:661 ← handle.py verdict path (LIVE) | **CLOSED-LOOP**. 1431 rows, current (Jul 16-17) |
| 2 | legacy `lessons.jsonl` (memory_ledger) | `_store_lesson` memory_ledger.py:798 ← `reflect_and_record` + diagnosis path loop_finalize.py:~513 (LIVE) | `load_lessons` memory_ledger.py:903 ← recall.py:595 loop substrate #1 (LIVE) | **CLOSED-LOOP** — but this is the KNOWN wrong-store finding: recall substrate #1 reads legacy, not tiered. 301 rows |
| 3 | `task_ledger.jsonl` | `append_task_ledger` memory_ledger.py:264 ← loop_execute.py:1386 (LIVE) | `load_task_ledger` memory_ledger.py:285 — **zero non-self callers** (grep across src/) | **WRITE-ORPHAN** (SURPRISE). 2978 rows accumulating, never read |
| 4 | `step_traces.jsonl` | `record_step_trace` memory_ledger.py:324 ← loop_finalize.py:591 (LIVE) | `load_step_traces` memory_ledger.py:369 ← evolver.py:176 ← loop_finalize.py:661 / heartbeat.py:894 (LIVE) | **CLOSED-LOOP**. 414 rows |
| 5 | `compressed_outcomes.jsonl` | `compress_old_outcomes` memory_ledger.py:1146 / `_save_compressed_batch`:1099 — zero callers | `load_compressed_batches`:1114, `load_outcomes_with_context`:1359 — zero callers | **DEAD** (SURPRISE). File absent from workspace |
| 6 | `MEMORY.md` index | `_update_memory_index` memory_ledger.py:1419 ← record paths (LIVE) | none in src (human-facing artifact) | WRITE-ONLY by design — not counted as a broken loop |
| 7 | tiered lessons `medium/lessons.jsonl`, `long/lessons.jsonl` (knowledge_web) | `record_tiered_lesson` knowledge_web.py:189 ← `reflect_and_record` memory.py:~430 + loop_finalize.py:495/~540 recovery lessons (LIVE) | Runtime canon reader `inject_tiered_lessons` knowledge_web.py:972 ← strategy_evaluator.py:445 **CLI `--compare` only**; `query_lessons`:1044 ← persona.py:431 (dormant, see surprises) | **WRITE-ORPHAN on the canon path** — KNOWN (recall reads legacy; canon starvation). Graveyard band of this same store IS read live (row 8) |
| 8 | graveyard (decay band + archive of tiered lessons; no separate file) | decay/GC via `maybe_consolidate` ← handle.py:725, heartbeat.py:1219 (LIVE) | `search_graveyard` knowledge_web.py:572 ← recall.py:652 with `resurrect=True` (LIVE); prereq.py:133 ← loop_planning.py:410 (LIVE) | **CLOSED-LOOP** |
| 9 | `canon_stats.jsonl` | `_record_canon_hit` knowledge_web.py:1137 ← `_increment_times_applied`:1093 ← ONLY `inject_tiered_lessons`:972 (CLI-only) | `get_canon_candidates` knowledge_web.py:1178 ← evolver_scans.py:583 (LIVE), cli.py:1240, knowledge.py:66 | **READ-ORPHAN** — KNOWN canon starvation. 477 rows, STALE since Apr 11 |
| 10 | `knowledge_nodes.jsonl` | `append_knowledge_node` knowledge_web.py:1336 ← `outcome_to_knowledge` knowledge_bridge.py:325 ← memory.py:515/679 (LIVE; nodes born `NODE_CANDIDATE`, conf 0.3) | `inject_knowledge_for_goal` knowledge_web.py:1478 ← recall.py:687 (LIVE) — ACTIVE-only filter; `query_knowledge`:1425 min_confidence path | **CLOSED-LOOP mechanically, KNOWN candidate-forever break**: live writes invisible to live reader. 609 rows. Plus in-memory `times_applied` no-op (surprise 4) |
| 11 | `knowledge_edges.jsonl` | `append_knowledge_edge` knowledge_web.py:1345 ← `record_skill_knowledge_edge` knowledge_bridge.py:278 — only fires when `skills_used` passed to `outcome_to_knowledge`, and memory.py:515/679 call it WITHOUT `skills_used`; `build_wiki_link_edges`:1522 zero callers | `load_knowledge_edges` knowledge_web.py:1389 — **zero callers** | **DEAD in practice** (SURPRISE): live writer never triggers, reader unreached. 2124 rows, stale Apr 11 |
| 12 | `standing_rules.jsonl` (knowledge_lens) | `observe_pattern` knowledge_lens.py:211 ← knowledge_web `_post_reinforce_hooks`/`promote_lesson` (LIVE); `refight_rule`:604 ← skill_lifecycle.py:684 ← evolver `run_skill_maintenance` ← loop_finalize.py:624 (LIVE) | `inject_standing_rules` knowledge_lens.py:505 ← recall.py:637 (LIVE); `contested_rules`:575 ← skill_lifecycle.py:694 (LIVE) | **CLOSED-LOOP** — KNOWN domain-mismatch degradation (agenda vs project slug). 4 rows |
| 13 | `hypotheses.jsonl` | `observe_pattern` knowledge_lens.py:211 (LIVE — same chain as row 12) | internal read by `observe_pattern` itself for confirmation-count/promotion; only external reader pack.py:495/557 (CLI import/export) | **CLOSED-LOOP internally** (write→confirm→promote to standing rule); no runtime injection reader. 0 rows currently |
| 14 | `decisions.jsonl` | `record_decision` knowledge_lens.py:772 — zero callers | `inject_decisions` knowledge_lens.py:882 ← recall.py:644 (LIVE) | **READ-ORPHAN** — KNOWN. File absent from workspace |
| 15 | `verification_outcomes.jsonl` | `record_verification` knowledge_lens.py:924 — zero callers | `verification_accuracy`:1009, `calibrated_alignment_threshold`:1043 — zero callers | **DEAD** — KNOWN (since a278575), calibration cluster dead too. 58 rows, stale Apr 11 |
| 16 | `rules.jsonl` (rules.py) | `graduate_skill_to_rule` rules.py:221 ← knowledge.py:354 (CLI-only); `record_rule_use`:165 / `record_rule_wrong_answer`:292 fire only if rules exist | `find_matching_rule` rules.py:139 ← loop_planning.py:620, `record_rule_use` ← loop_planning.py:622 (LIVE) | **READ-ORPHAN** — KNOWN. File absent from workspace |
| 17 | `playbook.md` (`config.playbook_path()`) | `append_to_playbook` playbook.py (writes under `## Learned`) ← evolver apply path (LIVE) | `inject_playbook` ← recall.py:680 (LIVE) — `max_chars=800` from first `## ` | **CLOSED-LOOP with KNOWN horizon bug** (reader starves Learned section) |
| 18 | `captains_log.jsonl` | `log_event` captains_log.py:337 ← throughout loop (LIVE) | `load_log` captains_log.py:465 ← recall.py:62 `recent_learning_activity` (LIVE); `query_log`:552 (evolver/CLI) | **CLOSED-LOOP**. 3813 rows |
| 19 | event `STANDING_RULE_CONTRADICTED` (captains_log.py:58) | `contradict_pattern` knowledge_lens.py:373→396 — **zero callers** | recall.py:54 `_LOOP_ACTIONABLE_EVENTS` (LIVE reader) | **READ-ORPHAN / phantom event** — KNOWN |
| 20 | event `RULE_GRADUATED` (captains_log.py:59) | emitted in `graduate_skill_to_rule` (CLI-only, row 16) | recall.py:54 (LIVE reader) | **READ-ORPHAN at runtime** — corollary of KNOWN rules.jsonl finding |
| 21 | event `HYPOTHESIS_PROMOTED` | `observe_pattern` promotion path (LIVE) | recall.py:54 (LIVE) | **CLOSED-LOOP** |
| 22 | event `RECALL_PERFORMED` (captains_log.py:162) | recall.py:709 (LIVE) | nothing reads it for control flow (crystallization substrate per design) | WRITE-ONLY by design |
| 23 | decision-prior `run_card.json` (per-run dir) | `run_curation.index_decision_prior` ← `curate_run` ← runs.py:677 (LIVE) | `load_decision_prior`/`format_prior_decisions` decision_prior.py ← recall.py:577 (LIVE) | **CLOSED-LOOP** |
| 24 | thread brain `goal_brain.md` (`<run_dir>/source/`) | `create_thread_brain`/`append_decision`/`append_compiled_truth`/`record_child`/`record_close` ← runs.py:325/336/345/576, loop_post_step.py:683/795, handle.py:2026/2142 (LIVE) | `load_thread_brain` ← director.py:445, navigator_shadow.py:145/343/451 (LIVE) | **CLOSED-LOOP** |
| 25 | worker memory slice — SqliteMemoryStore `module/index.db` (memory_bridge) | `ingest_lessons_to_store` memory_bridge.py:178 ← director.py:~430 (LIVE, default-on) | `recall_for_worker`:256 / `format_worker_memory_block`:313 / `stamp_items_with_age`:286 ← director.py:430-466 (LIVE) | **CLOSED-LOOP** |
| 26 | `navigator_lessons.jsonl` | `crystallize_navigator_lessons` navigator_shadow.py:945 ← adjudication navigator_shadow.py:913 ← `adjudicate_navigator_divergences` ← evolver.py:858-859 (LIVE) | `load_navigator_lessons` navigator_shadow.py:994 ← navigator_prompt.py:332-333, gated on `navigator.lesson_inject` config (code-default off; enabled on this box per decree) ← `decide` ← navigator_shadow.py:236/364/469 ← handle_queue.py:157 `shadow_dispatch_live` (LIVE) | **CLOSED-LOOP** (config-gated). 1 row |
| 27 | `change_log.jsonl` (evolver audit trail, lives in memory_dir) | evolver_store.py:322 apply path ← evolver (LIVE) | evolver_scans.py:633-635, evolver_store.py revert:695, knowledge_web.py:749 | **CLOSED-LOOP** (evolver subsystem, listed for completeness). 414 rows |

Workspace cross-check (`ls`/`wc`, read-only): current writes Jul 16-17 for rows 1-4, 10, 12, 18, 26-27; stale Apr 11 for `knowledge_edges.jsonl`, `verification_outcomes.jsonl`, `canon_stats.jsonl`; empty `hypotheses.jsonl`; absent `decisions.jsonl`, `rules.jsonl`, `compressed_outcomes.jsonl` — workspace state corroborates every orphan/dead verdict above.

## Surprises (NOT in the known-findings list)

1. **`task_ledger.jsonl` is a WRITE-ORPHAN.** `append_task_ledger` fires on every step (loop_execute.py:1386, 2978 rows and growing); `load_task_ledger` (memory_ledger.py:285) has zero callers anywhere in src/. Pure disk burn.
2. **The entire outcome-compression pipeline is DEAD.** `compress_old_outcomes` (memory_ledger.py:1146), `load_compressed_batches` (:1114), and `load_outcomes_with_context` (:1359) have zero callers; `compressed_outcomes.jsonl` has never been created on this box. `outcomes.jsonl` therefore grows unbounded (1431 rows) with no compaction path wired.
3. **`knowledge_edges.jsonl` is dead on both ends.** The only live-path writer trigger requires `skills_used` to be passed to `outcome_to_knowledge` (knowledge_bridge.py:325), but both live call sites (memory.py:515 and :679) omit it — so `record_skill_knowledge_edge` (knowledge_bridge.py:278) never fires at runtime; `build_wiki_link_edges` (knowledge_web.py:1522) has zero callers; and `load_knowledge_edges` (knowledge_web.py:1389) has zero callers. The Web layer's edge graph (2124 rows, frozen since Apr 11) is disconnected scaffolding.
4. **The live ACTIVE-node read path's application counter silently no-ops.** `inject_knowledge_for_goal` does `node.times_applied += 1` at knowledge_web.py:1505 on the in-memory object only — never persisted back to `knowledge_nodes.jsonl`. Even for nodes that DO reach ACTIVE, usage evidence is discarded, so any future promotion/decay logic keyed on `times_applied` reads zeros.
5. **Persona template memory reads are wired-live but dormant.** `render_persona_template` (persona.py:383) lazily reads `load_standing_rules`/`query_lessons` (persona.py:417-440) and IS on the live path via `build_persona_system_prompt` (persona.py:475 ← handle.py:1428, workers.py:133, conductor.py:562, scope.py:453) — but zero persona files in the repo or `~/.maro/workspace/personas/` contain `{{ standing_rules }}` or `{{ recent_lessons }}` (grep found no `{{` at all), so the seam never fires in practice.
6. **`hypotheses.jsonl` has no runtime injection reader** — its only external reader is pack.py:495/557 (CLI import/export). The store still functions as the internal confirmation counter for standing-rule promotion, so hypotheses themselves are invisible to recall until they graduate.
7. **The verification DEAD finding extends to the calibration cluster**: `verification_accuracy` (knowledge_lens.py:1009) and `calibrated_alignment_threshold` (:1043) — the intended consumers that would have made verification data useful — are independently uncalled, not just the raw record/load pair.
8. **`RULE_GRADUATED` is a second phantom-adjacent event** (like `STANDING_RULE_CONTRADICTED`): listed in recall's `_LOOP_ACTIONABLE_EVENTS` (recall.py:54) but its only emitter is the CLI-only `graduate_skill_to_rule`, so it never appears in the live log.