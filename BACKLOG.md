# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

Last reviewed: 2026-04-14 (session 30)

---

## Bugs (fix before next stability sprint)

### Session 20 (2026-04-14) — adversarial review findings (`output/self-review-report-20260414T040637Z-blind.md`)

- [x] **CRITICAL: Evolver broken state persistence** — FIXED (commit `4b8dd7e`). `_verify_post_apply` now tracks `applied_ids` and iterates `revert_suggestion` on test failure. 3 new tests cover fail→revert, pass→no-revert, and legacy int-count backward compat. The `revert_suggestion` no-op for `prompt_tweak` is honest now (lessons decay naturally) — separate item if we want true snapshot/restore.
- [x] **CRITICAL: Silent exception swallowing (systemic)** — FIXED (session 20.5, commit `d8364a6`). All 14 bare-pass exception sites in agent_loop.py first 1k lines upgraded: ERROR for safety/security/correctness (kill switch, interrupts, security scan, hooks); WARNING for resumption-affecting (checkpoint, manifest, dead_ends, claim verifier, skill outcome); DEBUG for telemetry. Also fixed lines 1000+ in the same session. Verified in session 22: no `except Exception: pass` patterns remain in agent_loop.py.
- [x] **CRITICAL: LoopPhase is string constants, not state machine** — FIXED (session 21). `LoopStateMachine` class with `_ALLOWED` transitions dict; `set_phase` raises `InvalidTransitionError`. Wired at 7 transition points in `run_agent_loop`. 8 tests.
- [x] **HIGH: Director bypassed in practice** — FIXED (session 21). Added `now_lane.escalate_to_director` config flag + `_is_complex_directive()` heuristic. Complex NOW-classified goals optionally reclassify to AGENDA for Director routing. Default: off (existing behavior unchanged).
- [x] **HIGH: Inspector signal reliability** — FIXED (session 20.5, commit `f0f6e36`). All 3 false-positive mechanisms fixed: (a) escalation tone: split tautological vs informative keywords, require ≥2 informative hits; (b) backtracking: sort outcomes by `created_at` chronologically before scanning; (c) context-churn: require ≥2 lessons + no keyword overlap with stuck narrative. +5 tests.
- [x] **HIGH: Evolver `cost_optimization` silent no-op** — FIXED (commit `4b8dd7e`). Explicit branch in `apply_suggestion` sets `applied=False`, `status=pending_human_review`, with block_reason. Test added. Real auto-apply executor still TODO if we ever want one.
- [~] **HIGH: Test coverage width not depth** — PARTIAL. pytest-cov with 70% floor: DONE (session 20.5, .coveragerc). Concurrent task_store tests: DONE (session 20.5, +5 tests). End-to-end integration tests: DONE (test_integration.py, 23 tests). Remaining: mutation testing (aspirational, no tooling) and real-LLM-fixture tests (expensive, defer). Item substantially closed.
- [x] **MODERATE: `_steps_are_independent` regex heuristic** — Expanded `_DEPENDENCY_PATTERNS` to catch aggregation verbs (compile/synthesize/aggregate/summarize/analyze) and generic prior-output references ("the findings", "based on results", "with the above", "given the data", "comparing the results"). 7-case regression test added. False-positive direction (mark independent as dependent) is safe — only disables parallelism. False-negative direction (the race-condition direction) is what got tightened.
- [x] **MODERATE: `rate^steps` math false alerts** — Replaced cumulative-product formula with a 5-step sliding window. Healthy 90% long runs no longer fire. Extracted `_compute_march_of_nines` helper for direct testing; 4 unit tests cover healthy long run, recent degradation, below-min-steps, exact-threshold boundary.
- [x] **MODERATE: Memory Stage 2→3 and 3→4 not implemented** — FIXED (session 21). Stage 2→3: evolver scans canon candidates, surfaces as crystallization Suggestions (human-gated). Stage 3→4: extract_skills() was silently broken (s.summary/s.step → AttributeError); fixed to use s.result/s.text. Skill crystallization now fires on successful runs.
- [x] **MODERATE: `_process_blocked_step` 18+ parameters** — Introduced `BlockedStepContext` dataclass; function now takes `(ctx, blk)` instead of 21 args. Body unchanged (unpack at top); call site rewritten to construct the dataclass.
- [x] **MINOR: `new_guardrail` permanently gated** — Now auto-applies in non-prod (default), held in prod. Override hierarchy: `POE_AUTO_APPLY_GUARDRAILS=1` forces on, `=0` forces hold, unset uses `config.environment` (default `dev`). 3 integration tests cover prod/dev/explicit-off paths.
- [~] **MINOR: Persona auto-selection missing** — Hallucinated. Auto-selection already exists: `persona.py:793` (`persona_for_goal`) with keyword routing + scoring + LLM fallback + freeform creation; called from `handle.py:615` in AGENDA flow. NOW lane intentionally skips persona injection (1-shot path). No fix needed.

### Session 20 infrastructure bugs

- [x] **File-claim verifier truncates first char of cited paths** — FIXED (commit `a34228b`). Tightened lookbehind from `(?<![\`'\"(])` to `(?<![\w\`'\"(])` so matches can't start one char into a backtick-wrapped path. 4 regression tests cover backtick/single-quote/paren/word-adjacent wrappers.
- [x] **pytest-via-subprocess 900s timeout** — FIXED (session 21). Default long-running timeout bumped 900→1800s; full-suite runs get 2× (3600s). `POE_LONG_RUNNING_TIMEOUT` env override. Better log message identifies full_suite vs long_running. 5 tests.
- [x] **`scripts/test-safe.sh` collection broken** — Fixed. Two-tier parse: try nodeid format first (`tests/path::test`), fall back to file-level (`tests/path.py: NN` → strip count suffix). Switched chunk dispatch from `$(cat chunk)` to `xargs -a chunk` for safer arg-passing. Now correctly chunks by file when pytest produces file-level output.

### Prior

- [x] **Flaky: test_mission_with_partial_milestone** — Fixed. Root cause: (1) `maybe_add_verification_step` fires on "analyze" in goal, adding extra step that exhausts ScriptedAdapter; (2) `negotiate_contract` + `grade_contract` consume 2 more LLM calls per feature; (3) `run_boot_protocol` + `run_hooks` added 10-90s latency. Fix: patch `_decompose`, `sprint_contract`, `boot_protocol`, and `hooks` in the test. Test now deterministic and <1s.

- [x] **Stale mission shortcircuit** — `poe_handle()` returned cached summary instead of new mission. Fixed: skip CEO layer when `--project` is explicit. (`e7ad725`)
- [x] **Rate-limit no recovery** — Claude "hit your limit" → immediate failure. Fixed: exponential backoff retry in `llm.py`. (`e7ad725`)
- [x] **Stale mission still possible without --project** — Fixed: CEO layer now only handles meta-commands (status/inspect/map); actual goals always go direct to run_agent_loop. (`low-hanging-fruit`)
- [x] **Flaky e2e tests** — Fixed `test_empty_result_step`, `test_loop_stuck_detection`, and `test_some_steps_done_some_stuck`. Root cause: multi-plan decompose (4 LLM calls) consumed execute-step responses out of sequence; `_generate_refinement_hint` called `build_adapter` (real subprocess, could block); Phase 45 auto-recovery re-ran with exhausted adapter. Fix: patch `_decompose`, `_generate_refinement_hint`, and `_recovery_in_progress` in affected tests. (2026-03-31)

## Systemic Improvements (ordered by impact)

