# Changelog

## [1.9.0] - 2026-04-03

Session 9: Pi steal — remaining NEXT items. Runtime tool extension, human-readable session export, session branching. 47 new tests (2329 total, 0 failures, 1 pre-existing flaky).

### Added — Runtime Tool Extension (Pi self-extending agent pattern)
- `src/runtime_tools.py` — `RuntimeTool` dataclass (name, description, bash_template, parameters); `_RuntimeToolStore` with lazy disk load + auto-register into `tool_registry` singleton; `register_runtime_tool()`, `dispatch_runtime_tool()`, `list_runtime_tools()`, `clear_runtime_tools()`. Persists to `memory/runtime_tools.json` across sessions.
- `register_tool` added to `EXECUTE_TOOLS` (WORKER role only) — agent provides name, description, bash_template, optional parameters_json; handler in step_exec.py registers the tool and injects it into `_active_tools` immediately.
- `dispatch_runtime_tool()` called in the `else` branch of step_exec tool dispatch — unknown tool names now check runtime registry before blocking.
- `src/agent_loop.py` — `_active_tools` replaced with `_resolve_tools()` closure re-queried per step; newly registered tools appear in subsequent steps without restarting.
- `tool_registry.py` — `register_tool` added to `_ROLE_MAP` as WORKER-only.
- `tests/test_runtime_tools.py` — 20 tests: RuntimeTool unit, register/dispatch, persistence round-trip, global registry integration.

### Added — Human-Readable Session Export
- `src/checkpoint.py` — `export_human(loop_id) -> Optional[str]`: renders checkpoint as markdown with goal, loop_id, progress summary, per-step sections (icon, status, truncated result). Returns None if checkpoint not found.
- `poe-checkpoint export <loop_id>` CLI subcommand; `-o FILE` flag writes to file instead of stdout.

### Added — Session Branching
- `src/checkpoint.py` — `parent_loop_id: str` field on `Checkpoint` dataclass; included in `to_dict()` only when set; `from_dict()` handles missing key. `branch_checkpoint(loop_id) -> Optional[str]`: copies checkpoint with new loop_id + parent tracked; returns new loop_id.
- `poe-checkpoint branch <loop_id>` CLI subcommand; prints new loop_id and resume command.
- `tests/test_checkpoint_extended.py` — 27 tests: parent_loop_id field, export_human (content, truncation, markdown structure, missing), branch_checkpoint (independence, parent tracking, chain), CLI integration.

## [1.8.0] - 2026-04-03

Session 8: Pi coding agent synthesis + steal execution. System prompt token audit (1892→936 tokens, -51%). Architecture non-goals doc. STEAL_LIST.md updated with Pi items. BACKLOG.md updated.

### Changed — System Prompt Token Audit (Pi steal)
- `src/step_exec.py` — `EXECUTE_SYSTEM` trimmed from ~844 to ~333 tokens (-61%). Cuts: removed editorial commentary ("your output is consumed by downstream agents"), deduplicated negatives (URL policy already implied "don't fetch"), removed rules 1 and 5 from TOKEN EFFICIENCY (covered by other sections), cut the polymarket example from DATA PIPELINE STRATEGY (the 3 steps are sufficient). All behavior-changing content preserved.
- `src/planner.py` — `DECOMPOSE_SYSTEM` trimmed from ~1048 to ~603 tokens (-42%). Cuts: reduced 3 BAD/GOOD pairs in STEP GRANULARITY to 2 (removed the clone/review example, covered by the setup rule), merged CODE REVIEW STEPS into the granularity section, removed the second BAD/GOOD pair in LONG-RUNNING COMMANDS (build is same pattern as test), shortened the PARALLEL EXECUTION example from 7 steps to 4. All rules preserved.
- Combined system prompt cost: 1892 → 936 tokens (-51%). Pi's target was <1k combined.

