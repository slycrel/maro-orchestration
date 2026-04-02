# Changelog

## [1.5.0] - 2026-04-01

Session 5: Persistent identity block (GAP 1), session checkpointing/resume (GAP 3). 42 new tests (1983 total, 0 failures).

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
