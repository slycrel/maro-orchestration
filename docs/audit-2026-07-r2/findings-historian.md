# Purgatorio r2 eye 7 — forward historian (delta-scoped re-audit)

**Date:** 2026-07-10. **Scope:** the fix wave `4e6dc1b..HEAD` (21 commits, 2026-07-09/10)
vs the compiled record. **Method:** every prior finding re-probed against the current
tree (file reads, greps, `git show`, repo-local store counts) — no commit message or doc
claim accepted as resolution evidence on its own. SF-13 compliance checked by diffing
Claude auto-memory files touched 2026-07-09/10 against GOAL_BRAIN Decisions entries.
Decision batches #2 (`07852f5`) and #3 (`880b549`) cross-checked item-by-item against
the brief's buckets A–E.

## Part A — prior findings re-verified

| id | prior claim (short) | current status | evidence probed | note |
|---|---|---|---|---|
| hist-01 | Hermes-swap decision + iMessage preference never entered the repo record | **partially-resolved** | GOAL_BRAIN.md:1342-1353 (back-filled Decisions entry, cites hist-01, iMessage rider + item-(a) candidate note present); BUT GOAL_BRAIN.md:241-242 compiled truth still says "Hermes stance unchanged: steal-from-don't-migrate; adapter deferred", Threads:1506-1507 still lists "then the Hermes adapter decision" as remaining, MILESTONES.md:17 #0 still ends "steal-from-don't-migrate. SHELVED 2026-07-04: revisit ~next week" (`git log 4e6dc1b..HEAD -- MILESTONES.md` = only 0a0a5ad, untouched) | Decisions half done; the "update Threads #0" half of the disposition was not — see hist-r2-01 |
| hist-02 | ToS/licensing worry about the `claude -p` lane recorded nowhere while README recommends it | **resolved** | GOAL_BRAIN.md:1393-1396 (batch #2 item 5, cites hist-02, "most-tested label plus caveat"); README.md:16-20 ("most-tested path... check your plan's terms of service — that's between you and your provider") and README.md:122 (same caveat in quickstart) | framing matches Jeremy's decision exactly |
| hist-03 | v0.1.0/v0.2.0 + PUBLISH_CHECKLIST invisible to the 1.0 arc; versioning incoherent | **resolved** | docs/PUBLISH_CHECKLIST.md:1-10 (status flipped to `living`, "Adopted 2026-07-09... 1.0 gate scaffold; version is 1.0.0 at tag time (internal CHANGELOG 1.x numbering retired)"); GOAL_BRAIN.md:1387-1392 (batch #2 item 4); GOAL_BRAIN.md:148 + `34241d9` (live PyPI name checks recorded in the checklist) | residual: checklist absent from docs/INDEX.md map — hist-r2-04 |
| hist-04 | May data hole: POE_ORCH_ROOT repo-local pin named as mechanism | **resolved** | RECONCILIATION.md:206-221 ("Mechanism named by eye 7 (hist-04), partially verified... hole documented as unrecoverable"); my re-probe: repo-local `memory/outcomes.jsonl` grep '2026-05' = 0 (matches the recon note — May rows went to a since-cleaned checkout or were never written); r2 data eye independently confirms outcomes month census still has zero May rows | closed-as-documented per RECONCILIATION:389; nothing further owed |
| hist-05 | "run this prompt with this persona" reusable-pattern ask captured as prose, dropped as work | **still-open** | grep "run this prompt"/reusable-pattern/brain-trust over BACKLOG.md, MILESTONES.md, skills/, src/maro_assets/skills/ = only the pre-existing GOAL_BRAIN.md:1076 prose; NOT in the decisions brief either (bucket E = E1..E6: hist-02/-01/-06/-03/SF-13-rule/closure-noise — hist-05 absent) | fell through both disposition channels — see hist-r2-02 |
| hist-06 | Adversarial-review ship-skill decree at risk behind closed (e) checkbox | **resolved** | `0a0a5ad` (code_review graduated with provenance, "(e) remainder closed same day"); `ls src/maro_assets/skills/` = 12 files incl. code_review.md (verified directly); BACKLOG.md:898 records the hist-06 reopen; brief E3 updated to "RESOLVED same day" with hand-verification detail (3/3 planted bugs, red herring refuted) | decree satisfied by the graduated skill |
| hist-07 | Heartbeat-gate design conversation (2026-06-21/22) absent from repo record; SF-1 teed up without it | **partially-resolved** | BACKLOG.md:124-131 ("OpenClaw-heartbeat hook — never coded (hist-07 confirmed), design intent stands... fire Maro's tick from the HOST's heartbeat... app, not systemic") + GOAL_BRAIN.md:1414-1418 (batch #2 (c) reaffirmation) — the host-hook half is now recorded and SF-1 was decided WITH it on the table; the local-model-gate half (free qwen answers "is there work?", escalate to paid only on yes; context assembler is the real work) still has zero repo trace (grep "is there work"/local-model heartbeat over BACKLOG/GOAL_BRAIN = 0; heartbeat.py tiers are scripted→LLM→escalate, no local gate) | auto-memory project_heartbeat_gate_design.md remains the only carrier of the gate design |
| hist-08 | Budget-posture decree had no GOAL_BRAIN Decisions entry | **resolved** | GOAL_BRAIN.md:1354-1360 (back-filled entry, cites hist-08: $200+$20 ceiling, API-key lane DECLINED until budget-models phase, "Don't re-pitch") | |
| hist-09 | Failed-run-retry ask unlinked to the re-attempt hinter TODO | **resolved** | BACKLOG.md:45-48 (decision-prior indexer now stamped "this half is an OWNER ASK: Jeremy 2026-07-04, 'task failures being retried, with the old task context available...'; Purgatorio hist-09 — prioritize accordingly") | provenance stamp applied as disposed |
| hist-10 | CLAUDE.md "repo not renamed" wording contradicts the actual rename | **resolved** | CLAUDE.md GitHub line now reads "(renamed from openclaw-orchestration 2026-06-26; kept the `-orchestration` suffix rather than bare `maro`)" | reworded as disposed |

## Part B — delta sweep (new findings)

| id | claim | evidence | subsystem | severity |
|---|---|---|---|---|
| hist-r2-01 | **GOAL_BRAIN now contradicts itself on the Hermes stance, and the executable queue would re-run a revisit that already happened.** The 07-09 Decisions entry (1342) says the swap decision "supersedes the standing steal-from-don't-migrate stance for the substrate lane", but compiled truth (GOAL_BRAIN.md:241-242 "Hermes stance unchanged: steal-from-don't-migrate; adapter deferred") and Threads (1506-1507 "...then the Hermes adapter decision") were not updated, and MILESTONES.md:17 #0 still ends "SHELVED 2026-07-04 (Jeremy): revisit ~next week" — the revisit happened 07-09 (hermes-maro-trial, verdict positive) and the decision landed one section over. The currency rule ("GOAL_BRAIN wins") can't arbitrate a GOAL_BRAIN-vs-GOAL_BRAIN conflict. | GOAL_BRAIN.md:241-242 vs :1342-1353; :1506-1507; MILESTONES.md:17; `git log 4e6dc1b..HEAD -- MILESTONES.md` (only 0a0a5ad — #0 tail untouched) | docs | cosmetic |
| hist-r2-02 | **hist-05 (persona-prompt-pattern owner ask) fell through the crack between the two disposition channels and now has no path to Jeremy.** RECONCILIATION:398-400 splits eye-7 output into "factual back-fills applied 2026-07-09 (hist-01/08, hist-09 stamp, hist-10 reword)" and "everything else waits on the brief" — but the brief's bucket E carries only hist-02/-01/-06/-03 + the SF-13 rule + closure noise; hist-05 is in neither set. The ask stays prose-only (GOAL_BRAIN:1076), untracked as work, unsurfaced for decision — while the pattern was re-improvised by hand a 4th time by this very r2 re-audit (7 parallel eye prompts). The exact SF-13 failure class, recurring one day after the fix was decreed. | RECONCILIATION.md:394-400; docs/history/2026-07-09-decisions-for-jeremy.md §E (6 items, no hist-05); grep "run this prompt\|reusable pattern" BACKLOG.md/MILESTONES.md/skills/ = 0; GOAL_BRAIN.md:1076 (unchanged prose) | Quality/Self-improvement / docs | real-but-deferrable |
| hist-r2-03 | **The SF-13 standing rule itself is recorded only outside the surfaces sessions actually read.** The rule ("any Jeremy statement worth an auto-memory write also gets a GOAL_BRAIN Decisions line at session close") lives in RECONCILIATION.md:261+ (record-species audit doc), brief E5, and this box's Claude auto-memory — grep SF-13/auto-memory over GOAL_BRAIN.md and CLAUDE.md = 0. The start-of-session checklist (CLAUDE.md → GOAL_BRAIN → MILESTONES → BACKLOG) never encounters it; on any machine without this box's auto-memory the rule doesn't exist. The fix for the auto-memory/repo-record boundary is itself stranded on the auto-memory side of that boundary. | grep -n "SF-13\|auto-memory\|standing rule" GOAL_BRAIN.md CLAUDE.md (no rule hit); RECONCILIATION.md:261; brief E5; auto-memory project_purgatorio_audit.md carries the operative copy | docs | cosmetic |
| hist-r2-04 | **docs/INDEX.md doesn't map the newly-living PUBLISH_CHECKLIST.** `34241d9` flipped PUBLISH_CHECKLIST.md to `status: living` and adopted it as the 1.0 gate, but the question→doc map (which carries the status legend and is the "where do I find X" surface) has no "what gates the 1.0 release?" row — grep -i publish docs/INDEX.md = 0. | docs/PUBLISH_CHECKLIST.md:1-10; docs/INDEX.md (full read, no row) | docs | cosmetic |
| hist-r2-05 | **Brief bucket D4 (memory_bridge ingest-stats filename collision) is confirmed live in code and recorded nowhere outside the brief.** `src/memory_bridge.py:243` keys `stats["sources"][source_path.name]` — the three lessons.jsonl sources still collide in the per-source dict (aggregate `ingested` count stays correct, matching D4's "cosmetic" self-rating). D1–D3 all reached the BACKLOG (f) scorecard (lines 931-944); D4 did not, and no BACKLOG item exists (grep memory_bridge BACKLOG.md = 0). Note: the 07-08 offset-key fix (basename→resolved path) fixed the *offsets*, not this stats dict. | src/memory_bridge.py:243; grep memory_bridge BACKLOG.md = 0; brief D4; BACKLOG.md:931-944 (D1-D3 present) | Memory/Knowledge | cosmetic |

## SF-13 compliance check (charter-directed)

Auto-memory files written/updated 2026-07-09/10, cross-checked against GOAL_BRAIN Decisions:

| auto-memory carrier | Jeremy content | GOAL_BRAIN line | verdict |
|---|---|---|---|
| feedback_data_retention.md (07-10) | retention decree + goal-search rider | 1453-1488 (full entry, quotes match; rider → BACKLOG.md:200 #17 item exists) | **followed** — first post-decree Jeremy conversation, recorded same day (dd5e930/97aa5ef) |
| project_hermes_swap_plan.md (07-09) | Hermes swap + iMessage | 1342-1353 | followed (historian back-fill) |
| project_budget_posture.md | budget ceiling decree | 1354-1360 | followed (back-fill) |
| project_slack_bridge.md (07-09) | mothball decision | 1378-1381 (batch #2 (2)) | followed |
| project_thread_architecture.md (07-09) | 9 decisions resolved | 1226+ | followed (pre-decree, already recorded) |
| project_purgatorio_audit.md (07-09) | the SF-13 rule itself | — (no GOAL_BRAIN carrier) | see hist-r2-03 (rule targets Jeremy statements, so not a strict self-violation — but the rule's own persistence is auto-memory-side) |

**Verdict: the rule was followed in every applicable session since it was decreed** (one applicable Jeremy conversation on 07-10 — retention — plus the two decision-batch conversations, all with same-day Decisions entries).

## Decision batches #2/#3 vs brief buckets (charter-directed cross-check)

- **A1–A8:** ratified wholesale (GOAL_BRAIN:1363) + Rider A implemented default-ON (`ccc20fc`, skills-lite two-tier) + Rider B recorded as post-1.0 scheduler design. OK
- **B (g)/(h):** ratified as non-provisional (same line). OK
- **C1–C9:** every item has a DECIDED/RESOLVED annotation in both the brief and GOAL_BRAIN (supervision shim `d6c143b` incl. evolver run-cadence per his "on cleanup of a run" instinct — src/evolver.py:184,377; dockerize decided, SECURITY_MODEL rewritten honest `1d3b77e`; user/ executed `bf144fc`+`7c1086c`; scope bug fixed; slack mothballed; PyPI checked `34241d9`; CI shipped `2017d42`; README repositioned `83ede86`; SF-2 shipped `754d5ea`/`d6c143b`). OK
- **C10 smalls:** blocked_backend rides (h) design doc (BACKEND_RESILIENCE_DESIGN.md:207); refight_rule + nightly-eval recorded as BACKLOG #20 residuals (BACKLOG.md:119-122); ops-12 containers removed (`0281403`). OK
- **D:** D1–D3 recorded in BACKLOG (f) scorecard (931-944); **D4 not** → hist-r2-05.
- **E:** E1–E4 disposed (see Part A); E5 → hist-r2-03; E6 recorded (verifier-environment class feeds SF-2, BACKLOG.md:937).

## Clean checks (probed, no finding)

1. Retention-decree entry's shipped claims verified on disk: `tests/test_no_silent_deletion.py` exists; BACKLOG #17 carries the goal-search rider with Jeremy/2026-07-10 attribution (BACKLOG.md:200).
2. hist-04 verification note re-probed independently: repo-local memory/outcomes.jsonl = 0 May rows; r2 data eye's month census agrees (hole correctly documented as unrecoverable).
3. code_review skill physically present in the ship set (`ls src/maro_assets/skills/` = 12 files) — the (e) census claim is real, not manifest-only.
4. README ToS caveat present in BOTH the prerequisites and the quickstart code comment — the decision's "prominent but honest" framing implemented in full.
5. PUBLISH_CHECKLIST carries the git-history personal-data review as an explicit unchecked gate item pointing at Jeremy's deferred review — the C3 residual can't be lost behind a closed checkbox (the hist-06 lesson applied).
6. Batch #2/#3 GOAL_BRAIN quotes spot-checked against the brief's recorded quotes — no paraphrase drift in the entries examined.

## Notes

- The systemic observation from r1 stands upgraded: the *forward* half of SF-13 (session-close rule) demonstrably works — the retention decree is the existence proof. The *backward* half (disposing the five already-dropped items) is where the residue lives: hist-05 untracked (hist-r2-02), hist-07's gate-design half unrecorded, the rule itself un-compiled (hist-r2-03). All small; none blocks 1.0.
- No blocker-for-1.0 findings from this eye. The record for 07-09/10 is substantially accurate: 21 commits, and every decision-bearing one has a matching GOAL_BRAIN entry.
