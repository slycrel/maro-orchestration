# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

Last reviewed: 2026-04-14 (session 20)

---

## Bugs (fix before next stability sprint)

### Session 20 (2026-04-14) — adversarial review findings (`output/self-review-report-20260414T040637Z-blind.md`)

- [x] **CRITICAL: Evolver broken state persistence** — FIXED (commit `4b8dd7e`). `_verify_post_apply` now tracks `applied_ids` and iterates `revert_suggestion` on test failure. 3 new tests cover fail→revert, pass→no-revert, and legacy int-count backward compat. The `revert_suggestion` no-op for `prompt_tweak` is honest now (lessons decay naturally) — separate item if we want true snapshot/restore.
- [ ] **CRITICAL: Silent exception swallowing (systemic)** — `agent_loop.py` has 15+ `except Exception: pass` sites in first 1,000 lines. Checkpoint, hook blocking, skill attribution, security scans all silently no-op. Fix: ERROR-level logging; for correctness-affecting sites, raise or set a finalization-blocking flag.
- [x] **CRITICAL: LoopPhase is string constants, not state machine** — FIXED (session 21). `LoopStateMachine` class with `_ALLOWED` transitions dict; `set_phase` raises `InvalidTransitionError`. Wired at 7 transition points in `run_agent_loop`. 8 tests.
- [x] **HIGH: Director bypassed in practice** — FIXED (session 21). Added `now_lane.escalate_to_director` config flag + `_is_complex_directive()` heuristic. Complex NOW-classified goals optionally reclassify to AGENDA for Director routing. Default: off (existing behavior unchanged).
- [ ] **HIGH: Inspector signal reliability** — (a) escalation tone detector uses keywords `error/failed/stuck` — fires on every stuck session; (b) backtracking detector uses positional order not timestamps; (c) context-churn check verifies lesson *presence* not *application*. Fix: LLM tone classifier; timestamp-ordered backtrack; lesson-reference detection.
- [x] **HIGH: Evolver `cost_optimization` silent no-op** — FIXED (commit `4b8dd7e`). Explicit branch in `apply_suggestion` sets `applied=False`, `status=pending_human_review`, with block_reason. Test added. Real auto-apply executor still TODO if we ever want one.
- [ ] **HIGH: Test coverage width not depth** — no `--cov`, LLM calls fully mocked, no mutation testing, no concurrency tests for `task_store.py` fcntl. Fix: add `pytest-cov` with 70% floor; 3+ end-to-end tests with real LLM fixtures; concurrent-write tests for task_store.
- [x] **MODERATE: `_steps_are_independent` regex heuristic** — Expanded `_DEPENDENCY_PATTERNS` to catch aggregation verbs (compile/synthesize/aggregate/summarize/analyze) and generic prior-output references ("the findings", "based on results", "with the above", "given the data", "comparing the results"). 7-case regression test added. False-positive direction (mark independent as dependent) is safe — only disables parallelism. False-negative direction (the race-condition direction) is what got tightened.
- [x] **MODERATE: `rate^steps` math false alerts** — Replaced cumulative-product formula with a 5-step sliding window. Healthy 90% long runs no longer fire. Extracted `_compute_march_of_nines` helper for direct testing; 4 unit tests cover healthy long run, recent degradation, below-min-steps, exact-threshold boundary.
- [x] **MODERATE: Memory Stage 2→3 and 3→4 not implemented** — FIXED (session 21). Stage 2→3: evolver scans canon candidates, surfaces as crystallization Suggestions (human-gated). Stage 3→4: extract_skills() was silently broken (s.summary/s.step → AttributeError); fixed to use s.result/s.text. Skill crystallization now fires on successful runs.
- [x] **MODERATE: `_process_blocked_step` 18+ parameters** — Introduced `BlockedStepContext` dataclass; function now takes `(ctx, blk)` instead of 21 args. Body unchanged (unpack at top); call site rewritten to construct the dataclass.
- [x] **MINOR: `new_guardrail` permanently gated** — Now auto-applies in non-prod (default), held in prod. Override hierarchy: `POE_AUTO_APPLY_GUARDRAILS=1` forces on, `=0` forces hold, unset uses `config.environment` (default `dev`). 3 integration tests cover prod/dev/explicit-off paths.
- [~] **MINOR: Persona auto-selection missing** — Hallucinated. Auto-selection already exists: `persona.py:793` (`persona_for_goal`) with keyword routing + scoring + LLM fallback + freeform creation; called from `handle.py:615` in AGENDA flow. NOW lane intentionally skips persona injection (1-shot path). No fix needed.

### Session 20 infrastructure bugs

