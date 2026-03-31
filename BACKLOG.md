# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

Last reviewed: 2026-03-31 (late evening session)

---

## Bugs (fix before next stability sprint)

- [x] **Stale mission shortcircuit** — `poe_handle()` returned cached summary instead of new mission. Fixed: skip CEO layer when `--project` is explicit. (`e7ad725`)
- [x] **Rate-limit no recovery** — Claude "hit your limit" → immediate failure. Fixed: exponential backoff retry in `llm.py`. (`e7ad725`)
- [x] **Stale mission still possible without --project** — Fixed: CEO layer now only handles meta-commands (status/inspect/map); actual goals always go direct to run_agent_loop. (`low-hanging-fruit`)
- [ ] **Flaky e2e tests** — `test_empty_result_step` and `test_loop_stuck_detection` occasionally fail due to ScriptedAdapter response cycling timing. Not blocking but should be deterministic.

## Systemic Improvements (ordered by impact)

### Verification / Hallucination Detection
- [x] **Adversarial verification step** — implemented in factory_thin (post-execute, pre-compile) and quality_gate (second pass on Mode 2 runs). Catches overclaimed mechanisms, wrong evidence tiers, contested findings. (`factory` branch, 2026-03-31)
- [ ] **LLM Council / multi-angle critique skill** — Karpathy's LLM Council ported to Claude Code skill: spawn N sub-agents with distinct critical framings (devil's advocate, domain skeptic, implementation critic) that critique a plan/idea before synthesis. Direct cure for AI sycophancy. Relevant for Director's pre-plan challenger and quality gate. (hesamation/@x, 2026-03-31)
- [ ] **Cross-reference check** — for factual claims, query a second source to verify. Flag disagreements.
- [x] **Confidence tagging** — each step result should carry a confidence indicator (strong evidence / weak evidence / model inference / unverified). Done: `confidence` field added to complete_step tool schema (optional enum), StepOutcome dataclass, and completed_context entries tagged with `[confidence:X]`. (2026-03-31)

### Token Efficiency
- [ ] **Data pipeline enforcement** — the DATA PIPELINE STRATEGY prompt is in place but agents still dump raw API output into context on some runs. Need stronger enforcement or a pre-execution check that detects "this step will generate >50KB of raw output" and auto-wraps it in a filter script.
- [x] **Completed context compression** — older entries compressed to one-liner after step 5; last 3 steps kept at full length. 47-63% reduction at 7-12 steps. Zero token cost. (`agent_loop.py`, 2026-03-31)
- [x] **Lesson injection overhead** — Fixed: capped inject output at 1200 chars in memory.py. (`low-hanging-fruit`)

### Self-Improvement Loop
- [ ] **Evolver signal scanning** — extend meta-evolver to scan outcomes for "business signals" and propose sub-missions autonomously. Mode 2 → Mode 3 bridge. (Grok/Zakin feedback)
- [x] **Phase 46: Intervention Graduation** — `graduation.py` shipped. Scans diagnoses for repeated failure classes (≥3x), proposes high-confidence Suggestions that evolver auto-applies. 8 failure classes covered. CLI: `poe-graduation`. (2026-03-31)
- [x] **Verification patterns on rules** — each graduated rule gets a machine-checkable test before going fully permanent. Done: `verify_pattern` shell command on all 8 templates; `verify_graduation_rules()` and `poe-graduation --verify` CLI. (meta_alchemist pattern, Phase 46 follow-on, 2026-03-31)
- [ ] **Problem generation (Agent0)** — Stanford's approach: generate problems, solve them, learn from mistakes without supervision. Research in progress via orchestration dogfood run.
- [ ] **LLM + genetic programming (FunSearch)** — iterative optimization where LLM generates and refines solutions. (garybasin link, DeepMind FunSearch paper)

### Director / Mission Level
- [x] **Clarification milestone** — director asks user for clarification on ambiguous goals before committing resources. YOLO option. Done: `check_goal_clarity()` in intent.py, wired in handle.py AGENDA path; skippable with `yolo: true` in user/CONFIG.md. (2026-03-31)
- [x] **User-level config defaults** — Added user/CONFIG.md. Wired: default_model_tier. Documented: yolo, always_skeptic, notify_on_complete. (`low-hanging-fruit`)
- [ ] **Skip-Director experiment** — for simple NOW-lane goals, skip Director entirely. Bitter Lesson test.
- [ ] **Multi-agent debate pattern** — bull/bear debaters + risk manager as quality gate. (TradingAgents repo pattern, research in progress)

