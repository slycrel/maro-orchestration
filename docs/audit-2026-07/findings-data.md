# Purgatorio Eye 2 — Data/learning-store health

Probed 2026-07-09 ~15:50–16:20 MDT, read-only (sqlite opened `mode=ro` URI; no
store written, no file touched). Every claim below was probed in this session
(row counts, example rows, consumer file:line cited). Deepens but does not
re-report ops-01/02/09 (heartbeat, evolver-never-ran, empty model field).

## What matters most for 1.0

1. **The lesson stores the learning loops inject from are ~30% pre-isolation
   test junk, and the junk is live, not archival** (data-01). The LONG tier —
   injected under the header "Long-Term Lessons (always apply)" — is 22/26
   test fixtures ("stable lesson", "highly validated lesson" ×11, "lesson two
   different words"), and they are the ONLY long-tier lessons for
   `task_type="general"` (the 4 real ones are all "agenda"). The memory-module
   store that the just-flipped-default-on worker slice recalls from contains
   125/414 junk items (102 `[dry-run]` + 23 fixture texts) because the bridge
   ingests the junk-laden top-level lessons.jsonl wholesale. Self-learning is
   1.0 item (f); its retrieval substrate is serving April test data to
   production prompts today.
2. **The entire learning read/quality layer is verdict-blind: not one of
   1381 outcomes rows has `goal_achieved`** (data-02). The done≠achieved
   split shipped verdicts into run *metadata* only; outcomes.jsonl — what the
   evolver, inspector, lesson extractor, and recall's repeat-guard actually
   consume — still equates `status=="done"` with success. On Jeremy's own
   organic evidence (4/5 done, 1 achieved), every success-rate the learning
   loops compute is inflated, and lessons are extracted from goal-failed runs
   as if they succeeded. This is the sharpest "quietly lying" instance found.
3. **May 2026 is a hole in the learning record while the run record shows
   476 runs** (data-03). runs/ has 476 May run dirs (321 agenda-lane, 191
   done; metadata mtimes organic May) and captains_log has 965 May events —
   but outcomes.jsonl, step-costs.jsonl, and the daily .md logs have ZERO May
   rows. Outcome-window consumers see a month that didn't happen; ops-10's
   "Apr 26→Jun 11 spend gap = project hiatus" is contradicted from the data
   side (activity happened; recording didn't).
4. **Lesson promotion via the consolidation cycle has never worked: 0
   promoted in all 10 decay cycles ever run** (data-04) — 279 lesson-loads
   decayed, 95 gc'd, promoted_ids empty every time, including the known
   Jun-11 wholesale gc (38/38). The only promotion channel that has ever
   fired is the reinforcement-time hook (4 lessons, ever). The medium tier is
   currently a lossy funnel, not a ladder.
5. **Healthy, verified good** (data-12): the memory-module store's sqlite
   index and JSONL event log agree exactly (414 = 414 ids, ingest offsets
   stored in-store — the ghost-index lesson holds); dev-recall's doc index is
   fresh (613 sources, 0 missing on disk, ≤1 stale >1h) though its
   session-transcript lane is 84 days stale; standing_rules' 4 rules are all
   real production rules; the medium tier is clean post-Jun-23; captains_log
   rotation quarantines the April test-event flood (load_log reads the
   current file only).

## Findings

| id | claim | evidence | subsystem | severity | status | disposition-suggestion |
|---|---|---|---|---|---|---|
| data-01 | Pre-isolation test fixtures are live in the injection paths: long/lessons.jsonl is 22/26 test junk and those 22 are the only LONG lessons for task_type "general"/"research" (injected as "always apply", loaded min_score=0.0); the worker-slice module store is 125/414 junk (102 "[dry-run]" + 23 fixture texts) via bridge ingest of lessons.jsonl (102/290 dry-run rows) | ~/.maro/workspace/memory/long/lessons.jsonl rows 4–25: source_goal "g1"/"g2"/"g", texts "stable lesson", "highly validated lesson" ×11, all task_type general (real 4 all agenda); knowledge_web.py:876-890 loads LONG min_score=0.0, header "Long-Term Lessons (always apply)"; module/index.db `mode=ro`: 102 items LIKE '%dry-run%', 23 fixture texts of 414; memory_bridge.py _lessons_source_paths ingests top-level lessons.jsonl; director.py:432,457 ingest+recall_for_worker at every worker dispatch (default-on since 2026-07-08) | memory/knowledge | blocker-for-1.0 | confirmed | backlog-item: one-time curation purge of pre-Jun test rows from long/lessons.jsonl + lessons.jsonl, then rebuild module store (delete module/, re-ingest — offsets/ids make it idempotent); add a provenance-era guard to bridge ingest. **PURGE EXECUTED 2026-07-09 (same session, before the (f) learning-ON dogfood runs):** long 26→4 rows (kept the 4 organic `[recovery-*]`), top-level 290→188 (dropped 102 `[dry-run lesson]`), module store rebuilt from scrubbed sources 414→308 items, 0 dry-run / 0 fixture remaining; originals backed up at `~/.maro/workspace/memory/backup-2026-07-09-data01/`. Provenance-era ingest guard still open. Side-finding during rebuild: ingest stats `sources` dict keys by filename so all three lessons.jsonl sources collide (cosmetic, counts still correct in total). |
| data-02 | Learning layer is verdict-blind: 1381/1381 outcomes.jsonl rows lack goal_achieved (all eras); evolver, inspector, lesson extraction, and recall's repeat-guard all classify on status=="done"; done≠achieved verdicts live only in runs/*/metadata.json consumed by report/curation code, never by learning consumers | outcomes census: goal_achieved ABSENT in 1272 Apr + 66 Jun + 43 Jul rows; memory.py:349 reflect_and_record(goal,status,...) has no verdict param, records lessons for every run incl. status=done-not-achieved; evolver.py:122-123 splits stuck/done; inspector.py:563 status=="done" + alignment; recall.py:152-155 dispatch_signals all_failing = all(a.status != "done") and :314 reads only meta status; verdicts exist in run metadata (handle.py:418-456,845-856) read by loop_report.py:875,1527 + run_curation.py:114-129 only | memory + quality/self-improve | blocker-for-1.0 | confirmed | goal-brain-correction + backlog-item: plumb goal_achieved (tri-state, absent=unjudged) into record_outcome/reflect_and_record and teach evolver/inspector/recall to prefer it; this IS the 1.0 "done-vs-achieved number" item seen from the store side |
| data-03 | May 2026 activity (476 runs: 155 now, 321 agenda; 191 done, 142 stuck, 129 error; metadata mtimes organically May) produced zero rows in outcomes.jsonl, step-costs.jsonl, and daily .md logs — a month-sized hole in everything the learning loops and the spend ledger read; contradicts ops-10's "hiatus" reading of the spend gap | runs/*/metadata.json census: 476 started_at 2026-05, file mtimes all 2026-05; outcomes.jsonl months = {04:1272, 06:66, 07:43} (no 05); step-costs months = {03,04,06,07} (no 05); memory/ has no 2026-05-*.md daily; captains_log.jsonl has 965 May events (e.g. LOOP_CREATED 2026-05-12T22:19 dry_run:false) | memory/platform | real-but-deferrable | confirmed | investigate-why (likely reflect_and_record/metrics wiring broken or bypassed in the May-era code state) + document the hole so no analysis treats May as "no activity"; candidate for backward-archaeologist eye |
| data-04 | Consolidation-cycle lesson promotion has never promoted anything: 10/10 run_decay_cycle entries have promoted:0 (sum: 279 decayed, 95 gc'd), incl. the known Jun-11 wholesale gc (38 of 38); the only working promotion channel is the reinforcement-time hook — 4 real LONG lessons ever, vs 290+ lessons recorded | ~/.maro/workspace/memory/medium/change_log.jsonl all 10 rows promoted_ids:[] (2026-06-11: total 38 gc 38; 06-22: gc 47/53; 07-03: gc 8; 07-04, 07-08: gc 1); long/lessons.jsonl has 4 non-fixture rows; captains_log LESSON_RECORDED 209 vs LESSON_REINFORCED 53 since June; arch skill gap #5 (decay cold-start) documents the mechanism | memory/knowledge | real-but-deferrable | confirmed | backlog-item: revisit promotion economics (decay 15%/day vs consolidation cadence guarantees gc-before-promote for anything not re-hit within days); measure desired funnel rate before tuning |
| data-05 | Knowledge-web lifecycle is inert beyond node appends: all 2124 edges date to the 2026-04-11 link-farm import (0 edges for the 174 nodes added Jun–Jul); times_applied==0 for 489/490 nodes; 174 post-June nodes sit status=candidate with no validation path exercised; query_knowledge docstring says "active nodes" but code never filters status | knowledge_edges.jsonl: 2124 rows, every created_at 2026-04-11 (0 dangling — endpoint ids verified against node_id set); knowledge_nodes.jsonl: 490 rows, times_applied {0:489, 1:1}, status {active:316, candidate:174}; knowledge_web.py:1304-1349 query_knowledge has confidence filter only, no status filter; edge writer knowledge_bridge.py:295 exists but produced nothing since import | memory/knowledge | real-but-deferrable | confirmed | backlog-item: either wire edge creation + times_applied crediting into the injection path or descope "web" claims for 1.0 docs (it is currently a flat node store with a frozen import-era edge set) |
| data-06 | maro-import appends foreign ledger rows with zero provenance marking — imported rows are indistinguishable from host rows in 18 ledgers; e.g. medium/change_log.jsonl now contains a decay-cycle row claiming total:0 (the trial container's fresh store) next to the host's total:105 the day before, and outcomes rows carry container paths /opt/data/home/.maro/... | memory/imports.jsonl 3 rows (2026-07-09, labels hermes-trial-*): appended into outcomes, lessons, medium/lessons, captains_log, knowledge_nodes, skills, step-costs +11 more, no source field written to target rows; medium/change_log rows ts 2026-07-09T10:10 total:0 / 10:22 total:1 vs 07-08 total:105; outcomes tail rows summary "Read /opt/data/home/.maro/workspace/..." | platform/memory | real-but-deferrable | confirmed | backlog-item: stamp imported rows (e.g. imported_from: label) at append time; rides the 1.0 item (g) portable/shareable-learning design — provenance is a prerequisite for sharing learning at all |
| data-07 | 5 hermes-trial outcomes (BACKLOG #18 lane) are in the production store with status=done, LLM-extracted lessons, and no verdict machinery (goal_achieved absent) — trial lessons already flowed into medium tier (9 rows) and the module store | outcomes.jsonl last 5 rows (haiku/couplet goals, 2026-07-09) goal_achieved ABSENT; imports.jsonl: outcomes appended 4+1, medium/lessons appended 7+2; reflect_and_record path (memory.py:390-404) recorded them at MEDIUM tier | quality/self-improve | real-but-deferrable | confirmed | already tracked as BACKLOG #18; add: when verdict machinery lands (data-02), backfill-or-flag these unverdicted imports |
| data-08 | 12 goal_achieved=True verdicts dated 2026-06-12..26 predate the 2026-07-02 closure-judging fix and carry no verdict-version field, so run_curation cards and loop_report render pre-fix (known-poisonable) verdicts identically to post-fix ones | runs metadata census: June judged rows = 12 True / 26 False across 06-12, 06-22..26; handle.py:1664 comment "2026-07-02: a rate-limit-stuck run got goal_achieved=True from ..."; consumers run_curation.py:114-129 ("may be absent = unverified" — no pre/post-fix distinction), loop_report.py:875,1527 | quality/self-improve | real-but-deferrable | confirmed | backlog-item (small): stamp verdict provenance (judge version or judged_at) going forward; annotate or discount the 12 pre-fix True rows in any organic-close-evidence analysis |
| data-09 | skills.jsonl is accretion without application: 243 skills (still growing, 59 in Jul), ALL tier=provisional, ALL circuit_state=closed, only 2/243 with use_count>0 — the graduation/application side of the skill lifecycle has effectively never operated on real runs (data-side twin of ops-02) | skills.jsonl census: tier {provisional:243}, circuit {closed:243}, use_count>0 in 2 rows; 243 rows / 242 unique ids (1 dup); skill-stats.jsonl 136 rows | quality/self-improve | real-but-deferrable | confirmed | fold into the ops-02 evolver disposition: production hours for the self-improve engine before 1.0 claims; the store itself is consistent, just unexercised |
| data-10 | Eight modules fall back to cwd-relative memory/ when workspace resolution fails, silently serving the repo-local stale copies (frozen 2026-04-03..11 but real-looking: outcomes.jsonl 257KB, events.jsonl 1MB) instead of erroring — a lying-fallback design, currently inert because orch_items always imports | thinkback.py:160 Path("memory/outcomes.jsonl") (pure relative); constraint.py:324, introspect.py:168,176, eval.py:408, graduation.py:173,181, scheduler.py:55, orch_items.py:230 all Path.cwd()/"memory"; repo memory/ mtimes Apr 3–11 | platform | real-but-deferrable | confirmed | backlog-item: make the fallback fail loudly (or resolve to workspace-only); repo-local memory/ staying stale is by design, but a silent fallback into it is the ghost-index pattern waiting to recur |
| data-11 | dev-recall's session-transcript and telegram ingest lanes are 84 days stale (last 2026-04-16) while the docs lane is fresh — Eye 7 (chat-history mining) will read an index missing 3 months of sessions unless re-ingested | correspondence.db mode=ro ingest_meta: last_ingest_sessions_utc=1776370114 (2026-04-16), last_ingest_utc=1783631962 (2026-07-09); chunks: 5653 over 613 sources, 0 missing on disk, 1 stale >1h | ops/dev-tooling | real-but-deferrable | confirmed | run dev-recall ingest-sessions before Eye 7; consider folding session ingest into the same habit as doc ingest |
| data-12 | Verified-good: module store sqlite==JSONL (414=414 ids, 0 divergence, offsets in schema_meta not sidecars); dev-recall doc index 0 missing sources; standing_rules 4/4 real production rules; medium tier clean since Jun-23 (post-isolation era only); captains_log rotation quarantines the Apr test-event flood (EVOLVER_APPLIED 1396, SKILL_PROMOTED 692, HYPOTHESIS_PROMOTED 1610 — all April, all in the rotated file; load_log reads current file only); hypotheses.jsonl empty is legit lifecycle (promoted hypotheses rewritten out, 4 rules exist) | module/index.db vs memory_events.jsonl id-set diff = 0/0; correspondence.db source census; standing_rules.jsonl 4 rows all [recovery-*] with confirmations 2–9; captains_log event census by month; captains_log.py:199-203 single-path load; knowledge_lens.py:216,278 rewrite-on-promotion | memory | cosmetic (positive) | confirmed | record as verified-good; the module-store discipline (state in-store, deterministic ids, shrink detection) is the pattern the other stores should copy |
| data-13 | Cosmetic litter: standing_rules last_applied never written (4/4 "", only dataclass default exists — rule application is untracked); stale sidecar .offset files contradict memory_bridge's "never writes sidecars" docstring; diagnoses.jsonl rows have no timestamp field (1366 rows — consumers can only join via loop_id); skills.jsonl 1 duplicate id; memory/2026-06-22.md is 0 bytes; gc_memory.py built but nothing schedules it (outcomes 645KB unrotated — growth modest) | knowledge_lens.py:53 sole last_applied writer is the default; medium/lessons.jsonl.offset + long/lessons.jsonl.offset mtimes Jul 7 vs memory_bridge.py:146-150 "never writes anything there"; diagnoses.jsonl key census (no ts key any row); skills 243 rows/242 ids; ls memory/2026-06-22.md = 0 bytes | memory | cosmetic | confirmed | fixed-inline candidates for a later chunk; none block 1.0 |

## Suspect verdicts (the four named suspects, explicitly)

- **Pre-fix goal_achieved poisoning**: partially reframed. Pre-Jun-12 rows are
  ABSENT (unjudged), not poisoned; the poisonable set is the 12 June True
  verdicts predating the 2026-07-02 judging fixes (data-08). The bigger issue
  is that outcomes.jsonl never got the field at all (data-02).
- **MEDIUM wholesale gc**: confirmed and generalized — not one bad catch-up
  cycle but a promotion channel that has never fired in any cycle (data-04).
- **A/B m3 contamination**: confirmed as already-disclosed-and-weighted in
  docs/history/2026-07-08-worker-slice-ab.md:95-99 ("m3's 4 cells have
  cross-run contamination and get low weight"); no separate store-side damage
  found beyond the A/B runs' ordinary (verdict-blind) outcomes rows. No new
  finding.
- **Hermes-trial outcomes without verdicts**: confirmed (data-07 rows +
  data-06 provenance gap).

## Census (coverage, not findings)

Stores enumerated and health-checked, whether or not they produced findings:
memory/ top-level — outcomes (1381), lessons (290), medium/lessons (116),
long/lessons (26), standing_rules (4), hypotheses (0 — legit), decisions (via
knowledge_lens path), knowledge_nodes (490), knowledge_edges (2124),
captains_log current (2545) + rotated Jun-11 (5.5MB), diagnoses (1366),
skills (243), skill-stats (136), skills.jsonl.bak (Apr), step-costs (3337),
step_traces (370), task_ledger (2650), calibration (8101 — escalation
telemetry, epoch-ts schema), preflight_calibration (376), events (7522),
suggestions (185, 37% applied_at), evolver-baselines (659 — written daily by
the loop for a consumer, evolver_scans.py:406, that has never run in prod),
verification_outcomes (58, frozen Apr 12), imports (3), medium/change_log
(10), persona-dispatch-log, handle_inputs, mission-log, canon_stats,
sandbox-audit, skill_provenance/ (Apr test litter), MEMORY.md, daily .md
logs (2026-04-03..07-09, May gap). Module store: index.db + memory_events.jsonl
(414/414). runs/: 672 dirs, 670 with metadata.json (2026-04: 2, 05: 476,
06: 141, 07: 51); goal_achieved coverage 0/478 pre-June, 38/141 June, 34/51
July. projects/: 231 dirs, 140 with NEXT.md, no lifecycle/GC, largest 34MB
(slycrelgo archive-run3); test-slug junk mingled with real persistent
projects. correspondence.db 16MB + 2 stale backups (Apr/Jul). Repo-local
memory/: frozen Apr 3–11 by design, no live reader found (fallbacks only,
data-10). Refuted before reporting: "2124 dangling edges" (wrong id key —
0 dangling); btc-price pollution in knowledge nodes (1 node only).