### Added — Architecture Non-Goals
- `docs/ARCHITECTURE_NON_GOALS.md` — Documents 8 deliberate non-goals: tool minimalism (Poe is an orchestrator, not a coding REPL), MCP-as-default, interactive approval gates, hidden sub-agents, full Neo4j, plugin marketplace, provider portability contracts, headless UI. Each entry has rationale and revisit conditions. Prevents scope creep during planning discussions.

### Changed — Steal List / Backlog
- `STEAL_LIST.md` — Added Pi coding agent section with 5 steal candidates (2 DONE, 2 TODO, 1 LATER). Sources section updated.
- `BACKLOG.md` — Token audit and architecture non-goals marked complete under Token Efficiency.

## [1.7.0] - 2026-04-02

Session 7: LLM parse robustness overhaul, bughunter anti-pattern detectors, Phase 41 implementation (tool registry, curated skill loader, step event model, tool search, deferred tools), magic keyword prefixes, doctor Phase 41 checks, skill auto-export. 139 new tests (2282 total, 0 failures).

### Added — LLM Parse Robustness
- `src/llm_parse.py` — `extract_json()` (depth-counter bracket matching, markdown fence stripping, type validation, list-unwrapping), `safe_float()` (None/NaN/Inf/non-numeric guards), `safe_str()`, `safe_list()`, `content_or_empty()`, `strip_markdown_fences()`, `_find_json_bounds()`
- `tests/test_llm_parse_robustness.py` — 90 tests covering all failure modes: markdown-fenced JSON, None content, malformed JSON, truncated responses, type mismatches, wrapped lists, refusal messages, nested braces in string fields
- Wired `llm_parse` into 17 modules replacing `rfind + json.loads` pattern: `director.py`, `step_exec.py`, `memory.py`, `intent.py`, `evolver.py`, `quality_gate.py`, `mission.py`, `attribution.py`, `inspector.py`, `planner.py`, `skills.py`, `sprint_contract.py`, `verification_agent.py`, `factory_thin.py`, `interrupt.py`, `cross_ref.py`, `thinkback.py`

### Added — Bughunter Anti-Pattern Detectors
- `src/bughunter.py` — BH011 (`json.loads(content[`/`raw[` rfind-slice pattern), BH012 (`float(data/raw/parsed/r/result.get(...)` float-on-LLM-dict pattern)
- `_scan_llm_parse_patterns()` — regex-based static detector wired into `scan_file()`; skips `llm_parse.py` itself and JSONL-reading patterns to avoid false positives

### Added — Phase 41: Tool Registry (step 1-2, completed previous session)
- `src/tool_registry.py` — `ToolDefinition`, `PermissionContext` (glob deny patterns), `ToolRegistry` (role→deny→is_enabled→sort pipeline), module-level `registry` singleton, `worker_context()`/`short_context()`/`inspector_context()`/`director_context()` factories
- Role constants: `ROLE_WORKER`, `ROLE_SHORT`, `ROLE_INSPECTOR`, `ROLE_DIRECTOR`, `ROLE_VERIFIER`
- `src/step_exec.py` — `get_tools_for_role(role, deny_patterns=None)` using registry; backward-compat lists retained
- `src/agent_loop.py` — `permission_context` param on `run_agent_loop()`; `_active_tools` resolved at composition time from `PermissionContext`
- `tests/test_tool_registry.py` — 45 tests

### Added — Phase 41: Curated Skill Loader (step 3-4)
- `src/skill_loader.py` — `SkillSummary` dataclass, `SkillLoader` class: `load_summaries(role)`, `find_matching(goal, role)`, `load_full(name)`, `get_summaries_block(role, goal)`; `_parse_frontmatter()` for YAML-ish frontmatter; module-level `skill_loader` singleton
- `skills/` directory — 4 seed SKILL.md files with YAML frontmatter: `web_research`, `code_implement`, `debug_investigate`, `data_analysis`
- Progressive disclosure: summaries (name + description + triggers) injected into decompose prompt; full body loaded on demand via `load_full()`
- Wired `skill_loader.get_summaries_block()` into `agent_loop._build_loop_context()` alongside runtime skills; merged into `skills_context` before decompose
- `_build_loop_context()` now accepts `permission_context=None` and forwards role to skill loader
- `tests/test_skill_loader.py` — 42 tests