- [x] **File-claim verifier truncates first char of cited paths** — FIXED (commit `a34228b`). Tightened lookbehind from `(?<![\`'\"(])` to `(?<![\w\`'\"(])` so matches can't start one char into a backtick-wrapped path. 4 regression tests cover backtick/single-quote/paren/word-adjacent wrappers.
- [ ] **pytest-via-subprocess 900s timeout** — `python3 -m pytest tests/ -q` via `ClaudeSubprocessAdapter` hits 900s timeout (real pytest ~100s). Diagnosis correctly classified as `adapter_timeout` and recovered via smaller sub-commands (`--lf`, `tail`, `head`). Root cause unclear — possibly stdout buffering. Worth investigating before next adversarial run.
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
- [ ] **Codebase Graph + LSP** — Pre-build ranked call graph before agent reads any file. Use real LSP (go-to-definition, call hierarchy) for surgical context. Multi-agent coordination via context bus. Claimed 1.8x faster, 2.1x cheaper (unverified). **Priority 9/10.** Source: @bniwael / SoulForge.
- [x] **Evals-as-Training-Data flywheel** — (2026-04-11 session 16) `mine_failure_patterns()` → `generate_evals_from_patterns()` → `run_eval_flywheel()`. Failure-class scoring for 9 types, trend tracking, auto-suggestions. Wired into `run_nightly_eval()`. 29 tests. Source: @realsigridjin.
- [x] **Thinking Token Budget** — (2026-04-11 session 16) `THINKING_HIGH/MID/LOW` constants, `thinking_budget` param on all adapters. AnthropicSDK: extended thinking API. Wired into decompose (HIGH) and advisor_call (MID). Source: @av1dlive.
- [ ] **Harness Is the Problem** — "Models are fine, the harness isn't good enough." Evolver should target harness code paths, not just prompts. Friction = harness quality signal. Strategic validation of project direction. **Priority 8/10.** Source: @sebgoddijn / Ramp Glass.
- [ ] **Harness Architecture Spectrum** — Thin (Anthropic) vs thick (LangChain) loop — best products live in the middle. Validate NOW/AGENDA checkpoint placement; inspector at all checkpoints. **Priority 7/10.** Source: @akshay_pachaar.
- [ ] **Event-driven subprocess wakeup** — Replace polling with event-driven wakeup (asyncio.Queue or file-based event). Workers post completion signals. **Priority 7/10.** Source: @teknium / NousResearch hermes-agent.
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
- [ ] **Stale test skills in workspace** — 13 of 31 skills in `~/.poe/workspace/memory/skills.jsonl` have wrong stored hashes, generating ~13 WARN lines per goal. IDs are obvious test fixtures (sk000000-4, skhashm, skbad001, skgood01, skpolyma, skmulti, skcookin, skresear, skstatt, skanalyz, sksystem, myskill, goodskill, skupdata, "iterative build" dup 91616d7a). Real skills verify clean. `poe-doctor --cleanup-skills` (session 19) removes orphans but doesn't catch stale hashes. Fix: extend `poe-doctor --cleanup-skills` to also detect `compute_skill_hash(skill) != stored_hash` and offer to prune.
- [x] **Playbook deduplication bug** — FIXED (session 19). `append_to_playbook()` now checks if core entry text exists before appending. Also wrapped with `locked_write()`.
- [x] **skills.py read-modify-write race** — FIXED (session 19). `save_skill()` and `record_skill_outcome()` now use `locked_write()` from file_lock.py.
- [ ] **Constraint false-positive on step descriptions** — Constraint system scans step *text* (from decomposer), not just LLM output. Decomposer wrote "Clone repo (rm -rf first)" → DESTROY-tier constraint blocked step before LLM ran (0 tokens, 8ms). Two fixes needed: (a) decomposer prompt should avoid shell commands in step text, (b) constraint pre-scan should be softer on step descriptions vs actual tool calls. Found during dev agent test.
- [ ] **11 unlocked bare-append JSONL paths** — captain-log, outcomes, step-costs, calibration, etc. all do bare `open('a').write()` without file locking. Safe for single-writer but will corrupt under concurrent appends. P3.
- [ ] **Inspector dual report classes (InspectorReport vs InspectionReport)** — Surfaced by 2026-04-13 regression self-audit (Issue 3). Two parallel dataclasses at inspector.py:316 and :412 with incompatible schemas; `inspect_session()` returns InspectionReport while spec §12 canonicalizes InspectorReport. Downstream (Evolver) reads `suggestions.jsonl` but report type producing it is ambiguous. Needs architectural decision — merge, rename, or document intentional split. P3.
- [ ] **Inspector verify_claim_tiered P1/P2 threshold asymmetry** — Surfaced by 2026-04-13 regression self-audit (Issue 5). P1 (lessons, line 888) uses `max(2, len//2)` proportional threshold; P2 (standing rules, line 910) uses hardcoded `2`. Rated MODERATE — asymmetry is real but intent is defensible (standing rules are durable/authoritative, may warrant looser match). Decide: document intent inline, or normalize thresholds. P4.
- [ ] **Cross-backend failover on 4xx/5xx** — Surfaced 2026-04-13 PM/dev regression: director default build_adapter path hit OpenRouter 402 (Payment Required), degraded to single-ticket fallback instead of trying subprocess/anthropic. `model.backend_order` config (shipped this session) handles *startup* ordering but not *runtime* failover when the chosen backend starts returning errors. Design: adapter wrapper that catches RuntimeError/402/429/5xx, logs the backend change, walks to the next backend in the configured order. Watch for infinite-loop risk (both backends down) and cost surprise (silently jumping to a billed route). P2.
- [ ] **Director persona authoring skill** — Mirrors existing skill-graduation pattern for personas. Trigger: director detects a recurring role/work pattern (e.g., N+1 dispatches of "file gh issues" with no matching persona). Action: spawn sub-goal to author `personas/<slug>.md` + write to workspace personas dir. Future runs pick it up via workspace→repo resolution. Seed case: session 19's PM/dev cycle — no `personas/pm.md` meant director wrote essays instead of filing issues, needed repeat directive scaffolding. Keep authorship **organic** (director-generated, not internet-sourced — prompt-injection surface). persona.py already has graduation hooks; extend skill-creation pipeline with a persona-creation variant. Aligns with project_next_leap (auto persona+skill packaging). P2.
- [ ] **Prompt-injection hardening for persona + skill ingestion** — Deferred, picks up after persona-authoring lands. Any externally-sourced persona or skill is an instruction-injection vector (can redirect tools, exfiltrate, bypass constraints). Needs: (a) allowlist of source directories, (b) content scan for known-bad patterns (`ignore previous`, `system:`, tool-call strings outside expected formats), (c) optional sandboxed validation run before a new persona/skill can be auto-applied, (d) explicit human-review gate on new externally-sourced artifacts. Not urgent while all persona authoring stays organic/internal. P3.

