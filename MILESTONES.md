# Milestones — Prioritized Work Queue

What to do next, in what order. Updated each session. Strategic phases live in ROADMAP.md; deferred ideas live in BACKLOG.md. This file is the bridge — the executable queue.

Last updated: 2026-04-14 (session 20.5 — adversarial review fix sprint complete)

---

Last updated: 2026-04-14 (session 20.5)

## Next Up

1. **LoopPhase state machine** — Session 20 finding 3.3 (CRITICAL). Replace string constants with `LoopStateMachine` + explicit allowed transitions; `set_phase` raises `InvalidTransitionError` on invalid transitions. Wrinkle: `phase` field on `LoopContext` exists but is never set anywhere — fix requires both adding the SM class AND identifying ~7 transition points in `run_agent_loop` (4,360 lines) to instrument. Estimated 2–4 hour focused session.
2. **Memory Stage 2→3 / 3→4 not implemented** — Architecture specifies a K-stage memory pipeline (observation → episodic → lesson → identity → skill). Stage 2→3 (lesson→identity consolidation) has a threshold spec but no implementation. Stage 3→4 (skill extraction) is "not reliably triggered." Lessons accumulate but never crystallize.
3. **NOW → Director routing for complex goals** — Session 20.5 follow-up to finding 3.4. NOW lane (`handle._run_now`) does a single LLM call with no Director involvement. Add config flag `now_lane.escalate_to_director` + `_is_complex_directive` heuristic + new code path in `handle()` that calls `run_director` when classifier returns NOW but complexity > threshold.
4. **pytest-via-subprocess 900s timeout** — `python3 -m pytest tests/ -q` via `ClaudeSubprocessAdapter` hits 900s timeout (real pytest ~100s). Diagnosis correctly recovered via smaller sub-commands. Root cause unclear — possibly stdout buffering or double-rupture in the adapter. Worth investigating before next adversarial run.
5. **Remaining 71 silent exceptions in agent_loop.py lines 1000+** — First 1,000 lines (16 high-risk sites) fixed in session 20.5. The rest are lower-risk best-effort telemetry; sweep + classify in a focused pass.
6. **Recovery mid-loop apply (remaining)** — Gap 2 from phase audit is ~mostly closed via the mid-loop diagnosis bridge (session 19). Remaining: `budget_exhaustion` is diagnosed only after max_iterations hit; consider a mid-loop "iteration budget running low" signal that bumps the budget instead of grinding to a stop.
7. **Artifact output routing cleanup** — Temp artifacts (per-step) → tmp dir (deleted by default, kept via config `keep_artifacts: true`). Permanent outputs → `~/.poe/workspace/output/`.

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