### Added — Phase 41: Step Event Model (step 5)
- `src/step_events.py` — `PreStepEvent`, `PostStepEvent`, `StepVeto`, `StepVetoedError`; `StepEventBus` with `@on_pre_step(match=)`, `@on_post_step(match=)`, `register_pre()`, `register_post()`, `unregister()`, `clear()`, `fire_pre()`, `fire_post()`, `list_handlers()`; module-level `step_event_bus` singleton
- Glob matcher on step_text — handlers fire only for matching steps (e.g. `match="create_*"`)
- Blocking semantics: `fire_pre()` returns `StepVeto` to veto execution; non-blocking: `fire_post()` swallows all handler exceptions
- Wired into `step_exec.execute_step()`: `fire_pre` fires after constraint check; `fire_post` fires before final return with elapsed_ms and result
- Refactored `execute_step` outcome paths to collect `_outcome` dict + single return (cleaner, enables post-fire)
- `tests/test_step_events.py` — 35 tests

### Added — Doctor Phase 41 checks
- `src/doctor.py` — Added Phase 41 checks: tool registry (expected tools registered), curated skills (SKILL.md count), step event bus (handler count), bughunter scan result
- Added `--json` flag stub to CLI (`argparse`-based)
- 10 tests in `tests/test_doctor.py`

### Added — Magic keyword prefixes
- `handle.py` — `ralph:` and `verify:` prefixes enable per-step Ralph verify loop (alias for `ralph_verify=True`)
- `handle.py` — `pipeline:` prefix marks goal as data-heavy (future: injects pipeline enforcement mode flag)
- `handle.py` — `strict:` prefix enables thorough quality passes: council + cross-reference checks (wires `run_council=True, run_cross_ref=True` into `run_quality_gate`)
- 8 new tests in `tests/test_handle.py`

### Added — Phase 41: Tool Search / Deferred Tool Resolution (step 6)
- `src/tool_search.py` — `resolve_deferred_tools(query, ctx, registry)`: glob/substring/description matching, returns full schemas for deferred tools; `format_tool_search_result()`: human-readable schema block for LLM injection; `inject_tool_search_if_needed()`: adds `tool_search` schema to tool list when deferred stubs present; `TOOL_SEARCH_SCHEMA`: always-full tool definition
- Wired into `step_exec.execute_step()`: deferred tool detection, `inject_tool_search_if_needed` applied before LLM call, `tool_search` tool call handling with schema resolution and LLM re-call with expanded tool list
- 26 tests in `tests/test_tool_search.py`

### Added — Skill Auto-Export (Hermes steal)
- `src/skill_loader.py` — `export_skill_as_markdown(skill, skills_dir, overwrite)`: converts a runtime `Skill` (from skills.jsonl) to SKILL.md in `skills/`; `_slugify()` for safe filenames; invalidates `skill_loader` cache on write
- `src/skills.py` — `maybe_auto_promote_skills()` now calls `export_skill_as_markdown()` after each promotion; newly established skills become available to `SkillLoader` immediately
- 18 new tests in `tests/test_skill_loader.py` (export + slugify)

### Changed
- `BACKLOG.md` — lat.md and promotion cycle marked done; last reviewed updated

## [1.6.0] - 2026-04-01

Session 6: Knowledge graph (lat.md), promotion cycle + decision journal, Polymarket claim validation, Phase 41 architecture design. 25 new tests (2013 total, 0 failures).

