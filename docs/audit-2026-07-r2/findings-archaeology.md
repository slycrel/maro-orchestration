# Purgatorio r2 eye 3 — backward archaeology, delta-scoped (findings)

**Date:** 2026-07-10. **Method:** re-probed every prior arch-* finding against
current code/config/store/branch state (never trusting commit messages or doc
claims as resolution evidence), then walked the 21 delta commits
(`4e6dc1b..97aa5ef`) for decisions from the 2026-07-09/10 batches that did not
fully reach code or record. Probes named per row. Prior file:
`docs/audit-2026-07/findings-archaeology.md`.

## Part A — prior findings, re-verified

| id | current status | evidence actually probed | note |
|---|---|---|---|
| arch-01 | **resolved** | `~/.maro/config.yml` — `scope_generation: true`, `scope_ab_skip` line removed with explanatory comment; handle.py:1282-1286 (absent flag = False = inject), handle.py:1429-1440 injects ResolvedIntent; live-probed: run `b90b3ff9-plucky-badger` SCOPE_GENERATED event 2026-07-10T02:31Z has `ab_skip: false` — first injected production run; DEFAULTS.md:91-92 rewritten honest (names the old mis-description) | Note: the six 07-09-evening dogfood runs (22:07–22:35Z, incl. `6dfaec5d` used for (f) findings) still ran control-arm `ab_skip: true`; the flip landed between 22:35Z and 02:31Z. Injected-arm organic evidence is n=1 so far. CLAUDE.md "LIVE since 2026-07-09" is accurate in local (MDT) time. |
| arch-02 | **partially-resolved** | DEFAULTS.md:91-92 + CLAUDE.md open-design-spaces row now record the 2026-04-22 A/B correctly (inject won, drove the flip). BUT BACKLOG.md:544-549 Phase 65 section still reads "MVE implemented, dormant... PAUSED by Jeremy 2026-04-23... the actual A/B measurement has not been run" — the false claim survives verbatim in the working queue doc | The corrected story lives in DEFAULTS/CLAUDE/GOAL_BRAIN; BACKLOG's Phase 65 header + A/B-blocker line were never touched by the fix wave. Currency-rule fix is one paragraph. |
| arch-03 | **resolved** (as dispositioned) | BACKLOG.md:315-321 — explicit tracked item "Orphan scope A/B datasets: adjudicate or write off (arch-03...)"; run dirs still present at `~/.maro/experiments/scope-ab-2026-04-25-v0/`, `-26-v1/` (ls probed, still no ANALYSIS.md) | Disposition was "backlog-item (adjudicate or explicitly write off)" — done. The record no longer contradicts the artifacts; the spend is now visibly on the books. |
| arch-04 | **resolved** | loop_finalize.py:586-590 — `run_skill_maintenance(adapter=adapter)` with comment "adapter threaded through (arch-04 fix, 2026-07-09)"; skill_lifecycle.py:688-690 refight block now receives an adapter on the production path; bonus vehicle: `evolver.run_cadence` (loop_finalize.py:613-637) set to `10` in `~/.maro/workspace/config.yml:10` | The decided refight mechanism can now actually fire per-run. |
| arch-05 | **still-open** | `grep run_nightly_eval src/` — sole caller remains heartbeat.py:739-740 (`_run_eval_bg`), reachable only from `heartbeat_loop` at `tick % eval_every` (heartbeat.py:1148-1163, daemon mode); `run_heartbeat` one-shot (heartbeat.py:492-597) has NO eval path; the accepted SF-1 posture is one-shot ticks with no recurring hook installed (BACKLOG #21: "hook itself stays uninstalled per decree") | Decision doc item C.10 says nightly-eval rewire "rides SF-1's vehicle decision"; SF-1 was decided (shim + run-cadence) and the evolver got its rewire — nightly eval did not. Under the decided posture it is still structurally unreachable. BACKLOG.md:1013 still carries the stale "wiring shipped Phase 42" closure note. |
| arch-06 | **resolved** | GOAL_BRAIN.md:491-497 — explicit "CORRECTION 2026-07-09 (Purgatorio arch-06)": the false in-process-heartbeat claim replaced with the accurate story (CLI-only reachability, never beaten in production, names the real in-process pieces) | Record healed with attribution. |
| arch-07 | **resolved** | Commit 18c2d85 (note: task brief's "18d2d85" is a typo) deleted `lat.md/poe-identity.md` entirely + all `[[poe-identity]]` backlinks and excised quality-gates.md layer 5; probed: `grep poe_self\|passes.py\|poe-passes\|poe-identity lat.md/` = zero hits; poe-identity.md absent from `ls lat.md/` | Fabricated nodes are out of the injection corpus. |
| arch-08 | **resolved** (adjudicated: descope + keep) | Decision batch #2/#9: knowledge web descoped-but-kept; BACKLOG.md:322-329 explicit KEEP item quoting Jeremy; `grep load_knowledge_edges src/` still only knowledge_web.py (by design now); README.md carries no knowledge-graph retrieval claim (grep probed) | The "silently dropped" part is cured — the drop is now a named, quoted, tracked decision. |
| arch-09 | **still-open** | `grep record_rule_wrong_answer src/` — still zero callers outside definition; `grep record_rule_wrong_answer\|auto-demote BACKLOG.md BACKLOG_DONE.md` — zero tracking anywhere | Sharp edge: SF-8's adjudication (decision #9) covered its siblings (arch-07 deletion done, arch-08 descope decided) but arch-09 fell out of the close-out with no BACKLOG line, no decision, no code change — the exact silent-drop pattern, now one generation deeper. |
| arch-10 | **still-open** | `git log main..origin/factory` — still 5 commits ahead (3cc7c5e..40467b6); `grep factory_full_sim\|origin/factory` over *.md, docs/, docs/history/ = zero non-audit hits; stash `temp-audit-skill-lifecycle-wip` still present (`git stash list`) | Listed in RECONCILIATION residuals; no delta commit touched it. Unadjudicated. |
| arch-11 | **resolved** | GOAL_BRAIN.md:1595-1600 — "CORRECTION 2026-07-09 (Purgatorio arch-11)": both thread-brain halves recorded SHIPPED 2026-07-03, stale open-question text replaced | But see arch-r2-02: the same doc grew a fresh instance of this exact staleness class the same night. |

## Part B — new findings (delta sweep)

| id | claim | evidence | subsystem | severity |
|---|---|---|---|---|
| arch-r2-01 | The **containerized-executor 1.0 decision has no vehicle**: batch #3 decision (2) "dockerize the executor path" is recorded in GOAL_BRAIN Decisions (:1435-1444) and SECURITY_MODEL.md §2 as "**Decided 1.0 direction**... DECIDED 2026-07-09, DESIGN PENDING — gets its own pass", but the design pass exists NOWHERE as work: `grep -i dockeriz/containeriz/sandbox` over MILESTONES.md + BACKLOG.md = zero executor-container items; GOAL_BRAIN Threads ("nothing leaves this list silently") has no such thread; the 1.0-arc thread's Remaining list (updated 772bb20) says escalation channel is "the only (a)–(h) item left"; and docs/PUBLISH_CHECKLIST.md — adopted that same night as **the living 1.0 gate** — has no container/isolation checkbox at all. A decided resolution to 9-blocker item #4 (no-sandbox-vs-SECURITY_MODEL) can be silently dropped and 1.0 tagged without it — the exact decision-without-vehicle pattern that produced arch-04/05. | docs/SECURITY_MODEL.md:57-86; GOAL_BRAIN.md:1435-1444, 1490 (Threads header), 1533-1538 (Remaining list); docs/PUBLISH_CHECKLIST.md (full read, no isolation item); grep MILESTONES.md+BACKLOG.md | Platform / record | blocker-for-1.0 |
| arch-r2-02 | GOAL_BRAIN's 1.0-arc thread **Remaining list is stale and self-contradictory as written in the 772bb20 close-out**: it lists "done-vs-achieved corpus analysis (~68 judged runs)" and "README/quickstart pass" as remaining, in the same sentence that calls the escalation channel "the only (a)–(h) item left" — but (b) done-vs-achieved is DONE with artifact (`docs/history/2026-07-09-done-vs-achieved.md`, exists on disk) and the README pass shipped twice (1d0707f quickstart revamp + 83ede86 repositioning, both verified in README.md). Anyone honoring the wins-by-decree rule would re-plan finished work — the same failure class as arch-11, reintroduced hours after arch-11's correction was written into the same file. | GOAL_BRAIN.md:1533-1538 vs MILESTONES.md:14 status sweep ((b) DONE, (d) DONE); ls docs/history/ (artifact exists); README.md:1-25 (repositioned headline probed) | docs | cosmetic |

## Severity roll-up

- **arch-r2-01 is the one that matters**: the fix wave resolved 9-blocker #4 by
  *deciding* the container and rewriting SECURITY_MODEL honestly — but the decided
  work has no queue item, no thread, and no checklist line. Both prior-pass
  dead-vehicle findings (arch-04, arch-05) started life exactly this way.
- arch-05 and arch-09 remain the two prior findings that are genuinely still open
  in code: nightly-eval is unreachable under the decided supervision posture, and
  `record_rule_wrong_answer` silently fell out of SF-8's close-out.
- arch-02's residue is one stale BACKLOG paragraph (Phase 65 header) — cheap fix.

## Clean checks (probed, no finding)

1. Scope injection live on-box: post-flip run `b90b3ff9` logs SCOPE_GENERATED
   `ab_skip: false`; config/code/DEFAULTS/CLAUDE.md all agree (arch-01 probe).
2. Retention decree fully landed: `keep_artifacts` gone from src/ (retirement
   comment only), `artifacts.auto_prune_days` sole prune path, DEFAULTS.md:94
   entry, just-finished-loop exemption in loop_finalize.py; deletion tripwire
   `tests/test_no_silent_deletion.py` **ran green** (with test_defaults_doc.py).
3. Defaults-registry decree holds through the wave: all four new keys
   (`sheriff.dormant_days`, `skills.lite_promotion`, `evolver.run_cadence`,
   `artifacts.auto_prune_days`) documented in docs/DEFAULTS.md; census test ran green.
4. Rider A skills-lite is real code, not just a decision line:
   `run_curation.promote_skills_lite`/`degrade_skills_lite` wired into the curator
   pass list (run_curation.py:489-491), default-ON per decree.
5. BACKLOG #18 verdict parity: `_closure_verdict_pass` called from both `maro run`
   (cli.py:564) and `maro resume` (cli.py:2055); the no-runs-dir residual is
   explicitly kept open in BACKLOG (lines 250-254), not laundered.
6. Bootstrap heartbeat crash-loop unit (blocker #5): the unit generator that
   exec'd the never-existed `sheriff.py --heartbeat` is gone; bootstrap.py:126-159
   now prints host-scheduler hook instructions around `maro heartbeat`.
7. CI (blocker #8) is genuinely alive: `.github/workflows/ci.yml` exists AND
   `gh run list` shows green runs on the latest main pushes (the one intermediate
   failure on 6c03068 was fixed by the next commit; 772bb20's suite-green note is
   corroborated).
8. README repositioning (blocker #9) landed as decided: accountability-first
   headline, honest status blurb, claude-CLI "most-tested path" + ToS-is-yours
   framing (batch #2 framing decree + batch #3 decision 3).
9. user/ neutral templates (blocker #3): zero `jeremy|slycrel` hits under user/;
   all three former repo-copy readers resolve workspace-overlay-first
   (GOAL_BRAIN:150 claim matches BACKLOG close-out and config.user_file usage).
10. SF-7 hermes trial containers: `docker ps -a` shows none (only OpenClaw's own
    two containers remain) — 0281403's claim verified live.
11. PUBLISH_CHECKLIST adopted with 1.0.0-at-tag + the PyPI name check recorded
    with specifics (bare `maro` squatted, `pymaro` = Microsoft) — matches 772bb20's
    compiled-truth entry.
12. maro_assets ships 12 skill symlinks incl. the graduated `code_review.md`; none
    dangle (ls -la probed); packaging census guards drift and dangling links.
13. `scripts/scope_ab_runner.py` survives the config cleanup: `set_scope_flags`
    inserts the `scope_ab_skip` line when absent (lines 38-58), so removing the
    flag from `~/.maro/config.yml` did not break the future re-run path.
14. Evolver LLM meta-cycle finally has a live vehicle: run-cadence block in
    loop_finalize.py + `run_cadence: 10` in the workspace config — the ops-02/arch-04
    "decisions ride a dead vehicle" storyline is materially closed on this box.
15. Batch #2 riders both tracked: official scheduler/timer (BACKLOG, post-1.0,
    auto-resume rides it) and knowledge-web read-side KEEP item present.
16. Repo hygiene at audit time: main == origin/main (nothing unpushed), working
    tree clean except this audit's own directory.

## Notes for reconciliation

- arch-r2-01 + arch-05 + arch-09 are one storyline continued from r1: decisions
  keep outrunning vehicles. The wave fixed the two *named* instances it was
  handed (arch-04 adapter, evolver cadence) and minted a new unnamed one
  (container design pass) the same night.
- arch-r2-02 is cosmetic but systemic: GOAL_BRAIN staleness (arch-11 class)
  recurred within hours of its own correction — the close-out ritual updates
  Compiled truth reliably but merges Remaining lists by hand.
- arch-01's residual worth one line somewhere: injected-arm organic evidence is
  n=1; the 07-09 dogfood corpus is all control-arm.
