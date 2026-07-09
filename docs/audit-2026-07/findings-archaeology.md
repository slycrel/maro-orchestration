# Purgatorio eye 3 — backward archaeology (findings)

**Date:** 2026-07-09. **Method:** walked BACKLOG_DONE.md, docs/history/ROADMAP_ARCHIVE.md,
GOAL_BRAIN.md Decisions, MILESTONES.md, BACKLOG.md, and the 2026-07-04 memory decision
brief; cross-checked every significant "decided X" / "shipped Y" claim against current
src/, box config (`~/.maro/config.yml`), workspace artifacts (`~/.maro/workspace/`,
`~/.maro/experiments/`), and git branch state. Every finding below was probed, not
inferred from the record. Overlap with eye 1 (ops) is flagged as triangulation.

## Findings

| id | claim | evidence | subsystem | severity | status | disposition |
|---|---|---|---|---|---|---|
| arch-01 | The record says the Phase 65 scope MVE is "PAUSED / shipped dormant / default OFF" (CLAUDE.md open-design-spaces; docs/DEFAULTS.md:75-76; BACKLOG Phase 65 section), but this box has run it **live since ~April** in the A/B-control configuration: `~/.maro/config.yml:28-29` = `scope_generation: true` + `scope_ab_skip: true`, so every AGENDA run pays the scope-generation LLM call (181 runs carry `resolved_intent.md`, 39 since 2026-07-01), the generated scope is **never injected** (handle.py:1412-1414 "record but don't inject"), yet the ResolvedIntent still flows into closure verification unconditionally (handle.py:1517, 1607, 1847 → closure_verify.py:411-412). The box permanently runs the *losing* A/B arm while docs call the feature dormant — and the 2026-07-09 "accept ResolvedIntent v0 on organic evidence" decision rests on organic evidence generated under this misdescribed config (injection off, closure-side on). | ~/.maro/config.yml:28-29; src/handle.py:1265, 1412-1419, 1517; src/closure_verify.py:411-412; docs/DEFAULTS.md:75-76 | Interface / docs | real-but-deferrable | confirmed | goal-brain-correction + backlog-item (decide the flag posture deliberately) |
| arch-02 | BACKLOG's Phase 65 section claims "the actual A/B measurement has not been run" — false: the scope A/B ran and was adjudicated 2026-04-23 (`~/.maro/experiments/scope-ab-2026-04-22/ANALYSIS.md`, 6 runs; primary signal: scope injection compresses plans 8 vs 15-40 steps, 3/3 treat clean vs 1/3 control). The measured *winning* arm (inject) is exactly what `scope_ab_skip: true` disables today (arch-01); the experiment's result never fed back into the flag decision. | BACKLOG.md Phase 65 "A/B blocker" note; ~/.maro/experiments/scope-ab-2026-04-22/ANALYSIS.md; docs/history/ROADMAP_ARCHIVE.md:1374-1379 | Interface / docs | real-but-deferrable | confirmed | goal-brain-correction |
| arch-03 | Two further paid A/B experiments exist with **no adjudication artifact anywhere**: `~/.maro/experiments/scope-ab-2026-04-25-v0/` and `scope-ab-2026-04-26-v1/` each hold full treat/control run dirs (repo.bundle, captains_log_slice, handle.log) but no ANALYSIS.md, and no archive/BACKLOG_DONE entry adjudicates them — while BACKLOG_DONE's ResolvedIntent entry states the "minimum before/after experiment (sub 1) never ran". Paid evidence was collected and never read; the record contradicts the artifacts. | ~/.maro/experiments/scope-ab-2026-04-25-v0/, scope-ab-2026-04-26-v1/ (run dirs, no ANALYSIS.md); BACKLOG_DONE.md ResolvedIntent v0 entry | Interface / data | real-but-deferrable | confirmed | backlog-item (adjudicate or explicitly write off) |
| arch-04 | GOAL_BRAIN decision 2026-06-11 records decay-by-invalidation's repair half (`refight_rule`) as "shipped same day", but it is **structurally unreachable in production**: the only live caller path is `loop_finalize.py:544-545` → `run_skill_maintenance()` with **no adapter**, and the refight block requires `adapter is not None` (skill_lifecycle.py:689); the only adapter-bearing callers are `run_evolver()` and heartbeat, which have never run (ops-01/ops-02 triangulation). A contradicted standing rule would demote to verify-before-relying and stay contested forever. Latent today (4 rules, 0 contested on box) but the decided mechanism cannot fire. | src/loop_finalize.py:544-545; src/skill_lifecycle.py:534-540, 680-692; GOAL_BRAIN 2026-06-11 decision; ~/.maro/workspace/memory/standing_rules.jsonl (4 rules, 0 contested) | Quality/Self-Improvement | real-but-deferrable | confirmed | backlog-item (pass adapter at finalize or move refight per-run) |
| arch-05 | Phase 42's `run_nightly_eval` has exactly one caller — `heartbeat.py:607-608` inside the never-running heartbeat loop — yet on 2026-07-04 a BACKLOG item was closed as "stale duplicate — run_nightly_eval→evolver wiring shipped Phase 42". Dead-vehicle code was cited as grounds to drop work; the nightly eval has never executed in production. | src/heartbeat.py:607-608 (sole caller, grep src/); src/eval.py:344,351 ("Called from heartbeat_loop() on a 24h cadence"); BACKLOG_DONE 2026-07-04 closure note | Quality/Self-Improvement | real-but-deferrable | confirmed | backlog-item (reopen or rewire per-run like the 5 scanners) |
| arch-06 | GOAL_BRAIN compiled truth contains a false claim that laundered the dead heartbeat as invariant-consistent: "heartbeat runs in-process when the app runs" (GOAL_BRAIN.md:462-463). No heartbeat invocation exists in handle.py or agent_loop.py (grep clean); `run_heartbeat`/`heartbeat_loop` are reachable only via the CLI command (cli.py:557-559). The in-process fallback the record asserts does not exist. | GOAL_BRAIN.md:461-463; grep run_heartbeat/heartbeat over src/handle.py, src/agent_loop.py (zero hits); src/cli.py:557-559 | Platform / docs | real-but-deferrable | confirmed | goal-brain-correction |
| arch-07 | The memory brief's two **fabricated lat.md nodes** (flagged 2026-07-04, fix listed under "Phase 0 — hygiene... do regardless of the rest" in the accepted direction) are still in the live injection corpus: `lat.md/poe-identity.md:9-10` cites `src/poe_self.py` + `user/POE_IDENTITY.md` (don't exist), `lat.md/quality-gates.md:13,20-21` cites `src/passes.py` + a `poe-passes` CLI (don't exist), and `lat_inject` actively injects lat.md nodes into planner and director prompts (planner.py:376-377, director.py:642-643). Known-fabricated file paths can be fed to the LLM 5 days after the arc that adopted the brief was marked complete. | lat.md/poe-identity.md:9-10; lat.md/quality-gates.md:13,20-21; src/planner.py:376-377; src/director.py:642-643; docs/history/2026-07-04-memory-decision-brief.md G5 + Phase 0 | Memory/Knowledge | real-but-deferrable | confirmed | fixed-inline candidate (delete/correct 2 nodes) → backlog-item |
| arch-08 | The knowledge graph is still write-only, post-arc: `knowledge_edges.jsonl` now holds 2,124 edges and `load_knowledge_edges` still has **zero callers outside knowledge_web.py** — the accepted memory direction's Phase 2 ("1-hop edge expansion in knowledge injection", G2) shipped only its BM25 half; the graph read-side was silently dropped while MILESTONES arc -1 is recorded complete. | ~/.maro/workspace/memory/knowledge_edges.jsonl (2,124 lines); grep load_knowledge_edges/record_knowledge_edge outside src/knowledge_web.py (zero hits); memory brief G2/Phase 2; MILESTONES.md arc -1 | Memory/Knowledge | real-but-deferrable | confirmed | backlog-item |
| arch-09 | G4a from the same accepted Phase 0 list ("wire `record_rule_wrong_answer` to the inspector") is still unwired: the function has zero callers repo-wide, so rule auto-demote remains manual-CLI-only, exactly as the brief flagged before the direction was accepted. | grep record_rule_wrong_answer src/ (zero non-definition hits); memory brief G4a + Phase 0 | Memory/Knowledge | real-but-deferrable | confirmed | backlog-item |
| arch-10 | Dropped branch: `origin/factory` sits 5 commits ahead of main (all 2026-03-31, `factory_full_sim` v2-v4 — "full architecture simulation in one prompt loop" with benchmark results) with **zero references in docs/, BACKLOG, or GOAL_BRAIN** — work finished, pushed, and lost from the record. Related loose end: stash `temp-audit-skill-lifecycle-wip` on `worktree-refactor-plan`. | `git log main..origin/factory` (3cc7c5e, 9d252f0, 86f4014, 69aeee8, 40467b6); grep factory_full_sim docs/ *.md (zero hits) | docs / Platform | cosmetic | confirmed | backlog-item (adjudicate: merge learnings, archive, or delete branch) |
| arch-11 | GOAL_BRAIN — the doc that **wins by decree** — is the stale one on thread-brain per-turn maintenance: its Open questions still list "(a) the compiled-truth half and (b) feeding the dispatch-navigator's rationale" as *remaining pieces* (GOAL_BRAIN.md:1409-1412), while MILESTONES #3 records both halves SHIPPED 2026-07-03 with tests ("#3 both halves now closed"). Anyone honoring the currency rule would re-plan already-shipped work. | GOAL_BRAIN.md:1409-1412 vs MILESTONES.md:33 | docs | cosmetic | confirmed | goal-brain-correction |

## Severity roll-up

- **New blockers-for-1.0: none from this eye alone.** But arch-04 and arch-05 harden
  eye 1's ops-02 blocker (evolver/self-learning never runs): they show *ratified
  decisions* — not just infrastructure — ride the dead heartbeat/evolver vehicle,
  which directly undercuts 1.0 item (f)'s "self-learning in the build-out" claim.
- arch-01 + arch-02 should be reconciled **before** 1.0 flag defaults are finalized:
  the DEFAULTS.md story ("dormant, paused") is true of the code default and false of
  the reference deployment, and the one adjudicated experiment says the currently
  disabled arm is the better one.

## Clean checks (probed, no finding)

Counted because absence-of-rot is signal too — 13 checks came back clean:

1. `maro-import` exists as recorded (commit ff02f6e).
2. `run_skill_maintenance` per-run wiring at loop_finalize.py:544 is real; the
   BACKLOG #13 "5 free statistical scanners per-run" claim matches the code comment
   at loop_finalize.py:551+ (the *adapter-gated* parts are arch-04).
3. Knowledge consolidation is genuinely in-process (handle.py:637 `maybe_consolidate`)
   — that half of the no-daemons story holds.
4. `user/COMPLETION_STANDARD.md` is consumed (handle.py references completion_standard)
   — not a dead operator doc.
5. `factory_thin.py` exists; the thin-mode lane wasn't deleted by the refactor tiers.
6. `checkpoint.py` is wired across agent_loop / loop_execute / loop_planning /
   loop_finalize / loop_post_step — resume claims hold at the wiring level.
7. `origin/feat/local-validator` and `worktree-refactor-plan`: 0 commits ahead of
   main — fully merged, not dropped (unlike arch-10).
8. `validate.write_fence` default-ON matches the DEFAULTS.md:49 record.
9. Navigator cutover history: the "round 2 before cutover" promise trail in the
   record is internally consistent (per-MOVE cutover, act_moves escalate-only default).
10. Fork/join async deferral is documented as deferred, not silently dropped.
11. Sandbox-hardens-a-stub is already tracked in BACKLOG (not a silent drop; eye 5's
    territory for the fix).
12. The M5 pip-packaging false "verified" claim was already self-corrected in
    GOAL_BRAIN Threads — the record healed itself there.
13. Standing-rules store is healthy-small (4 rules, 0 contested, lock file present) —
    no data rot on that lane.

## Notes for reconciliation

- Triangulation map: arch-04/arch-05/arch-06 all orbit ops-01/ops-02 (dead
  heartbeat/evolver) — merge into one "decisions recorded as shipped ride a vehicle
  that never runs" super-finding if desired.
- arch-01/02/03 form one storyline: experiment ran → won → flags left in control
  configuration → docs drifted to "dormant" → later decisions cited "organic
  evidence" from the misdescribed config. Recommend adjudicating as a unit.
- arch-07/08/09 are all "accepted recommendation (2026-07-07 memory direction),
  Phase 0/2 items silently dropped while the arc was marked complete."