### Verification / Hallucination Detection
- [x] **Adversarial verification step** — implemented in factory_thin (post-execute, pre-compile) and quality_gate (second pass on Mode 2 runs). Catches overclaimed mechanisms, wrong evidence tiers, contested findings. (`factory` branch, 2026-03-31)
- [x] **LLM Council / multi-angle critique skill** — 3 critics (devil's advocate, domain skeptic, implementation critic) run in `quality_gate.py` via `run_llm_council()`. Escalates if 2+ rate WEAK. Wired into `run_quality_gate(run_council=True)`. 21 tests. (2026-03-31)
- [x] **Cross-reference check** — `src/cross_ref.py`: extracts verifiable claims from step output, queries a fresh LLM context with no prior response (prevents confirmation bias), flags disputed claims. `ClaimVerification` + `CrossRefReport` dataclasses. Wired into `run_quality_gate(run_cross_ref=True)` as Pass 2.5. Disputed claims escalate the verdict. `poe-cross-ref` CLI. 39 tests. (2026-04-01)
- [x] **Confidence tagging** — each step result should carry a confidence indicator (strong evidence / weak evidence / model inference / unverified). Done: `confidence` field added to complete_step tool schema (optional enum), StepOutcome dataclass, and completed_context entries tagged with `[confidence:X]`. (2026-03-31)

### Token Efficiency
- [x] **Data pipeline enforcement** — `_is_data_heavy_step()` detects risky steps (keywords: fetch all, list all, polymarket-cli, etc.) and injects a stronger `DATA PIPELINE ENFORCEMENT` block into the user_msg. `_result_looks_like_raw_dump()` post-checks results (>2000 chars + high brace density or long lines) and prepends `[RAW_OUTPUT_DETECTED]`. 12 tests. (2026-03-31)
- [x] **Completed context compression** — older entries compressed to one-liner after step 5; last 3 steps kept at full length. 47-63% reduction at 7-12 steps. Zero token cost. (`agent_loop.py`, 2026-03-31)
- [x] **Lesson injection overhead** — Fixed: capped inject output at 1200 chars in memory.py. (`low-hanging-fruit`)
- [x] **System prompt token audit (Pi steal)** — Audited EXECUTE_SYSTEM and DECOMPOSE_SYSTEM against Pi coding agent's <1k target. Cut redundant negatives, editorial commentary, and duplicate BAD/GOOD examples. Result: EXECUTE_SYSTEM 844→333 tokens (-61%), DECOMPOSE_SYSTEM 1048→603 tokens (-42%), combined 1892→936 tokens (-51%). All behavior-changing content preserved. (2026-04-03)
- [x] **Architecture non-goals doc (Pi steal)** — `docs/ARCHITECTURE_NON_GOALS.md` documents 8 deliberate non-goals with rationale: tool minimalism, MCP-as-default, interactive gating, hidden sub-agents, Neo4j, plugin marketplace, provider portability contracts, headless UI. Helps say no cleanly to scope creep. (2026-04-03)
- [x] **Compact notation / shorthand vocabulary** — TESTED, NOT RECOMMENDED (2026-04-10). A/B test: 9 rounds on cheap model, avg +0.7% reduction (median +9.3%), range -97.8% to +63.6%. Variance too high — LLM doesn't reliably adopt shorthand. Sometimes spends *more* tokens mixing styles. `always_inject` stays false. Existing measures (500-tok target, context compression, pipeline enforcement) are sufficient. LLMLingua remains deferred option if server-side compression is needed. A/B harness: `compact_ab.py`, report at `output/compact_ab/`.

### Self-Improvement Loop
- [x] **Evolver signal scanning** — `scan_outcomes_for_signals()` in `evolver.py`. Scans done outcomes for actionable leads/opportunities, converts to `sub_mission` Suggestion entries. Wired into `run_evolver(scan_signals=True)`. 8 tests. (2026-03-31)
- [x] **Phase 46: Intervention Graduation** — `graduation.py` shipped. Scans diagnoses for repeated failure classes (≥3x), proposes high-confidence Suggestions that evolver auto-applies. 8 failure classes covered. CLI: `poe-graduation`. (2026-03-31)
- [x] **Verification patterns on rules** — each graduated rule gets a machine-checkable test before going fully permanent. Done: `verify_pattern` shell command on all 8 templates; `verify_graduation_rules()` and `poe-graduation --verify` CLI. (meta_alchemist pattern, Phase 46 follow-on, 2026-03-31)
- [x] **Problem generation (Agent0)** — Research complete (2026-04-05). 8/8 steps, $2.49, loop `ee4d5e86`. Key: two-agent co-evolution (Curriculum + Executor), R_unc frontier reward (target 50% solve-rate), no human labels. Mapped to Poe: failure-chain recording (DONE), majority-vote pseudo-labels (TODO M), frontier task targeting (TODO M), skill validation harness (TODO M). See `docs/research/agent0-synthesis.md`. Added steal items: `failure_chain` field on Outcome shipped; remaining items in STEAL_LIST.
- [x] **LLM + genetic programming (FunSearch)** — All steal items complete (2026-04-05). Implemented: compactness-adjusted scoring, ranked-candidate mutation context in rewrite_skill, pre-scoring discard gate, skill stemmer, island model diversity, replay-based fitness oracle (`src/strategy_evaluator.py` — TF-IDF cosine over outcomes.jsonl, no LLM in eval path; wired into frontier rewrite loop as pre-score gate; 35 tests). Design doc: `docs/research/funsearch-agent-design.md`.

### Director / Judgment Quality
- [x] **GStack Tier 1 — Decision taxonomy + confidence gates** — `EscalationDecision` extended with `decision_class` + `confidence`; `handle_escalation()` enforces user_challenge→surface, low-confidence→surface, medium-confidence caveat; anti-sycophancy rules in escalation prompt; calibration logging to `memory/calibration.jsonl`. 6 tests. (2026-04-04)
- [x] **GStack Tier 2 — Calibration review loop** — `scan_calibration_log()` in evolver.py; flags high override rate + low mean confidence; wired into `run_evolver(scan_calibration=True)`. 10 tests. (2026-04-04)

### Director / Mission Level
- [x] **Clarification milestone** — director asks user for clarification on ambiguous goals before committing resources. YOLO option. Done: `check_goal_clarity()` in intent.py, wired in handle.py AGENDA path; skippable with `yolo: true` in user/CONFIG.md. (2026-03-31)
- [x] **User-level config defaults** — Added user/CONFIG.md. Wired: default_model_tier. Documented: yolo, always_skeptic, notify_on_complete. (`low-hanging-fruit`)
- [x] **Skip-Director experiment** — `_is_simple_directive()` classifier (≤15 words, no complex keywords); `skip_if_simple=True` in `run_director()` routes to `run_agent_loop` directly; `direct:` prefix in `handle.py` forces AGENDA lane + skips quality gate + escalation overhead. `skip_if_simple=True` wired into `telegram_listener.py`. 28 tests (classifier + integration). (2026-03-31)
- [x] **Multi-agent debate pattern** — `run_debate()` in `quality_gate.py`: Bull argues FOR output, Bear argues AGAINST, Risk Manager gives PROCEED/CAUTION/REJECT. CAUTION+REJECT escalate. Wired into `run_quality_gate(with_debate=True)` as Pass 4. `DebatePosition` + `DebateVerdict` dataclasses. 15 tests. Bug found: `import json` missing inside outer try block — all parsing failed silently. (2026-03-31)

### Phase 65 — Constraint/Premise Orchestration (proposed, not yet implemented)

See `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`. Items below are the review's sharp findings that must be resolved before code lands.

- [x] **Rename decided: "scope".** (2026-04-16) `ScopeSet`, `generate_scope()`, `src/scope.py`. Rationale: captures both what IS and what IS NOT in the bounded space (complements specs). Avoids collision with `src/constraint.py` (HITL/risk harness).
- [ ] **BLOCKER: Autonomous-path behavior.** Design says "human gate (unless yolo)" as if binary. Heartbeat/cron path has no channel. Document the behavior: skip? auto-approve after N? block+fail? Default should probably be "log inversion output for post-hoc review, continue with it as planner context, no gate."
- [ ] **BLOCKER: A/B mechanism.** Cannot evaluate "bounded planning produces measurably better outcomes than unbounded planning" without running goals both ways. Build the A/B capability before enabling anywhere. Probably a config flag or `inversion:` prefix.
- [ ] **BLOCKER: Cost ceiling.** Given April 7-9 token burn, do not ship a feature adding per-goal LLM calls without a per-goal token budget + circuit breaker. Instrumentation first.
- [ ] **Gate heuristic.** Design's "AGENDA goals above N words" is wrong (short goals often benefit most, long ones often don't). Needs an actual judgment signal — possibly complexity classifier, or "use for goals with ≥3 deliverables."
- [ ] **Triad vs. single persona.** Design calls for PM/engineer/architect triad. Review says start with one persona; only add triad if ablation shows the extra personas produce different constraint lines. Cost: 3x LLM calls for premise-setting. Signal: unvalidated.
- [ ] **Persona content vs. costumes.** Design assumes personas produce genuinely different perspectives. Current `persona.py` is largely system-prompt overrides + skeptic modifier. Validate that PM/engineer/architect personas *actually* draw different inversion lines (not just prompt flavor) before investing in triad.
- [ ] **Scope: verification sibling.** Design addresses the *planning* phase. Biggest defect in the system is in the *verification* phase — slycrel-go "passed" because nobody ran a browser. Constraint-setting alone won't close this gap. Needs sibling design for ground-truth verification (real browsers, real endpoints, real test execution — not LLM judgment).
- [ ] **Completion-standard coexistence.** Design says "completion standard is subsumed." Migration plan needed: does completion-standard still run during rollout? If both, do they contradict?
- [ ] **continuation_depth interaction.** Phase 64 restart carries ancestry context across boundaries. Constraints/premises must also be preserved (or explicitly refreshed) across restart. Design is silent.
- [ ] **Concurrent-loop interaction.** `team:` and DAG executor run parallel workers. Do they share the constraint set? Who catches cross-worker conflicts that individually-satisfy-but-together-violate? Unspecified.