### Observability
- [ ] **Dashboard as real tool** — Phase 36 dashboard still a prop. Add: mission ancestry tree, live cost, parallel workers, replay button. (Grok feedback)
- [ ] **Replay with "factory mode"** — re-run a mission letting evolver inject self-generated sub-goals.

### Factory Mode Experiment (Mode 3 test)
- [x] **"factory" branch** — created. Two variants: `factory_minimal` (single-call Haiku $0.04-0.06/60s) and `factory_thin` (loop+adversarial Haiku $0.38/375s). Bitter Lesson result: minimal surprisingly competitive; thin+adv matches Mode 2 quality at ~2x lower cost. Scaffolding that's load-bearing: adversarial verification. Scaffolding that's not: persona routing, lesson injection, multi-plan comparison. (2026-03-31)
- [x] **Factory comparison complete** — Full comparison table in /tmp/factory-comparison.md. Key: thin+adv+verify nootropic: $0.36/493s/6 steps done. thin+adv polymarket: $1.40/574s/7 of 8 steps (Haiku token explosion on research = 4.4× Mode 2 tokens, so cost advantage disappears for complex goals). Mode 2 polymarket: $1.27/1156s/8 steps done on Sonnet. (2026-03-31)
- [ ] **Factory branch merge decision** — Adversarial patterns already merged to main (quality_gate two-pass, handle.py contested claims). Remaining option: add `--mode thin` flag to handle.py for when wall-time matters more than depth. Ralph verify (--verify) validated useful for research goals. (2026-03-31)
- [x] **Token efficiency prompt in factory_thin** — Added "Target under 500 tokens" constraint to FACTORY_STEP. Matches Mode 2's EXECUTE_SYSTEM language. (2026-03-31)
- [x] **Factory branch merge decision** — Adversarial patterns already merged to main. Factory files (factory_minimal.py, factory_thin.py) available as standalone modules. Full merge (factory to main) done 2026-03-31.

### Conversation Mining (Phase 48 idea)
- [ ] **Research pass through Telegram + Claude session data** — scrape Poe/Jeremy conversations (Telegram bot history + `~/.claude/projects/` session logs) for orchestration-related ideas, patterns, and deferred concepts. Run them through the system as research goals. Revisiting old ideas with current maturity will surface patterns we missed the first time. Jeremy's gut: as the project progresses, revisiting earlier conversations will yield better/more mature perspectives.

### Infrastructure
- [ ] **Phase 38 subpackage move** — src/ is flat with 49 modules. Deferred (33+ imports per group), revisit when it causes real problems.
- [x] **Phase 42 nightly eval** — wire eval suite to evolver on a schedule. Done: `run_nightly_eval()` in eval.py; fires via `eval_every=1440` in heartbeat_loop(); failures → evolver Suggestion entries. (2026-03-31)
- [ ] **Auto-resume daemon** — detect API rate limits, pause mission, poll, resume. (oh-my-claudecode pattern, partially addressed with retry)
- [x] **Cron persistence** — scheduled missions survive restarts. `jobs.json` pattern. Done: `src/scheduler.py` with `JobStore` backed by `memory/jobs.json`; supports once/daily/interval schedules; `drain_due_jobs()` wired into `heartbeat_loop()`; `poe-schedule` CLI. 21 tests. (724-office steal, 2026-03-31)
- [x] **ScheduleCronTool in Poe heartbeat** — wire Poe's cron tool so she can schedule her own future runs from within a mission. Closes the self-managing loop. Done: `schedule_run` tool added to `EXECUTE_TOOLS` in step_exec.py; parses 'daily at HH:MM' / 'in N minutes/hours/days' / ISO datetime; calls scheduler.add_job(); 13 tests. (2026-03-31)