### Added — Phase 55: lat.md Knowledge Graph
- `lat.md/` directory — 9 concept files cross-linked via `[[wiki links]]`: `core-loop`, `memory-system`, `self-improvement`, `worker-agents`, `quality-gates`, `poe-identity`, `checkpointing`, `intent-classification`, `constraint-system`
- `lat.md/lat.md` — index of all concept nodes
- `# @lat: [[node#Section]]` backlinks added to key source modules: `agent_loop.py`, `planner.py`, `checkpoint.py`, `memory.py`, `worker-agents.md`
- `lat check` passes clean (0 broken links)

### Added — Phase 56: Promotion Cycle — Standing Rules + Decision Journal
- `StandingRule` dataclass — `rule_id`, `rule`, `domain`, `confirmations`, `contradictions`, `promoted_at`, `source_lesson_id` (JSONL: `memory/standing_rules.jsonl`)
- `Hypothesis` dataclass — pre-promotion rule candidate (JSONL: `memory/hypotheses.jsonl`)
- `observe_pattern(lesson, domain, source_lesson_id)` — create/increment hypothesis; auto-promotes at `RULE_PROMOTE_CONFIRMATIONS=2`; returns `StandingRule` on promotion
- `contradict_pattern(lesson, domain)` — demotes hypothesis if `contradictions > confirmations`; increments rule contradiction count
- `inject_standing_rules(domain)` — formatted rules block injected into every decompose call (unconditional)
- `Decision` dataclass — `decision_id`, `decision`, `rationale`, `domain`, `alternatives`, `trade_offs`
- `record_decision(decision, rationale, domain, ...)` — writes to `memory/decisions.jsonl`
- `search_decisions(query, domain, limit)` — TF-IDF ranking; returns relevant prior decisions
- `inject_decisions(goal, domain)` — formatted prior decisions block for decompose injection
- Wired into `agent_loop.py` — standing rules + decisions injected into every `_build_decompose_context()` call
- `memory_status()` updated to report standing rule count
- `tests/test_promotion_cycle.py` — 25 tests

### Research / Documentation
- `research/POLYMARKET_BTC_LAG_VALIDATION.md` — @slash1sol BTC lag claim validated as UNCONFIRMED. Binary YES/NO contracts (not continuous price feed), 4% round-trip fees vs 0.3% claimed edge = −13x EV. No build warranted.
- `research/PHASE41_TOOL_REGISTRY_DESIGN.md` — Phase 41 implementation design from Claude Code architecture analysis. 8 sections: tool registry, role-gated visibility, progressive skill disclosure, hook lifecycle, function calling schema. Implementation order documented.
- `research/X_LINKS_SYNTHESIS.md` — ingested 5 X/Twitter posts via Jina-based orchestration loop; ranked steal candidates; lat.md + promotion cycle selected for immediate build
- STEAL_LIST.md — X links research batch added; lat.md and promotion cycle marked DONE; Phase 41 and Polymarket claim documented
- ROADMAP.md — Phases 55 and 56 added with full spec entries
- CLAUDE.md — current state table updated through Phase 56

---

## [1.5.0] - 2026-04-01

Session 5: Persistent identity block (GAP 1), session checkpointing/resume (GAP 3). 42 new tests (1988 total, 0 failures).

### Added
- `src/poe_self.py` — `load_poe_identity()`, `with_poe_identity()` — identity block injected into every decompose call (Phase 53)
- `user/POE_IDENTITY.md` — durable, user-editable Poe identity file; used as source of truth for `poe_self`
- `src/checkpoint.py` — `write_checkpoint()`, `load_checkpoint()`, `resume_from()`, `delete_checkpoint()`, `list_checkpoints()` (Phase 54)
- `poe-checkpoint` CLI — list/show/delete saved checkpoints
- `run_agent_loop(resume_from_loop_id=...)` — checkpoint resume parameter; skips already-completed steps
- Checkpoint written after each step; deleted on successful loop completion; retained for resume on stuck/partial
- `tests/test_poe_self.py` — 18 tests
- `tests/test_checkpoint.py` — 24 tests

