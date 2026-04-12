# Milestones — Prioritized Work Queue

What to do next, in what order. Updated each session. Strategic phases live in ROADMAP.md; deferred ideas live in BACKLOG.md. This file is the bridge — the executable queue.

Last updated: 2026-04-11 (session 16)

---

## Next Up

1. **Real-world regression tests** — Polymarket behavioral analysis, nootropic re-run. Now backed by eval flywheel for auto-generating targeted evals from failures.

2. **K2 follow-up: Import links collection** — Knowledge node infrastructure is built (schema, storage, query, injection, wiki-link graph). Next: import enriched posts as knowledge nodes. Jeremy setting up links repo.

3. **Test coverage for dangerous paths** — evolver auto-apply integration test. workers.py done (22 tests), constraint.py already had 62 tests. See BACKLOG adversarial review section.

4. **Eval flywheel hardening** — Current flywheel works end-to-end. Next: failure clustering (dedup related patterns), train/test split validation, eval pass-rate dashboard.

## Queued

6. **Event-driven subprocess wakeup** — Replace polling with asyncio.Queue signal. (7/10)
7. **Phase 62: Auto persona+skill packaging**
8. **Codebase Graph + LSP** — Pre-build call graph; LSP-guided context slicing. (9/10, longer term)

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
