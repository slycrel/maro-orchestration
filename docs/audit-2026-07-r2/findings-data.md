# Purgatorio r2 Eye 2 — Data/learning-store health (re-audit)

Probed 2026-07-10 ~07:30–08:00 UTC, read-only (sqlite `mode=ro`; no store
written; single-file test runs only: test_no_silent_deletion.py and
test_verdict_learning.py, both green). Delta audited: 4e6dc1b..HEAD
(21 commits), focused on d6c143b (verdict-aware learning), 754d5ea
(tri-state closure), ccc20fc (skills-lite), 97aa5ef/dd5e930 (retention
decree), plus data-01 purge residual health. Every status below is from a
live probe this session, not a commit message.

## Headline

1. **Verdict-aware learning (SF-2) is genuinely plumbed for outcomes.jsonl
   and its readers, with one live specimen** — the 2026-07-10T02:34 lighthouse
   run's outcomes row carries `goal_achieved: true, goal_verdict_source:
   "closure"` (the first verdict-bearing row in 1389). Consumers verified in
   code: evolver.py:128-143, inspector.py:561-566, recall.py:100-341,
   skills.py:258-263, metrics.py:465-524, attribution.py:362-368,
   evolver_scans.py:859-864, knowledge_bridge.py:60,129. 21/21 tests pass
   (test_verdict_learning.py). data-02 → **partially-resolved**: no backfill
   (by design), and the lesson/skill stores are still outside the loop (new
   finding data-r2-01).
2. **The residual verdict gap moved, it didn't close**: agenda-lane lesson
   extraction and skill crystallization run at finalize, BEFORE closure
   judges; the retro-stamp (`annotate_outcome_verdict`) rewrites only
   outcomes.jsonl. The tiered lesson store has zero verdict awareness
   (`record_tiered_lesson` has no verdict param; 0 `goal_achieved` refs in
   knowledge_web.py). Live: the judged-True lighthouse run's own lessons rows
   are verdict-ABSENT; 0/209 lessons.jsonl rows carry a verdict.
3. **data-01 purge held**: 0 junk in long (4/4 organic), top-level lessons
   (209 rows), medium (110 rows), module store (308 items; sqlite==JSONL
   308=308); backup intact at `memory/backup-2026-07-09-data01/` (996K).
   Provenance-era ingest guard still absent in memory_bridge (rider, not the
   finding core).
4. **Retention decree is real and tripwired**: every deletion site in src/
   AST-audited against a justified allowlist (test_no_silent_deletion.py —
   ran green); `artifacts.auto_prune_days` default 0 = never
   (loop_finalize.py:25-50); decay-GC and skill culls now archive-not-delete
   (knowledge_web.py:442-490); run-dir rmtree is user-CLI-only
   (run_curation.prune_run).
5. **Skills-lite shipped but unproven live**: promote_skills_lite is wired
   into CURATORS with default-ON config, but no completed run postdates
   ccc20fc — workspace skills/ overlay is empty (dir mtime Apr 11), 0 lite
   provenance rows, and the old promotion funnel is empirically unchanged
   (11/11 decay cycles promoted 0; LONG tier still exactly 4 organic rows).

## (A) Prior findings — re-verified

| id | prior claim (short) | current status | probe evidence |
|---|---|---|---|
| data-01 | Pre-isolation test junk live in injection paths | **resolved** | long/lessons.jsonl = 4 rows, all organic `[recovery-*]`; lessons.jsonl 209 rows / 0 junk; medium/lessons.jsonl 110 rows / 0 junk; module/index.db (mode=ro) 308 items, 0 LIKE dry-run/fixture; sqlite ids == memory_events.jsonl ids (308=308, diff 0); backup dir `~/.maro/workspace/memory/backup-2026-07-09-data01/` present, 996K, contains lessons.jsonl + fixtures copy + module/. Residual rider: memory_bridge.py still has no provenance-era ingest guard (grep: no era/cutoff/junk filter) — junk would re-ingest if it reappeared. |
| data-02 | Learning layer verdict-blind; 1381/1381 rows lack goal_achieved | **partially-resolved** | Write path: memory.py:380-450 (reflect_and_record ga/source/loop_id params), memory_ledger.py:320-334 `_verdict_row` tri-state, :455-520 `annotate_outcome_verdict` (locked_rmw, never overwrites provenance False); callers handle.py:921-936 (NOW, verdict-at-record), handle.py:1692 (provenance stamp), handle.py:1793 (closure stamp), cli.py:514, loop_finalize.py:506-524 (loop_id threaded). Consumers verified at evolver.py:128-143, inspector.py:566, recall.py:145-188+334-341, skills.py:261-263, metrics.py:465-524, attribution.py:368, evolver_scans.py:864. load_outcomes round-trips the field (memory_ledger.py:708-720). Live store: 1389 rows, exactly 1 with goal_achieved (2026-07-10T02:34 lighthouse, True/closure) — the only run finalized post-commit. No backfill by decree ("No backfill", d6c143b) → 1388 rows stay unjudged. Residual lesson-store gap split out as data-r2-01. test_verdict_learning.py 21/21 green. |
| data-03 | May 2026 = month-sized hole in learning record | **still-open** | outcomes.jsonl recorded_at month census today: {2026-04: 1272, 2026-06: 66, 2026-07: 51} — still zero May rows. Historical hole; nothing in the fix wave touches it (documented as SF-9 in RECONCILIATION.md:196-197). |
| data-04 | Consolidation-cycle promotion never fired (0 promoted / 10 cycles) | **still-open** | medium/change_log.jsonl now 11 cycles; 11th (2026-07-09T22:22:49, total 120, decayed 97) again `promoted_ids: []`. long/lessons.jsonl still 4 rows. Post-decree change: decay-GC now archives before dropping (knowledge_web.py:442-490) but no cycle has run since 97aa5ef — lessons_archive.jsonl does not exist yet. Skills-lite (ccc20fc) is a NEW promotion channel, distinct from this funnel (see data-r2-02). |
| data-05 | Knowledge web inert beyond node appends | **still-open** (one sub-claim corrected) | knowledge_edges.jsonl: 2124 edges, all created 2026-04 (frozen import set); nodes 490→511, times_applied>0 still 1/511; candidates grew 174→195 with no validation path. CORRECTION to prior evidence: `load_knowledge_nodes` HAS defaulted to `status=NODE_ACTIVE` since 2026-04-14 (git blame knowledge_web.py:1353, filter at :1368), so query_knowledge does serve active-only — the "never filters status" sub-claim was wrong; the inertness core stands. |
| data-06 | Imported ledger rows carry zero provenance | **still-open** | workspace_import.py `import_ledgers` (lines 91-123) still appends raw source lines with no per-row stamp; only run dirs (`imported_from` in metadata + imported_from.json, :80-90) and daily logs (:135 marker) are marked — both predate the fix wave (sole commit ff02f6e). imports.jsonl still 3 rows. |
| data-07 | Hermes-trial outcomes in store without verdicts | **still-open** | The 5 trial rows (haiku/couplet goals, 2026-07-09) remain goal_achieved-ABSENT — store census: only 1/1389 rows has the key. d6c143b explicitly chose "No backfill"; the prior disposition's "backfill-or-flag when verdict machinery lands" did not happen. Trial containers removed (0281403) but the rows persist. |
| data-08 | 12 pre-fix June True verdicts indistinguishable from post-fix | **still-open** | runs metadata census: 38 June judged rows, 38 have goal_verdict_source, 0 have judged_at/verdict_version/any provenance-era stamp; the new Jul-10 judged run also carries no judged_at — verdict-version stamping was not added by the fix wave. |
| data-09 | skills.jsonl accretion without application | **still-open** (dup id resolved) | 261 rows (was 243; +77 created in Jul), 261 unique ids (prior 1 dup gone), tier {provisional: 261}, circuit {closed: 261}, use_count>0 in 2. Culling now archives (97aa5ef) — skills_archive.jsonl doesn't exist yet (no cull since). Application side still never exercised. |
| data-10 | cwd-relative memory/ fallbacks (lying-fallback design) | **still-open** | All sites unchanged, re-grepped: thinkback.py:160 `Path("memory/outcomes.jsonl")`; constraint.py:324, eval.py:408, introspect.py:168+176, graduation.py:173+181, scheduler.py:55, orch_items.py:230 all `Path.cwd()/"memory"`. Repo-local memory/ still frozen (newest file Apr 11; dir untouched since Jul 3). |
| data-11 | dev-recall session/telegram ingest 84 days stale | **partially-resolved** | correspondence.db ingest_meta (mode=ro): last_ingest_sessions_utc = 1783636489 (2026-07-09T23:14Z — sessions lane re-ingested, was Apr 16); last_ingest_telegram_utc still 1776370114 (2026-04-16). |
| data-12 | Verified-good: module store discipline etc. | **resolved** (still good) | Re-probed: module sqlite==JSONL 308=308 ids post-purge-rebuild; offsets in schema_meta (no sidecars written by bridge); shrink detection confirmed in code (memory_bridge.py:160-165) — relevant live: medium/lessons.jsonl stored offset 81862 > current size 78973 after decay-gc; next ingest resets to 0 with deterministic-id dedupe, by design. |
| data-13 | Cosmetic litter list | **still-open** | standing_rules last_applied still ""×4; stale sidecar .offset files still present (medium+long, mtime Jul 7) contradicting bridge docstring; memory/2026-06-22.md still 0 bytes. (gc_memory now allowlisted as user-invoked CLI, dry-run default — that item improved.) |

## (B) New findings (r2 sweep)

| id | claim | evidence | subsystem | severity |
|---|---|---|---|---|
| data-r2-01 | Verdict-aware learning stops at outcomes.jsonl: agenda-lane lesson extraction + per-run skill crystallization happen at finalize BEFORE closure judges, the retro-stamp touches only the outcomes row, and the tiered lesson store has no verdict fields at all — so a done-but-judged-False agenda run's success-framed lessons stay in the injected MEDIUM tier at full confidence forever, and 0/209 lessons.jsonl rows carry any verdict (including the judged-True lighthouse run's own 2 lessons) | loop_finalize.py:506-524 (reflect_and_record before closure; comment says verdict "unknown here"); lesson tier write memory.py:419-432 → record_tiered_lesson has no verdict param (knowledge_web.py:185-197; `grep goal_achieved knowledge_web.py` = 0 hits); annotate_outcome_verdict rewrites outcomes.jsonl only (memory_ledger.py:455-520); skill crystallization fires at finalize on `loop_status=="done"` (loop_finalize.py:541-544) so extract_skills' judged-False filter (skills.py:258-263) can't see a verdict that doesn't exist yet; live store: lessons.jsonl 209 rows, 0 with goal_achieved key; medium/lessons.jsonl 110 rows, 0 with verdict; outcomes row 2026-07-10T02:34 stamped True but its lessons unstamped | memory + quality/self-improve | real-but-deferrable |
| data-r2-02 | Skills-lite promotion (Rider A, default-ON) is shipped and unit-tested but has never fired in production — the promotion-funnel half of SF-10 is still empirically at zero on every channel: 11/11 decay cycles promoted 0, LONG tier still 4 rows, workspace skills overlay empty, 0 skills-lite provenance rows | run_curation.py:243-491 (promote_skills_lite in CURATORS, `skills.lite_promotion` default True, DEFAULTS.md:61 row); live: `~/.maro/workspace/skills/` contains no .md (dir mtime Apr 11 17:47), skills.jsonl 261 rows with 0 lite/companion-provenance entries; newest completed run b90b3ff9 started 2026-07-10T02:31Z = 20:31 MDT, BEFORE ccc20fc (21:37 MDT) — zero post-commit runs, so "promotion actually fires" cannot be confirmed live; change_log 11/11 promoted_ids [] | quality/self-improve | real-but-deferrable |
| data-r2-03 | Ledger rewrites silently narrow file permissions to 0600: atomic_write uses mkstemp (0600) + os.replace with no perm preservation, so any locked_rmw rewrite (verdict annotation, decay cycles, reinforcement) flips a 0644 ledger to owner-only — observed live on outcomes.jsonl and evolver_cadence.json (0600) beside 0644 siblings; harmless single-user today, a read-denial footgun once the dockerized executor (decided for 1.0) or any different-uid reader touches the workspace | file_lock.py:212-233 (mkstemp, no chmod/copystat); `ls -la ~/.maro/workspace/memory/`: outcomes.jsonl -rw------- (rewritten by annotate_outcome_verdict 2026-07-09 20:36), evolver_cadence.json -rw-------, vs lessons.jsonl etc. -rw-r--r-- | platform/memory | cosmetic |

## Clean checks (probed, nothing wrong)

- Retention decree end-to-end: deletion-site AST tripwire green
  (test_no_silent_deletion.py, ran this session); allowlist entries all
  user-invoked/ephemeral/opt-in; `artifacts.auto_prune_days` default 0 =
  never, just-finished loop always excluded (loop_finalize.py:25-70);
  checkpoint kept on completion (allowlist pin: only checkpoint.py deletes).
- data-01 backup intact per retention decree (996K, original junk preserved).
- Tri-state discipline in serialization: `_verdict_row` omits (never nulls)
  unjudged verdicts, matching the 1381-row precedent; closure_unverifiable
  never sets goal_achieved and never overwrites a provenance False
  (memory_ledger.py:320-334, 500-510; test_verdict_learning.py 21/21).
- Module-store shrink detection works as designed for the live
  offset-past-EOF condition on medium/lessons.jsonl (post-decay-gc):
  detected → full re-ingest with deterministic-id dedupe.
- evolver.run_cadence live config = 10 (workspace config.yml), counter
  ticking (evolver_cadence.json runs_since_evolve=1); counter increments
  even at cadence 0 so later enablement works (evolver_store.py:132-162).
- No fix-wave writes leaked into repo-local memory/ (newest file Apr 11).
- captains_log/medium tier junk: still 0 post-purge; imports.jsonl still 3
  rows (no new unstamped imports).
