# Changelog

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