### Changed
- `planner.py::decompose()` — identity block now prepended to `DECOMPOSE_SYSTEM` via `with_poe_identity()` on every call

---

## [1.4.0] - 2026-04-01

Session 4 (overnight, continued): Cross-reference fact verification. 39 new tests.

### Added — Phase 52: Cross-Reference Check (`src/cross_ref.py`)
- `ClaimVerification` dataclass: claim, category, status (confirmed/disputed/unknown), confidence, note, elapsed_ms
- `CrossRefReport` dataclass: verified list, disputes list, `has_disputes` property, `dispute_summary()`, `full_summary()`
- Two-stage pipeline: `extract_verifiable_claims(text, adapter)` → `verify_single_claim(claim, category, adapter)`
- Verification uses fresh LLM context per claim — verifier never sees the original response (prevents confirmation bias)
- `run_cross_ref(text, adapter, dry_run, max_claims, dispute_threshold)` — full pipeline, never raises
- `cross_ref_annotation(report)` — empty string when no disputes, safe to always append
- Wired into `run_quality_gate(run_cross_ref=True)` as Pass 2.5; disputes trigger ESCALATE + `QualityVerdict.cross_ref` field added
- `poe-cross-ref --text "..." [--file FILE] [--max-claims N] [--dispute-threshold N]` CLI

### Tests
- 39 tests: `tests/test_cross_ref.py` — claim extraction, verification, full pipeline, annotation, quality_gate integration

---

## [1.3.0] - 2026-04-01

Session 4 (overnight): Thinkback replay, unified passes pipeline, Hermes evaluation, factory experiment findings. 60 new tests.

### Added — Phase 50: Thinkback Replay (`src/thinkback.py`)
- `ThinkbackReport` dataclass: per-step `StepReview` (decision_quality: good/acceptable/poor, hindsight_note, counterfactual), overall_assessment, mission_efficiency (0.0–1.0), key_lessons, would_retry, retry_strategy
- `run_thinkback(loop_result)` — replays a LoopResult through hindsight LLM analysis; falls back to dry-run mode on adapter failure; never raises
- `run_thinkback_from_outcome(outcome_dict)` — works directly from outcomes.jsonl records (synthesizes steps from summary + lessons)
- `_save_thinkback_lessons()` — writes extracted lessons to `memory/lessons.jsonl` tagged `[thinkback:{run_id}]`
- `load_latest_outcome()` / `load_outcome_by_id(id)` — outcome loading helpers for the CLI
- `poe-thinkback --latest [--task-type TYPE] [--dry-run] [--save]` CLI

### Added — Phase 51: Passes — Unified Multi-Pass Review Pipeline (`src/passes.py`)
- `PassConfig` — configures which passes to run: quality_gate, adversarial, council, debate, thinkback
- Named presets: `quick` (quality_gate), `standard` (+adversarial), `thorough` (+council), `full` (+debate), `all` (+thinkback)
- `PassConfig.from_names(["council","debate"])` / `PassConfig.from_preset("thorough")` constructors
- `PassResult` — per-pass verdict/reason/escalate/elapsed_ms
- `PassReport` — aggregates all passes: escalate=True if any pass escalated, escalation_reason = first escalating pass reason
- `run_passes(goal, step_outcomes, config=..., preset=..., loop_result=...)` — chains passes, never raises
- Council/debate absorbed into quality_gate's internal passes when co-enabled; thinkback always standalone
- `poe-passes --goal "..." --passes council,debate [--latest-outcome] [--output FILE]` CLI

### Changed
- `pyproject.toml`: added `poe-thinkback` and `poe-passes` entry points

