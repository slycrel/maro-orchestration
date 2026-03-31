# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

Last reviewed: 2026-03-31

---

## Bugs (fix before next stability sprint)

- [x] **Stale mission shortcircuit** — `poe_handle()` returned cached summary instead of new mission. Fixed: skip CEO layer when `--project` is explicit. (`e7ad725`)
- [x] **Rate-limit no recovery** — Claude "hit your limit" → immediate failure. Fixed: exponential backoff retry in `llm.py`. (`e7ad725`)
- [x] **Stale mission still possible without --project** — Fixed: CEO layer now only handles meta-commands (status/inspect/map); actual goals always go direct to run_agent_loop. (`low-hanging-fruit`)
- [ ] **Flaky e2e tests** — `test_empty_result_step` and `test_loop_stuck_detection` occasionally fail due to ScriptedAdapter response cycling timing. Not blocking but should be deterministic.

## Systemic Improvements (ordered by impact)

### Verification / Hallucination Detection
- [x] **Adversarial verification step** — implemented in factory_thin (post-execute, pre-compile) and quality_gate (second pass on Mode 2 runs). Catches overclaimed mechanisms, wrong evidence tiers, contested findings. (`factory` branch, 2026-03-31)
- [ ] **Cross-reference check** — for factual claims, query a second source to verify. Flag disagreements.
- [ ] **Confidence tagging** — each step result should carry a confidence indicator (strong evidence / weak evidence / model inference / unverified).

### Token Efficiency
- [ ] **Data pipeline enforcement** — the DATA PIPELINE STRATEGY prompt is in place but agents still dump raw API output into context on some runs. Need stronger enforcement or a pre-execution check that detects "this step will generate >50KB of raw output" and auto-wraps it in a filter script.
- [ ] **Completed context compression** — as steps accumulate, `completed_context` grows linearly. Summarize older steps to fixed-length summaries after N steps.
- [x] **Lesson injection overhead** — Fixed: capped inject output at 1200 chars in memory.py. (`low-hanging-fruit`)

### Self-Improvement Loop
- [ ] **Evolver signal scanning** — extend meta-evolver to scan outcomes for "business signals" and propose sub-missions autonomously. Mode 2 → Mode 3 bridge. (Grok/Zakin feedback)
- [ ] **Verification patterns on rules** — each rule gets a machine-checkable test before graduating to permanent. (Phase 46, meta_alchemist pattern)
- [ ] **Problem generation (Agent0)** — Stanford's approach: generate problems, solve them, learn from mistakes without supervision. Research in progress via orchestration dogfood run.
- [ ] **LLM + genetic programming (FunSearch)** — iterative optimization where LLM generates and refines solutions. (garybasin link, DeepMind FunSearch paper)

### Director / Mission Level
- [ ] **Clarification milestone** — director asks user for clarification on ambiguous goals before committing resources. YOLO option. (Jeremy request)
- [x] **User-level config defaults** — Added user/CONFIG.md. Wired: default_model_tier. Documented: yolo, always_skeptic, notify_on_complete. (`low-hanging-fruit`)
- [ ] **Skip-Director experiment** — for simple NOW-lane goals, skip Director entirely. Bitter Lesson test.
- [ ] **Multi-agent debate pattern** — bull/bear debaters + risk manager as quality gate. (TradingAgents repo pattern, research in progress)

### Observability
- [ ] **Dashboard as real tool** — Phase 36 dashboard still a prop. Add: mission ancestry tree, live cost, parallel workers, replay button. (Grok feedback)
- [ ] **Replay with "factory mode"** — re-run a mission letting evolver inject self-generated sub-goals.

### Factory Mode Experiment (Mode 3 test)
- [x] **"factory" branch** — created. Two variants: `factory_minimal` (single-call Haiku $0.04-0.06/60s) and `factory_thin` (loop+adversarial Haiku $0.38/375s). Bitter Lesson result: minimal surprisingly competitive; thin+adv matches Mode 2 quality at ~2x lower cost. Scaffolding that's load-bearing: adversarial verification. Scaffolding that's not: persona routing, lesson injection, multi-plan comparison. Polymarket thin+adv pending (needs session-idle run). (2026-03-31)
- [ ] **Factory branch merge decision** — decide whether to merge factory insights back to main or keep experimental. Options: (a) add `--mode thin` flag to handle.py routing thin+adv loop, (b) merge adversarial review patterns (already done in quality_gate), (c) keep factory as benchmark branch.

### Conversation Mining (Phase 48 idea)
- [ ] **Research pass through Telegram + Claude session data** — scrape Poe/Jeremy conversations (Telegram bot history + `~/.claude/projects/` session logs) for orchestration-related ideas, patterns, and deferred concepts. Run them through the system as research goals. Revisiting old ideas with current maturity will surface patterns we missed the first time. Jeremy's gut: as the project progresses, revisiting earlier conversations will yield better/more mature perspectives.

### Infrastructure
- [ ] **Phase 38 subpackage move** — src/ is flat with 49 modules. Deferred (33+ imports per group), revisit when it causes real problems.
- [ ] **Phase 42 nightly eval** — wire eval suite to evolver on a schedule.
- [ ] **Auto-resume daemon** — detect API rate limits, pause mission, poll, resume. (oh-my-claudecode pattern, partially addressed with retry)
- [ ] **Cron persistence** — scheduled missions survive restarts. `jobs.json` pattern. (724-office)
- [ ] **ScheduleCronTool in Poe heartbeat** — wire Poe's cron tool so she can schedule her own future runs from within a mission. Closes the self-managing loop. (claw-code pattern)

### claw-code steal list (github.com/instructkr/claw-code — Claude Code architecture map)
- [ ] **verificationAgent as first-class agent** — Claude Code has `verificationAgent` as a peer to `planAgent`/`exploreAgent` in its built-in agent suite. Promote `verify_step()` to a named agent type with its own system prompt and tool set, not just a function call.
- [ ] **TeamCreateTool pattern** — model-directed dynamic team creation/deletion at runtime. The LLM decides team composition mid-mission, not just at plan time. More dynamic than our Director/Worker hierarchy.
- [ ] **thinkback replay** — session-level decision replay for self-improvement. Replay past missions with hindsight, compare decisions. Maps to Phase 44/45 but at session scope.
- [ ] **effort modifier** — add `effort:` keyword to handle.py routing that sets a thinking/token budget level. Claude Code has a `/effort` command for this; we should support it as a goal prefix modifier.
- [ ] **passes command** — multi-pass review as a unified first-class concept (vs our separate Inspector + adversarial reviewer). Worth unifying.
- [ ] **ultraplan / ultrareview modes** — on-demand deep planning/review beyond normal operation. Discrete "go deeper" mode rather than always-on scaffolding.
- [ ] **bughunter mode** — self-directed code quality scan. Poe scanning her own orchestration code for bugs, not just diagnosing runtime failures.
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
