# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

Last reviewed: 2026-04-10 (session 14)

---

## Bugs (fix before next stability sprint)

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
- [ ] **Compact notation / shorthand vocabulary** — `skills/compact_notation.md` created (2026-04-07). A/B test harness built (2026-04-10): `compact_ab.py` with green/blue per-step comparison, 11 tests. CLI: `python3 -m compact_ab --model cheap --rounds 3`. Pending: run live test, evaluate results. If ≥15% output token reduction with no quality loss, enable `always_inject=true`. Consider LLMLingua as complement if vocabulary approach hits limits.

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

### Conversation Mining (Phase 48 idea)
- [x] **Research pass through Telegram + Claude session data** — DONE (2026-04-05). `poe-mine --no-git` scanned 902 session log ideas → 336 unique after dedup. High-confidence (11): mostly already in BACKLOG. No new ideas injected above threshold. Notable finding from sessions: "knowledge graveyard" concept (temp storage for sub-goal learnings), "positive mid-IQ agent" (ralph approach, done), context size concern for sub-agents (done via context_firewall). Scan tool: `src/convo_miner.py`.

### Architectural (from self-review pass 5, 2026-04-10)
- [x] **Extract LoopStateMachine from agent_loop.py** — DONE (2026-04-10). 16 methods extracted across 14 commits. run_agent_loop reduced from ~1,800 to ~470 lines. While loop body is ~300 lines of orchestration (budget checks, step execution call, extracted method dispatch). All heavy logic in standalone functions. Next: convert to LoopStateMachine class where LoopContext becomes `self`.
- [ ] **Evolver drift detection** — Evolver modifies prompts/thresholds but can't detect if prior evolutions made things worse. Track rolling quality metric per cycle. If metric drops below pre-evolution baseline for N consecutive cycles, flag for rollback.
- [ ] **Lesson contradiction check** — Before promoting any lesson to standing rule, compare against existing rules for contradiction. "Always skip verification" contradicts "always verify." LLM-based comparison at promotion time.
- [ ] **Inspector threshold calibration** — Hardcoded thresholds (`_BREACH_THRESHOLD=0.30`, friction scores, etc.) not validated against real run distribution. Move to config file, add calibration mode that reports false-positive/negative rates against historical outcomes.
- [x] **Handle result formatting unification** — (2026-04-10) pipeline/team/direct/default AGENDA paths in handle.py had 4 near-identical LoopResult→HandleResult formatting blocks. Extracted `_loop_result_to_handle()` helper. Original BACKLOG framing ("plan_NOW/plan_AGENDA/replan are 3 implementations") was inaccurate — they're architecturally different planning modes (NOW=1-shot, Director=multi-ticket, decompose=step pipeline), not duplicated code.

### Memory / Knowledge Layer (K stages — from research/orchestration-knowledge-layer)
- [x] **memory.py decomposition (K1-aligned)** — DONE (2026-04-10). 2,968→530 lines (82% reduction). Split into: `memory_ledger.py` (944L — outcomes, lessons, compression, step traces), `knowledge_web.py` (1,006L — tiered lessons, decay/promotion, TF-IDF, canon tracking), `knowledge_lens.py` (758L — rules, hypotheses, decisions, verification). memory.py is now a thin public API with re-exports + coordination functions (bootstrap_context, reflect_and_record, inject_lessons_for_task).
- [ ] **Consolidate knowledge layer research** — Two locations: `research/orchestration-knowledge-layer/` (original architecture + K0-K8 phases) and `docs/knowledge-layer/` (K0 baseline). Merge into one canonical location with implementation paths documented.
- [x] **llm_parse.py test coverage** — (2026-04-10) 68 unit tests added. Covers all 6 public functions + edge cases (None, NaN, fences, type mismatch unwrapping).

### Test Coverage Gaps (from 2026-04-10 audit)
- [x] **task_store.py tests** — (2026-04-10) 36 unit tests added. Covers enqueue/claim/complete/fail/archive, dependency resolution, cycle detection, stale claim recovery, atomic writes.
- [ ] **orch.py tests** — Orchestration hub, no direct tests. Integration-covered via agent_loop tests but core adapter selection logic untested in isolation.

### Self-Extensibility / Decision Point Hooks (design exploration)
- [ ] **Composable decision-point hooks** — The system currently has pre/post step hooks (step_events.py), inspector observation, quality gate, and prompt injection (standing rules/lessons/skills into decompose). But these aren't composable: you can't say "after decompose, before execution, run extra verification on steps 3 and 5." MTG-style stack where effects can be intercepted at targeted points. For now, prompt-stage injection is sufficient. Revisit when operational experience shows which decision points actually need interception. Key constraint: any self-extensibility must be human-gated (see evolver guardrail auto-apply fix).

### Concurrent Run Safety (hardening)
- [ ] **First-class project isolation** — Currently: file locking on full-rewrite paths (skills, tiered lessons, hypotheses, rules) prevents data corruption; standing rules and decisions are domain-filtered during injection. Still needed for true concurrent runs: per-project skill pools (or project tag on skills + filtered matching), project-scoped lesson injection (currently filters by task_type but not project), per-project lockfile in set_loop_running(), concurrent run safety audit across all write paths. Add project field to Skill dataclass and wire through find_matching_skills(). Captain's Log should tag entries with project for filtered views. Low priority while runs are sequential; required before enabling parallel missions.

### Captain's Log extensions (from Grok Round 5 feedback, 2026-04-10)
- [ ] **Input classification tag** — Extend `context` field in log entries with input characteristics (URL type, content type, source). Prevents circuit breakers from firing on domain mismatches (the Jina scenario). Log `INPUT_MISMATCH` when a skill is invoked on out-of-domain input.
- [ ] **Director context hook** — Let the Director query last N captain's log entries during decompose. "What has the learning system been doing?" context injection. Stubbed in spec, not yet wired.
- [ ] **Dashboard captain's log panel** — When dashboard becomes command center (Jeremy's vision), captain's log is natural sidebar/tab. Scrollable, filterable, linked to artifacts.

### From X research runs (2026-04-09)

Six X posts researched via live Poe missions. Actionable items extracted:

- [ ] **markitdown integration** — Microsoft's `markitdown` (96K stars) converts PDF/Word/Excel/Audio/HTML → Markdown for LLMs. `pip install 'markitdown[all]'`. Evaluate for `web_fetch.py` or new `file_ingest.py` module. MCP server available (`markitdown-mcp`). Source: @_vmlops post.
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