### Research / Documentation
- Hermes (NousResearch/hermes-agent) evaluation complete — keep OpenClaw + poe-orchestration. Steal candidates: Skill Document auto-extraction, persistent user modeling (Honcho-style), terminal persistence backends. See BACKLOG.md.
- Factory overnight experiment: factory_minimal hit subprocess timeout (300s) on complex research goal — confirms single-call architecture has hard ceiling. Phase 49 prerequisite: configurable timeout.
- PAI (danielmiessler/Personal_AI_Infrastructure) steal items documented in BACKLOG.md: TELOS-style structured context injection, hook-based lifecycle callbacks.
- ROADMAP.md: Phases 50 and 51 added.

### Tests
- 31 tests: `tests/test_thinkback.py` — StepReview, ThinkbackReport, dry-run, adapter, from_outcome, save_lessons
- 29 tests: `tests/test_passes.py` — PassConfig presets, PassResult, PassReport, run_passes integration
- All 65 source files pass bughunter scan (0 issues)

---

## [1.2.0] - 2026-03-31

Session 3: Verification, council, evolver signals, data pipeline enforcement, context compression, Skip-Director, dashboard, TeamCreateTool, multi-agent debate, factory mode. 450+ new tests (total ~1290+).

### Added — Adversarial Verification (`src/quality_gate.py`, `src/verification_agent.py`)
- `VerificationAgent` class: `verify_step()`, `adversarial_pass()`, `quality_review()` — first-class verification agent
- `run_llm_council()` — 3 critics (devil's advocate, domain skeptic, implementation critic) run in parallel; escalates if 2+ rate WEAK. `CouncilVerdict` + `CouncilCritique` dataclasses
- `run_debate()` — Bull/Bear/Risk Manager pattern; `DebatePosition` + `DebateVerdict` dataclasses; CAUTION+REJECT both escalate; wired as Pass 4 in `run_quality_gate(with_debate=True)`
- `poe-verify` CLI

### Added — Evolver Signal Scanning (`src/evolver.py`)
- `scan_outcomes_for_signals()` — scans done outcomes for actionable leads, converts to `sub_mission` Suggestion entries; wired into `run_evolver(scan_signals=True)`

### Added — Data Pipeline Enforcement (`src/agent_loop.py`)
- `_is_data_heavy_step()` — detects risky steps (fetch all, list all, polymarket-cli, etc.) and injects `DATA PIPELINE ENFORCEMENT` block
- `_result_looks_like_raw_dump()` — post-checks results (>2000 chars + high brace density) and prepends `[RAW_OUTPUT_DETECTED]`

### Added — Skip-Director Experiment (`src/director.py`, `src/handle.py`)
- `_is_simple_directive()` classifier (≤15 words, no complex keywords)
- `skip_if_simple=True` in `run_director()` routes simple goals directly to `run_agent_loop`
- `direct:` prefix in `handle.py` forces AGENDA lane + skips quality gate + escalation overhead
- `skip_if_simple=True` wired into `telegram_listener.py`

### Added — Dashboard as Real Tool (`src/observe.py`)
- Cost panel: 24h spend + per-model breakdown from `step-costs.jsonl`
- Mission Ancestry Tree: scans all workspace projects for `ancestry.json` files
- Replay button: POST /api/replay re-runs last outcome's goal in background thread
- External binding: `0.0.0.0` (was 127.0.0.1) — reachable on LAN

### Added — TeamCreateTool Pattern (`src/team.py`)
- `create_team_worker(role, task)` — spins up specialist with custom persona
- 8 known roles: market-analyst, risk-auditor, fact-checker, data-extractor, devil-advocate, synthesizer, strategist, domain-skeptic
- `create_team_worker` tool in `EXECUTE_TOOLS_WORKER` (not SHORT/INSPECTOR)

### Added — Phase 46: Intervention Graduation (`src/graduation.py`)
- Scans diagnoses for repeated failure classes (≥3x), proposes permanent rules as high-confidence suggestions
- 8 failure classes covered with verify_pattern shell commands
- `poe-graduation [--verify]` CLI

### Added — Other
- Completed context compression: older entries → one-liner after step 5; 47-63% reduction at 7-12 steps
- Confidence tagging: `confidence` field in `StepOutcome`, `complete_step` tool schema, `completed_context` entries
- Clarification milestone: `check_goal_clarity()` in `intent.py`; skippable with `yolo: true` in `user/CONFIG.md`
- User-level config: `user/CONFIG.md` — default_model_tier, yolo, always_skeptic, notify_on_complete
- Cron persistence: `src/scheduler.py` with `JobStore` backed by `memory/jobs.json`; `poe-schedule` CLI
- `schedule_run` tool in `EXECUTE_TOOLS` — agents can schedule their own future runs
- `effort:low/mid/high` prefix in `handle.py` — overrides model tier
- `ultraplan:` prefix — sets model=power, max_steps=12
- `mode:thin` prefix — routes to factory_thin loop
- `btw:` prefix — non-blocking observation mode
- `bughunter` — AST scanner for BH001/BH003/BH004/BH010; `poe-bughunter` CLI
- Nightly eval wired to evolver via heartbeat

### Fixed
- `_GOAL_MAP_KEYWORDS` "how does" too broad — removed; added specific phrases
- `run_debate` import json missing in outer try block — all parsing silently failed
- `run_debate` parameter name collision with function name — renamed to `with_debate`

### Factory Mode
- `factory_minimal` (single-call Haiku) and `factory_thin` (loop+adversarial) built and benchmarked
- Adversarial patterns merged to main; load-bearing scaffolding identified
- Full comparison: `docs/FACTORY_MODE_FINDINGS.md`

---

## [1.1.0] - 2026-03-27

Token burn reduction: 789k → 67k (91%) through pre-fetch layer, clean markdown fetching, and sub-agent tool restrictions.

### Added — Web Pre-fetch Layer (`src/web_fetch.py`)
- `_jina_fetch()`: Jina AI Reader (`r.jina.ai`) returns clean markdown from any URL — no raw HTML in context
- `fetch_x_tweet()`: tries Jina first (full thread), then authenticated X CLI, then oEmbed fallback
- `fetch_x_article()`: returns immediate human-readable notice (X native articles are deprecated/inaccessible)
- `_x_cli_available()` / `_x_cookie_env()` / `_fetch_via_x_cli()`: authenticated X scraping via OpenClaw's `x-twitter-cli.sh`
- Second-pass URL following: resolves t.co links and X article links found in fetched content (not just step text)
- `enrich_step_with_urls(extra_context=...)`: scans prior step summaries so later steps can access URLs introduced earlier

### Changed — Sub-agent Token Hygiene (`src/agent_loop.py`, `src/llm.py`)
- `ClaudeSubprocessAdapter` now passes `--disallowedTools WebFetch,WebSearch` — prevents sub-agent from fetching raw HTML (was primary source of 200–535k token spikes per step)
- `_EXECUTE_SYSTEM` — added URL FETCHING POLICY: sub-agent must use only pre-fetched content, no curl/wget/tool fetches
- `_EXECUTE_SYSTEM` — added TOKEN EFFICIENCY section: prefer concise output, avoid verbatim quotes, work with partial info
- `_execute_step()` — passes `completed_context` as `extra_context` to `enrich_step_with_urls` so URLs from step 1 are available to step 3 (fixes context-carry bug that caused step 3 blocks)

### Tests
- 1290 tests passing (up from 1264)
- `tests/test_web_fetch.py`: 26 tests covering html stripping, URL extraction, X routing, Jina integration, enrich pipeline

---

## [1.0.0] - 2026-03-23

Phases 1–7 complete. Poe is now a fully autonomous, self-improving AI concierge reachable via Telegram.

### Added — Phase 1: Autonomous Loop
- `src/agent_loop.py`: goal → decompose → execute steps → done|stuck loop (`poe-run`)
- Basic stuck detection (same action 3x)

### Added — Phase 2: NOW/AGENDA Routing
- `src/intent.py`: LLM + heuristic intent classifier
- `src/handle.py`: unified entry point auto-routing to fast (NOW) or deep (AGENDA) lane (`poe-handle`)

### Added — Phase 3: Director/Worker Hierarchy
- `src/director.py`: Director agent plans, delegates, reviews (`poe-director`)
- `src/workers.py`: research/build/ops/general workers with persona system prompts

### Added — Phase 4: Loop Sheriff + Heartbeat
- `src/sheriff.py`: per-project stuck detection + `check_system_health()` + heartbeat state I/O
- `src/heartbeat.py`: 60s health loop with 3-tier recovery (scripted → LLM diagnosis → Telegram escalation)
- `deploy/poe-heartbeat.service`: systemd unit

### Added — Phase 5: Memory + Learning (Reflexion)
- `src/memory.py`: outcome recording, LLM lesson extraction, session bootstrap, lesson injection
- Files: `memory/outcomes.jsonl`, `memory/lessons.jsonl`, daily logs

### Added — Phase 6: Telegram + Platform-agnostic LLM
- `src/llm.py` (rewrite): `ClaudeSubprocessAdapter`, `AnthropicSDKAdapter`, `OpenRouterAdapter`, `OpenAIAdapter` behind one interface; `MODEL_CHEAP/MID/POWER` constants; `build_adapter("auto")`
- `src/telegram_listener.py`: long-poll listener, slash commands, immediate-ack + edit UX (`poe-telegram`)
- `src/ancestry.py`: goal ancestry chain (§18 spec) — `ancestry.json` per project, prompt injection, `orch ancestry/impact` CLI
- `deploy/poe-telegram.service`: systemd unit
- Slash commands: `/status /director /research /build /ops /ancestry /help`

### Added — Phase 7: Meta-Evolution (§19)
- `src/evolver.py`: analyzes last N outcomes, identifies failure patterns, generates structured suggestions; wired into heartbeat loop every 10 ticks (`poe-evolver`)
- `memory/suggestions.jsonl`: persistent suggestion store

### Added — Phase 8: Scaling + Evaluation
- `src/metrics.py`: quality tracking — success rate, cost, token usage per task type (`poe-metrics`)
- `src/eval.py`: benchmark suite with known-good goals and scoring (`poe-eval`)
- Concurrent loop support: `run_parallel_loops()` in agent_loop.py
- Crew composition: `infer_crew_size()` in workers.py
- Auto-apply evolver suggestions: `poe-evolver --list` / `poe-evolver --apply <id>`

### Added — Docs
- `docs/ARCHITECTURE.md`: full system architecture, module dependency graph, data flows
- `ROADMAP.md`: updated to reflect Phases 0–7 complete, Phase 8 next

### Changed
- `src/agent_loop.py`: ancestry context injected into decompose + execute prompts
- `src/cli.py`: 14 new subcommands across all phases

### Tests
- 346 tests passing (up from ~50 at v0.4.0)

---

## [0.4.0] - 2026-03-11

### Added
- `src/cli.py` with `init|next|done|log|blocked|report`
- priority file support (`projects/<slug>/PRIORITY`) and priority-aware global scheduling
- blocked-project triage and report generation helpers
- parser/unit tests and CLI integration tests (`tests/`)
- smoke harness (`scripts/smoke.sh`)
- CI workflow (`.github/workflows/ci.yml`)
- migration + queue adapter + compatibility + security + end-to-end docs

### Changed
- `scripts/new_project.sh` and `scripts/mark_next_done.sh` now route through CLI
- scripts and CLI now emit explicit error taxonomy codes for common failures

### Fixed
- roadmap M1-M4 items were converted from plan-only to executable implementation