### claw-code steal list (github.com/instructkr/claw-code — Claude Code architecture map)
- [ ] **verificationAgent as first-class agent** — Claude Code has `verificationAgent` as a peer to `planAgent`/`exploreAgent` in its built-in agent suite. Promote `verify_step()` to a named agent type with its own system prompt and tool set, not just a function call.
- [ ] **TeamCreateTool pattern** — model-directed dynamic team creation/deletion at runtime. The LLM decides team composition mid-mission, not just at plan time. More dynamic than our Director/Worker hierarchy.
- [ ] **thinkback replay** — session-level decision replay for self-improvement. Replay past missions with hindsight, compare decisions. Maps to Phase 44/45 but at session scope.
- [x] **effort modifier** — add `effort:` keyword to handle.py routing that sets a thinking/token budget level. Done: `effort:low/mid/high` prefix in handle.py strips keyword and overrides model tier (low→cheap, mid→mid, high→power). (claw-code steal, 2026-03-31)
- [ ] **passes command** — multi-pass review as a unified first-class concept (vs our separate Inspector + adversarial reviewer). Worth unifying.
- [ ] **ultraplan / ultrareview modes** — on-demand deep planning/review beyond normal operation. Discrete "go deeper" mode rather than always-on scaffolding.
- [x] **bughunter mode** — self-directed code quality scan. Poe scanning her own orchestration code for bugs, not just diagnosing runtime failures. Done: `src/bughunter.py` with stdlib AST scanner (BH001 bare except, BH003 mutable defaults, BH004 shadowed builtins, BH010 TODOs); `poe-bughunter` CLI. 16 tests. Src scans clean. (claw-code steal, 2026-03-31)
- [ ] **btw (by-the-way) mode** — non-blocking observation mode; agent surfaces observations without interrupting workflow. Good for Inspector-style notes that don't block step execution.

## Research to Process

### Links fetched but not fully digested
- [x] **TradingAgents** (github.com/TauricResearch/TradingAgents) — multi-agent Polymarket trading. Dogfood run complete. Steal items in STEAL_LIST.md: commitment-forced verdicts (done), pre-plan challenger, two-tier model routing.
- [x] **Stanford Agent0** — self-improvement without supervision. Dogfood run complete. Results in projects/agent0-research/. Key: problem generation + self-evaluation loop. Maps to evolver.
- [ ] **Polymarket behavioral analysis** (hrundel75) — 400M trades / 2400 wallets. Good prompt for different Polymarket test: "find behavioral patterns not picks."
- [x] **LLM sycophancy** (rohanpaul/karpathy) — models mirror prompts not truth. Addressed: adversarial verification step now auto-injects for research goals.
- [ ] **Build-your-own-X** (agenticgirl) — 484k star repo, learning methodology. Low priority.
- [ ] **FunSearch/EUREKA/Voyager papers** (garybasin) — LLM + genetic programming. Mode 3 territory. Read the actual papers.
- [x] **claw-code** (github.com/instructkr/claw-code) — Python skeleton of Claude Code's leaked TS source. Most code is stubs but the tool/command inventory is a goldmine. Key findings: verificationAgent is a first-class built-in; TeamCreateTool exists; thinkback/replay is a real pattern; $ralph mode (OmX) validated our Ralph verify loop. Steal list added above. (2026-03-31)
- [x] **vtrivedy10 tweet** (x.com/vtrivedy10/status/2038346865775874285) — Viv @Vtrivedy10 (LangChain agents/evals) on "harnesses" for autonomous agents. Key findings from related @systematicls article: (1) Instruction fade-out is real — agents cut corners as context accumulates, event-driven reminders at decision points (not just system prompt) fix this. (2) Verification is the highest-leverage investment — success correlates with ability to verify own work. (3) Multi-layer defense: prompt + schema + runtime gates + tool validation + lifecycle hooks. (4) Dual-memory: episodic (events.jsonl) + working (completed_context) — we have both. These validate Mode 2 scaffolding direction. ~~Steal candidate: inject contextual guidance at step retry/budget-exceeded decision points (not just in initial system prompt).~~ **DONE: agent_loop.py now re-injects goal+constraints every 5 steps and on every retry. (2026-03-31)** New steal items added to STEAL_LIST.md LATER: role-specific tool visibility, back-pressure lifecycle hooks, subagent context firewall.

### Grok feedback sessions
- [x] grok-response-2.txt — oh-my-claudecode, 724-office, Mimir steal list. Processed, items in STEAL_LIST.md.
- [x] grok-response-3.txt — Bitter Lesson Engineering + Mode 1/2/3 taxonomy. Processed, implemented outcome-first decomposition + user context.
- [ ] **PAI (danielmiessler/Personal_AI_Infrastructure)** — 10.7k stars, TELOS files, hooks system. Worth a deeper look for the hook patterns.

## Test Ideas

- [ ] **Polymarket behavioral test** — "Analyze 400M+ Polymarket trades to find behavioral patterns among top wallets — what do winners do differently?" (from hrundel75 link)
- [ ] **"Get Jeremy rich" prompt** — long-term, after trading patterns are validated and backtested. Baby steps.
- [ ] **Nootropic with verification** — same nootropic stack prompt but with adversarial verification pass added to the pipeline.
- [ ] **Cross-domain transfer** — run a goal from a completely new domain (e.g. home automation, travel planning) to test generalization.

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