### Observability
- [x] **Dashboard as real tool** — Added: Cost panel (24h spend, per-model breakdown from step-costs.jsonl), Mission Ancestry Tree (scans all workspace projects, shows parent/child depth), Replay button (POST /api/replay re-runs last outcome's goal in background thread). 12 tests. (2026-03-31)
- [x] **Replay with "factory mode"** — evolver signal scan on recent outcomes → queues highest-confidence sub-missions as new goals. `/api/replay-factory` endpoint + "Factory Mode Replay" button in dashboard. 4 tests. (2026-04-05)

### Factory Mode Experiment (Mode 3 test)
- [x] **"factory" branch** — created. Two variants: `factory_minimal` (single-call Haiku $0.04-0.06/60s) and `factory_thin` (loop+adversarial Haiku $0.38/375s). Bitter Lesson result: minimal surprisingly competitive; thin+adv matches Mode 2 quality at ~2x lower cost. Scaffolding that's load-bearing: adversarial verification. Scaffolding that's not: persona routing, lesson injection, multi-plan comparison. (2026-03-31)
- [x] **Factory comparison complete** — Full comparison table in /tmp/factory-comparison.md. Key: thin+adv+verify nootropic: $0.36/493s/6 steps done. thin+adv polymarket: $1.40/574s/7 of 8 steps (Haiku token explosion on research = 4.4× Mode 2 tokens, so cost advantage disappears for complex goals). Mode 2 polymarket: $1.27/1156s/8 steps done on Sonnet. (2026-03-31)
- [x] **Factory branch merge decision** — Adversarial patterns already merged to main (quality_gate two-pass, handle.py contested claims). `mode:thin` prefix added to handle.py — routes to factory_thin loop for wall-time-sensitive goals. Ralph verify (--verify) validated useful for research goals. 4 tests. (2026-03-31)
- [x] **Token efficiency prompt in factory_thin** — Added "Target under 500 tokens" constraint to FACTORY_STEP. Matches Mode 2's EXECUTE_SYSTEM language. (2026-03-31)
- [x] **Factory branch merge decision** — Adversarial patterns already merged to main. Factory files (factory_minimal.py, factory_thin.py) available as standalone modules. Full merge (factory to main) done 2026-03-31.
- [x] **Factory overnight experiment** — Ran factory_minimal on PAI goal (overnight 2026-03-31→04-01). Result: subprocess adapter timed out at 300s on first call — factory_minimal is a single-call approach and the PAI goal is too complex for one 300s window. Key insight: factory_minimal's single-call architecture has a hard ceiling at the subprocess timeout; complex research goals need factory_thin's loop approach or Mode 2. Documents the Phase 49 prerequisite: need timeout config to make factory experiments reliable.

### From X research (2026-04-11 — 10 posts, live orchestration, 2 loops)

Full report: `~/.poe/workspace/output/x-research-20260411T081706Z.md`

- [x] **Advisor Pattern** — (2026-04-11 session 16) `advisor_call()` in llm.py. Wired into: stuck detection, evolver medium-confidence gate (0.6-0.79), milestone boundary decompose failures, recovery plan wisdom check. Source: @aakashgupta.
- [x] **Codebase Graph + LSP** — DONE (session 26, AST-only, no LSP). `src/codebase_graph.py`: 5-pass AST analysis (collect, parse, resolve imports, centrality, rank). Basename import resolution. Centrality = 0.7×in_degree + 0.3×line_coverage. Goal-biased ranking in `format_graph_context()`. Wired into `_build_loop_context()`. 39 tests. `llm.py` confirmed tops centrality (54 importers). LSP deferred (overkill given AST already works). **Priority 9/10.** Source: @bniwael / SoulForge.
- [x] **Evals-as-Training-Data flywheel** — (2026-04-11 session 16) `mine_failure_patterns()` → `generate_evals_from_patterns()` → `run_eval_flywheel()`. Failure-class scoring for 9 types, trend tracking, auto-suggestions. Wired into `run_nightly_eval()`. 29 tests. Source: @realsigridjin.
- [x] **Thinking Token Budget** — (2026-04-11 session 16) `THINKING_HIGH/MID/LOW` constants, `thinking_budget` param on all adapters. AnthropicSDK: extended thinking API. Wired into decompose (HIGH) and advisor_call (MID). Source: @av1dlive.
- [x] **Harness Is the Problem** — DONE (session 24, 2026-04-14). `scan_harness_friction()` in harness_optimizer.py: aggregates adapter_error, timeout, retry_storm, tool_error, phase_failure signals from traces. FrictionPoint + HarnessFrictionReport. Wired into `run_evolver(scan_harness_friction=True)`. `--friction` CLI flag. 19 new tests. category="harness_friction" Suggestions surfaced for medium/high severity. Source: @sebgoddijn / Ramp Glass.
- [x] **Harness Architecture Spectrum** — DONE (session 26). Friction scan wired into inspector heartbeat tick alongside run_inspector() (heuristic, no LLM). Inspector friction summary injected into quality gate Pass 1 user message. Checkpoint audit: NOW is intentionally thin (1-shot), AGENDA has pre-flight + quality gate + post-hoc inspector. Injection guard wired at synthesize_skill(). **Priority 7/10.** Source: @akshay_pachaar.
- [x] **Event-driven subprocess wakeup** — FIXED (session 22). `run_agent_loop` calls `post_heartbeat_event("loop_done", payload=project)` after releasing the loop lock. Heartbeat's `_wakeup_event.wait()` unblocks immediately → next task picked up in near-zero time instead of waiting up to `interval` seconds. 3 tests. Source: @teknium / NousResearch hermes-agent.
- [ ] **Large Memory Models (LMMs)** — Engramme: new architecture beyond RAG. Watch list — monitor for API release. **Priority 6/10.** Source: @svpino.
- [ ] **Google MCP Toolbox** — Opinionated MCP server for data tools. Forward-looking; adopt when JSONL memory needs structured DB. **Priority 5/10.** Source: @_vmlops.
- [ ] **Polymarket 36GB dataset + backtester** — 72M trades, free on GitHub. Useful for polymarket-backtest skill. **Priority 4/10.** Source: @recogard.

### Session 15 bugs (2026-04-11)
- [x] **memory_dir split-brain** — `orch_items.memory_dir()` and `config.memory_dir()` resolved to different locations. Captain's log went to `~/.poe/workspace/memory/` while everything else went to the repo's `memory/`. Fixed: `orch_items.memory_dir()` now defaults to `~/.poe/workspace/memory/` (same as config.py) when no workspace env var is set. Tests unaffected (they pin OPENCLAW_WORKSPACE).
- [x] **_check_cycle false-positive** — task_store cycle detection raised on linear A→B→C chains. Root cause: added job_id to visited set then found it on first recursive call. Fixed: track visited deps, not the job being checked.
- [x] **user_goal queue** — `enqueue_goal()`, `enqueue_goals()`, `poe-enqueue` CLI. Director-level queue for user-submitted missions. Sequential blocking via task_store DAG deps.

### Conversation Mining (Phase 48 idea)
- [x] **Research pass through Telegram + Claude session data** — DONE (2026-04-05). `poe-mine --no-git` scanned 902 session log ideas → 336 unique after dedup. High-confidence (11): mostly already in BACKLOG. No new ideas injected above threshold. Notable finding from sessions: "knowledge graveyard" concept (temp storage for sub-goal learnings), "positive mid-IQ agent" (ralph approach, done), context size concern for sub-agents (done via context_firewall). Scan tool: `src/convo_miner.py`.

### From real-world regression runs (2026-04-12, session 18 — 4 parallel goals)

Ran 4 live goals: Polymarket research, nootropic synthesis, recipe site build, self-audit.

**Bugs found:**
- [x] **Output path resolution** — FIXED (session 19). Replaced 5 hardcoded `orch_root() / "prototypes" / "poe-orchestration" / "projects"` paths with `_project_dir_root()` → `orch_items.projects_root()`. Output now goes to `~/.poe/workspace/projects/<slug>/`.
- [x] **Subprocess adapter orphan process leak** — FIXED (session 19). `_run_subprocess_safe()` with `start_new_session=True` and `os.killpg()` on timeout/completion. Applied to ClaudeSubprocessAdapter + CodexCLIAdapter. Still needs: (a) subprocess cwd pinning so `claude -p` doesn't run tests on wrong codebase, (b) process count guard in heartbeat.
- [x] **Stale test skills in workspace** — FIXED (session 22). `poe-doctor --cleanup-skills` now detects `compute_skill_hash(skill) != stored_hash` (stale hashes from test fixtures). Removes them in Pass 1 before dedup. `_skill_hash_is_stale()` helper + `skills_path` kwarg for testing. 6 tests. Ran on live workspace: 15 stale-hash + 2 dup removed, 14 clean skills remain.
- [x] **Playbook deduplication bug** — FIXED (session 19). `append_to_playbook()` now checks if core entry text exists before appending. Also wrapped with `locked_write()`.
- [x] **skills.py read-modify-write race** — FIXED (session 19). `save_skill()` and `record_skill_outcome()` now use `locked_write()` from file_lock.py.
- [x] **Constraint false-positive on step descriptions** — FIXED (session 22). Two-part fix: (a) DECOMPOSE_SYSTEM prompt gets STEP DESCRIPTION STYLE section — "describe task/outcome, not shell commands"; (b) `hitl_policy(is_description=True)` downgrades DESTROY→WRITE and caps HIGH risk at MEDIUM for planner-generated step text. step_exec.py passes `is_description=True` for the pre-LLM scan. 3 tests.
- [x] **11 unlocked bare-append JSONL paths** — FIXED (session 22). Added `locked_append()` to file_lock.py; converted 11 highest-traffic sites (captains_log, memory_ledger×5, metrics, evolver×4, inspector×2). Also fixed knowledge_web.py (nodes+edges). +5 tests.
- [x] **Inspector dual report classes (InspectorReport vs InspectionReport)** — RESOLVED by documentation (session 22). Added explicit docstrings to both classes: InspectorReport = heavyweight spec §12 via run_inspector(); InspectionReport = lightweight scan via run_inspection_cycle(). Separate storage files, separate purposes. No merge needed.
- [x] **Inspector verify_claim_tiered P1/P2 threshold asymmetry** — RESOLVED (session 23). Asymmetry is intentional: standing rules are authoritative and written for broad applicability; fixed match-2 threshold is deliberately looser than P1's proportional. Added inline comment in inspector.py explaining the rationale. No threshold change needed. P4.
- [x] **Cross-backend failover on 4xx/5xx** — FIXED (session 22). `build_adapter("auto")` returns `FailoverAdapter` (wraps all available adapters in priority order). On 402/401/403/5xx errors, tries next backend automatically. Single-backend case returns adapter directly. `_is_failover_error()` for explicit checks. Logs WARNING on failover. 14 tests. Closes BACKLOG P2.
- [x] **Director persona authoring skill** — DONE (session 22). `record_persona_dispatch()` logs persona selections with is_fallback flag to memory/persona-dispatch-log.jsonl. `scan_persona_gaps()` groups fallback clusters by inferred role (keyword-verb matching), returns gaps with ≥3 occurrences. `run_evolver(scan_persona_gaps=True)` converts gaps to persona_authoring Suggestions (confidence=0.75, human review before auto-apply). handle.py calls record_persona_dispatch() after persona_for_goal() in AGENDA path. +6 tests.
- [x] **Prompt-injection hardening for persona + skill ingestion** — DONE (session 26). `src/injection_guard.py`: 17 regex patterns (override/tool-call/exfil), allowlist (skills/personas/workspace/builtin/internal), `InjectionScanReport` with risk_level + safe_to_auto_apply, fail-closed. Wired into: `scan_personas_dir()` YAML loading, `create_freeform_persona()` goal scanning, `evolver.apply_suggestion()`, `evolver.synthesize_skill()`. 59 tests. P3.

**Architectural gaps surfaced:**
- [x] **Phase audit: verify "done" phases against current code** — DONE (session 23). Verified phases 44-62: all implementations are real, not surface-level. Phase 45 "action side never closed" was stale — plan_recovery() is wired at agent_loop:4181-4227. Phase 48 (convo_miner), 50 (thinkback.py), 51 (passes.py), 53 (poe_self.py), 54 (checkpoint.py), 55 (knowledge_web.py), 56 (memory.load_standing_rules), 57 (llm.MODEL_*), 58 (pre_flight.PlanReview), 59 (record_tiered_lesson/detect_goal_gaps), 60 (inspector.InspectorReport) all verified present and importable. No phantom phases found.
- [x] **Cross-ref not wired into step execution** — FIXED (session 19). `verify_step_with_cross_ref()` in step_exec.py. Heuristic `_has_specific_claims()` detects file paths, line numbers, function names. Triggers cross-ref for specific claims. Annotates disputes, doesn't block.
- [x] **No anti-hallucination prompt in EXECUTE_SYSTEM** — FIXED (session 19). ANTI-HALLUCINATION section + NEED_INFO mechanism added to EXECUTE_SYSTEM. Steps can say NEED_INFO: [what's missing] to trigger research sub-steps.
- [x] **Shared artifact layer for step context** — FIXED (session 19). `complete_step` tool extended with `artifacts` field. Stored in `loop_shared_ctx` as `artifact:{step}:{name}`. Injected into subsequent steps as "Artifacts from prior steps" block.
- [x] **PAT missing pull_requests:write** — Fixed mid-session by Jeremy (session 18). Token 2 now has PR write permission.

**Test goal results:**
- Polymarket: 8/8 done, 1.47M tokens, 16min, quality gate PASS (0.85), 3 contested claims
- Nootropic: 8/8 done, 544K tokens, 12min, quality gate PASS (0.80), 5 contested claims
- Recipe site: 10/10 done (pending confirmation)
- Self-audit: 11/11 done, found 5 contradictions + structural bugs, 2 critical races

**Test goals (future runs):**
- [ ] **Local LLM research** — Research tiny LLMs suitable for bundling with the orchestrator or self-hosting on cheap hardware (e.g. local network). Evaluate: inference speed, quality at orchestration tasks (step decomposition, lesson extraction), memory footprint, quantization options. Goal: reduce API dependency for cheap-tier work.
- [ ] **Recipe site PM agent** — Recurring goal against slycrel/orchestrator-test-recipes: review code, open issues for missing features, review PRs, suggest architectural improvements. Tests GitHub integration + multi-step judgment.
- [ ] **Recipe site dev agent** — Recurring goal: pick open issues, implement on branches, open PRs, maintain running Docker instance on this machine. Tests code generation + git workflow + deployment.

**Output routing policy:**
- [x] **Artifact output routing cleanup** — DONE (session 21). Per-step artifacts deleted at loop end by default. Config `keep_artifacts: true` retains them. Permanent files (PARTIAL.md, plan.md, loop log, scratchpad) always kept. Implemented in agent_loop.py around line 1650.

### Architectural (from self-review pass 5, 2026-04-10)
- [x] **Extract LoopStateMachine from agent_loop.py** — DONE (2026-04-10). 16 methods extracted across 14 commits. run_agent_loop reduced from ~1,800 to ~470 lines. While loop body is ~300 lines of orchestration (budget checks, step execution call, extracted method dispatch). All heavy logic in standalone functions. Next: convert to LoopStateMachine class where LoopContext becomes `self`.
- [x] **Break circular import skills.py ↔ evolver.py** — (2026-04-12) Extracted `Skill`, `SkillStats`, `SkillTestCase`, `SkillMutationResult`, `compute_skill_hash`, `verify_skill_hash`, `skill_to_dict`, `dict_to_skill` to `src/skill_types.py`. Both modules import types from there. skills.py re-exports for backward compat.

### From adversarial review (2026-04-12, 3 rounds — haiku + full model)
- [x] **Test isolation: workspace + API key leakage** — (2026-04-12) 62 test files had no workspace isolation. Added `tests/conftest.py` with autouse fixture: `POE_WORKSPACE` → tmp, API keys stripped, credential file paths redirected. Prevents tests from writing to `~/.poe/workspace/` or hitting real LLM endpoints.
- [x] **Director 500-char context truncation** — (2026-04-12) `director.py:503` truncated worker results at 500 chars when building context for final report. Bumped to 2000.
- [x] **agent_loop cost-warn flag persists across runs** — (2026-04-12) `_cost_warned` set on function object, never reset. Added reset at top of `run_agent_loop()`.
- [x] **test_loop_stuck_detection failure** — (2026-04-12) `AlwaysStuckAdapter` had no `model_key`, so tier-up replaced it with real `ClaudeSubprocessAdapter`. Added `model_key = "explicit-test"` to prevent override.
- [x] **Evolver auto-apply integration test** — Already exists at `tests/integration/test_evolver_apply.py` (12 tests, 350s). Covers skill mutation, change_log, backup, prompt_tweak→lesson, guardrail gating, confidence thresholds. Adversarial review missed it (looked only in `tests/`, not `tests/integration/`).
- [x] **workers.py minimum viable tests** — (2026-04-12 session 17) 22 tests: dispatch routing, type inference, crew sizing, mock adapters.
- [x] **constraint.py enforcement tests** — Already had 62 tests. Adversarial review hallucinated this gap.
- [x] **Evolver confidence calibration** — DONE (session 22). `_record_suggestion_outcomes()` writes per-suggestion verified/passed outcomes to suggestion_outcomes.jsonl. `scan_suggestion_outcomes()` computes empirical pass rate vs mean self-reported confidence, flags systematically overconfident categories. Wired into `run_evolver(scan_suggestion_calibration=True)`. +6 tests.
- [x] **Evolver suggestion rollback API** — (2026-04-12 session 17) `revert_suggestion(suggestion_id)` reads change_log.jsonl, reverses action based on before_state (restore skill desc, remove created skill, remove dynamic constraint). CLI: `poe-evolver --revert <id>`. Logs EVOLVER_REVERTED to captain's log.
- [~] **Semantic memory deduplication** — SUBSTANTIALLY ADDRESSED. `record_lesson()` already does at-write-time near-dedup: exact-text match + word-overlap Jaccard ≥ 0.8 within most-recent 100 lessons. Unbounded growth prevented. Embedding-based similarity (true semantic) remains aspirational P3 — requires API call at every write, cost not justified given current lesson volume.
- [x] **LoopStateMachine conversion** — DONE (session 23 continued, 2026-04-14). `LoopStateMachine(LoopContext)` — inherits all context fields; instance `set_phase(new_phase)` replaces classmethod. `_initialize_loop` creates `LoopStateMachine()`. 6 production call sites + 8 test functions updated. +1 subclass check test.

### Session bugs (2026-04-11)
- [x] **Meta-command detection false-positives** — (2026-04-11) Rebuilt with two-tier hard gate: (1) reject if message contains URLs or is >12 words — missions are long; commands aren't. (2) exact phrase match only — no substring tricks. Slash-commands are prefix-only. Eliminates the template-placeholder collision class: `inspector.py`, `/status/123`, `status=done` all correctly rejected. 3 tests added, 1 test updated. (`src/poe.py`)

### From adversarial review (2026-04-11 seeded-haiku, escalated to sonnet)
- [x] **platform_confusion detection stub** — (2026-04-11) Added to batch `detect_friction()` with expanded 6-keyword set (summary, stuck_reason, result_summary). Was only in heuristic `detect_friction_signals()`.
- [x] **Evolver auto-apply audit trail** — (2026-04-11) Enriched `change_log.jsonl` with `suggestion_text`, `confidence`, and `before_state` (old skill description on updates, mutation type for creates/appends). Enables rollback without guessing from a hash.
- [x] **repeated_rephrasing threshold** — (2026-04-11) Lowered from 3 to 2. Most failure loops die at 2 attempts.
- [x] **CLI enqueue --reason ignored** — (2026-04-11) `--reason` CLI arg was parsed but silently overwritten by constructed payload. Fixed: explicit `--reason` used when provided, falls back to payload when default.
- [x] **Evolver drift detection** — (2026-04-11) `scan_quality_drift()` tracks per-cycle quality snapshots in `evolver-baselines.jsonl`. Flags when success_rate drops or avg_cost rises beyond 15% of rolling baseline for 3+ consecutive cycles. Wired into `run_evolver(scan_drift=True)`. Generates observation suggestions with escalating confidence.
- [x] **Lesson contradiction check** — (2026-04-11) `check_contradiction()` in knowledge_lens.py uses text similarity + negation keyword pairs to detect opposing rules. Wired into `observe_pattern()` — blocks promotion when candidate contradicts existing standing rule. Also wired `observe_pattern()` into `promote_lesson()` in knowledge_web.py, closing the standing-rules pipeline (was dead code).
- [x] **Early model escalation on wide-scope goals** — (2026-04-11) Two-layer fix: (a) handle.py now lifts model to mid when pre-flight scope=wide/deep (zero-cost, <1ms heuristic check before adapter build); (b) agent_loop.py trajectory check after step 3 — if done-rate <50% on cheap model, raises session floor to mid for remaining steps. Both reuse existing infrastructure (estimate_goal_scope, _session_tier_floor). No new LLM calls.
- [x] **Inspector threshold calibration** — (2026-04-11) Extracted 6 hardcoded thresholds to module-level variables with env var overrides (INSPECTOR_BREACH_THRESHOLD, INSPECTOR_ESCALATION_MIN_HITS, INSPECTOR_CONTEXT_CHURN_TOKENS, INSPECTOR_ALIGNMENT_GOOD, INSPECTOR_ALIGNMENT_POOR, INSPECTOR_REPHRASING_MIN_COUNT). Added `inspector_thresholds()` for introspection. Calibration mode against historical outcomes deferred — needs real run data first.
- [x] **Handle result formatting unification** — (2026-04-10) pipeline/team/direct/default AGENDA paths in handle.py had 4 near-identical LoopResult→HandleResult formatting blocks. Extracted `_loop_result_to_handle()` helper. Original BACKLOG framing ("plan_NOW/plan_AGENDA/replan are 3 implementations") was inaccurate — they're architecturally different planning modes (NOW=1-shot, Director=multi-ticket, decompose=step pipeline), not duplicated code.

### From adversarial review (2026-04-11, Opus deep scan)
- [x] **Shell injection in runtime_tools.py** — (2026-04-11) CRITICAL. `subprocess.run(shell=True)` with unsanitized LLM args. Fixed: shlex.quote all args before substitution, shlex.split instead of shell=True.
- [x] **Missing `os` import in evolver.py** — (2026-04-11) CRITICAL. `os.environ.get("POE_AUTO_APPLY_GUARDRAILS")` silently crashed with NameError, caught by bare except. Guardrail gate never fired. Fixed: added `import os`.
- [x] **Broken `import o` in scan_calibration_log** — (2026-04-11) CRITICAL. Nonexistent module `o` made calibration scan dead code. Fixed: `from orch_items import memory_dir`.
- [x] **`_reinforce_tiered_lesson` stale data race** — (2026-04-11) HIGH. In-memory mutation lost because `_rewrite_tiered_lessons(tier)` re-loaded from disk. Fixed: reload, replace mutated lesson, pass explicit list to rewrite.
- [x] **File handle leak in handle.py** — (2026-04-11) MEDIUM. `_inputs_path.open().write()` without `with` leaked fd per message. Fixed: `with` block.
- [x] **Operator precedence bug in observe_pattern** — (2026-04-11) MEDIUM. `or` vs `and` precedence caused empty-domain hypotheses to match across unrelated domains. Fixed: explicit parens + require non-empty domain for fuzzy match.
- [x] **Tiered lessons missing adversarial check** — (2026-04-11) MEDIUM. `record_tiered_lesson()` had no `_lesson_looks_adversarial()` check (flat-tier did). Fixed: added check at entry.
- [x] **Wrong attribute in record_step_trace** — (2026-04-11) LOW. `getattr(s, "step")` should be `getattr(s, "text")` per StepOutcome dataclass. Removed phantom `summary` field.
- [x] **Dynamic constraint DoS potential** — (2026-04-11) TTL on dynamic constraints (`added_at` + `_DYNAMIC_CONSTRAINT_TTL_DAYS`, default 30d). Circuit breaker opens after N consecutive dynamic-only blocks (`_DYNAMIC_BLOCK_CIRCUIT_BREAKER`, default 5), disables for cooldown window. 8 tests.
- [x] **Parallel fan-out skips security scanning** — (2026-04-11) `_run_steps_parallel()._run_one` now runs `scan_external_content` on step result; HIGH-risk → blocked, lower risk → sanitized in-place. Ralph verify not added (requires session-level state incompatible with fan-out).
- [x] **Constraint checker combines goal text** — (2026-04-11) `_check_patterns` changed to `step_text.lower()` only. Goal text excluded to prevent goal-keyword false-positives (e.g. goal containing "research" blocking every research step). 2 tests.
- [x] **Security scanner 50K truncation bypass** — (2026-04-11) `sanitized` now always bounded to `scan_target` (max_length chars). Before: no-signal path returned full `text`, allowing injection past position 50K. 2 tests.

### Adversarial review (2026-04-11, session 15 self-review via orchestration)
- [x] **BUG-1: verbose always True** — `verbose=args.verbose or True` → `verbose=args.verbose`. Two call sites in handle.py.
- [x] **Dead imports/vars** — 7 items cleaned: sys/time/uuid from poe.py, os/field/_btw_t0 from handle.py, field/Any from orch_items.py.
- [x] **BUG-2: lock file open mode** — (2026-04-11 session 16) `_lock_task` now opens with `'a'` mode. Prevents inode deletion race where another process could unlink+recreate between touch and open.
- [x] **BUG-3: project starvation sort** — (2026-04-11 session 16) `select_global_next` now prefers oldest mtime for equal-priority projects (inverted tiebreak). Most neglected project gets picked.
- [x] **SEC-2/SEC-3: f-string + swallowed exc** — (2026-04-11 session 16) Fixed 4 f-strings without placeholders in poe.py. Swallowed mission dispatch exception now logged at DEBUG.

### Memory / Knowledge Layer (K stages — from research/orchestration-knowledge-layer)
- [x] **K3 partial: Captain's log read bridge** — (2026-04-11) Captain's log (11K events, write-only since creation) now wired as read source into: (1) decompose context injection in `agent_loop.py` — planner sees last 5 actionable learning events; (2) evolver LLM analysis in `evolver.py` — evolver sees recent skill/rule changes before generating suggestions. Filters: SKILL_PROMOTED/DEMOTED/CIRCUIT_OPEN, EVOLVER_APPLIED, DIAGNOSIS, HYPOTHESIS_PROMOTED, STANDING_RULE_CONTRADICTED, RULE_GRADUATED. (`captains_log.load_log()` API already existed — just had zero consumers.)
- [x] **memory.py decomposition (K1-aligned)** — DONE (2026-04-10). 2,968→530 lines (82% reduction). Split into: `memory_ledger.py` (944L — outcomes, lessons, compression, step traces), `knowledge_web.py` (1,006L — tiered lessons, decay/promotion, TF-IDF, canon tracking), `knowledge_lens.py` (758L — rules, hypotheses, decisions, verification). memory.py is now a thin public API with re-exports + coordination functions (bootstrap_context, reflect_and_record, inject_lessons_for_task).
- [x] **Consolidate knowledge layer research** — (2026-04-10) Merged into `docs/knowledge-layer/` as canonical location. Architecture, K-stages, research landscape, gaps docs moved from research/. Raw transcripts archived. README with K-stage status table added. K0 (baseline) and K1 (module split) marked DONE.
- [x] **llm_parse.py test coverage** — (2026-04-10) 68 unit tests added. Covers all 6 public functions + edge cases (None, NaN, fences, type mismatch unwrapping).

### Test Coverage Gaps (from 2026-04-10 audit)
- [x] **task_store.py tests** — (2026-04-10) 36 unit tests added. Covers enqueue/claim/complete/fail/archive, dependency resolution, cycle detection, stale claim recovery, atomic writes.
- [x] **orch.py tests** — DONE. test_orch_core.py has 48 tests covering start/finalize_run, run_tick, run_loop, run_once, validation hooks, artifact path validation, worker session bridge, manifest-driven execution. Item was stale.

### Self-Extensibility / Decision Point Hooks (design exploration)
- [ ] **Composable decision-point hooks** — The system currently has pre/post step hooks (step_events.py), inspector observation, quality gate, and prompt injection (standing rules/lessons/skills into decompose). But these aren't composable: you can't say "after decompose, before execution, run extra verification on steps 3 and 5." MTG-style stack where effects can be intercepted at targeted points. For now, prompt-stage injection is sufficient. Revisit when operational experience shows which decision points actually need interception. Key constraint: any self-extensibility must be human-gated (see evolver guardrail auto-apply fix).

### Phase Transition Contracts (architecture — revisit after operational data)
- [ ] **Formal stage contracts between pipeline phases** — Currently phase transitions are implicit: decompose outputs strings, execute takes strings, finalize takes outcomes. No typed contracts, no hard validation gates between phases. Pre-flight is advisory-only (loop proceeds regardless). Trajectory check is the first real mid-pipeline gate. Need: (1) typed output contracts per phase (not just "a list of strings" but "atomic steps that cover the goal scope"); (2) hard gates that re-plan or abort instead of proceeding with garbage input; (3) audit which existing checks are load-bearing vs noise. The Starship optimization: delete the advisory checks that never change behavior and replace with fewer, harder gates. Defer until operational data shows which gates actually matter.

### Data Portability / Workspace Consolidation (hardening)
- [x] **memory_dir consolidated** — (2026-04-11) `orch_items.memory_dir()` and `config.memory_dir()` now both default to `~/.poe/workspace/memory/`. Captain's log + all learning data in one place.
- [x] **Two-tier YAML config** — (2026-04-11) `~/.poe/config.yml` (user) + `~/.poe/workspace/config.yml` (workspace). Inspector thresholds and constraint settings wired to config. 17 tests.
- [x] **Route output + projects to workspace** — (2026-04-11 session 16) `output_root()` and `projects_root()` now route to `~/.poe/workspace/` via config.py. `relative_display_path()` helper for safe cross-root path display. 12 `relative_to(orch_root())` calls fixed.
- [x] **poe-export / poe-import for learning data** — (2026-04-11 session 16) `scripts/poe_export.py`: export/import of `~/.poe/workspace/` as tar.gz. Excludes secrets, prototypes, ephemeral state. 12MB→910KB compressed. Merge-restore with path traversal protection. 13 tests.

### Concurrent Run Safety (hardening)
- [x] **First-class project isolation** — DONE (session 27). `Skill.project` field (""=global, non-empty=project-scoped). `find_matching_skills(project=...)` filters to global + project-specific skills. `set_loop_running(project=...)` writes per-project lock file. `get_running_project_loop()` + `is_project_running()` for concurrent-run safety checks. 11 tests. Remaining: project-scoped lesson injection (currently filters by task_type but not project) and captain's log project tagging — deferred until parallel missions actually needed.

### Captain's Log extensions (from Grok Round 5 feedback, 2026-04-10)
- [x] **Input classification tag** — DONE (session 23). `classify_input_type()` in captains_log.py (url/code/structured_data/plain_text). `INPUT_MISMATCH` + `METACOGNITIVE_DECISION` event constants. `update_skill_utility()` logs INPUT_MISMATCH when circuit opens on url-skill-vs-non-url-input domain mismatch. `attribute_failure_to_skills()` threads step_text through. 9 tests. EVENT_TYPES 28→30.
- [x] **Director context hook** — (2026-04-11 session 16) Captain's log context + playbook + knowledge nodes now injected into `_build_loop_context()`. Director sees recent learning events, operational wisdom, and relevant knowledge at decompose time.
- [x] **Dashboard captain's log panel** — DONE (session 27). `_read_captain_log_entries(limit=20)` in observe.py reads captains_log.jsonl newest-first. Wired into `_snapshot_json()` and `_DASHBOARD_HTML`. Badge color-coding by event type. 6 tests in TestCaptainLogDashboard.

### From X research runs (2026-04-09)

Six X posts researched via live Poe missions. Actionable items extracted:

- [x] **markitdown installed** — `pip install --user markitdown` done (Python 3.14). HTML→MD confirmed working. High-value use case: PDF/Word/Excel ingestion (Jina can't handle these). Wiring into `web_fetch.py` or `file_ingest.py` is next step — needs `fetch_file(path_or_url)` that falls back to markitdown for non-HTML content types.
- [ ] **TOOLS.md + STYLE.md gaps** — @imjustinbrooke's "7 files to run your business" framework maps to Poe: SOUL.md ✓, AGENTS.md ✓, USER.md ✓, MEMORY.md ✓, HEARTBEAT.md ≈ heartbeat scripts. Missing: explicit TOOLS.md (tool registry covers this partially) and STYLE.md (persona covers this partially). Consider whether explicit files add value.
- [ ] **Eval-driven harness hill-climbing** — @mr_r0b0t + @ashpreetbedi both endorse @Vtrivedy10's LangChain article on using evals as autonomous learning signal. This IS evolver.py's pattern. Read full article when available — may have concrete recipes to improve the eval→lesson→skill pipeline.
- [ ] **Letta API comparison** — @carsonfarmer/@sarahwooders: Anthropic's Managed Agents API mirrors Letta's 1yr-old API. Provider-managed memory = lock-in. Poe's file-based memory is aligned with "memory outside providers" thesis. Monitor Managed Agents API for useful features without adopting their memory model.
- [ ] **Team OS / shared context layer** — @aakashgupta: 250+ structured docs/quarter compound into organizational knowledge. Validates knowledge layer K1-K2 investment. The "learning flywheel" pattern (each commit makes the repo smarter) is the vision for standing rules + lesson promotion.
- [x] **Auto-detect repo stack → skill discovery + summarization** — DONE (session 25). `src/repo_scan.py`: 50+ file indicators, deep-scan requirements.txt/package.json for frameworks, detect Docker/CI/DB. `format_repo_context()` injects compact stack summary into `_build_loop_context()`. Wired via project slug heuristic (~/claude/{project}/) + `--repo` CLI flag. 53 tests. Source: @ihtesham2005

### Infrastructure
- [ ] **Phase 38 subpackage move** — src/ is flat with 49 modules. Deferred (33+ imports per group), revisit when it causes real problems.
- [x] **Phase 42 nightly eval** — wire eval suite to evolver on a schedule. Done: `run_nightly_eval()` in eval.py; fires via `eval_every=1440` in heartbeat_loop(); failures → evolver Suggestion entries. (2026-03-31)
- [x] **Heartbeat backgrounding** — evolver, inspector, nightly eval each moved to daemon threads with double-checked locking flags; heartbeat tick no longer blocks on slow runs. 11 tests. (2026-04-04)
- [x] **Heartbeat service deployment** — poe-heartbeat.service and poe-telegram.service installed as systemd units, enabled + started. Fixed UnboundLocalError: `global` declarations missing for all 6 bg-thread flags in `heartbeat_loop`; without them Python treated writes as local → crash on tick 1 every 30s → ~0% duty cycle. (2026-04-04)
- [x] **Context firewall (depth-gated)** — `_context_firewall()` in handle.py: depth ≥ 2 strips accumulated history, keeps only original goal + remaining steps. Wired into continuation task handling. 5 tests. (2026-04-04)
- [x] **Mutable task graph (inject_steps)** — `complete_step` tool accepts `inject_steps` (max 3); serial and parallel agent_loop prepend injected steps to remaining_steps mid-execution. 2 tests. (2026-04-04)
- [x] **SlowUpdateScheduler** — `src/slow_update_scheduler.py`: 4-state machine (IDLE_WAIT→WINDOW_OPEN→UPDATING→PAUSING) gates heavy background LLM work to idle windows. Thread-safe with `start_work()`/`finish_work()` context manager; wired into `heartbeat_loop()` before evolver/inspector/eval dispatch. 16 tests. (MetaClaw steal, 2026-04-04). Follow-on done: scheduler state exposed in `poe-doctor` health check (snapshot()-based) and `poe-observe` dashboard (state badge + workers/cooldown/idle_since panel). 60 tests pass. (2026-04-04)
- [x] **Auto-resume on rate limits** — multi-cycle polling retry in `ClaudeSubprocessAdapter`: 6 retries, exponential backoff 60→1800s, stops early on non-rate-limit errors. 5 tests. (2026-04-04)
- [x] **Cron persistence** — scheduled missions survive restarts. `jobs.json` pattern. Done: `src/scheduler.py` with `JobStore` backed by `memory/jobs.json`; supports once/daily/interval schedules; `drain_due_jobs()` wired into `heartbeat_loop()`; `poe-schedule` CLI. 21 tests. (724-office steal, 2026-03-31)
- [x] **ScheduleCronTool in Poe heartbeat** — wire Poe's cron tool so she can schedule her own future runs from within a mission. Closes the self-managing loop. Done: `schedule_run` tool added to `EXECUTE_TOOLS` in step_exec.py; parses 'daily at HH:MM' / 'in N minutes/hours/days' / ISO datetime; calls scheduler.add_job(); 13 tests. (2026-03-31)

### claw-code steal list (github.com/instructkr/claw-code — Claude Code architecture map)
- [x] **verificationAgent as first-class agent** — `src/verification_agent.py` with `VerificationAgent` class: `verify_step()`, `adversarial_pass()`, `quality_review()`. step_exec.py's `verify_step` delegates to it. `poe-verify` CLI. 21 tests. (2026-03-31)
- [x] **TeamCreateTool pattern** — `src/team.py`: `create_team_worker(role, task)` spins up a specialist with a custom persona. 8 known roles (market-analyst, risk-auditor, fact-checker, data-extractor, devil-advocate, synthesizer, strategist, domain-skeptic); free-form roles get generic persona. `create_team_worker` tool added to `EXECUTE_TOOLS_WORKER` (not SHORT/INSPECTOR). Step-terminating: agent delegates step to specialist, synthesizes in next step. 30 tests. (2026-03-31)
- [x] **thinkback replay** — session-level decision replay for self-improvement. `src/thinkback.py`: `ThinkbackReport` with per-step StepReview (good/acceptable/poor), mission_efficiency, key_lessons, would_retry, retry_strategy. `run_thinkback(loop_result)` + `run_thinkback_from_outcome(outcome_dict)`. Optionally writes lessons back to memory tagged `[thinkback:{run_id}]`. `poe-thinkback --latest [--save]` CLI. 31 tests. (Phase 50, 2026-03-31)
- [x] **effort modifier** — add `effort:` keyword to handle.py routing that sets a thinking/token budget level. Done: `effort:low/mid/high` prefix in handle.py strips keyword and overrides model tier (low→cheap, mid→mid, high→power). (claw-code steal, 2026-03-31)
- [x] **passes command** — multi-pass review as a unified first-class concept. `src/passes.py`: `PassConfig` with presets (quick/standard/thorough/full/all), `run_passes()` chains quality_gate → adversarial → council → debate → thinkback. `PassReport` aggregates all pass verdicts into one escalation signal. `poe-passes --goal "..." --passes council,debate` CLI. 29 tests. (Phase 51, 2026-03-31)
- [x] **ultraplan / ultrareview modes** — `ultraplan:` prefix added to handle.py: strips keyword, sets model=power, passes max_steps=12 to run_agent_loop. For complex multi-part goals needing thorough decomposition. 3 tests. `ultrareview:` deferred — quality gate already covers the review use case. (2026-03-31)
- [x] **bughunter mode** — self-directed code quality scan. Poe scanning her own orchestration code for bugs, not just diagnosing runtime failures. Done: `src/bughunter.py` with stdlib AST scanner (BH001 bare except, BH003 mutable defaults, BH004 shadowed builtins, BH010 TODOs); `poe-bughunter` CLI. 16 tests. Src scans clean. (claw-code steal, 2026-03-31)
- [x] **btw (by-the-way) mode** — non-blocking observation mode; `btw:` prefix routes to NOW lane with `_BTW_SYSTEM` prompt, tags result as `[Observation]`. 5 tests. (2026-03-31)

### X Links steal list (2026-04-01 research batch — research/X_LINKS_SYNTHESIS.md)

- [x] **lat.md — Knowledge graph docs** (9/10) — DONE (2026-04-01). 9 cross-linked concept nodes in `lat.md/`, `[[wiki links]]`, `lat check` CI clean. Phase 55.
- [x] **Promotion cycle + decision journal** (8/10) — DONE (2026-04-01). `observe_pattern()` → hypothesis → StandingRule at 2 confirmations. `contradict_pattern()` demotes. `inject_standing_rules()` + `inject_decisions()` wired into every decompose call. Phase 56.
- [x] **Polymarket BTC lag edge validation** (6/10) — Research complete (2026-04-02). **Verdict: UNCONFIRMED — promotional fiction.** Structural failures: (1) Wrong product type — Polymarket BTC contracts are binary YES/NO (prob markets), not continuous price feeds; no "lag" surface exists. (2) Fee economics — even at corrected ~4% round-trip fee, the 0.3% claimed edge is 13x smaller than fees. (3) Near-zero liquidity — no resting orders to fill against. (4) Resolution mismatch — single Binance 12:00 ET candle close; intraday moves irrelevant. Full report: `research/POLYMARKET_BTC_LAG_VALIDATION.md`. No further investigation warranted unless claim is restated for a different venue (perpetual futures, spot CEX).
- [x] **Claude Code declarative skill/hook architecture** (5/10) — IMPLEMENTED (2026-04-02, steps 1-6). `tool_registry.py`, `skill_loader.py`, `step_events.py`, `tool_search.py` all shipped. 139 new tests. Step 7 (MCP) remains. Design doc: `research/PHASE41_TOOL_REGISTRY_DESIGN.md`.
- [x] **Magic keyword triggers** — `ralph:`, `verify:`, `pipeline:`, `strict:` prefixes in handle.py. DONE 2026-04-02. 8 tests.
- [x] **Magic prefix registry** — `_PrefixRule` dataclass + `_PREFIX_REGISTRY` + `_apply_prefixes()` replaces 9 scattered `startswith()` chains. Stacking, case-insensitive, model tier precedence. 11 tests. (2026-04-04)
- [x] **Hermes steal: Skill Document auto-extraction** — `export_skill_as_markdown()` in skill_loader.py; called from `maybe_auto_promote_skills()`. DONE 2026-04-02. 18 tests.
- [x] **poe-doctor Phase 41 checks** — tool registry, curated skills, step event bus, bughunter. DONE 2026-04-02. 10 tests.

## Research to Process

### Links fetched but not fully digested
- [x] **TradingAgents** (github.com/TauricResearch/TradingAgents) — multi-agent Polymarket trading. Dogfood run complete. Steal items in STEAL_LIST.md: commitment-forced verdicts (done), pre-plan challenger, two-tier model routing.
- [x] **Stanford Agent0** — self-improvement without supervision. Dogfood run complete. Results in projects/agent0-research/. Key: problem generation + self-evaluation loop. Maps to evolver.
- [ ] **Polymarket behavioral analysis** (hrundel75) — 400M trades / 2400 wallets. Good prompt for different Polymarket test: "find behavioral patterns not picks."
- [x] **LLM sycophancy** (rohanpaul/karpathy) — models mirror prompts not truth. Addressed: adversarial verification step now auto-injects for research goals.
- [ ] **Build-your-own-X** (agenticgirl) — 484k star repo, learning methodology. Low priority.
- [x] **FunSearch/EUREKA/Voyager papers** (garybasin) — Research complete (2026-04-05). 7 shared primitives extracted. Critical gap: generator/evaluator separation (evolver.py mixes both). Design sketch written. See `docs/research/funsearch-agent-design.md`. Steal candidates: island model diversity, replay-based fitness oracle, score-weighted mutation context, brevity penalty in skill scoring.
- [x] **claw-code** (github.com/instructkr/claw-code) — Python skeleton of Claude Code's leaked TS source. Most code is stubs but the tool/command inventory is a goldmine. Key findings: verificationAgent is a first-class built-in; TeamCreateTool exists; thinkback/replay is a real pattern; $ralph mode (OmX) validated our Ralph verify loop. Steal list added above. (2026-03-31)
- [x] **vtrivedy10 tweet** (x.com/vtrivedy10/status/2038346865775874285) — Viv @Vtrivedy10 (LangChain agents/evals) on "harnesses" for autonomous agents. Key findings from related @systematicls article: (1) Instruction fade-out is real — agents cut corners as context accumulates, event-driven reminders at decision points (not just system prompt) fix this. (2) Verification is the highest-leverage investment — success correlates with ability to verify own work. (3) Multi-layer defense: prompt + schema + runtime gates + tool validation + lifecycle hooks. (4) Dual-memory: episodic (events.jsonl) + working (completed_context) — we have both. These validate Mode 2 scaffolding direction. ~~Steal candidate: inject contextual guidance at step retry/budget-exceeded decision points (not just in initial system prompt).~~ **DONE: agent_loop.py now re-injects goal+constraints every 5 steps and on every retry. (2026-03-31)** New steal items added to STEAL_LIST.md LATER: role-specific tool visibility, back-pressure lifecycle hooks, subagent context firewall.

### Persona System
- [x] **garrytan persona** — GStack phase-gated persona (THINK→PLAN→BUILD→REVIEW→TEST→SHIP→REFLECT), six forcing questions, CRITICAL/MODERATE/MINOR severity, founder taste layer, anti-sycophancy guardrails. `garrytan:` prefix or keyword-detected. (2026-04-04)
- [x] **Persona injection in AGENDA path** — personas now active for all AGENDA goals (was only CEO meta-commands). `forced_persona` field on `_PrefixResult` / `_PrefixRule`; `ancestry_context_extra` populated before `run_agent_loop`. (2026-04-04)
- [x] **Dynamic persona discovery** — persona system is now auto-discoverable. `scan_personas_dir()` loads all `personas/*.yaml` at import time; `persona_for_goal()` keyword-matches against loaded specs with confidence threshold fallback; `create_freeform_persona()` writes a minimal YAML spec (`personas/<slug>.yaml`) and registers it in module cache when no existing persona matches well. Free-form path: goal → kebab slug (first 5 words) → mid-tier spec with goal-derived system prompt → session scope. 140 persona tests passing, 0 failures. (2026-04-04)

### Grok feedback sessions
- [x] grok-response-2.txt — oh-my-claudecode, 724-office, Mimir steal list. Processed, items in STEAL_LIST.md.
- [x] grok-response-3.txt — Miessler Bitter Lesson Engineering + Zakin Mode 1/2/3 taxonomy. Processed (session 25). Key steal items implemented:
  - BLE goal rewriter: `rewrite_imperative_goal()` in intent.py. 15 tests.
  - SIGNALS.md → signal alignment: `_load_user_signals()` in evolver.py. 5 tests.
  Deferred items: USER/ folder formalization (CONFIG/GOALS/SIGNALS already exist), replay factory mode toggle in dashboard (dashboard is still basic).

### Steal-list items from Miessler/Zakin (grok-response-3.txt)
- [x] **BLE goal rewriter** — DONE (session 25). `rewrite_imperative_goal()` strips imperative steps, rewrites as outcome-focused. Wired into AGENDA path before clarity check. Non-blocking.
- [x] **SIGNALS.md signal alignment** — DONE (session 25). User-declared research priorities injected into signal scanning. Factory sub-missions now aligned with user intent.
- [ ] **Dashboard: replay as factory mode** — "Replay this run as factory mode" button. Re-runs the original goal but lets evolver inject one self-generated sub-goal from recent signals. Instant Mode 3 visibility. Low priority until dashboard gets real usage.
- [ ] **Eval-driven harness hill-climbing** — @mr_r0b0t/@ashpreetbedi: use evals as autonomous learning signal for hill-climbing on harness quality. Already partially done (eval flywheel, evolver signal scan). Read the Vtrivedy10 LangChain article when it's available for concrete recipes.
- [x] grok-response-3.txt — Bitter Lesson Engineering + Mode 1/2/3 taxonomy. Processed, implemented outcome-first decomposition + user context.
- [x] **PAI (danielmiessler/Personal_AI_Infrastructure)** — Research run complete (2026-03-31, partial — subprocess timeout on step 6). Key findings: 964 TELOS files across 5 categories (world/self/goals/projects/standards), 340 hooks files, rich hook pattern library. Steal candidates: TELOS-style structured context injection; hook-based lifecycle callbacks at decision points. Jeremy's gut: good bones, too much ceremony for Poe's use case.
- [x] **Hermes (NousResearch/hermes-agent)** — Jeremy asked if we should set up Hermes instead of OpenClaw. Research complete (2026-03-31). Verdict: **keep OpenClaw + poe-orchestration**. Hermes is optimized for repeated iterative tasks with automatic skill refinement; our system is more sophisticated in multi-agent oversight, recovery, and mission structure. Selective steal candidates below.
  - **Hermes steal: Skill Document auto-extraction** — formalize lessons.jsonl into SKILL.md files that get FTS-searched automatically (vs. manual lesson injection). Maps to Phase 32 skill synthesis.
  - **Hermes steal: Persistent user modeling** — Honcho-style user preference tracking across sessions. Jeremy-specific knowledge compounding over time. Partial overlap with Phase 28 companion persona.
  - **Hermes steal: Terminal persistence backends** — SSH/Modal backends for long-lived sandboxed execution separate from the primary process. Complements Phase 18 sandbox hardening.

## Self-Review Quality (from 2026-04-06 haiku adversarial run — vetted)

Findings from the haiku blind run ($7.87, 11 steps, adaptive tiering). Hallucinations discarded; only verified findings listed.

- [x] **CRITICAL: Evolver dry-run gate bug** — `_run_skill_test_gate` passed `adapter=None` to `validate_skill_mutation`, causing `dry_run=True` → `blocked=False` always. Gate never blocked any mutation. **Fixed 2026-04-06**: gate now builds a cheap adapter; heuristic fallback only if adapter unavailable.
- [x] **Skill backup before mutation** — `_apply_suggestion_action` wrote to `skills.jsonl` with no backup. Bad mutations had no automated rollback. **Fixed 2026-04-06**: `skills.jsonl.bak` written before any skill_pattern mutation.
- [x] **Memory decay scores not persisted** — `run_decay_cycle` computed decayed scores in memory, then reloaded from disk for the rewrite, losing all score changes. Middle-ground decay (above GC, below promote threshold) was silently lost on restart. **Fixed 2026-04-06**: rewrite uses in-memory lesson list with updated scores.
- [x] **No real LLM coverage in tests** — Added `tests/integration/test_integration.py` (23 mocked-LLM integration scenarios) and `tests/regression/test_regression.py` (7 golden-path scenarios). Both trace handle() end-to-end with ScriptedAdapter. Live Haiku integration still TODO (infrastructure cost). (2026-04-06)
- [x] **No coverage measurement** — `pytest-cov` installed, `.coveragerc` configured, `dev` extras updated in pyproject.toml. Run with `python3 -m pytest --cov=src tests/`. (2026-04-06)
- [x] **Memory decay persistence across restarts** — Non-issue (investigated 2026-04-06): `record_tiered_lesson` → `_append_tiered_lesson` persists immediately; `reinforce_lesson` → `_rewrite_tiered_lessons` also persists immediately. Decay is recomputed from `last_reinforced` date on every `load_tiered_lessons` call (inline, line ~1272), so no decay is lost across restarts. Scores used for injection are always correct. Only cosmetic gap: inline-computed decay scores aren't written back unless `run_decay_cycle` runs (fixed in prior session for that path). No action needed.
- [x] **Skill rollback CLI** — `poe-skills --rollback <skill_name>` restores `skills.jsonl` from `.bak` backup. `--dry-run` supported. (2026-04-06)

## Self-Review Quality (from 2026-04-07 Sonnet seeded run)

Findings from Sonnet seeded run (full code read). Vetted; hallucinations discarded.

- [x] **Director review exhaustion silent** — after MAX_REVIEW_ROUNDS, director fell through silently. **Fixed 2026-04-07**: added WARNING log + `for-else` branch in review loop; 2 tests added. (11c05c3)
- [x] **WorkerResult schema validation** — director.py now spot-checks `result.worker_type` matches `ticket.worker_type` and `result.ticket` is non-empty after each `dispatch_worker` call. Logs WARNING on mismatch. (2026-04-07)
- [x] **Prefix combination validation** — added log.warning in `_apply_prefixes` when conflicting model tiers detected (e.g. effort:high + effort:low). (2026-04-07)
- [x] **Lesson staleness detection** — `load_tiered_lessons()` now accepts `max_age_days` parameter; lessons older than N days skipped at load time. 2 tests. (2026-04-07)
- [x] **Introspection lens determinism** — `run_lenses(deterministic=True)` uses `temperature=0` for LLM-based lenses. `LensRegistry.run_all()` uses `inspect.signature` to pass kwarg only to supporting lenses. `_quality_lens()` accepts `deterministic` kwarg. (2026-04-06)
- [x] **LLM schema hallucination crash** — when Haiku returned a JSON schema dict instead of string for `summary` field, `step_summary[:200]` raised `KeyError: slice(None,200,None)`. **Fixed 2026-04-07**: coerce summary to str in `step_exec.py` + defensive guard in `agent_loop.py`. (df8375b)

## Self-Review Quality (from 2026-04-06 blind adversarial run)

Real findings from the run — hallucinations already vetted and discarded:

- [x] **Evolver audit trail** — `evolver.py` appends to `memory/change_log.jsonl` before any suggestion mutation. `memory.py` logs decay cycle (promoted_ids + gc_ids) before rewriting lesson store. Creates rollback surface without requiring git tracking of runtime files. (2026-04-06)
- [x] **No end-to-end integration test** — `tests/integration/test_integration.py` added: 23 mocked-LLM scenarios covering both lanes, magic keywords, constraint enforcement. `tests/regression/test_regression.py` added: 7 golden-path scenarios. (2026-04-06)
- [x] **`tests/regression/` has spec but no tests** — `tests/regression/test_regression.py` implements 7 golden-path scenarios (NOW, AGENDA, direct:, btw:, pipeline:, stuck, prefix stacking). (2026-04-06)
- [x] **Phase 24 (Slack)** — `src/slack_listener.py` (424L) + `tests/test_slack_listener.py` (25 tests). Socket Mode, slash commands, interrupt routing. Item was stale — already done. (verified session 29)
- [x] **`lat.md` knowledge graph — wired into director.py (2026-04-06)** — `lat_inject.py` with TF-IDF `inject_relevant_nodes()` now wired into `_produce_spec()` in director.py (same pattern as planner.py). Silently skips if no relevant nodes match.
- [x] **Adversarial review hallucination rate too high** — FULLY DONE (session 29). `claim_verifier.py` extended with Python symbol (function/class/method) existence checking: `extract_symbol_claims()`, `_build_symbol_index()` (direct .py scan, no grep subprocess), `verify_symbol_claims()`, `verify_all_claims()`, `SymbolReport`, `CompoundClaimReport`. `annotate_result()` surfaces `SYMBOL_CLAIMS_NOT_FOUND`. 24 new tests (61 total). All three hallucination-detection vectors now covered: file paths, symbols, and decompose prompt hardening.

### From link-farm (2026-04-09–11 batch)

- [ ] **Latent Briefing — KV cache compaction for multi-agent memory** — Ramp Labs paper (quoted by @vral). KV cache compaction technique for efficient context sharing across hierarchical multi-agent systems; eliminates need to pass full .md files between agents. Currently Poe passes context as text files; this approach would let child agents share a compressed parent context layer directly. **Priority 5/10 — monitor until implementation details are public.** Source: @vral.

- [ ] **Isolated worktree per sub-agent** — from Alpha Batcher's breakdown of Claude Code's architecture (@alphabatcher). Each sub-agent gets its own git worktree so writes don't collide. Relevant to concurrent run safety (Phase 62 project isolation). Current `is_project_running()` + per-project lock file is a simpler version; worktree isolation is stronger. **Priority 6/10 — revisit when parallel missions are actually running.** Source: @alphabatcher.

- [ ] **Claude Skills quality gate for synthesize_skill** — Avid (@av1dlive) highlights that 80K+ community skills are mostly poorly built. Anthropic engineers' 16-min talk covers what separates good from bad skills. Steal: add a `_skill_quality_score()` heuristic to `synthesize_skill()` — check for: concrete trigger condition, measurable success criterion, ≤5 steps, no LLM hallucinated tool names. Block skills scoring below threshold. **Priority 7/10 — directly applicable to evolver.py skill synthesis.** Source: @av1dlive.

- [ ] **Kronos financial foundation model** — Nav Toor (@heynavtoor). Open-source time-series model trained on 12B candlestick records from 45 exchanges, 93% more accurate than leading models, zero-shot across any asset/timeframe, 4M–499M param sizes. Available on HuggingFace. **Watch list — if Polymarket research resumes, evaluate as price-prediction layer instead of LLM-based price inference.** Source: @heynavtoor.

### From 18-link research runs (2026-04-14, session 30)

Full reports: `docs/research/ai-agent-memory-synthesis.md`, `docs/research/ai-agent-memory-steal-list.md`, `docs/research/x-posts-steal-list-20260414.md`

- [ ] **Proactive memory injection at loop entry** — Engramme (@svpino) architecture: memories surface automatically without explicit query. Portable to Poe: call `knowledge_lens.rank()` at `_build_loop_context()` entry, inject top-3 nodes into system context before step execution. ~10 lines, no new infrastructure. **Priority 8/10 — zero infra cost, direct improvement to memory utilization.** Source: @svpino/Engramme.

- [ ] **synthesize_skill() 3-gate pre-promotion check** — Anthropic engineers' quality bar for Claude Skills (80K+ skills, most poorly built). Three failure modes → three gates before score check: (1) trigger precision — must fire 0 times on 10 off-target inputs; (2) output schema — must define and validate structure; (3) edge case coverage — must pass ≥3 adversarial cases. **Priority 7/10 — directly applicable to evolver.py:synthesize_skill(), high-value for skill quality.** Source: @av1dlive/@eng_khairallah1.

- [ ] **Eval harness + holdout discipline** — evals = new training data (@realsigridjin/Better Harness). Reward-hacking risk: evolver currently evaluates on the same outcomes it was trained on. Fix: add train/holdout split to `run_nightly_eval()` → evolver validates on holdout set only. Prevents self-congratulatory loops where evolver improves its own eval metrics without improving real behavior. **Priority 6/10 — addresses reward-hacking as system matures.** Source: @realsigridjin.

- [ ] **Harness hill-climbing as autonomous loop** — @ashpreetbedi/@mr_r0b0t: use eval benchmark scores as autonomous hill-climbing signal for harness improvement (LangChain TerminalBench 2.0: 52.8→66.5% with no model change). Poe has `eval.py` + `evolver.py` but they're not wired as an autonomous feedback loop. Fix: `run_nightly_eval()` → failure trace analysis → harness proposal → evolver suggestion → `_verify_post_apply`. **Priority 6/10 — closes the verify→learn loop that's currently 80% done.** Source: @ashpreetbedi + @Vtrivedy10.

- [ ] **Associative JSONL memory links (related_ids)** — Engramme: associative recall surfaces neighboring memories without explicit query. Portable: add `related_ids` field to JSONL memory nodes; cosine-similarity pass in `reflect_and_record()` links new nodes to nearby existing ones; when a node is accessed, inject linked neighbors. Approximate associative recall at file-based scale. **Priority 5/10 — medium effort, enhances memory depth.** Source: @svpino/Engramme.

- [ ] **Dumb loop audit (scaffolding designed to be removed)** — Alpha Batcher breakdown of Claude Code: Anthropic's deliberate "thin harness" philosophy. Each scaffold should pass the future-proof test: dropping in a more powerful model should improve performance WITHOUT requiring harness complexity changes. Run a scaffolding audit on agent_loop.py — label each check as load-bearing vs removable. Manus precedent: rebuilt agent 5× in 6 months, each rewrite removed complexity. **Priority 5/10 — strategic/architectural, no code cost.** Source: @alphabatcher/@akshay_pachaar.

### Grok Round 4 feedback (2026-04-07)
- [x] **`poe evolver apply` CLI** — `poe-evolver list|apply|run` subcommands. `apply` supports interactive/--all/--dry-run/by-id modes. Registered as `poe-evolver` entry point. (2026-04-07)
- [x] **`estimate_goal_scope` debug CLI** — `poe-preflight-stats --scope-check "goal"` prints scope + effect string. Registered as `poe-preflight-stats` entry point. (2026-04-07)
- [x] **RAG query API for workers** — `query_lessons(query, n=3, task_type, tiers)` in memory.py. Uses hybrid BM25+RRF (falls back to TF-IDF). Returns List[TieredLesson]. Workers can call this to pull relevant past lessons without full injection. (2026-04-07)
- [x] **Replay mode for A/B testing** — `poe-replay` CLI in strategy_evaluator.py. Supports `--compare` (fitness delta with/without lessons) and `--outcome-id` (load past outcome by id). 5 tests. (2026-04-07)
- [x] **NVIDIA NeMo DataDesigner** — (goodhunt tweet, 95K views) Research complete (2026-04-07). 7 steal items identified: (1) discriminated union config for skills, (2) processor pipeline for skill generation, (3) Jinja2 dependency injection in personas, (4) ViolationType enum config, (5) AIMD throttling for workers, (6) skill usage telemetry, (7) sampler constraints for skill A/B testing. Full report: `output/x-research-20260407T063015Z.md`. Est. 1-2 weeks to implement Phase 57.
- [x] **Feynman research agent** — Research complete (2026-04-07). 6 steal items identified: (8) task ledger + verification log, (9) evidence table + claim tracing, (10) multi-round loop with gap analysis, (11) verifier agent (inline citation), (12) reviewer agent with severity levels, (13) provenance records for skills. Full report: `output/x-research-20260407T063015Z.md`. Est. 2-3 weeks to implement Phase 58.
- [ ] **SERV model family** — (open_founder tweet) "SERV-nano matched GPT-5.4 at 20x lower cost and 3x speed." New model family worth tracking as potential OpenRouter routing option. Research: is there an API? What benchmarks? Low priority until available.
- [x] **Claude Code / OpenClaw / Hermes misconception thread** — (exm7777 tweet) Good framing: these are general-purpose agents not just coding tools. Example: academic research skills for Claude Code (literature review, etc.). Confirms the direction; no new steal items.

## Test Ideas

- [ ] **Polymarket behavioral test** — "Analyze 400M+ Polymarket trades to find behavioral patterns among top wallets — what do winners do differently?" (from hrundel75 link)
- [ ] **"Get Jeremy rich" prompt** — long-term, after trading patterns are validated and backtested. Baby steps.
- [x] **Nootropic with verification** — DONE (2026-04-05). 6/6 steps, 679k tokens, ~11min. `verify:` prefix activated cross-reference pass. Key downgrades from verification: Alpha-GPC evidence weak in healthy adults (only 4 RCTs in MCI/Alzheimer's); Lion's Mane neurogenesis claims are preclinical only; Bacopa "25 studies" corrected to ~12 RCTs. Results: `docs/research/nootropic-stack-verified.md`.
- [x] **Cross-domain transfer** — DONE (2026-04-05). Smart home automation goal: 6/6 steps, 191k tokens, 231s. Full protocol comparison (Zigbee/Z-Wave/WiFi), rollout order, hub scoring (HA 63/70 > Hubitat 51 > SmartThings 50), cost tiers ($625/$1,049/$1,815). Generalization confirmed — system handled a completely new domain without customization.

## Completed (archive)

Items moved here when done, for reference:

- [x] FileTaskStore port (`task_store.py`) — 2026-03-29
- [x] Phase 44 (Self-Reflection) — 2026-03-29
- [x] Phase 45 (Recovery Planner) — 2026-03-29
- [x] Mission resilience (partial milestone status) — 2026-03-29
- [x] 14 e2e smoke tests — 2026-03-29
- [x] Concise step prompting — 2026-03-29
- [x] Data pipeline strategy (prompt) — 2026-03-30
- [x] Outcome-first decomposition (Bitter Lesson) — 2026-03-30
- [x] User context injection (user/ folder) — 2026-03-30
- [x] Agent-generated tools (backtester) — 2026-03-30

From jeremy (clean up and integrate with the above later)
- [ ] Examine the research in research/orchestration-knowledge-layer, and the follow-up research in docs/knowledge-layer (and consolidate into one or the other location). document proper implementation paths and implement the framework, with notes on how to flesh this out as needed. **Note (2026-04-10):** Tracked above in "Memory / Knowledge Layer" section. K0 baseline done; memory.py decomposition + K1-K8 implementation plan needed.
