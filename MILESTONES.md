# Milestones — Prioritized Work Queue

What to do next, in what order. Updated each session. Strategic phases live in ROADMAP.md; deferred ideas live in BACKLOG.md. This file is the bridge — the executable queue.

Last updated: 2026-04-14 (session 27 — dev round 4 closed #12/#23/#24, first-class project isolation)

---

## Next Up

- **BACKLOG cleanup** — Review and close stale BACKLOG items from earlier sessions.
- **Evolver confidence calibration follow-up** — `scan_suggestion_outcomes` wired; verify calibration is improving (check live workspace suggestion stats).
- **Grok response 3 steal items** — Bitter Lesson Engineering analysis: PAI comparison, thin-harness audit. Most items already shipped. Key remaining: ensure Mode 2→3 bridge is solid (signals→sub_mission flow).

## Done (session 27, 2026-04-14 — dev round 4 closed #12/#23/#24, first-class project isolation)

- [x] **Dev round 4** — Closed #12 (rate limit test for /api/recipes), #23 (HTML edit blank-name guard), #24 (photo_url/review_text length validators). 9 new tests: rate limit, field validators, HTML blank-name class. All 52 recipe tests pass.
- [x] **First-class project isolation** — `Skill.project` field (""/global or project slug). `find_matching_skills(project=...)` filters to global + project-specific. `set_loop_running(project=...)` writes per-project lock. `get_running_project_loop()` + `is_project_running()` API. 11 new tests. Closes BACKLOG item.

## Done (session 26, 2026-04-14 — codebase graph, injection guard, harness spectrum, PM/dev rounds 3)

- [x] **Codebase Graph** — `src/codebase_graph.py` (39 tests). AST-based Python call graph; 5-pass algorithm (collect → parse → resolve imports → centrality → rank). Basename import resolution (`from llm import ...` → `llm.py`). Centrality = 0.7×in_degree + 0.3×line_coverage. Goal-biased keyword ranking in `format_graph_context()`. Wired into `_build_loop_context()` (fail-open). `poe-codebase-graph` CLI. Verified: `llm.py` tops centrality (54 importers), `agent_loop.py` in top 10.
- [x] **Prompt injection guard** — `src/injection_guard.py` (59 tests). 17 regex patterns across 3 categories (override, tool-call, exfil). `InjectionScanReport` with risk_level + safe_to_auto_apply. Wired into: evolver `apply_suggestion()`, persona `scan_personas_dir()` YAML loading, persona `create_freeform_persona()` goal scanning. Fail-closed (returns False on exceptions).
- [x] **Dev run 2 closed** — #17 (json.loads crash), #18 (test isolation conftest.py), #19 (rating DB constraint) all closed.
- [x] **Harness Architecture Spectrum** — Friction scan wired into inspector heartbeat tick (heuristic, no LLM, runs alongside inspector). Inspector friction summary injected into quality gate Pass 1 user message. Closes BACKLOG P7/10. NOW lane intentionally thin by design; AGENDA has pre-flight + quality gate + post-hoc inspector. Injection guard wired into synthesize_skill() in evolver.
- [x] **user/GOALS.md** — Created user/GOALS.md (north star + active + medium-term goals + values). Wired into planner.py user context injection alongside CONTEXT.md + SIGNALS.md. Director now sees Jeremy's current goals when decomposing missions.
- [x] **Longitudinal evolver impact analysis** — `scan_evolver_impact()`: for each EVOLVER_APPLIED event, compare stuck_rate before vs after. `format_impact_summary()` shows delta. `poe-evolver impact` CLI subcommand. Closes K6 "self-improvement validation" gap. 9 tests.
- [x] **PM round 3 + dev round 3** — PM-3 filed #21-25 (Pydantic validation, rate limit regression, SQL exception handling, field length limits, HTML form blank-name). Dev-3 targeting #22/#21/#25.

## Done (session 25, 2026-04-14 — repo scan, BLE, SIGNALS.md, PM/dev round 2)

- [x] **Repo stack auto-detection** — `src/repo_scan.py` (53 tests). Scans repo for tech stack via 50+ file indicators, deep-scans requirements.txt/package.json for frameworks. Wired into `_build_loop_context()` via project slug heuristic + `--repo` CLI flag. `repo_path` threaded through run_agent_loop → LoopContext → _build_loop_context (fail-open).
- [x] **Bitter Lesson goal rewriter** — `rewrite_imperative_goal()` in intent.py. Strips prescribed execution steps from imperative-heavy goals, rewrites as outcome-focused. Wired into AGENDA path in handle.py before clarity check. `_IMPERATIVE_MARKERS` heuristic avoids LLM cost for already-clean goals. 15 tests.
- [x] **SIGNALS.md → evolver signal alignment** — `_load_user_signals()` reads user/SIGNALS.md. `scan_outcomes_for_signals()` now passes user research priorities as context when proposing sub-missions. Closes the Mode 2→3 loop: factory-mode signal proposals align with user-declared interests. 5 tests.
- [x] **PM round 2** — Filed 5 new issues (#16-20) on orchestrator-test-recipes: N+1, json.loads crash, test isolation, rating constraint, review dedup.
- [x] **Dev run 1** — Closed #16 (N+1 query), #13 (FTS), #11 (timestamps). Plus previous session fixes for #7, #10.

## Done (session 24, 2026-04-14 — K4 write path)

- [x] **K4: Knowledge write path** — `src/knowledge_bridge.py`: `outcome_to_knowledge()` heuristic + LLM extraction of insight/principle/pattern nodes. Dedup via Jaccard ≥0.7 on titles. `upsert_knowledge_from_candidate()` updates confidence on re-validation. `record_skill_evolution()` wired into evolver promote/demote paths. `validate_principle()` for bidirectional validation (validated/contradicted). `reflect_and_record()` in memory.py now calls `outcome_to_knowledge()` as non-blocking hook (fail-open). 27 tests. Closes BACKLOG K4 item + docs/knowledge-layer/README.md updated.

## Done (session 23 continued, 2026-04-14 — input classification, Phase 61 complete, LoopStateMachine, sub_mission enqueue)

## Done (session 23, 2026-04-14 — Phase 61, lesson dedup, phase audit, Stage 3→4, input classification)

- [x] **Memory Stage 3→4 regression tests** — 3 tests: `test_step_outcome_has_result_attribute`, `test_skill_extraction_fires_when_not_dry_run`, `test_skill_extraction_outcome_uses_step_result`. Skill extraction exceptions upgraded debug→warning.
- [x] **Lesson deduplication** — `deduplicate_lessons()` two-pass (exact + Jaccard ≥0.8). `--cleanup-lessons`/`--dry-run` in doctor.py. 5 tests.
- [x] **Phase audit** — Verified phases 44-62: all implementations confirmed real. Phase 45 "never closed" note was stale. No phantom phases found.
- [x] **BACKLOG cleanup** — Closed artifact routing, orch.py tests, HIGH coverage item, inspector threshold asymmetry, Phase 45 note. Updated 6 items.
- [x] **Phase 61 integration depth** — 12 new tests across 4 classes: checkpoint recovery (3), memory injection (2), AGENDA lane heartbeat e2e (3), FailoverAdapter chain (4). Integration total: 31→49.
- [x] **Input classification tag** — `classify_input_type()` in captains_log.py (url/code/structured_data/plain_text). `INPUT_MISMATCH` + `METACOGNITIVE_DECISION` event constants. `update_skill_utility()` logs INPUT_MISMATCH when circuit opens on url-skill-vs-non-url-input domain mismatch. `attribute_failure_to_skills()` threads step_text through. 9 tests. EVENT_TYPES 28→30.
- [x] **LoopStateMachine conversion** — `LoopStateMachine` now inherits `LoopContext`; `set_phase` is an instance method (`ctx.set_phase(X)` replaces `LoopStateMachine.set_phase(ctx, X)`). 6 call sites updated in `run_agent_loop`, 8 test functions updated + 1 new subclass check. `_initialize_loop` creates `LoopStateMachine()` instead of `LoopContext()`. Eliminates two-class pattern; ctx IS the state machine now.
- [x] **sub_mission auto-enqueue** (Mode 2→3 bridge) — `_apply_suggestion_action()` now handles `sub_mission` category. `evolver.auto_enqueue_signals=True` → `enqueue_goal()` on the heartbeat queue. Default: hold for review, record to playbook `Signals` section. Config-gated opt-in. 3 tests.

1. ~~**Memory Stage 3→4 (verify extraction in live runs)**~~ — DONE (session 23). Added 3 regression tests: `test_step_outcome_has_result_attribute` (guards s.result attr), `test_skill_extraction_fires_when_not_dry_run` (verifies extract_skills is called), `test_skill_extraction_outcome_uses_step_result` (verifies step result data flows correctly). Upgraded skill extraction log.debug → log.warning so failures surface. Bug is verified closed.
2. ~~**K2 follow-up: Import links collection**~~ — DONE (session 22). `import_link_farm()` + `poe-knowledge import-links` CLI. 315 nodes already in workspace. +9 tests.
3. ~~**Director persona authoring skill**~~ — DONE (session 22). `record_persona_dispatch()` logs each selection with is_fallback flag. `scan_persona_gaps()` surfaces recurring unmatched roles. Wired into `run_evolver(scan_persona_gaps=True)` as persona_authoring Suggestions. +6 tests.
4. ~~**Evolver confidence calibration**~~ — DONE (session 22). `_record_suggestion_outcomes` + `scan_suggestion_outcomes` wired into run_evolver. 6 tests.
5. ~~**11 unlocked bare-append JSONL paths**~~ — DONE (session 22). Added `locked_append()` to file_lock.py; converted 11 highest-traffic sites (captains_log, memory_ledger×5, metrics, evolver×4, inspector×2). +5 tests.

## Done (session 22, 2026-04-14 — stale hash cleanup, event-driven wakeup, constraint fix, FailoverAdapter)

- [x] **Cross-backend failover on 4xx/5xx (FailoverAdapter)** — `build_adapter("auto")` now returns `FailoverAdapter` when multiple backends are available. Tries each in priority order; 402/401/403/5xx errors trigger failover to next backend. Single-backend case returns adapter directly (no overhead). 14 tests. Closes BACKLOG P2.
- [x] **Constraint false-positive on step descriptions** — Two-part fix: (a) decompose prompt gets STEP DESCRIPTION STYLE section — "describe the task, not shell commands"; (b) `hitl_policy(is_description=True)` downgrades DESTROY tier → WRITE and caps HIGH risk at MEDIUM for step descriptions. Step_exec.py passes `is_description=True`. 3 tests. Closes BACKLOG item.
- [x] **Event-driven subprocess wakeup** — `run_agent_loop` calls `post_heartbeat_event("loop_done", payload=project)` after releasing the loop lock. Heartbeat's `_wakeup_event.wait()` unblocks immediately → next task picked up in near-zero time. 3 tests. Closes MILESTONES item.
- [x] **Stale test skills in workspace** — `poe-doctor --cleanup-skills` now detects skills where `compute_skill_hash(skill) != stored_hash` (test fixtures that leaked in). Ran on live workspace: 15 stale-hash + 2 duplicates removed, 14 real skills remain. `_skill_hash_is_stale()` helper for testability. `skills_path` kwarg for testing. 6 tests. Closes BACKLOG item.

## Done (session 21, 2026-04-14 — budget bump + exception sweep + LoopStateMachine + Stage 2→3 pipeline + skill extraction fix + NOW→Director escalation)

- [x] **Eval flywheel train/test split** — `_train_test_split_patterns()`: oldest 70% → train (suggestion generation), newest 30% → holdout. Skips split when < 4 patterns. `run_eval_flywheel` now has `test_fraction=0.3` param and surfaces `patterns_train`/`patterns_test` in summary. Prevents gaming eval metric by training on the same patterns tested. 5 tests.
- [x] **Eval flywheel pass-rate dashboard** — `_read_eval_trend()` in observe.py; wired into `_snapshot_json()` as `eval_trend` key; new HTML panel "Eval Pass Rate" in `poe-observe serve` dashboard shows last 10 runs with builtin score, gen pass rate, trend direction (improving/declining/stable badge). 4 tests. Closes MILESTONES eval-flywheel hardening.
- [x] **Artifact output routing cleanup** — Per-step artifacts (`loop-{id}-step-*.md`) auto-deleted at loop end by default. Config `keep_artifacts: true` retains them. Permanent files (PARTIAL.md, plan.md, loop log, scratchpad) always kept. 3 tests. Closes MILESTONES artifact-routing item.
- [x] **pytest-via-subprocess timeout fix** — Root cause: 900s wasn't enough for full test suite (pytest ~100-300s on this hardware + Claude response time). Bumped to 1800s default for long-running steps, 3600s for full-suite (`tests/ ` hint). `POE_LONG_RUNNING_TIMEOUT` env var for override. Improved timeout log message identifies `full_suite` vs `long_running`. 5 tests. Closes MILESTONES #2.
- [x] **~35 silent exceptions upgraded (agent_loop.py lines 1000+)** — `except Exception: pass` upgraded to `log.warning` (learning data loss: diagnosis lesson, plan manifest) or `log.debug` (optional context injections, adapter fallbacks, lifecycle telemetry). 4 safety-critical bare-pass sites kept. No behavior change — failures now surface in debug logs. Closes MILESTONES #3.
- [x] **Recovery mid-loop budget bump** — When 75%+ of `max_iterations` consumed, >2 steps remain, and ≥50% of steps done: bumps `max_iterations` by 50% (min +10), fires at most once (`_budget_bumped` guard). Logs `METACOGNITIVE_DECISION` to captain's log. Prevents hard synthesis fallback when good progress is in flight. 5 tests. Closes MILESTONES #6.

## Done (session 21, 2026-04-14 — LoopStateMachine + Stage 2→3 pipeline + skill extraction fix + NOW→Director escalation)

- [x] **Stage 2→3 crystallization pipeline** — `scan_canon_candidates()` in evolver.py surfaces long-tier lessons with 10+ applies across 3+ task types as `crystallization` Suggestions. Wired into `run_evolver(scan_canon=True)` (default on). `apply_suggestion` explicitly holds crystallization for human review (never auto-applies). 7 tests. Closes BACKLOG MODERATE "Memory Stage 2→3."
- [x] **Stage 3→4 skill extraction bug** — `extract_skills()` was silently failing every run due to `s.summary` / `s.step` attribute errors on StepOutcome (which has `.result` and `.text`). Fixed attribute names. `reflect_and_record` outer except upgraded to log.warning. 1 regression test. Skill crystallization now actually fires.
- [x] **NOW → Director escalation** — `_is_complex_directive()` heuristic (>25 words, multi-step language, complex verbs + 8+ words, multiple sentences). Config flag `now_lane.escalate_to_director` (default False, opt-in). When enabled, complex NOW-classified goals reclassify to agenda and get full Director pipeline. 10 tests. Closes MILESTONES #3.

## Done (session 21, 2026-04-14 — LoopStateMachine)

- [x] **LoopPhase state machine** — `LoopStateMachine` class with `_ALLOWED` transitions dict; `set_phase(ctx, phase)` raises `InvalidTransitionError` on invalid transitions. Wired into `run_agent_loop` at all 7 phase boundaries (A→B, B→C, C→D, D→E, E→F, F→G, plus FINALIZE on early exits). `LoopContext.phase` field was present but never set — now set at every transition. 8 tests. Closes BACKLOG CRITICAL finding 3.3.

## Done (session 20.5, 2026-04-14 — fix sprint, 10 commits)

Adversarial-review-driven cleanup. 8 of 14 findings fixed, 2 verified hallucinated, 4 deferred (above).

- [x] **claim_verifier path truncation** (`a34228b`) — Regex lookbehind tightened. 4 regression tests for backtick/quote/paren/word-adjacent wrappers.
- [x] **Evolver `cost_optimization` held for review** (`4b8dd7e`) — Explicit branch sets `applied=False`, `status=pending_human_review` instead of falling through.
- [x] **Evolver auto-revert on verify failure** (`4b8dd7e`) — `_verify_post_apply` tracks `applied_ids` and iterates `revert_suggestion` on test failure. Closes worst self-improvement-safety hole. +3 tests.
- [x] **Silent exception swallowing in agent_loop first 1k lines** (`d8364a6`) — 14 sites surfaced. ERROR for safety/security/correctness (kill switch, interrupts, security scan, hooks); WARNING for resumption-affecting (checkpoint, manifest, dead_ends, claim verifier, skill outcome); DEBUG for telemetry.
- [x] **Inspector 3 false-positive mechanisms** (`f0f6e36`) — Escalation tone (split tautological vs informative keywords, ≥2 informative threshold), backtracking (sort by created_at chronologically), context_churn (require ≥2 lessons + no keyword overlap with stuck narrative). +5 tests.
- [x] **Coverage floor + concurrency tests + unskip router** (`719ee1d`) — pytest-cov wired with 70% fail_under in `.coveragerc` (current 73%). Polymarket_backtest scripts excluded. `scripts/test-cov.sh` opt-in wrapper. +5 task_store concurrency tests (threaded race, multiprocess race, stale recovery, concurrent enqueue, serialized claim/complete). Installed sklearn → 29 router tests no longer skipped.
- [x] **scripts/test-safe.sh collection** (`880a4c5`) — pytest `--collect-only -q` format changed to `path: count`; script now parses both nodeid and file-level output. Fixed.
- [x] **march-of-nines false alerts** (`d95bed1`) — Replaced `(rate)^N` cumulative product with sliding window over last 5 outcomes. Healthy long runs no longer fire. Extracted `_compute_march_of_nines` helper. +4 unit tests.
- [x] **`_process_blocked_step` 21 → 2 params** (`d95bed1`) — Introduced `BlockedStepContext` dataclass. Body unchanged via local-binding unpack at top.
- [x] **`_steps_are_independent` regex too narrow** (`d95bed1`) — Expanded `_DEPENDENCY_PATTERNS` to catch aggregation verbs (compile/synthesize/aggregate/summarize/analyze) and generic prior-output references. +1 test with 7 case-table entries.
- [x] **`new_guardrail` permanently gated** (`50ae71f`) — Auto-applies in non-prod by default; prod hold; `POE_AUTO_APPLY_GUARDRAILS=0/1` overrides. +3 integration tests.

## Hallucinated findings (verified, no fix needed)

- **3.4 Director bypassed** — `skip_if_simple` defaults to `False` (not `True`). NOW lane skips Director entirely by design. Real architectural question logged in BACKLOG.
- **3.14 Persona auto-selection missing** — Already exists at `persona.py:793` (`persona_for_goal`); called from `handle.py:615` in AGENDA flow. Keyword routing + LLM fallback + freeform creation. NOW lane skips by design (1-shot path).

## Done (session 20, 2026-04-14)

- [x] **5 parallel live regression goals** — peptaura-peptides (411K tok, 9/9), polymarket-edges incremental first run (1.28M tok, 8/8), recipe-pm (721K tok, 8/8, 5 issues filed #11–15), recipe-dev (572K tok, 8/8, #14 closed), adversarial-review blind (1.39M tok, 45.7min, 14 findings). All green, all escalated to mid-tier quality gate.
- [x] **Polymarket-edges incremental workspace pattern validated** — `~/.poe/workspace/projects/polymarket-edges/` git-initialized, first run deepened Edge 04 (hypothesized→evidenced, N=65 markets) and added Edge 08 (negRisk cross-market arb). Pattern: read ledger, deepen 1, add 1, commit. Compounds across runs.
- [x] **Phase 62 adaptive replanning validated live** — adversarial review's step-16 subprocess timeout correctly diagnosed as `adapter_timeout`; mid-loop replanning recovered via 3 sub-steps (`--lf`, `tail`, `head`) with zero manual intervention.
- [x] **14 adversarial findings logged to BACKLOG** — 3 CRITICAL, 4 HIGH, 5 MODERATE, 2 MINOR. Top 4 elevated to MILESTONES "Next Up".

## Queued

3. ~~**Real-world regression tests**~~ — DONE (session 18). 4 goals run, PM + dev agents tested. Results documented.

9. **Artifact output routing cleanup** — Temp artifacts (per-step) → tmp dir (deleted by default, kept via config `keep_artifacts: true`). Permanent outputs → `~/.poe/workspace/output/`.

10. **K2 follow-up: Import links collection** — Knowledge node infrastructure built. Next: import enriched posts.

11. **Eval flywheel hardening** — Failure clustering, train/test split, pass-rate dashboard.

12. **Local LLM research** — Tiny LLMs for bundling with orchestrator or self-hosting on cheap hardware. Reduce API dependency for cheap-tier work.

13. **Event-driven subprocess wakeup** — Replace polling with asyncio.Queue signal. (7/10)
14. **Phase 63: Auto persona+skill packaging**
15. **Codebase Graph + LSP** — Pre-build call graph; LSP-guided context slicing. (9/10, longer term)

## Done (session 19, continued)

- [x] **Phase 59 real cost computation** — `record_skill_outcome()` now gets real `cost_usd` from `metrics.estimate_cost(tokens_in, tokens_out, model)`. Model is the per-step adapter's `model_key` (tier overrides reflected). Plumbed via new `step_model` kwarg on `_process_done_step`. Commit 3dd3e3d.
- [x] **Wire diagnosis into mid-loop blocking** — `_handle_blocked_step` now consults `diagnose_loop()` after 2 retries. Maps 4 failure classes to targeted actions: retry_churn→redecompose, decomposition_too_broad→redecompose, empty_model_output→retry-with-tool-call-hint, constraint_false_positive→retry. Closes Gap 1 from PHASE_AUDIT. +8 tests. Commit edaceda.
- [x] **Clean workspace skills** — 41 orphan skills in ~/.poe/workspace/memory/skills.jsonl, 4 content_hash groups with duplicates. One-shot cleanup (kept 31 unique), grouped by content_hash, scored by creation time + success metrics. Added `poe-doctor --cleanup-skills` flag. Reduces ~100 lines log spam per goal.
- [x] **Add poe-doctor check for workspace skills duplicates** — Phase 62 enhancement, detects duplicate content_hash in workspace skills, reports findings with cleanup command. Validates dedup after each run.
- [x] **Relax timing tolerance in DAG parallel test** — test_dag_executor.py::TestDagWithParsedDeps::test_parallel_after_tags timing flake (25ms→50ms window). System scheduling variability allowed, parallelism still validated.

## Done (session 19)

- [x] **Phase 62: Adaptive Replanning (ALL 8 deliverables)** — Convergence tracking, mid-loop re-decomposition, sibling failure correlation, NEED_INFO mechanism, anti-hallucination prompt, cross-ref in verification, metacognitive logging, shared artifact layer (complete_step `artifacts` field → loop_shared_ctx → injected into subsequent steps).
- [x] **Fix output path resolution** — Replaced 5 hardcoded `orch_root() / "prototypes" / "poe-orchestration" / "projects"` with `_project_dir_root()` → `orch_items.projects_root()`. Output now goes to `~/.poe/workspace/projects/<slug>/`.
- [x] **Phase audit (8 high-risk phases)** — 5 confirmed working (graduation, tier escalation, milestone expansion, dashboard, skills synthesis). 2 loop-end only (diagnosis, recovery). 1 ghost feature fixed: Phase 59 skill telemetry wired (`record_skill_outcome()` now called from success + failure paths).
- [x] **Fix subprocess process leak** — `_run_subprocess_safe()` with `start_new_session=True` and `os.killpg()` on timeout/completion. Applied to ClaudeSubprocessAdapter + CodexCLIAdapter.
- [x] **Fix playbook dedup bug** — Dedup guard in `append_to_playbook()`: checks if core entry text exists before appending. Also wrapped with `locked_write()`.
- [x] **Fix skills.py bare writes** — `save_skill()` and `record_skill_outcome()` now use `locked_write()` from file_lock.py. 
- [x] New tests: convergence tracking (8), anti-hallucination prompt (3), cross-ref detection (4), subprocess process group (2), playbook dedup (3)

## Done (session 17)

- [x] **Test isolation overhaul** — `tests/conftest.py` autouse fixture: workspace → tmp, API keys stripped, credential paths redirected. 62 previously un-isolated test files now safe. Skill hash mismatch warnings eliminated.
- [x] **Adversarial review: 3 rounds** — haiku (round 1 + 2), full model (round 3 running). Round 1: found test isolation leak, confirmed 4/7 prior findings fixed. Round 2: cleaner findings — circular import, test coverage gaps for dangerous paths, evolver opacity.
- [x] **Break circular import skills.py ↔ evolver.py** — Extracted shared types to `src/skill_types.py`. Both modules import types from there. Re-exports in skills.py for backward compat.
- [x] **Fix director context truncation** — 500 → 2000 chars for worker result context in final report compilation.
- [x] **Fix agent_loop cost-warn persistence** — `_cost_warned` flag now resets per `run_agent_loop()` call.
- [x] **Fix test_loop_stuck_detection** — Added `model_key` to stub adapter; all 6 slow tests now pass.
- [x] **README overhaul** — Prerequisites section, restructured quickstart, workspace layout docs, collapsed benchmark section, fixed stale test count.
- [x] Knowledge injection already wired — `inject_knowledge_for_goal()` in `_build_loop_context()` since session 16. Marked MILESTONES #3 as done.
- [x] workers.py test coverage — 22 tests for dispatch routing, type inference, crew sizing, mock adapters.
- [x] Confirmed constraint.py already has 62 tests (adversarial review hallucinated this gap).
- [x] 3553 tests passing (all 6 slow tests now pass too)

## Done (session 16)

- [x] **Workspace routing** — `output_root()` and `projects_root()` now route to `~/.poe/workspace/` (via config.py) instead of repo dir. `relative_display_path()` helper for safe path display. Fixed 12 `relative_to(orch_root())` calls across orch.py, orch_bridges.py, agent_loop.py.
- [x] **Thinking Token Budget** — `THINKING_HIGH/MID/LOW` constants, `thinking_budget` param on all adapters. Wired into: AnthropicSDK (extended thinking API), decompose (THINKING_HIGH for plan quality), advisor_call (THINKING_MID for decisions). Temperature auto-disabled when thinking enabled.
- [x] **Advisor Pattern wiring** — 3 new integration points: (1) evolver auto-apply gate for medium-confidence suggestions (0.6-0.79), (2) milestone boundary decompose failures, (3) introspect recovery plan wisdom check for medium/high-risk plans.
- [x] **K2: Knowledge node infrastructure** — `KnowledgeNode` + `KnowledgeEdge` schema, JSONL storage, TF-IDF query, `inject_knowledge_for_goal()`, wiki-link extraction + graph building. 24 tests.
- [x] **Evals-as-Training-Data flywheel** — Full pipeline: `mine_failure_patterns()` → `generate_evals_from_patterns()` → `run_eval_flywheel()`. Failure-class-specific scoring (9 failure types), eval persistence with dedup, pass-rate trend tracking, auto-suggestion generation for evolver. Wired into `run_nightly_eval()`. 29 new tests.
- [x] Fixed missing logger in knowledge_web.py (pre-existing bug, adversarial rejection path)
- [x] 3489 tests passing (up 53 from 3436)

## Done (session 15)

- [x] 5 adversarial bugs fixed (constraint DoS, parallel fan-out, goal-text, security bypass, meta-command)
- [x] Meta-command detection: hard syntactic gate (URL-strip + word-count + exact match)
- [x] 10 X links researched via live orchestration (two loops, quality gate auto-escalated)
- [x] Workspace consolidated on `~/.poe/workspace/` — fixed memory_dir split-brain
- [x] Two-tier YAML config: `~/.poe/config.yml` (user) + `~/.poe/workspace/config.yml` (workspace)
- [x] Inspector + constraint thresholds wired to config.yml
- [x] Captain's log read bridge (K3 partial) — 11K events now surface at reasoning time
- [x] Advisor Pattern: `advisor_call()` in llm.py, wired into stuck detection
- [x] `poe-enqueue` CLI + user_goal queue + `_check_cycle` DFS fix
- [x] Adversarial self-review via orchestration — 11 findings, 7 fixed
- [x] Dead import cleanup (7 items across poe.py, handle.py, orch_items.py)
- [x] markitdown installed
- [x] 3436 tests passing (up 35 from 3401 at session start)