**Architectural gaps surfaced:**
- [ ] **Phase audit: verify "done" phases against current code** — Multiple phases likely marked done that are only surface-level implemented. Phase 45 (recovery planner) is the proven example: diagnosis built, action side never closed. Run orchestrator against each phase's ROADMAP description, verify claims match code. Jeremy's priority.
- [x] **Cross-ref not wired into step execution** — FIXED (session 19). `verify_step_with_cross_ref()` in step_exec.py. Heuristic `_has_specific_claims()` detects file paths, line numbers, function names. Triggers cross-ref for specific claims. Annotates disputes, doesn't block.
- [x] **No anti-hallucination prompt in EXECUTE_SYSTEM** — FIXED (session 19). ANTI-HALLUCINATION section + NEED_INFO mechanism added to EXECUTE_SYSTEM. Steps can say NEED_INFO: [what's missing] to trigger research sub-steps.
- [x] **Shared artifact layer for step context** — FIXED (session 19). `complete_step` tool extended with `artifacts` field. Stored in `loop_shared_ctx` as `artifact:{step}:{name}`. Injected into subsequent steps as "Artifacts from prior steps" block.
- [ ] **PAT missing pull_requests:write** — Dev agent pushed branch but couldn't create PR. Token 2 needs PR write permission added. (Fixed mid-session by Jeremy but document for future tokens.)

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
- [ ] **Artifact output routing cleanup** — Temp artifacts (per-step intermediaries) should go to a tmp dir, deleted by default, optionally kept via config flag (`keep_artifacts: true` — flip to true during testing). Semi-permanent outputs (final reports, research results) should route to `~/.poe/workspace/output/`. Currently everything mixes together unpredictably.

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
- [ ] **Evolver confidence calibration** — Self-reported confidence (0.0-1.0) never validated against real outcomes. Track outcome of each applied suggestion, compute empirical confidence. P2.
- [x] **Evolver suggestion rollback API** — (2026-04-12 session 17) `revert_suggestion(suggestion_id)` reads change_log.jsonl, reverses action based on before_state (restore skill desc, remove created skill, remove dynamic constraint). CLI: `poe-evolver --revert <id>`. Logs EVOLVER_REVERTED to captain's log.
- [ ] **Semantic memory deduplication** — Lesson dedup uses hash/first-100-chars, not semantic similarity. Embedding-based similarity check at write time would prevent unbounded growth. P2.
- [ ] **LoopStateMachine conversion** — LoopContext becomes `self`. Enables exhaustive state coverage in tests. Eliminates context-threading complexity. P1 architectural.

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
- [ ] **orch.py tests** — Orchestration hub, no direct tests. Integration-covered via agent_loop tests but core adapter selection logic untested in isolation.

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
- [ ] **First-class project isolation** — Currently: file locking on full-rewrite paths (skills, tiered lessons, hypotheses, rules) prevents data corruption; standing rules and decisions are domain-filtered during injection. Still needed for true concurrent runs: per-project skill pools (or project tag on skills + filtered matching), project-scoped lesson injection (currently filters by task_type but not project), per-project lockfile in set_loop_running(), concurrent run safety audit across all write paths. Add project field to Skill dataclass and wire through find_matching_skills(). Captain's Log should tag entries with project for filtered views. Low priority while runs are sequential; required before enabling parallel missions.

### Captain's Log extensions (from Grok Round 5 feedback, 2026-04-10)
- [ ] **Input classification tag** — Extend `context` field in log entries with input characteristics (URL type, content type, source). Prevents circuit breakers from firing on domain mismatches (the Jina scenario). Log `INPUT_MISMATCH` when a skill is invoked on out-of-domain input.
- [x] **Director context hook** — (2026-04-11 session 16) Captain's log context + playbook + knowledge nodes now injected into `_build_loop_context()`. Director sees recent learning events, operational wisdom, and relevant knowledge at decompose time.
- [ ] **Dashboard captain's log panel** — When dashboard becomes command center (Jeremy's vision), captain's log is natural sidebar/tab. Scrollable, filterable, linked to artifacts.

### From X research runs (2026-04-09)

Six X posts researched via live Poe missions. Actionable items extracted:

- [x] **markitdown installed** — `pip install --user markitdown` done (Python 3.14). HTML→MD confirmed working. High-value use case: PDF/Word/Excel ingestion (Jina can't handle these). Wiring into `web_fetch.py` or `file_ingest.py` is next step — needs `fetch_file(path_or_url)` that falls back to markitdown for non-HTML content types.
- [ ] **TOOLS.md + STYLE.md gaps** — @imjustinbrooke's "7 files to run your business" framework maps to Poe: SOUL.md ✓, AGENTS.md ✓, USER.md ✓, MEMORY.md ✓, HEARTBEAT.md ≈ heartbeat scripts. Missing: explicit TOOLS.md (tool registry covers this partially) and STYLE.md (persona covers this partially). Consider whether explicit files add value.
- [ ] **Eval-driven harness hill-climbing** — @mr_r0b0t + @ashpreetbedi both endorse @Vtrivedy10's LangChain article on using evals as autonomous learning signal. This IS evolver.py's pattern. Read full article when available — may have concrete recipes to improve the eval→lesson→skill pipeline.
- [ ] **Letta API comparison** — @carsonfarmer/@sarahwooders: Anthropic's Managed Agents API mirrors Letta's 1yr-old API. Provider-managed memory = lock-in. Poe's file-based memory is aligned with "memory outside providers" thesis. Monitor Managed Agents API for useful features without adopting their memory model.
- [ ] **Team OS / shared context layer** — @aakashgupta: 250+ structured docs/quarter compound into organizational knowledge. Validates knowledge layer K1-K2 investment. The "learning flywheel" pattern (each commit makes the repo smarter) is the vision for standing rules + lesson promotion.
- [ ] **Auto-detect repo stack → skill discovery + summarization** — @ihtesham2005: project scan → tech-stack detection → skill suggestions/install + compact agent summary (CLAUDE.md-style condensation). The real steal is skill discovery + summarization, not just install. Later: pruning irrelevant skills, keeping context lean, monorepo-aware routing, updating recommendations as repo changes. Medium priority onboarding/discovery idea. Source: codex analysis of https://x.com/ihtesham2005/status/2042338547429212367

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
- [ ] **Phase 24 (Slack) still PARTIAL** — primary async human interface, no `slack.py`, no tests. (known, noted again)
- [x] **`lat.md` knowledge graph — wired into director.py (2026-04-06)** — `lat_inject.py` with TF-IDF `inject_relevant_nodes()` now wired into `_produce_spec()` in director.py (same pattern as planner.py). Silently skips if no relevant nodes match.
- [x] **Adversarial review hallucination rate too high (partial)** — `src/claim_verifier.py` shipped (2026-04-06). Zero-LLM file-path extractor checks synthesis step results against filesystem; annotates NOT_FOUND claims. Wired into agent_loop.py on synthesis steps. Addresses option (a). Options (b) stream decompose (done via decompose prompt 2026-04-06) and (c) haiku sanity pass (pre_flight.py, done 2026-04-06) also shipped. Remaining: claim verifier only catches file paths — function/module existence and "X has no tests" type claims still undetected. Future: extend to grep-based function existence check.

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
