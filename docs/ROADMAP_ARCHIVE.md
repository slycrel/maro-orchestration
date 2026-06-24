## Phase 0: Foundation Audit *(COMPLETE)*

Honest baseline. Tag what we actually have before building forward.

- [x] Audit every file — tag what works, what's scaffolding, what's dead
- [x] Close stale issues/tasks that reference old M1-M4 plan
- [x] Add `VISION.md` (intent guide) to repo root
- [x] Replace `ROADMAP.md` with this phased plan
- [x] Move source docs (`poe_intent.md`, `poe_orchestration_spec.md`, `poe_miscommunication_patterns.md`) into `docs/`
- [x] Update `MAINLINE_PLAN.md` to reflect v0.5.0 baseline
- [x] Tag `v0.5.0` — honest foundation

**Shippable artifact:** Tagged v0.5.0 release with accurate documentation reflecting the real state.

---

## Phase 1: Autonomous Loop *(COMPLETE)*

**THE critical unlock.** Without this, nothing else matters. Poe gets an LLM brain.

- [x] Define LLM adapter interface (model-agnostic: input messages → response + tool calls)
- [x] Implement adapter for cheapest available model first
- [x] Build loop runner: goal → plan → act → observe → decide (continue/done/stuck)
- [x] Wire loop runner to existing task queue
- [x] Add structured output: each loop iteration produces a log entry
- [x] Implement basic stuck detection (same action repeated 3x)
- [x] End-to-end test: give a goal via CLI → watch it execute autonomously
- [x] Verify existing `pytest` passes

**Artifact:** `poe-run "goal"` — autonomous loop. `src/agent_loop.py`

---

## Phase 2: NOW/AGENDA Lanes *(COMPLETE)*

Route work to the right execution path.

- [x] Build intent classifier: NOW or AGENDA
- [x] NOW lane: 1-shot execution
- [x] AGENDA lane: multi-step loop runner
- [x] Wire both lanes to `poe-handle "message"`
- [x] Test: routing verified

**Artifact:** `poe-handle "message"` auto-routes. `src/handle.py`, `src/intent.py`

---

## Phase 3: Director/Worker Hierarchy *(COMPLETE)*

Multi-agent delegation.

- [x] Director agent: directive → SPEC + TICKET → worker dispatch
- [x] Worker agents: research, build, ops, general — each with persona
- [x] Plan acceptance gates: explicit vs inferred
- [x] Review cycle: Director reviews worker output
- [x] Handle relays Director summaries to Jeremy

**Artifact:** `poe-director "directive"`. `src/director.py`, `src/workers.py`

---

## Phase 4: Loop Sheriff + Heartbeat *(COMPLETE)*

Independent progress validation and self-healing.

- [x] Loop Sheriff: monitors running loops for progress
- [x] Progress detection: artifact freshness + decision log freshness
- [x] Escalation chain: Sheriff → Telegram
- [x] Heartbeat loop: periodic health check (gateway, model, config, disk)
- [x] Tiered recovery: scripted fixes (T1) → LLM diagnosis (T2) → Telegram escalation (T3)
- [x] Heartbeat fires meta-evolver every 10 ticks for continuous improvement
- [x] Tests: Sheriff, health checks, recovery tiers verified

**Artifact:** `poe-heartbeat [--loop]`. `src/sheriff.py`, `src/heartbeat.py`

---

## Phase 5: Memory + Learning *(COMPLETE)*

Poe remembers across sessions and improves over time.

- [x] Session bootstrap: loads full state from persisted files
- [x] Outcome tracking: record what worked/failed and why
- [x] Reflexion pattern: lessons extracted per run, injected in future prompts
- [x] Audit trail: `memory/outcomes.jsonl`, daily logs, `memory/lessons.jsonl`
- [x] Test: complete a goal → verify memory persisted and lessons loaded

**Artifact:** `memory context/outcomes/lessons`. `src/memory.py`

---

## Phase 6: OpenClaw + Telegram Integration *(COMPLETE)*

The gateway integration. Poe talks to Jeremy through Telegram.

- [x] Telegram polling listener: messages → intent classification → lane routing → response
- [x] Platform-agnostic LLM adapters: subprocess (claude -p), Anthropic SDK, OpenRouter, OpenAI — all behind one interface
- [x] Response timing: immediate ack (~1s) + edit with final response
- [x] Slash commands: `/director`, `/research`, `/build`, `/ops`, `/status`, `/ancestry`, `/help`
- [x] Goal ancestry (§18): parent_id + ancestry chain, prompt injection, CLI traversal helpers
- [x] systemd service files: `poe-telegram.service`, `poe-heartbeat.service`
- [x] Wire to OpenClaw gateway (ws://127.0.0.1:18789) — `src/gateway.py` (Phase 15)
- [x] Interruption handling: new messages additive/corrective — `src/interrupt.py` (Phase 9)

**Artifact:** `poe-telegram [--loop]`. `src/telegram_listener.py`, `src/llm.py`, `src/ancestry.py`

---

## Phase 7 (§19): Meta-Evolution *(COMPLETE)*

Poe proposes its own improvements based on failure patterns.

- [x] Meta-Evolver: reviews last N outcomes, identifies failure patterns
- [x] Generates structured suggestions (prompt_tweak | new_guardrail | skill_pattern | observation)
- [x] Stores suggestions to `memory/suggestions.jsonl`
- [x] Wired into heartbeat loop (fires every 10 heartbeat cycles)
- [x] Telegram notification of suggestions (optional)
- [x] Auto-application of suggestions — `poe-evolver --apply <id>` (Phase 8)

**Artifact:** `poe-evolver [--dry-run]`. `src/evolver.py`

---

## Phase 8: Scaling + Evaluation *(COMPLETE)*

Concurrent projects, crew composition, quality tracking.

- [x] Concurrent project support: multiple AGENDA lanes running in parallel
- [x] Crew composition: dynamic worker pool sizing based on task complexity
- [x] Quality tracking: per-goal success rate, time-to-completion, cost-per-goal
- [x] Evaluation suite: benchmark goals with known-good outcomes
- [x] Cost optimization: identify expensive patterns, generate cheaper alternatives
- [x] Auto-apply evolver suggestions after review-and-approve workflow

**Artifact:** `poe-metrics`, `poe-eval [--dry-run]`, `poe-evolver --list/--apply`. `src/metrics.py`, `src/eval.py`, `run_parallel_loops()`, `infer_crew_size()`

---

---

## Phase 9: Interruption Handling *(COMPLETE)*

Source-agnostic interrupt system baked into the agent loop, not just Telegram.

- [x] `InterruptQueue`: file-backed, thread-safe — any interface posts, loop consumes
- [x] Intent classification: additive / corrective / priority / stop (LLM + heuristic fallback)
- [x] Loop refactored from `for` to `while` with mutable step queue — supports mid-run injection
- [x] Loop lock file (PID-verified) — Telegram routes to interrupt queue when loop is active
- [x] `/stop` slash command in Telegram listener
- [x] `poe-interrupt "message"` CLI subcommand
- [x] 35 new tests, 449 total passing

**Artifact:** `poe-interrupt "message"`. `src/interrupt.py`, loop integration in `src/agent_loop.py`

---

## Phase 10: Mission Layer + Background Execution *(COMPLETE)*

**Milestone-gated multi-day goal pursuit.** The goal ancestry chain gains a formal hierarchy with validation checkpoints and fresh context per unit of work.

Inspired by Factory AI Missions research.

- [x] Formal hierarchy: Mission → Milestone → Feature → Worker Session
- [x] Each Feature gets a fresh context window (no single session holds the whole project)
- [x] Milestone validation gate: before advancing, validate accumulated work (tests, artifacts, integration)
- [x] Sequential-first parallelization: parallelize within features, sequential across milestones
- [x] Background execution primitive: start long-running process, continue other work, poll result asynchronously
- [x] Skill library: extract reusable execution patterns from completed goal chains, surface to future orchestration
- [x] Skills wired into agent_loop decompose prompts
- [x] `poe-mission`, `poe-mission-status`, `poe-background`, `poe-skills` CLI subcommands
- [x] 72 new tests, 521 total passing

**Artifact:** `src/mission.py`, `src/background.py`, `src/skills.py`, `poe-mission` CLI

---

## Phase 11: Hooks + Reviewers at Every Level *(COMPLETE)*

**Pluggable callbacks at each hierarchy level.** Any layer — mission, milestone, feature, step — can have hooks attached: code reviewers, coordinators, simple reporters, or custom scripts.

Inspired by Jeremy's request for injectable reviewers/coordinators, and Factory AI's planning-tool-with-live-checkoffs pattern.

- [x] Hook registry: register named hooks at mission/milestone/feature/step scope
- [x] Hook types: `reviewer` (LLM critique before advancing), `reporter` (non-blocking), `coordinator` (routing injection), `script` (shell command, non-blocking), `notification` (Factory System Notifications — injects context mid-run)
- [x] Built-in hooks: step reviewer, milestone validator, progress reporter, plan alignment notification
- [x] Factory System Notifications pattern: notification hooks inject contextual guidance at the right moment
- [x] Wired into `run_mission()` and `run_agent_loop()` — only fires when hooks are enabled
- [x] `poe-hooks list/enable/disable/add-reporter/run-builtin` CLI
- [x] 35 new tests, 556 total passing

**Artifact:** `src/hooks.py`, built-in hook library, `poe-hooks` CLI

---

## Phase 12: Oversight + Quality Self-Examination *(COMPLETE)*

**End-to-end quality layer.** Not health monitoring (that's heartbeat) — this is alignment and quality: is the system producing the right results, are the processes working, is Poe on track with its goals?

Inspired by Factory AI Signals research (LLM-as-judge + friction detection) and Jeremy's explicit ask for self-examination separate from Poe-the-orchestrator.

- [x] Inspector: independent quality agent, completely separate from execution chain
- [x] Friction detection (Factory 7-signal model): error events, repeated rephrasing, escalation tone, platform confusion, abandoned tool flows, backtracking, context churn — heuristic-first, LLM-optional
- [x] Goal alignment scoring per session (heuristic default + optional LLM)
- [x] Cross-session pattern analysis → feeds evolver suggestions
- [x] Closed loop: threshold breach → suggestion → evolver pipeline
- [x] `run_evolver_with_friction()`: evolver gets richer context from inspection findings
- [x] Inspector wired into heartbeat (every 20 ticks); heartbeat report gains quality_summary
- [x] `poe-inspector [--loop]`, `poe-inspector-status`, `poe-quality` CLI
- [x] `deploy/poe-inspector.service` systemd unit
- [x] 41 new tests, 597 total passing

**Artifact:** `src/inspector.py`, `deploy/poe-inspector.service`, `poe-inspector` CLI

---

## Phase 13: Poe as CEO *(COMPLETE)*

**Explicit role separation.** Poe stops being an executor and becomes a communicator, planner, and advisor. Directors plan and review. Workers execute. Inspector validates. Poe's interface with Jeremy is at mission/goal level — not steps.

- [x] `poe.py`: CEO-layer entry point — routes to Mission/Director/Inspector, never executes steps directly
- [x] Executive summary compilation: Poe distills active missions + quality signals into 3-5 bullet summary for Jeremy
- [x] Autonomy tier system: `manual` / `safe` / `full` — configurable per project and per action type, persists to `memory/autonomy.json`
- [x] `assign_model_by_role()`: role-semantic model selection (orchestrator→POWER, worker→MID, classifier→CHEAP) wired into agent_loop, director, mission
- [x] `goal_map.py`: active mission relationship graph, conflict detection, `/map` Telegram command
- [x] Telegram: `/status` routes through Poe CEO layer, `/map` shows goal graph, natural language → `poe_handle()` first
- [x] `handle.py` backward compat preserved — AGENDA tasks delegate to `poe_handle()`, dry_run stays on legacy path
- [x] 88 new tests, 685 total passing

**Artifact:** `src/poe.py`, `src/autonomy.py`, `src/goal_map.py`, `poe`/`poe-status`/`poe-map`/`poe-autonomy` CLI

---

## Phase 14: Skill Evolution — Failure Attribution + Unit-Test Gate *(COMPLETE)*

**Tighten the self-improvement loop.** Inspired by Memento-Skills (arXiv:2603.18743). The current meta-evolver operates at task granularity; this phase adds sub-skill attribution, per-skill scoring, and a test gate that prevents regressions before any mutation goes live.

- [x] **Failure attribution**: when a session gets stuck, LLM attributor pinpoints which specific step/skill caused the failure — not just "task failed". Feeds structured attribution into Inspector and evolver with precise signal.
- [x] **Per-skill success rate tracking**: every skill invocation records pass/fail in `memory/skill-stats.jsonl`. `skills.py` gains `record_skill_outcome()` and `get_skill_stats()`.
- [x] **Threshold-based escalation**: when a skill's empirical success rate drops below configurable threshold (default 0.4), escalate from "patch this skill" to "redesign from scratch" — new synthesis path in evolver.
- [x] **Structured skill format**: adopt three-section markdown spec (declarative spec + executable behavior + guardrails). `skills.py` gains `parse_skill_sections()` and `render_skill_markdown()`.
- [x] **Unit-test gate on skill mutations**: before evolver writes a skill update to the live library, auto-generate synthetic test cases from failure examples and run them. Block write-back if tests fail. `skills.py` gains `generate_skill_tests()` and `validate_skill_mutation()`.
- [x] **Skill poisoning defense**: hash-based write verification (SHA256 of name+description+steps). Warns on mismatch, never crashes.
- [x] Wire attribution into Inspector: `detect_friction()` gains attribution context; Inspector report includes per-skill failure breakdown.
- [x] Wire test gate into evolver: `apply_suggestion()` for skill_pattern category runs test gate before write. Blocked mutations get status `gate_blocked`.
- [x] `poe-attribution`, `poe-skill-stats`, `poe-skill-test` CLI subcommands
- [x] 61 new tests; 785 total passing

**Artifact:** `src/skills.py` (extended), `src/attribution.py`, `memory/skill-stats.jsonl`, `poe-attribution` / `poe-skill-stats` / `poe-skill-test` CLI

---

## Phase 15: Skill Sandbox + OpenClaw Gateway *(COMPLETE)*

- [x] sandbox.py: subprocess isolation for skill execution; static safety analysis blocks eval/exec/import os/shutil/open; sandboxed test gate
- [x] gateway.py: ws://127.0.0.1:18789 connect/send/receive; graceful ImportError fallback if websockets not installed; reads auth from openclaw.json (never logged)
- [x] sheriff: openclaw_gateway health check wired (TCP fallback in sheriff.py; gateway module available for direct use)
- [x] poe-gateway status/send, poe-sandbox test CLI subcommands
- [x] 43 new tests; 828 total passing

**Artifact:** `src/sandbox.py`, `src/gateway.py`

---

## Phase 16: Tiered Memory — Short, Medium, Long Term *(COMPLETE)*

**Memory with selective forgetting.** Not all memory is equal. Step-level results are noise in a week; lessons from a failed mission are load-bearing for months. This phase introduces tiered retention with exponential decay, forgetting, and promotion — using Grok's decay model.

Inspired by Jeremy: "I think we want to 'forget' some things and some things only apply in different memory spans."

Decay model (Grok): `score *= 0.85` per non-reinforced day; `score = min(1.0, score + 0.3)` on reinforcement; promote at `score ≥ 0.9 AND sessions_validated ≥ 3`; GC at `score < 0.2`.

- [x] **Three memory tiers:**
  - `short` — in-process only (no file I/O). `short_set/get/clear/all()`. Evicted at session end.
  - `medium` — `memory/medium/lessons.jsonl`. Decays daily; auto-promotes on eligibility; GC when stale.
  - `long` — `memory/long/lessons.jsonl`. Explicit promotion required (score + sessions gate).
- [x] **Decay model:** `TieredLesson.score` decays exponentially (`DECAY_FACTOR=0.85`) per non-reinforced day. Applied inline on `load_tiered_lessons()`.
- [x] **Promotion path:** `promote_lesson(id)` moves medium → long when eligible. `run_decay_cycle()` auto-promotes. Inspector/evolver can call directly.
- [x] **Selective forgetting:** `poe-memory forget <id>` explicitly expires an entry. `poe-memory decay --dry-run` previews what would be pruned.
- [x] **Context injection respects tiers:** `inject_tiered_lessons()` queries long-tier first (always included, no min_score filter), then medium-tier (min_score=0.3), then short-tier only if `include_short=True`. Short-tier never bleeds into new sessions.
- [x] **Skill library tiers:** `Skill.tier = "provisional"` (default) | `"established"`. `promote_skill_tier()` requires `pass^3 ≥ 0.7` (success_rate^3). Both fields serialized through `_skill_to_dict`/`_dict_to_skill`.
- [x] `poe-memory status/forget/decay/promote/list/record` CLI — full tier management
- [x] 48 new tests; 961 total passing

**Artifact:** `src/memory.py` (tiered section appended), `src/skills.py` (tier field + `promote_skill_tier()`), `memory/medium/`, `memory/long/`, `poe-memory` CLI, `tests/test_tiered_memory.py`

**Open design question captured:** Jeremy raised the "muscle memory" concept — should long-tier lessons eventually graduate from query-able data into AGENTS.md identity (system prompt)? Answer: yes, via a "canon promotion" path. Inspector surfaces candidates (`times_applied ≥ N` across diverse task types); human gate required before writing to AGENTS.md. See `docs/MEMORY_ARCHITECTURE.md` for full design rationale and graduation path.

---

## Phase 17: Behavior-Aligned Routing *(COMPLETE)*

**RL-trained skill router.** Currently `find_matching_skills()` uses keyword matching. Memento-Skills showed that training a router on execution-success signal (not just semantic similarity) gives ~10% relative recall improvement and routes to skills that actually work, not just skills that sound relevant.

Inspired by Memento-Skills arXiv:2603.18743: one-step offline RL, multi-positive InfoNCE loss, Boltzmann policy.

- [x] **Outcome label collection:** skill-stats.jsonl from Phase 14 used directly as training data. Positive: success_rate > 0.6; Negative: success_rate < 0.4; ambiguous middle skipped.
- [x] **Feature extraction:** sentence-transformers (all-MiniLM-L6-v2) when available; TF-IDF unigram+bigram fallback; character-level fallback of last resort. Graceful ImportError handling throughout.
- [x] **Router training:** sklearn LogisticRegression on TF-IDF features → success_probability. 80/20 holdout split. Falls back to keyword matching below MIN_TRAINING_SAMPLES=50. Saves to `memory/router-model.pkl`.
- [x] **Inference:** `find_matching_skills(goal, use_router=True)` uses router scores when model is trained; sorted by predicted success probability. Keyword fallback preserves exact prior behavior when router unavailable.
- [x] **Router retraining hook:** wired into `run_evolver()` — when skill-stats grows by RETRAIN_EVERY_N=50 entries, `maybe_retrain()` triggers retraining automatically.
- [x] `poe-router stats` — show training data size, last trained, accuracy on holdout
- [x] `poe-router retrain` — force retrain
- [x] `poe-router route "goal text"` — show top skill matches with scores and method
- [x] 29 new tests (24 pass + 5 skipped for sklearn); 857 total passing

**Artifact:** `src/router.py`, `memory/router-model.pkl`, `memory/router-stats.json`, `poe-router` CLI

---

## Phase 19: Harness Patterns — Sprint Contracts + Agent Separation *(COMPLETE)*

**Pre-flight "done" definitions and explicit Generator/Evaluator separation.** Inspired by Anthropic's engineering posts on long-running agent harnesses. The core insight: define "done" before starting, not after. No Worker should grade its own output.

- [x] **Sprint contracts:** before any Feature Worker starts, it negotiates a sprint contract with Inspector — explicit testable success criteria for this feature. Inspector grades against the contract, not a vague "did it work?" Wired into the hook system as a mandatory pre-feature hook. (`src/sprint_contract.py`)
- [x] **Worker boot protocol:** when a Worker session starts (or restarts), mandatory boot sequence: read progress log → check git state → run existing tests → verify environment → then pick next task. Prevents re-doing completed work or declaring premature success. Implemented as an Initializer hook. (`src/boot_protocol.py`)
- [x] **Immutable feature manifest:** missions generate a `feature_list.json` alongside `mission.json`. Each feature has `passes: false` initially. Workers can only flip to `true` — never remove or downgrade. Inspector validates monotonicity. (`src/mission.py: generate_feature_manifest, mark_feature_passing, validate_manifest_monotonicity`)
- [x] **Inspector skepticism calibration:** add few-shot examples (good/mediocre/bad session outcomes) to Inspector's evaluation prompts. Tune toward skepticism. Inspector should catch "confidently mediocre" output — not just obviously broken work. (`src/inspector.py: SKEPTICISM_EXAMPLES, _build_skeptic_prompt_prefix`)
- [x] **`pass@k` / `pass^k` metrics in skill test gate:** `pass@k` for exploratory skill capabilities, `pass^k` for regression/stability gates. Skills must pass `pass^3` before promoting from provisional to established tier. (`src/metrics.py: compute_pass_at_k, compute_pass_all_k, check_skill_promotion_eligibility`)
- [x] **Running failure docs:** Workers write to a persistent `DEAD_ENDS.md` in the project directory — approaches tried and failed, in-progress at session end. Inspector and meta-evolver mine this directly. Prevents duplicate effort across sessions. (`src/agent_loop.py` + `src/boot_protocol.py: update_dead_ends`)
- [x] **GAN principle enforced:** no Worker context ever evaluates its own output. Skill QA and skill execution are separate invocations. Inspector is always a different context from the worker that produced the output being evaluated. (grade_contract called from `mission.py`, never from `agent_loop.py`)
- [x] **March of Nines defense:** measure per-step success rate in agent_loop; when `step_success_rate^steps` < 0.5, alert Inspector. Track in metrics.py. (`src/agent_loop.py: march_of_nines_alert` in `LoopResult`)

**Artifact:** `src/sprint_contract.py`, `src/boot_protocol.py`, `feature_list.json` manifest, Inspector skepticism calibration, `DEAD_ENDS.md` per project, `poe-contract`/`poe-boot`/`poe-manifest`/`poe-metrics pass-k` CLI commands, 61 new tests (913 total, 5 skipped)

---

## Phase 18: Sandbox Hardening *(COMPLETE)*

**Production-grade skill isolation.** The current sandbox (Phase 15) runs skills in a plain `python3` subprocess with static content analysis. This phase hardens it to configurable resource limits, network isolation, and a full audit log.

- [x] **`SandboxConfig` dataclass:** configurable per-execution — `timeout_seconds`, `max_cpu_seconds`, `max_file_size_mb`, `max_open_files`, `block_network`, `use_venv`, `audit`. All documented defaults.
- [x] **Resource limits via `preexec_fn`:** `_make_preexec_fn(config)` sets `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE` in child process before exec. `RLIMIT_AS` intentionally omitted — breaks Python mmap on Linux with overcommit.
- [x] **Network isolation (soft):** `_NETWORK_BLOCKER_CODE` monkey-patches `socket.socket.connect` to raise `ConnectionRefusedError`. Injected into runner script preamble when `block_network=True`. No root required.
- [x] **venv isolation (optional):** `_get_venv_python()` tries `uv venv` first, falls back to `python3 -m venv --without-pip`. `use_venv=False` by default (avoids ~500ms overhead). `venv_isolated` flag in result.
- [x] **Audit log:** every sandboxed execution optionally logged to `memory/sandbox-audit.jsonl` (JSONL, newest-first on read). Fields: `audit_id`, `timestamp`, `skill_id`, `skill_name`, `static_safe`, `exit_code`, `elapsed_ms`, `timed_out`, `success`, `network_blocked`, `venv_isolated`, `resource_limited`, `output_preview`, `error`. Failures never block execution.
- [x] **`SandboxResult` Phase 18 fields:** `audit_id` (12-char UUID prefix), `network_blocked`, `venv_isolated`, `resource_limited`.
- [x] **Updated dangerous patterns:** added `import ctypes`, `socket.connect`, `requests.get/post`, `httpx.`, `aiohttp.`, `pickle.loads`, `marshal.loads`, `ctypes.`, `cffi.`, `urllib.request`.
- [x] **`poe-sandbox` CLI extensions:** `test` gains `--no-network-block`, `--venv` flags; `audit [--limit N] [--format json]` subcommand; `config` subcommand shows current defaults.
- [x] **`load_audit_log(limit=50)`:** reads JSONL newest-first.
- [x] 33 new tests (1041 total passing, 5 skipped). Covers config/result fields, static analysis, network blocker code validity, subprocess network block, audit creation/ordering/limits, adversarial edge cases.

**Artifact:** `src/sandbox.py` (hardened), `tests/test_sandbox_hardening.py`, `memory/sandbox-audit.jsonl` (runtime)

---

## Phase 20: Persona System — Modular, Composable Agent Identities *(COMPLETE)*

**Personas are composable data primitives.** Compose > inherit (Jeremy + Grok confirmed). No subclassing — personas merge by combining system prompt sections, taking the union of tool_access, highest model tier, and broadest memory scope.

- [x] **Persona spec format:** YAML frontmatter + markdown body (backward compatible — bare .md files work). Fields: name, role, model_tier, tool_access, memory_scope, communication_style, hooks, composes. Malformed frontmatter gracefully falls back to defaults.
- [x] **Persona registry:** `PersonaRegistry` scans `personas/`, loads by name, caches, excludes README.md. Built-in: `researcher`, `builder`, `ops`, `critic`, `summarizer`, `strategist`.
- [x] **Researcher persona:** YAML frontmatter added to `research-assistant-deep-synth.md`. model_tier=power, memory_scope=session. Loaded as `researcher`.
- [x] **Composition (compose > inherit):** `compose_persona(*names)` merges specs left-to-right: system prompts concatenated (section separator), tool_access unioned (deduped), hooks unioned (deduped), highest model tier wins (power > mid > cheap), broadest memory scope wins (global > project > session), communication_style concatenated. Optional `extra_prompt` param.
- [x] **Spawn-on-demand:** `spawn_persona(name, goal, dry_run=True|False, compose_with=[...])` — fresh agent loop with persona system prompt. `short_clear()` at spawn start and end (memory isolation). Resolves LLM adapter from model_tier.
- [x] **Memory isolation:** each spawn calls `short_clear()` + sets `persona_name`/`persona_goal` in short-term store. Session memory evicted on spawn exit.
- [x] **`poe-persona` CLI:** `list / show / compose / spawn` subcommands. `spawn --dry-run` previews without executing. `spawn --compose` adds additional personas at runtime.
- [x] **`/research` Telegram command** runs `run_agent_loop()` with live step-by-step Telegram progress updates via `step_callback`.
- [x] **Canon promotion (Phase 16 addition):** `times_applied` tracking wired into `inject_tiered_lessons()` (default on). `_record_canon_hit()` writes to `memory/canon_stats.jsonl`. `get_canon_candidates()` surfaces long-tier lessons eligible for AGENTS.md identity promotion. `poe-memory canon-candidates` CLI. 10 new tests.
- [x] 47 new tests (37 persona + 10 canon); 1008 total passing, 5 skipped

**Artifact:** `src/persona.py`, `personas/` (6 built-in YAML-frontmatter specs), `poe-persona` CLI, `/research` Telegram command, `src/memory.py` (canon tracking appended)

---

## Phase 21: Production Readiness — Bootstrap + Decoupling + macOS *(COMPLETE)*

**Make the system installable and self-bootstrapping.** Previously tied to this specific box and OpenClaw directory layout. Now decoupled, bootstrappable, and macOS-compatible.

- [x] **Full OpenClaw decoupling:** `src/config.py` centralizes workspace resolution with env var priority: `POE_WORKSPACE` → `OPENCLAW_WORKSPACE` → `WORKSPACE_ROOT` → `~/.poe/workspace` (no OpenClaw required). All hardcoded `/home/clawd/.openclaw/` absolute paths removed from `llm.py`, `sheriff.py`, `gateway.py`, `telegram_listener.py`, `interrupt.py`, `orch.py`. `OPENCLAW_CFG` env var overrides openclaw.json path.
- [x] **`src/config.py`:** `workspace_root()`, `memory_dir()`, `secrets_dir()`, `credentials_env_file()`, `load_credentials_env()`, `openclaw_cfg_path()`, `load_openclaw_cfg()`, `deploy_dir()`. No third-party deps. Priority-ordered credential discovery: `POE_ENV_FILE` → `<workspace>/secrets/.env` → legacy OpenClaw path.
- [x] **Bootstrap tool (`src/bootstrap.py`):** `poe-bootstrap install|dirs|services|status|smoke`. Creates `memory/`, `skills/`, `projects/`, `output/`, `secrets/`, `logs/` dirs. Writes systemd (Linux) or launchd (macOS) service files for poe-heartbeat, poe-telegram, poe-inspector. Smoke test: dry-run NOW-lane task.
- [x] **macOS compatibility:** `platform.system()` detection in `write_service_files()`. Generates `.plist` files under `deploy/launchd/` on Darwin; `.service` files under `deploy/systemd/` on Linux. `KeepAlive`, `RunAtLoad`, log paths injected into plists.
- [x] **`pyproject.toml` extras:** `[telegram]`, `[gateway]`, `[memory]`, `[all]`. No mandatory third-party deps in core. All existing `ImportError` fallbacks preserved.
- [x] **Entry points:** `poe-bootstrap`, `poe-run`, `poe-handle`, `poe-memory`, `poe-persona`, `poe-sandbox`, `poe-skills`, `poe-inspector`, `poe-test`.
- [x] 23 new tests (1064 total, 5 skipped). Covers workspace_root priority, credential discovery, dir creation idempotency, systemd/launchd content, workspace injection, status.

**Artifact:** `src/config.py`, `src/bootstrap.py`, `deploy/systemd/` (generated), `deploy/launchd/` (generated), `pyproject.toml` extras + entry points

---

## Future Considerations

Ideas that are real but not yet scheduled. Not prioritized against each other — captured here for planning discussions.

---

### Phase 22: Knowledge Crystallization — Hardening Decisions Into Infrastructure *(DONE)*

*"A young sapling is flexible. As it grows it becomes the foundation for other young shoots."*

Full design in `docs/KNOWLEDGE_CRYSTALLIZATION.md`. The short version: every LLM call that answers a question Poe has answered correctly 50 times before is waste. The path is:

```
Fluid LLM → Lesson (tiered memory) → Identity (canon/AGENTS.md) → Skill (Python) → Rule (zero-cost)
```

**Shipped (Phase 22 first cut):**
- `src/knowledge.py`: `poe-knowledge status [--stage N]` crystallization dashboard showing Stages 2–5 + graveyard + incidental counts + evolver suggestions
- `src/knowledge.py`: `poe-knowledge promote` — lists all available cross-stage promotions (read-only)
- Wired into `pyproject.toml` and `src/cli.py` as `poe-knowledge` entry point
- `tests/test_knowledge.py`: 14 tests

**Shipped (Phase 22 Stage 5 — Skill → Rule graduation):**
- `src/rules.py`: `Rule` dataclass + full lifecycle (`graduate_skill_to_rule`, `find_matching_rule`, `record_rule_use`, `record_rule_wrong_answer`, `demote_rule_to_skill`, `get_rule_graduation_candidates`)
- Storage: `memory/rules.jsonl` — one JSON object per line, append/update pattern
- Graduation threshold: established skill, pass^3 >= 0.70, use_count >= 3
- Auto-demotion: 3 Inspector wrong-answer signals → rule deactivated, falls back to Stage 4
- `src/agent_loop.py`: `_build_loop_context()` checks for matching rule before LLM decompose; rule match bypasses `_decompose()` entirely (zero inference cost for matched goals)
- `src/knowledge.py`: Stage 5 dashboard section, `graduate`/`demote`/`rules` CLI commands
- `tests/test_rules.py`: 28 tests covering all Rule lifecycle functions + `_build_loop_context` integration

**Remaining (not blocking):**
- **Model tier auto-optimization**: evolver should track per-task-type success rates by tier and suggest downgrades

---

### Phase 23: Observability — Execution Visualization *(DONE)*

Currently no real-time view of what Poe is doing. `loop.lock` shows the active goal; `heartbeat-state.json` shows health; but no timeline, step trace, or resource graph.

**Shipped (Phase 23 first cut):**
- `src/observe.py`: `poe-observe` execution snapshot — reads loop.lock, heartbeat-state.json,
  outcomes.jsonl, sandbox-audit.jsonl, memory stats — all local reads, no LLM calls
- Subcommands: `loop`, `heartbeat`, `outcomes [--limit N]`, `audit [--limit N]`, `memory`
- `tests/test_observe.py`: 27 tests
- Wired into `pyproject.toml` as `poe-observe` entry point

**Still pending:**
- **TUI dashboard** (`rich` or `textual`): live view with `watch`-style refresh. Can be done by wrapping `print_snapshot()` in a loop with `os.system("clear")`. Needs `rich` dep.
- **Simple web UI** (`fastapi` + plain HTML): same data over HTTP for remote access from Slack/browser.
- **Hook-based step stream**: `reporter` hook at `fire_on=step` writes to `observe.jsonl` giving per-step granularity. Hook infrastructure is in place.

---

### Phase 24: Messaging Integrations — Slack, Signal, iMessage *(PARTIAL)*

Telegram is the current interface. Others when needed (truly later, no urgency):

**Slack — SHIPPED (skeleton):**
- `src/slack_listener.py`: Socket Mode listener (no public endpoint needed).
  Mirrors `telegram_listener.py` exactly: same slash commands (`/status`, `/observe`,
  `/knowledge`, `/director`, `/research`, `/build`, `/ops`, `/stop`, `/help`), same
  interrupt routing when a loop is active, same dry_run/verbose API.
- Credential resolution: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_ALLOWED_CHANNELS`
  from env → secrets/.env → openclaw.json. Graceful degradation without `slack-sdk`.
- `/observe` and `/knowledge` inline — snapshot and crystallization status accessible
  from Slack without any extra setup.
- `tests/test_slack_listener.py`: 25 tests
- `pyproject.toml`: `slack = ["slack-sdk>=3.0"]` extra; `poe-slack` entry point

**To activate**: `pip install slack-sdk`, set `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN`,
create a Slack app at api.slack.com with Socket Mode enabled.

**Still pending:**
- **Signal**: `signal-cli` daemon + REST API. E2E encrypted, good for personal use.
- **iMessage**: macOS-only via AppleScript or Shortcuts. Brittle; low priority.

Note: routing layer (`handle.py`, `poe.py`) is already interface-agnostic. Adding an interface is ~100 lines.

---

### Phase 25: Ops Hardening — Resource Profiling and Load Testing *(PARTIAL)*

Before high-volume or mission-critical workloads:

**Shipped (Phase 25 disk GC):**
- `src/gc_memory.py`: `poe-gc` command — memory garbage collection with configurable retention
  - `poe-gc status` / `poe-gc run [--yes] [--dry-run]`
  - GC targets: outcomes.jsonl (default 90-day retention), sandbox-audit.jsonl (last 1000 entries), tiered lessons below GC_THRESHOLD (0.2), daily narrative logs (default 180-day retention)
  - All operations are dry_run by default; `--yes` skips confirmation prompt (for cron)
  - `tests/test_gc_memory.py`: 23 tests covering all GC operations, dry_run semantics, CLI
  - `pyproject.toml`: `poe-gc` entry point

**Still pending:**
- **Memory profiling**: Python process size under various loop depths; relevant for parallel missions.
- **Parallelization analysis**: `background.py` uses `max_workers=2`. Right ceiling? Bottleneck is probably API rate limits, not CPU.
- **Load testing**: 100 NOW-lane tasks + 10 concurrent AGENDA loops. Where does it break?

---

### Phase 26: Container Image — Portable Deployment *(COMPLETE)*

Shipped before dogfooding as recommended. Isolated containers give you clean integration-test runs without risking the main workspace. One afternoon of work → huge confidence before going "in anger."

Goal: `docker run poe-orchestration` on any Linux/macOS with Docker.

**Recommendation: Alpine Linux + Python 3.12 slim. Not Go.** The value is in the Python LLM ecosystem; a Go rewrite would be significant work with no clear benefit. Go makes sense for single-binary CLIs with no runtime; that's not this system.

- [x] `Dockerfile`: Alpine + Python 3.12 slim, `POE_WORKSPACE=/data` volume, `pyyaml` only dep, `poe-bootstrap status` default command. Verified: `docker run --rm poe-orchestration:latest`.
- [x] `docker-compose.yml`: three services (heartbeat, telegram, inspector) sharing `poe-data` volume, `restart: unless-stopped`, credentials via env or mounted `secrets/.env`.
- [x] `.dockerignore`: excludes memory/, projects/, secrets/, tests/, docs/ — no workspace data baked into image.

**Artifact:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`

---

### Phase 27: Prerequisite Knowledge Sub-Goals and Graveyard Query *(DONE)*

*From Jeremy's "kanji painting → learn Japanese" scenario, March 2026.*

Two related gaps in the current learning architecture:

**Graveyard query — SHIPPED:**
- `TieredLesson.acquired_for: Optional[str]` — tag incidental/prerequisite lessons with the goal_id that triggered them
- `memory.search_graveyard(topic, ...)` — fuzzy keyword match across graveyard (score 0.2–0.4), sorted by match ratio; `resurrect=True` calls `reinforce_lesson()` on all matches
- `record_tiered_lesson(acquired_for=goal_id)` — propagates tag through to JSONL
- Dashboard: `poe-knowledge status` now shows `incidental_count` in Stage 2 view
- `tests/test_phase27.py`: 16 tests

**Sub-goal knowledge acquisition — PENDING:**
The system still has no explicit mechanism to detect "this step requires domain knowledge I don't have" and spawn a sub-loop to acquire it before proceeding. Missing: a `knowledge_prerequisites` check during `_decompose()` that calls `search_graveyard(topic)` first, and only spawns a new `is_sub_goal=True` loop if graveyard is empty.

Full design in `docs/MEMORY_ARCHITECTURE.md`.

---

### Phase 28: Poe Personality — Complementary Interaction Model *(PARTIAL)*

*Jeremy, March 2026: "I'm a 6w5/INFJ... I expect a properly built persona would complement me and allow us both to interact better together."*

**The opportunity**: a persona layer that understands Jeremy's cognitive patterns well enough to adapt communication style — not content decisions — to what actually lands. The Enneagram 6w5 + INFJ combination has specific, predictable patterns:
- Needs certainty and honesty about unknowns (don't manufacture confidence for a Type 6)
- Analytical overlay: wants the *why*, not just the *what* (5-wing)
- Systems thinker who dislikes surface-level (INFJ)
- Trusts directness over reassurance; hates being managed
- High energy cost for social navigation → the agent should reduce friction, not add it

**The hard line — complementary vs. manipulative:**
- **Complementary**: adapts *communication style* to what lands best. Direct, systematic, honest about uncertainty, no unnecessary warmup.
- **Manipulative**: adapts *content or recommendations* based on what the user's type will accept. Uses cognitive patterns to steer decisions.

The first is useful and healthy. The second is a line that should never be crossed, and Poe should be explicitly designed to not cross it. The way to ensure this: Jeremy authors the "who I am" document himself (controls what's in Poe's model of him), the persona is transparent about what it knows, and it should never use that knowledge to pre-empt Jeremy's judgment on something he'd want to evaluate himself.

**The fascinating part**: a well-built version of this could make every interaction more efficient precisely because Poe isn't starting from scratch socially each session. The scary part is real — which is why the design should be explicit and human-auditable, not emergent. Jeremy should be the gardener here more than anywhere else.

**Shipped (Phase 28 first cut):**
- `personas/jeremy.md` — scaffold for Jeremy to author. Explicitly placeholders for Jeremy to fill in. Includes the hard-line design notes so the intent is clear when he reads it.
- `personas/companion.md` — communication style adapter persona. Reformats other personas' outputs to match Jeremy's preferences (conclusion-first, no preamble, direct uncertainty, systems framing). Explicit table of complementary vs. manipulative that it must never cross. Composes with any other persona at the output stage.

**Still pending:**
- Jeremy fills in `personas/jeremy.md` — that's his document, not Poe's to write
- CEO-layer integration: optionally load `companion` as an output filter on user-facing messages
- "Does this still feel right?" review cadence

---

### Phase 29: Human Psychology / Neurology / Philosophy Research Track *(DONE)*

*Jeremy, March 2026: "Human psychology, neurology, and philosophy ideas probably can come into play here. I'm definitely no expert in any of those areas, but seems like there are things we should learn about over time."*

Not a single phase but a research track that informs multiple phases:
- **For memory architecture**: cognitive science on how human memory consolidation works (sleep replay, spaced repetition, the role of emotion in encoding) → informs decay model tuning
- **For the loop**: decision theory, satisficing vs. optimizing, fast/slow thinking (Kahneman) → informs when the agent should deliberate vs. act quickly
- **For persona design (Phase 28)**: Enneagram, MBTI as frameworks for communication style, not personality determinism
- **For crystallization (Phase 22)**: expertise research — what makes novice → expert knowledge transfer work, what gets tacit vs. explicit

**Shipped (Phase 29 scaffolding):**
- `personas/psyche-researcher.md` — targeted research specialist persona. Evidence-grounded, implication-focused. Writes to `docs/research/` with a standard artifact format (question, findings, implications, confidence, sources). Composes with `reality-checker` for significant findings.
- `docs/research/README.md` — active question queue (6 open questions, tied to phases), artifact format, completed log.

**Shipped (Phase 29 first research run — March 2026):**
- `docs/research/productive-persistence.md` — ML + psychology synthesis on agent persistence
  (UCB/Thompson/Gittins, Duckworth grit, Seligman learned helplessness, productive failure, Kapur).
  6/6 steps, 0 blocked, 123k tokens, 250s.

**Shipped (additional research runs — March 2026):**
- `docs/research/zoom-metacognition.md` — double-loop learning, OODA loops, adaptive expertise; when to re-decompose vs retry (6/6, 164k tokens)
- `docs/research/spaced-repetition-confidence.md` — SM-2/FSRS confidence signals, optimal review timing; implications for decay model tuning (6/6, 97k tokens)
- `docs/research/system1-system2-agents.md` — Kahneman dual-process theory; signals for deliberate vs. fast agent action; decompose/execute split implications (6/6, 174k tokens)

**Shipped (Phase 29 final research runs — 2026-04-05):**
- `docs/research/tacit-vs-explicit.md` — Polanyi/Dreyfus/SECI synthesis; Stage 4→5 transition mechanics; 8 design implications for crystallization pipeline (6/6, 188k tokens)
- `docs/research/enneagram-6w5-infj.md` — 6w5 communication patterns, trust calibration mechanics, INFJ communication failures; companion persona design principles (6/6, 276k tokens)

---

### Phase 30: Token Cost Visibility + Model Routing *(DONE)*

*March 2026 — discovered after Max tier upgrade caused silent Opus usage and token burn in an hour.*

**The problem**: cost constants in `metrics.py` were ~12x too low (old Haiku pricing used for Sonnet-class workloads). Alerting thresholds based on those numbers were never firing. Additionally, the subprocess adapter (primary on this box) skips prompt caching, and there's no per-model breakdown in the metrics report.

**Shipped (Phase 30 first cut):**
- `src/metrics.py`: `COST_BY_MODEL` dict — per-model USD/MTok pricing for Opus 4.6, Sonnet 4.6, Haiku 4.5, and short-form aliases. `estimate_cost()` now accepts `model=` arg and looks up accurate rates; falls back to Sonnet 4.6 defaults when model is unknown.
- `~/.claude/settings.json`: model pinned to `claude-sonnet-4-6` (explicit ID, not alias) to prevent silent Opus fallback on tier upgrades or `--continue` session resumption.

**Shipped (Phase 30 second cut — March 26, 2026):**
- `src/llm.py`: `CodexCLIAdapter` — wraps `codex exec --json` as a subprocess. Uses ChatGPT OAuth from `~/.codex/auth.json` (no extra API key), model `gpt-5.4`. Supports prompt caching (`cached_input_tokens` in JSONL output). Auto-detection now ranks Codex above `claude -p` subprocess since it caches aggressively.
- `tests/test_llm.py`: updated monkeypatching for `_codex_auth_available` in 3 existing tests.
- Confirmed: `gpt-5.4` works with ChatGPT Plus/Pro OAuth; `codex-mini-latest` does not.

**Shipped (Phase 30 third cut — March 28, 2026):**
- Per-step USD cost in loop log: `cost_step=$0.42 cost_total=$1.70 model=sonnet`
- `cost_budget` parameter on `run_agent_loop()`: fail fast if upfront estimate exceeds budget + 20% slush; warn at 80%; hard stop at budget + slush
- `estimate_loop_cost()` in `metrics.py`: upfront cost estimate from historical step costs by type
- Per-model pricing already existed in `COST_BY_MODEL` — now actively used in loop logging

**Shipped (Phase 30 third cut — March 2026, 91% token reduction):**
- `src/web_fetch.py`: URL pre-fetch layer — Jina Reader (clean markdown), authenticated X/Twitter CLI, t.co resolution, context-carry across steps
- `src/llm.py`: `--disallowedTools WebFetch,WebSearch` in subprocess to prevent sub-agent raw HTML fetches
- `src/agent_loop.py`: URL FETCHING POLICY + TOKEN EFFICIENCY sections in `_EXECUTE_SYSTEM`; `completed_context` passed as `extra_context` to `enrich_step_with_urls` (fixes step-3 context-carry bug)
- Result: X/tweet research tasks: 789k → 67k tokens (91%), 0 blocked, 84s

**Shipped (Phase 30 follow-on, 2026-04-05):**
- **Per-model cost breakdown in `poe-metrics`**: `Outcome.model` field added; `record_outcome`/`reflect_and_record` accept `model=`; `agent_loop` passes `adapter.model_key`; `SystemMetrics.by_model` dict (ModelMetrics per tier); `format_metrics_report` emits "By Model" section. 4 new tests.
- **Haiku routing for simple sub-tasks**: `assign_model_by_role()` already had `classifier → MODEL_CHEAP` tier. `classify_step_model()` routes cheap step types to Haiku. Both wired.
- **Sub-agent token tracking**: `scan_step_costs()` in evolver.py — scans step-costs.jsonl for high-burn step types (avg > 2× median), generates `cost_optimization` Suggestion entries with Haiku routing recommendation. `run_evolver(scan_costs=True)` wires it in. 9 new tests.

**Still pending:**
- **Budget alerting**: configurable per-session token budget with Telegram alert when crossed.

---

### Phase 31: Persona Auto-Selection *(DONE)*

Currently, persona selection requires explicit `/research`, `/build`, `/ops` commands. Goal content should drive persona selection automatically.

**Shipped (Phase 31 first cut):**
- [x] **`persona_for_goal(goal)`** in `persona.py`: keyword routing with word-boundary-aware matching. Scoring table covers research/build/ops/psyche/finance/legal/health/creative/companion. Returns `(persona_name, confidence)` tuple.
- [x] **Confidence gate**: `confidence_threshold=0.5` default; below threshold returns `None` (generic worker)
- [x] **`classify_step_model(step_text)`** in `poe.py`: per-step Haiku vs Sonnet routing via keyword heuristic — cheap for retrieval/classify/format/verify, mid for synthesis/analysis/implement
- [x] **Three-tier resolution in `workers.py`**: `PersonaRegistry.load(worker_type)` → `_PERSONA_FILES` map → inline fallback, giving all 18+ personas access from worker dispatch path

- [x] **Feedback loop**: `record_persona_outcome()` in `persona.py` writes `(persona, goal, status, confidence, loop_id)` to `memory/persona-outcomes.jsonl` after each loop. `load_persona_outcomes()` for evolver consumption. Wired in `poe.py` after `run_agent_loop`.
- [x] **Routing expansion**: 13-rule routing table (up from 5) covering health, legal, strategy, creative, scraping, simplifier, critic, systems-design personas. `persona_for_goal()` wired into `poe_handle()` AGENDA path.

**Artifact:** `persona_for_goal()` + `record_persona_outcome()` + `load_persona_outcomes()` in `persona.py`, wired in `poe.py`

---

### Phase 32: Skills Auto-Promotion + Self-Rewriting *(DONE)*

Skills are manually seeded or extracted as provisional. Promotion to established requires pass^3 ≥ 0.7 but the mechanism isn't wired to fire automatically. The larger opportunity (from Memento-Skills research — `docs/research/sumanth-agent-research.md`) is making skills self-rewriting: when a skill fails, the agent reflects and rewrites it.

**Shipped (Phase 32 first cut — March 2026):**
- [x] **Utility scoring per skill**: `utility_score` field (EMA, alpha=0.3), updated on success (↑) / failure (↓). `update_skill_utility()` in `skills.py`.
- [x] **Circuit breaker**: CLOSED → OPEN after 3 consecutive failures (blip tolerance). OPEN → HALF_OPEN on first success → CLOSED after 2 consecutive successes. Failure during HALF_OPEN re-opens immediately. Single failures stay closed — distinguishes network blip from structural failure.
- [x] **Failure attribution**: `attribute_failure_to_skills()` — maps stuck step text to matching skills, calls `update_skill_utility(success=False)`. Targeted per-skill signal, not blanket goal failure.
- [x] **Auto-promotion in evolver**: `maybe_auto_promote_skills()` — provisional → established when utility ≥ 0.70 AND use_count ≥ 5. Runs in `run_skill_maintenance()`.
- [x] **Demotion + rewrite path**: `maybe_demote_skills()` — established → provisional when circuit OPEN or utility < 0.40. `rewrite_skill()` — LLM rewrites skill body for OPEN-circuit skills; sets HALF_OPEN (probationary). Gated on `circuit_state == "open"` — no rewrites from isolated failures.
- [x] **Selective TF-IDF retrieval**: `_tfidf_skill_rank()` in `skills.py` — smooth IDF variant, cosine similarity. Used as middle tier when keyword matching misses; still caps at top 3. Prevents irrelevant skills from reaching the prompt.
- [x] **Skills dashboard**: `poe-skills --status` shows provisional/established counts, circuit state breakdown (closed/half-open/open), rewrite candidates, per-skill utility + consecutive failure/success stats.
- [x] Wired into `agent_loop.py`: skill utility updated on step done/blocked. `run_skill_maintenance()` called at end of each evolver cycle.

- [x] **Skill synthesis**: `synthesize_skill()` in `evolver.py` — when no skill matched at loop start, LLM synthesizes a new provisional skill from the goal + outcome summary after successful completion. Deduplicates by name. Wired in `agent_loop.py` via `_had_no_matching_skill` flag.

**Artifact:** `utility_score` + circuit breaker + `attribute_failure_to_skills()` in `skills.py`; `rewrite_skill()` + `synthesize_skill()` + `run_skill_maintenance()` in `evolver.py`; `poe-skills --status` CLI

---

### Phase 33: Sub-Agent Token Self-Improvement *(DONE)*

The evolver currently optimizes for success rate. It should also optimize for token cost — detecting step patterns that burn disproportionate tokens and proposing cheaper alternatives.

**Shipped (Phase 33 first cut — March 2026):**
- [x] **Per-step cost recording**: `classify_step_type()` heuristic (8 categories); `record_step_cost()` writes to `memory/step-costs.jsonl`; `load_step_costs()` + `analyze_step_costs()` (lower-median 2x threshold). Wired in `agent_loop.py` on step done/blocked.
- [x] **Cost analyzer**: `analyze_step_costs()` identifies expensive step types relative to median; returns summary dict with `expensive_types` list.

- [x] **Cheap-first decomposer injection**: `analyze_step_costs()` supplies expensive_types list; injected as COST AWARENESS note into `_decompose()` via new `cost_context` param so the planner avoids high-cost step types.
- [x] **Token budget per loop**: `token_budget: Optional[int]` param on `run_agent_loop()`; aborts with `status="stuck"` and descriptive `stuck_reason` when total tokens exceed budget. Default None = no limit.

**Artifact:** `step-costs.jsonl`, `classify_step_type()` + `record_step_cost()` + `analyze_step_costs()` in `metrics.py`; `token_budget` + `cost_context` in `agent_loop.py`

---

### Phase 35: Backlog Steal-List — DeerFlow / Nanoclaws / AutoHarness *(DONE)*

Research triage in `docs/research/backlog-triage.md`. P1 items (highest leverage):

**Shipped (Phase 35 P1 — March 2026):**
- [x] **Cost-aware model routing** — `classify_step_model(step_text)` in `poe.py`: routes simple steps (classify/format/verify/retrieve) to MODEL_CHEAP, synthesis/analysis to MODEL_MID. Per-step, zero token cost. Wired into `agent_loop.py` step execution.
- [x] **Parallelized sub-agent fan-out** — dependency-aware parallel execution. Planner annotates steps with `[after:N,M]` dependency tags. `parse_dependencies()` + `build_execution_levels()` in `planner.py` group steps into parallel batches. Main loop executes each level concurrently via ThreadPoolExecutor. Falls back to sequential when no annotations present. 5 independent safety rails: max_workers, cost_budget, token_budget, POE_STEP_TIMEOUT, max_iterations.
- [x] **Constraint harness layer** — `src/constraint.py`: 5 pattern groups (destructive, secret, path_escape, unsafe_network, unsafe_exec). HIGH blocks execution, MEDIUM warns. Pluggable `CONSTRAINT_REGISTRY`. Fires before LLM call in `agent_loop.py`.
- [x] **TF-IDF memory retrieval** — `_tokenize()` + `_tfidf_rank()` in `memory.py`: pure stdlib cosine similarity. `inject_tiered_lessons()` and `load_lessons()` re-rank by relevance when `query=` provided. Smooth IDF variant handles small corpora.

**Shipped (Phase 35 P2 — March 2026):**
- [x] **Machine-readable agent capability manifest** — `generate_manifest()` + `save_manifest()` + `load_manifest()` in `persona.py`; `poe-persona manifest [--format text|json|save]` CLI subcommand. Scans `PersonaRegistry` + routing keyword map; sorted alphabetical output.
- [x] **Structured iterative refinement loop** — `_generate_refinement_hint()` in `agent_loop.py`: round 2 retry uses cheap LLM to generate targeted patch suggestions based on step text + block reason. Extended `_step_retries` cap from 1 to 2 rounds.
- [x] **Reporter/synthesis agent role** — `personas/reporter.md`: consolidation/synthesis persona (model_tier: mid). Wired into `_PERSONA_ROUTING` + `_PERSONA_ROUTING_KEYWORDS` in `persona.py` with consolidation/synthesis/final-report trigger keywords.
- [x] **Systematic HITL gating taxonomy** — `classify_action_tier()` + `hitl_policy()` + `ACTION_TIER_*` constants in `constraint.py`. Four tiers: READ (none), WRITE (warn), DESTROY (block), EXTERNAL (confirm). `hitl_policy()` folds tier + constraint risk into a single policy dict. 42 tests pass.

---

### Phase 34: Overnight Autonomous Mission Execution *(DONE)*

The core north star: Jeremy sets a mission before sleeping; the system executes autonomously through the night, recovers from blocks, and reports in the morning. No step supervision required.

- [x] **Mission drain detection**: `pending_missions()` — scans all projects, returns missions with remaining milestones (excludes done). Heartbeat calls this every `mission_check_every=5` ticks.
- [x] **Morning briefing**: `morning_briefing()` — Telegram-ready status summary bucketed as Completed / In progress / Queued, with UTC timestamp header and `max_missions` cap per section.
- [x] **Autonomous mission drain**: `drain_next_mission()` — picks oldest pending mission, runs milestones+features sequentially, persists state after each milestone. File-based lock (`memory/mission-drain.lock`) prevents double-drain. Heartbeat spawns it as a daemon Thread.
- [x] **Milestone-level progress notifications**: `_send_milestone_notification()` — Telegram alert per milestone completion (not per step). Final briefing sent when all milestones done.

**Artifact:** `drain_next_mission()` + `is_drain_running()` + `_send_milestone_notification()` + `pending_missions()` + `morning_briefing()` in `mission.py`; drain thread in `heartbeat.py`

---

### Phase 36: Agent Command Center — Observability Dashboard *(DONE)*

*From OpenClaw TASKS.md backlog (March 2026): "Build v1 agent command center: ingest orchestrator run events/logs and render a live dashboard of agents/jobs/queue."*

Currently `poe-observe` gives a static snapshot. The missing piece is a live event stream + web UI for remote monitoring — useful when running headless overnight missions.

**Shipped (Phase 36 — March 2026):**
- [x] **Event stream**: `write_event()` in `observe.py` → `memory/events.jsonl` append-only. Emits `loop_start`, `step_done`, `step_stuck`, `loop_done` events with goal, project, loop_id, step, tokens, elapsed.
- [x] **`poe-observe events [--limit N]`**: new CLI subcommand displays recent events with status icons (✓/✗/→), loop_id, and token counts. Wired into `agent_loop.py` at all key lifecycle points.
- [x] **`poe-observe watch`**: already existed — periodic full-snapshot refresh (poll interval configurable).

- [x] **Web dashboard (v1)**: `poe-observe serve [--host HOST] [--port PORT]` — stdlib `http.server`, no deps. Serves live HTML dashboard at default `http://127.0.0.1:7700`; auto-refreshes every 5s. Shows loop state, heartbeat, memory stats, recent outcomes, live event table. `/api/snapshot` JSON endpoint for programmatic access.

**Artifact:** `serve_dashboard()` + `_snapshot_json()` + `_DASHBOARD_HTML` in `observe.py`; `poe-observe serve` CLI

**Artifact:** `write_event()` + `print_events_tail()` in `observe.py`; `poe-observe events` CLI; wired in `agent_loop.py`

---

### Phase 37: Skill Synthesis — skill-creator Bootstrap *(DONE)*

*The final unshipped Memento-Skills idea (arXiv:2603.18743). Phases 31-32 gave us rewriting; this adds creating.*

- [x] **Gap detection + synthesis**: `_had_no_matching_skill` flag in `agent_loop.py`; after successful loop with no initial match, calls `synthesize_skill()` to crystallize the pattern.
- [x] **Skill synthesis**: `synthesize_skill(goal, outcome_summary, ...)` in `evolver.py` — LLM generates name/description/triggers/steps; saves as provisional/closed; deduplicates by name; never raises.
- [x] **Circuit breaker protection**: synthesized skills start provisional/closed and go through the same 3-strike OPEN → rewrite cycle as extracted skills.

Auto-delete on immediate failure and synthesis rate-limiting are de-scoped (overkill for current usage — the name dedup and circuit breaker provide sufficient protection).

**Artifact:** `synthesize_skill()` in `evolver.py`; `_had_no_matching_skill` wiring in `agent_loop.py`

---

### Phase 38: Subpackage Structure + Code Consolidation *(PARTIAL)*

*"30+ flat files work until they don't. The seams are already showing."*

The flat `src/` layout made sense at v0.1. At 46 files / 27K lines it creates three real problems: import ambiguity, test isolation friction, and cognitive load when navigating. The path forward is surgical — no flag days.

**Proposed grouping:**

```
src/
  core/          orch.py, config.py, cli_args.py, bootstrap.py, boot_protocol.py
  agents/        agent_loop.py, director.py, workers.py, persona.py, inspector.py
  memory/        memory.py, skills.py, rules.py, knowledge.py, gc_memory.py
  ops/           heartbeat.py, sheriff.py, mission.py, background.py, autonomy.py
  io/            telegram_listener.py, slack_listener.py, gateway.py, router.py, poe.py
  tools/         web_fetch.py, sandbox.py, constraint.py, security.py
  analytics/     metrics.py, eval.py, observe.py, attribution.py
  evolution/     evolver.py, hooks.py
```

**Strategy:** add `__init__.py` re-exports so existing flat imports keep working during transition. Tests stay green throughout.

**Shipped (Phase 38 first cut — March 2026):**
- [x] **`memory_dir()` consolidated**: added canonical `memory_dir()` to `orch_items.py` (resolution order: `POE_MEMORY_DIR` env → `orch_root()/memory` → `cwd/memory`). Eliminated 12 copies of the same try/except fallback chain across `memory.py`, `gc_memory.py`, `observe.py`, `router.py`, `inspector.py` (×4), `attribution.py`, `eval.py`, `sandbox.py`, `skills.py` (×3), `evolver.py` (×2), `rules.py`. Re-exported from `orch.py`.
- [x] README architecture diagram (Mermaid) added.

**Deferred:**
- [ ] Physical subpackage move — analysis showed 33 import references need updating for just the ops/ group. The churn:benefit ratio is unfavorable while runtime reliability improvements have higher leverage. Revisit when the flat layout causes a real problem (circular imports, test isolation failure).

---

### Phase 39: OSS Hygiene *(DONE)*

MIT license, .github polish, architecture diagram in README, Polymarket example project.

**Shipped (Phase 39 — March 2026):**
- [x] **MIT LICENSE** file added
- [x] **README architecture diagram** — Mermaid flowchart showing goal → decompose → workers → inspector → memory → evolver loop
- [x] **`.github/`** already had CI, bug/feature templates, CODEOWNERS, PR template — confirmed complete

**Shipped (Phase 39 — March 2026):**
- [x] **MIT LICENSE** added
- [x] **README architecture diagram** — Mermaid flowchart of full goal→execute→learn→evolve loop
- [x] `.github/` already complete (CI, bug/feature templates, CODEOWNERS, PR template)

**Still pending:**
- [ ] Polymarket bot example in `projects/` (good first external showcase)
- [ ] GitHub repo topics/tags (do manually)

---

### Phase 40: Pluggable Memory Backend *(DONE 2026-04-04)*

*Shipped: `memory_backends.py` — abstract `MemoryBackend`, `JSONLBackend` (default), `SQLiteBackend` (opt-in via `--memory-backend sqlite`), `migrate` CLI subcommand, `get_backend` factory wired into `memory.py`; 30/30 tests pass.*

*Original goal: keep jsonl as default. Add SQLite as an optional flag for better querying.*

Current jsonl is simple and reliable for a single-box setup. The pain points hit when: (a) GC needs to scan thousands of lessons efficiently, (b) "show me every Polymarket lesson" requires grep instead of a query, (c) concurrent writers need real row-level locking.

**Plan:**
- `MEMORY_BACKEND=sqlite` env var (or `config.toml` key) switches `memory.py` storage layer
- SQLite schema mirrors jsonl structure exactly — same fields, no new abstractions
- jsonl stays default; sqlite is opt-in
- `poe-memory migrate` CLI command converts existing jsonl → sqlite
- TF-IDF ranking stays pure stdlib (no new deps)

---

### Phase 41: Tool Registry + Expanded Function Calling *(DONE)*

*`web_fetch` and `sandbox` exist. Make tools discoverable by workers.*

Design doc: `research/PHASE41_TOOL_REGISTRY_DESIGN.md`

**Implementation status (as of 2026-04-02):**

- [x] **Step 1-2: ToolDefinition + PermissionContext + ToolRegistry** (`src/tool_registry.py`)
  - `ToolDefinition` (declarative per-tool object: name, description, input_schema, roles_allowed, is_enabled, should_defer)
  - `PermissionContext` (role + glob deny_patterns; `allows()` method)
  - `ToolRegistry` — filter pipeline: role → deny patterns → is_enabled → alphabetical sort
  - Module-level `registry` singleton populated from `step_exec.EXECUTE_TOOLS`
  - Role constants: `ROLE_WORKER`, `ROLE_SHORT`, `ROLE_INSPECTOR`, `ROLE_DIRECTOR`, `ROLE_VERIFIER`
  - Convenience factories: `worker_context()`, `short_context()`, `inspector_context()`, `director_context()`
  - `step_exec.get_tools_for_role()` using registry; `agent_loop.run_agent_loop(permission_context=)` for composition-time tool filtering
  - 45 tests in `tests/test_tool_registry.py`

- [x] **Step 3-4: SKILL.md format + SkillLoader + progressive disclosure** (`src/skill_loader.py`, `skills/`)
  - `SkillSummary` dataclass; `_parse_frontmatter()` for YAML-ish frontmatter (no deps)
  - `SkillLoader`: `load_summaries(role)`, `find_matching(goal, role)`, `load_full(name)`, `get_summaries_block(role, goal)`, `invalidate()`
  - Glob trigger matching, role filtering, cache with `invalidate()`
  - `skills/` directory — 4 seed files: `web_research.md`, `code_implement.md`, `debug_investigate.md`, `data_analysis.md`
  - Progressive disclosure: summaries in decompose prompt; full body loaded on demand
  - Wired into `agent_loop._build_loop_context()` — curated skills merged with runtime skills
  - 42 tests in `tests/test_skill_loader.py`

- [x] **Step 5: Hook event model** (`src/step_events.py`)
  - `PreStepEvent`, `PostStepEvent` typed payloads
  - `StepVeto` + `StepVetoedError` for blocking semantics
  - `StepEventBus` — `@on_pre_step(match=)`, `@on_post_step(match=)`, glob matcher on step_text
  - `fire_pre()` (blocking — returns `StepVeto` or None), `fire_post()` (non-blocking, swallows exceptions)
  - Wired into `step_exec.execute_step()` — pre fires after constraint check; post fires before final return
  - `step_exec` refactored to collect `_outcome` + single return (enables post-fire)
  - 35 tests in `tests/test_step_events.py`

- [x] **Step 6: Deferred tool resolution** (`src/tool_search.py`)
  - `TOOL_SEARCH_SCHEMA` — always-full tool schema; injected when deferred stubs present
  - `resolve_deferred_tools(query, ctx, registry)` — exact/partial/glob/description matching; returns full schemas for deferred tools visible to ctx
  - `format_tool_search_result()` — human-readable schema block for LLM injection
  - `inject_tool_search_if_needed(schemas)` — auto-adds `tool_search` when `[deferred]` stubs detected; idempotent
  - Wired into `step_exec.execute_step()`: deferred detection, `tool_search` call handling with LLM re-call with expanded tool list
  - 26 tests in `tests/test_tool_search.py`

- [x] **Step 7: MCP integration** (`src/mcp_client.py`, `src/tool_registry.py`, `src/heartbeat.py`)
  - `MCPServerClient` with stdio/HTTP transport, initialize handshake, `list_tools()`, `call_tool()`
  - `ToolRegistry.load_mcp_server(cmd_or_url)` — connects client, registers all remote tools as deferred stubs
  - `ToolRegistry.resolve_and_call(tool_name, input_data)` — unified dispatch (MCP + generic)
  - `heartbeat_loop()` init reads `mcp_servers:` from `user/CONFIG.md`, connects each at startup
  - 22 tests in `tests/test_mcp_loader.py`

---

### Phase 42: Nightly Eval Wired to Evolver *(DONE)*

*`eval.py` exists. Make it run automatically and feed failures back into evolver.*

**Shipped (2026-03-31):** `run_nightly_eval()` added to `eval.py`. Wired into `heartbeat_loop()` via `eval_every=1440` parameter (~24h at 60s tick). Failures generate Suggestion entries with `category="observation"` and `confidence=0.9` which the evolver auto-applies. All 4 built-in benchmarks currently passing.

---

### Phase 43: Structured Logging Expansion *(DONE)*

*"If you can't see what the system is doing, you can't fix it when it sticks."*

The agent loop and persona spawn now have structured logging via stdlib `logging` under the `poe.*` namespace. This needs to expand to cover every execution pathway so that runtime debugging never requires code changes.

**Shipped (Phase 43 — March 2026):**
- [x] `poe.loop` logger: step lifecycle (start/done/blocked), adapter timing, constraint checks, loop summary, decompose fallback
- [x] `poe.persona` logger: spawn start/end, adapter resolution, timing
- [x] `poe.evolver` logger: run start/end, suggestion apply, skill synthesis
- [x] `poe.heartbeat` logger: tick start/end, health status, escalation count
- [x] `poe.memory` logger: reflect_and_record, lesson extraction count
- [x] `poe.director` logger: run_director start/end, ticket count, timing
- [x] `poe.inspector` logger: run_full_inspector start/end, session/signal counts
- [x] `poe.mission` logger: run_mission + drain_next_mission lifecycle
- [x] `poe.constraint` logger: hitl_policy BLOCKED/gated decisions at DEBUG
- [x] `poe.introspect` logger: diagnosis results, lens findings
- [x] `_configure_logging()`: `POE_LOG_LEVEL` env var (DEBUG/INFO/WARNING/ERROR), `verbose=True` → DEBUG
- [x] Format: `HH:MM:SS L poe.module: message` on stderr

- [x] `poe.io.telegram` logger: poll lifecycle
- [x] `poe.io.slack` logger: poll lifecycle

Phase 43 complete — all 11 execution modules instrumented.

**Design notes:**
- Every logger is `poe.<module>` — filter by module in production (`poe.loop` only) or get everything (`poe`)
- INFO = lifecycle events with timing. DEBUG = content/decisions. WARNING = blocks/failures. ERROR = crashes.
- Existing `if verbose: print(...)` patterns coexist; no mass conversion needed, just add `log.*` alongside where useful
- Log output goes to stderr so it doesn't contaminate stdout result streams

---

### Phase 44: Self-Reflection — Run Observer + Failure Classifier *(DONE)*

*"The orchestrator should not only act — it should continuously watch itself act."*

Full design in `docs/SELF_REFLECTION.md`. The short version: every manual debugging session we do (watch logs → classify failure → apply fix) should be a built-in subsystem. The four layers are: instrumentation (done) → introspection (this phase) → policy (Phase 45) → graduation (Phase 46).

**Phase 44 scope — the introspection layer:**

- `src/introspect.py`: `diagnose_loop(loop_id)` reads events.jsonl + step outcomes and produces a `LoopDiagnosis` with failure class, evidence, and recommendation
- Failure taxonomy: `setup_failure`, `adapter_timeout`, `constraint_false_positive`, `decomposition_too_broad`, `empty_model_output`, `retry_churn`, `budget_exhaustion`, `token_explosion`, `artifact_missing`, `integration_drift`
- Heuristic-only — no LLM calls, just pattern matching on trace data
- `poe-introspect <loop_id>` CLI for manual diagnosis
- Wired into `_finalize_loop()` — auto-diagnose after every loop, write to `memory/diagnoses.jsonl`
- Feeds into `poe-observe serve` dashboard as a new "Diagnoses" panel

**Shipped (Phase 44 complete — 2026-03-29):**
- `src/introspect.py` (1138 lines): `diagnose_loop()`, `run_lenses()`, `aggregate_lenses()`, `plan_recovery()`, `save_diagnosis()`
- 10-class failure taxonomy: setup_failure, adapter_timeout, constraint_false_positive, decomposition_too_broad, empty_model_output, retry_churn, budget_exhaustion, token_explosion, artifact_missing, integration_drift
- `poe-introspect <loop_id>` / `poe-introspect --latest [--lenses]` CLI
- Auto-diagnose wired into `_finalize_loop()` → writes to `memory/diagnoses.jsonl`
- Diagnoses panel in `poe-observe serve` dashboard
- Fixed dead-code double-except in agent_loop.py

---

### Phase 45: Self-Reflection — Recovery Planner *(DONE)*

*Given a failure class, choose the cheapest intervention.*

The introspection layer (Phase 44) produces diagnoses. This phase adds a decision table that maps each failure class to a recovery action:

- `decomposition_too_broad` → re-decompose with tighter step count
- `constraint_false_positive` → add pattern to allowlist, retry
- `adapter_timeout` → switch adapter type, reduce step scope
- `budget_exhaustion` → increase max_iterations, enable landing
- `token_explosion` → truncate completed_context to summaries
- `empty_model_output` → retry with explicit tool-call instruction
- `artifact_missing` → re-run with explicit artifact instruction

No LLM needed — it's a lookup table with preference for cheapest action first. Optionally auto-applies low-risk recoveries (retry, redecompose) and escalates high-risk ones (switch adapter, change policy) for human review.

**Shipped (Phase 45 — 2026-03-29):**
- `_RECOVERY_TABLE`: all 10 failure classes mapped to `RecoveryPlan` entries with `auto_apply` + `risk` classification
- `plan_recovery()` / `plan_recovery_all()`: return best or all applicable plans
- Auto-recovery wired into `run_agent_loop()`: when loop ends stuck, diagnose → pick low-risk auto-apply plan → re-run with adjusted parameters (recursion-guarded)
- High-risk plans logged as `NEEDS-REVIEW` suggestions, not auto-applied

---

### Phase 46: Self-Reflection — Intervention Graduation *(DONE)*

*When humans repeatedly apply the same fix, propose a durable rule.*

Closes the full loop: observe → classify → fix → verify → graduate.

**Shipped (2026-03-31):** `src/graduation.py` — new module.

- Scans `memory/diagnoses.jsonl` for repeated failure classes (default: ≥3 occurrences)
- For each pattern above threshold (not yet proposed): writes a high-confidence Suggestion to `memory/suggestions.jsonl`
- Evolver auto-applies suggestions with confidence ≥ 0.8 on next run — graduation is non-interactive by default
- Deduplication: once a graduation suggestion is proposed for a failure class, it won't be re-proposed
- 8 failure classes covered with heuristic templates (adapter_timeout, constraint_false_positive, decomposition_too_broad, token_explosion, empty_model_output, retry_churn, budget_exhaustion, integration_drift)
- Wired into `run_evolver()` as non-fatal post-analysis pass
- CLI: `poe-graduation [--min-count N] [--lookback N] [--dry-run]`
- 18 tests, all passing

---

### Phase 47: Factory Mode Experiment — Bitter Lesson Validation *(PARTIAL — see Phase 49)*

*"If the prompt works as well as the code, the code is legacy cruft."*

**Shipped (2026-03-31):** `factory_minimal` (single-call, $0.04-0.06/60s) and `factory_thin+adv` (decompose → execute → adversarial review → compile) built and benchmarked. Key findings extracted and merged. Full results in `docs/FACTORY_MODE_FINDINGS.md`.

**What was learned:**
- Adversarial review is load-bearing → merged to `quality_gate.py` and `handle.py`
- Ralph verify loop adds value for research goals → available via `--verify` flag and `ralph:` prefix
- Token efficiency prompt critical for Haiku → added to `FACTORY_STEP`
- Not load-bearing: persona routing, lesson injection, multi-plan comparison
- Haiku token explosion on complex research goals makes factory_thin not reliably cheaper than Mode 2 on Sonnet

**What's deferred:** Full factory-vs-Mode-2 conclusion is premature. Variance is high, Mode 2 is still evolving, no scoring rubric yet. Revisit in Phase 49 after Mode 2 stabilizes. See `BACKLOG.md` for open items.

---

### Phase 49: Factory Mode Revisit — Full Comparison *(TODO — shelved until Phase 46+ ships)*

*"Test factory mode vs a stable target, not a moving one."*

Revisit the factory-vs-Mode-2 comparison after Mode 2 has stabilized (post-Phase 46 graduation, post-context compression). The key question — can we prompt our way into the full pipeline? — needs:

1. **A scoring rubric** — subjective quality comparison isn't enough. Define 5-10 criteria (evidence tier accuracy, actionability, contested claim rate, hallucination rate) with numeric scores.
2. **3+ goal types** — nootropic and polymarket aren't enough. Add at least one code-related goal and one ops/planning goal.
3. **Sonnet factory run** — all Phase 47 runs used Haiku. A Sonnet factory run isolates prompt design from model capability.
4. **Token efficiency validation** — confirm the `FACTORY_STEP` token efficiency prompt fix actually closes the Haiku verbosity gap.

**Decision gate:** After Phase 49, make a binary call: merge factory as `--mode thin` flag in `handle.py`, or discard factory branch entirely.

*Prerequisite: Phase 46 shipped, Mode 2 stable for 2+ weeks.*

---

### Phase 50: Thinkback Replay — Session-Level Hindsight Analysis *(DONE)*

*"The evolver learns across runs. Thinkback learns within a run."*

The evolver (Phase 7) works at the pattern level — aggregating lessons across many runs. But within a single completed session, there's signal the evolver never sees: were the *step-by-step decisions* optimal given what we now know happened? A hindsight analyst that reviews each step choice against the final outcome is a qualitatively different signal.

**Shipped (2026-03-31):** `src/thinkback.py` — new module.

- `ThinkbackReport` dataclass: per-step `StepReview` (decision_quality: good/acceptable/poor, hindsight_note, counterfactual), overall_assessment, mission_efficiency (0.0–1.0), key_lessons, would_retry, retry_strategy
- `run_thinkback(loop_result)` — replays a LoopResult through hindsight LLM analysis; dry_run mode skips LLM
- `run_thinkback_from_outcome(outcome_dict)` — works with raw outcomes.jsonl records (synthesizes steps from summary + lessons)
- `load_latest_outcome()` / `load_outcome_by_id()` — outcome loading helpers
- `_save_thinkback_lessons()` — writes extracted lessons back to `memory/lessons.jsonl` tagged `[thinkback:{run_id}]`
- Wired into `passes.py` as the thinkback pass; CLI: `poe-thinkback --latest [--save]`
- 31 tests, all passing

---

### Phase 51: Passes — Unified Multi-Pass Review Pipeline *(DONE)*

*"Council, debate, and thinkback are more valuable together than separately."*

The quality_gate, adversarial pass, LLM council, debate, and thinkback are each powerful but scattered. This phase unifies them into a single composable pipeline: configure which passes to run, chain their verdicts, get one escalation signal.

**Shipped (2026-03-31):** `src/passes.py` — new module.

- `PassConfig` dataclass: quality_gate, adversarial, council, debate, thinkback flags; `from_names(["council","debate"])` / `from_preset("thorough")`
- Named presets: `quick` (quality_gate only), `standard` (+adversarial), `thorough` (+council), `full` (+debate), `all` (+thinkback)
- `PassReport` dataclass: per-pass `PassResult` with verdict/reason/escalate/elapsed_ms; aggregate `escalate` (any pass escalated), `escalation_reason`, `elapsed_ms`
- `run_passes(goal, step_outcomes, config=..., preset=...)` — chains passes, aggregates verdict
- Council/debate absorbed into quality_gate's internal passes when co-enabled; thinkback always standalone
- CLI: `poe-passes --goal "..." --passes all`, `poe-passes --preset thorough --latest-outcome`
- 29 tests, all passing

---

### Phase 52: Cross-Reference Check — Second-Source Fact Verification *(DONE)*

*"The verifier never sees the original response — so it can't pattern-match against the first answer."*

For factual claims in research output, query a second LLM context with no prior answer in scope. Disagreements surface alongside the output, flagging where the original may have hallucinated or overclaimed.

**Shipped (2026-04-01):** `src/cross_ref.py` — new module.

- Two-stage pipeline: (1) claim extraction — asks LLM to identify verifiable facts (numbers, named studies, mechanisms, comparisons); (2) claim verification — fresh LLM context per claim, no source answer visible
- `ClaimVerification` dataclass: claim, category, status (confirmed/disputed/unknown), confidence, note, elapsed_ms
- `CrossRefReport` dataclass: verified list, disputes list, has_disputes property, dispute_summary(), full_summary()
- `run_cross_ref(text)` — full pipeline; dry_run mode skips LLM; never raises
- `cross_ref_annotation(report)` — returns empty string if no disputes (safe to always append)
- Wired into `run_quality_gate(run_cross_ref=True)` as Pass 2.5; disputes trigger ESCALATE
- `QualityVerdict.cross_ref` field added for downstream access
- `poe-cross-ref --text "..." [--file FILE] [--max-claims N]` CLI
- 39 tests, all passing

---

### Phase 53: Persistent Identity Block — Session Coherence Fix *(DONE)*

*"Agents without an always-in-context identity block lose coherence across sessions."*

Research GAP 1 addressed: Poe now has a stable self-model injected into every decompose call. Previously each planning session started cold — no consistent "who I am" framing.

**Shipped (2026-04-01):** `src/poe_self.py` — new module. `user/POE_IDENTITY.md` — durable identity file.

- `load_poe_identity(use_cache, max_chars)` — reads `user/POE_IDENTITY.md`, falls back to built-in minimal identity
- `with_poe_identity(system_prompt, separator)` — prepends `## Who I Am` block to any system prompt
- Wired into `planner.py::decompose()` — every planning call now runs with identity context
- `_IDENTITY_FALLBACK` — always-available minimal identity if file is missing
- 18 tests, all passing

---

### Phase 54: Session Checkpointing — Loop Resume (GAP 3) *(DONE)*

*"A loop interrupted at step 6 of 8 should resume from step 7, not restart from scratch."*

Research GAP 3 addressed: long-running loops that fail, timeout, or get interrupted can now be resumed from the last completed step rather than restarting from zero.

**Shipped (2026-04-01):** `src/checkpoint.py` — new module.

- `write_checkpoint(loop_id, goal, project, steps, step_outcomes)` — writes per-step progress JSON; called after each step in the main loop
- `load_checkpoint(loop_id)` — loads checkpoint by loop_id; returns None if not found
- `delete_checkpoint(loop_id)` — cleans up after successful completion
- `resume_from(ckpt)` — extracts (remaining_steps, completed) for handoff to caller
- `list_checkpoints()` — lists all saved checkpoints, newest first
- `Checkpoint` dataclass: `remaining_steps`, `next_step_index`, `is_complete()` properties
- Wired into `run_agent_loop()` — `resume_from_loop_id` parameter; checkpoint written every step; deleted on `status=done`
- `poe-checkpoint list/show/delete` CLI
- 24 tests, all passing

---

### Phase 55: lat.md Knowledge Graph *(DONE)*

*"Flat AGENTS.md doesn't scale — concepts need cross-references, not prose paragraphs."*

Replace documentation that lives in flat prose with a graph of cross-linked concept files. Wiki-style `[[links]]` between sections. `lat check` verifies no broken links exist. Source files carry `# @lat:` backlinks pointing at the concept nodes they implement.

**Shipped (2026-04-01):** `lat.md/` directory — 9 concept files + index.

- `lat.md/core-loop.md` — autonomous execution loop: decompose, execute, stuck detection, roadblock recovery
- `lat.md/memory-system.md` — tiered lessons, hybrid search, promotion cycle, decision journal
- `lat.md/self-improvement.md` — evolver, thinkback, graduation, intervention pipeline
- `lat.md/worker-agents.md` — Director/Worker/Verifier hierarchy, persona system, tool visibility
- `lat.md/quality-gates.md` — Inspector, adversarial pass, council, cross-ref, passes pipeline
- `lat.md/poe-identity.md` — persistent identity block, with_poe_identity injection, fallback
- `lat.md/checkpointing.md` — per-step checkpoint write, resume_from_loop_id, CLI
- `lat.md/intent-classification.md` — NOW/AGENDA routing, magic keywords, intent.py
- `lat.md/constraint-system.md` — pre-execution constraint enforcement, pattern groups, HITL
- `lat.md/lat.md` — index of all concept nodes
- Source backlinks: `# @lat: [[node#Section]]` comments added to key modules
- `lat check` passes clean (0 broken links)

---

### Phase 56: Promotion Cycle — Standing Rules + Decision Journal *(DONE)*

*"Lessons observed once are hints. Lessons confirmed twice become rules."*

Three-tier self-improving memory: raw observation → hypothesis (2+ confirmations) → standing rule applied unconditionally to every decompose call. Contradictions demote. Decision journal records architectural choices; searched before new decisions are made.

**Shipped (2026-04-01):** Extended `src/memory.py`.

- `StandingRule` dataclass — `rule_id`, `rule`, `domain`, `confirmations`, `contradictions`, `promoted_at`, `source_lesson_id`
- `Hypothesis` dataclass — same shape, lives in `memory/hypotheses.jsonl` until promoted
- `observe_pattern(lesson, domain, source_lesson_id)` — create/increment hypothesis; promote to rule at `RULE_PROMOTE_CONFIRMATIONS=2`; returns `StandingRule` on promotion
- `contradict_pattern(lesson, domain)` — demotes hypothesis if `contradictions > confirmations`; increments rule contradiction counter
- `inject_standing_rules(domain)` — returns formatted rules block for unconditional injection into every decompose call
- `Decision` dataclass — `decision_id`, `decision`, `rationale`, `domain`, `alternatives`, `trade_offs`
- `record_decision(decision, rationale, domain, ...)` — writes to `memory/decisions.jsonl`
- `search_decisions(query, domain, limit)` — TF-IDF ranking via `_FakeTL` adapter; returns relevant prior decisions
- `inject_decisions(goal, domain)` — formatted prior decisions block for decompose injection
- Wired into `agent_loop.py` — standing rules prepended before lessons; decisions appended; both in every planning call
- 25 tests, all passing

---

### Phase 48: Conversation Mining — Idea Archaeology *(DONE)*

*"Revisiting ideas with current maturity yields perspectives we missed the first time."*

Research pass through all Poe/Jeremy conversation history:
- Telegram bot messages (`@edgar_allen_bot` history)
- Claude Code session logs (`~/.claude/projects/` JSONL files)
- OpenClaw workspace MEMORY.md, TASKS.md history
- Git commit messages and PR discussions

Extract orchestration-related ideas, patterns, deferred concepts, and "what if" musings. Run them through the system as research goals. Cross-reference against current BACKLOG.md and STEAL_LIST.md for items that were noted but never pursued, or ideas whose time has come now that the foundation is stronger.

**Why this matters:** Early conversations contain raw intuitions that were too ambitious at the time but may now be achievable. The system's improved self-improvement loop means these ideas get evaluated by a smarter planner than when they were first discussed.

---


---

## MILESTONES Done-log (archived 2026-06-24)

## Done (2026-06-11 — captain's log rotation: hot-path read cost)

The active log had grown to 6MB / 19K entries, and `load_log` JSON-parses the whole file on every call — it sits on every dispatch recall. Rotation is about read cost, not disk.

- [x] **Size-gated rotation riding on `log_event`** (no cron — no-scheduler invariant): past `captains_log.rotate_mb` (default 5, `0` disables), everything but the newest `captains_log.rotate_keep` entries (default 1000) moves to a timestamped archive beside the active file. Data never deleted; head/tail split is disjoint. `LOG_ROTATED` event (47th type) opens each fresh active file as the audit trail. Re-entrancy guard stops the audit-append cascade; same-second archives get a collision suffix instead of overwriting (test-caught data-loss bug).
- [x] **Readers split by purpose** — `query_log`/`timeline` (archaeology) span archives oldest-first; `load_log` (hot path) stays active-file-only, which the retained tail makes safe. Dashboard/dev-script direct readers only want recent entries — unaffected.
- [x] **First live rotation verified on this box**: 18,345 entries archived, 1,001 retained (tail + audit entry), zero loss; active file 6MB → 552KB (~11× hot-path read reduction). Archive reachable through `query_log` post-rotation.

## Done (2026-06-11 — last_verified freshness signal, rule layer)

Entropy thread companion to decay v0: reinforcement and validity are different signals; the most-reinforced rule is the most dangerous one at world-shift time. Trust at injection is now f(record, time-since-verified), read-time only.

- [x] **`StandingRule.last_verified`** — stamped at promotion, on production re-confirmation, and on re-fight keep/revise. `promoted_at` is the fallback anchor for rules written before the field existed. Distinct from `last_applied` (use ≠ verification).
- [x] **Anchoring bug fixed: post-promotion re-confirmations never reached the rule.** `observe_pattern` only matched hypotheses, so re-confirming a promoted lesson seeded a *duplicate hypothesis* (its original was removed at promotion) that could re-promote into a duplicate rule, while `rule.confirmations` stayed frozen at its promotion value forever. An observation matching an existing rule now verifies the rule instead — `RULE_VERIFIED` event (46th type).
- [x] **Stale gate at injection** — uncontradicted rules unverified for `knowledge.rule_staleness_days` (default 30, `0` disables) inject under "Stale rules (unverified for N+ days — verify before relying)" instead of "apply unconditionally". Contested takes precedence over stale. Read-time derivation; data untouched.
- Skill/playbook freshness layers still open (skills have score + circuit breaker already); revisit when staleness shows up there in practice.

## Done (2026-06-11 — suite fully green: 2 pre-existing failures root-caused)

- [x] **Worker-manifest string commands ran as one quoted token** — regression from `a799871`: string commands funneled through the list-argv quote-join became a single `shlex.quote`d token, so `/bin/sh -c` searched for a program literally named the whole command line (127 → 'blocked'). 5 test_orch_core tests affected since the build-loop merge stream. String commands now pass verbatim; args append quoted; list commands unchanged.
- [x] **Scheduler lease test was time-of-day dependent** — lease stamped at real wall clock vs staleness probed at synthetic `next_run+5min`; only passed 03:05–09:00 UTC. `now` seam added to `mark_job_dispatched` (mirrors `check_due_jobs`).

## Done (2026-06-11 — decay-by-invalidation v0: re-fight on collision, rule layer)

Jeremy's pinned first pass (entropy thread): on crystallized-artifact failure, inject the existing mechanism + the failure into the prompt and re-fight the battle. Skills already had the shape (circuit breaker → `rewrite_skill`); this generalizes it to standing rules — the layer where "most-reinforced is most dangerous at world-shift" bites hardest, because a contradicted rule kept being injected "apply unconditionally" forever.

- [x] **Contested gate at injection** — `inject_standing_rules` splits rules with any recorded contradiction into a "Contested rules (verify before relying)" block with their confirmed/contradicted record. Read-time trust derivation; rule data untouched (decay trust, never data).
- [x] **`refight_rule()`** (knowledge_lens) — re-derives a contested rule against its contradiction evidence (STANDING_RULE_CONTRADICTED summaries pulled from the captain's log — the append-only evidence layer). Verdicts: **keep** (trust restored, contradictions zeroed), **revise** (corrected text, record reset — must re-earn), **retire** (demoted back to hypothesis — the demotion the dataclass comment promised but code never did). Unusable output leaves the rule contested, never silently re-trusted.
- [x] **Wired into the evolver cycle** — `run_skill_maintenance` re-fights up to 3 contested rules per cycle when an adapter is present, beside the skill rewrites; agent_loop's adapterless call skips it (spend stays on the evolver path). `RULE_REFOUGHT` event (45th type) is the audit trail.
- Not yet exercised live: no standing rules exist on this box (accretion became possible in M2). `last_verified` freshness signal shipped later same day (see entry above).

## Done (2026-06-11 — recall() loop-slice relocation: one memory read seam)

- [x] **Loop slice complete** — `_build_loop_context`'s memory half (8 substrates: lessons, standing rules, decisions, graveyard, failure notes, learning activity, playbook, knowledge — ~110 inline lines) relocated behind `recall(slice="loop")`; `RecallResult.as_loop_block()` preserves the historical injection order. Skills/cost/codebase-graph/repo-scan stayed in agent_loop (selection context, not memory).
- [x] **Captain's-log read bridges absorbed** — agent_loop K3 bridge and evolver `_llm_analyze` context both read via shared `recall.recent_learning_activity()` (each keeps its own event-type set). Log consumers are now visibility + the seam only.
- [x] **`lesson-cited` edge stamp live** — loop-slice recalls record `lessons_cited` (injected lesson texts) in RECALL_PERFORMED; edge derivable from the log, no new store (RECALL_DESIGN.md vocabulary table updated).
- Side effects now visible at the seam: loop slice runs the dispatch base too (planner sees prior-attempt pressure); `search_graveyard(resurrect=True)` lifecycle mutation documented as inherited.

## Done (2026-06-11 — navigator shadow round 2 + live dispatch shadow)

- [x] **Shadow round 2 (random sample)** — answered the round-1 selection-bias caveat. Seeded random N=20 (seed 42, stratified 12 done / 4 stuck / 4 error, excluding round-1 runs): **0/6 false escalates on well-formed goals** (4 execute, 2 extend — mild conservatism, one extra planning turn, never a human interrupt); all 8 escalates targeted chop debris or repeat burn; 16/20 decided at cheap tier, 0 power. Side finding: 11/20 random goals were decompose-chop debris *including most pipeline-"done" ones* — `done` status ≠ goal health. Full results in `docs/NAVIGATOR_SCHEMA.md`.
- [x] **Live dispatch shadow wired** — `shadow_dispatch_live()` (navigator_shadow.py) called from `handle_task()` right after the dispatch-guard verdict, sharing the guard's RecallResult (one recall, two consumers — no extra scanning). `pipeline_actual.move_equivalent` = `execute` | `guard_refused`, `live: true`; every NAVIGATOR_DECIDED row is navigator-vs-pipeline agreement data. Config-gated `navigator.shadow_dispatch` (default **off** in code — model call per dispatch is real spend; this box opted in via workspace config), `navigator.shadow_tiers` default `["cheap"]`. Never raises, never alters dispatch, skipped on dry_run. Smoke-verified against the real cheap adapter (execute 0.92, event landed in workspace log). Next: accumulate live agreement data → per-class cutover discussion (not before).

## Done (session 40, 2026-06-10 — M1: memory lifecycle fixes + in-process consolidation)

Jeremy's directive: fix-in-place over rewrite; keep it a program, not an operating system (no cron/daemons — rogue-process history); installable harness. Working through M1–M5; M1 shipped:

- [x] **Three latent data-corruption bugs in knowledge_web.py fixed** — tier-blind decay eroding LONG-tier lessons on every load; `run_decay_cycle` + every RMW write persisting decayed scores without re-anchoring `last_reinforced` (compounding rot); RMW paths truncating stores >50 lessons via default `limit=50`. Decay is now strictly a read-time derivation (`raw=True` loading discipline on all rewrite paths).
- [x] **In-process consolidation ("dream cycle")** — `maybe_consolidate()`: marker-gated (`memory/last_consolidation.json`) to once per `memory.consolidation_interval_hours` (default 24h, `memory.consolidation_enabled` to disable). Wired into `handle()` post-request (try/finally, never affects request outcome, skipped on dry_run), heartbeat tick (runs even health-only — pure local file work), and `poe-memory consolidate [--force]`. Captain's-log `MEMORY_CONSOLIDATED` event per run.
- [x] **Dry-run hermeticity** (found via test hang): `dry_run=True` was making real authenticated `claude -p` calls through the decompose planner-lift and per-step model selection — real token burn, test_handle.py took 2h06m. Both sites now only re-tier `isinstance(_, LLMAdapter)` adapters (build_adapter products), so dry-run + injected test doubles pass through untouched; conftest additionally blocks `claude`/`codex` binaries at the `llm._run_subprocess_safe` seam. test_handle.py: 2h06m → ~8s. Details + remaining fragile spots in BACKLOG.md.
- [x] **Step-shape auto-split non-convergence fixed**: analysis-first steps with an incidental exec keyword (e.g. "Analyze findings from build X") re-split into themselves every iteration until max_iterations → 'stuck'. Splitter now sanitizes the run part; executor guard executes as-is when a split wouldn't converge.
- [x] Regression tests: long-tier-never-decays, no-decay-persistence, no-truncation, consolidation gating/config/force, dry-run-never-builds-adapter.

## Done (session 40, 2026-06-10 — M2: standing rules accrete + promotion timing race)

Before M2, `standing_rules.jsonl`/`hypotheses.jsonl` could never grow: `observe_pattern()` was called exactly once per lesson (at medium→long promotion), but rules need 2+ confirmations — and the promotion it depended on was itself racy (one day of decay drops 1.0 → 0.85, below the 0.9 threshold, so consolidation only promoted same-day-reinforced lessons). All in `_post_reinforce_hooks` (knowledge_web.py), which runs on every reinforcement path:

- [x] **Promotion-at-reinforcement-time** — a MEDIUM lesson meeting eligibility (score ≥ 0.9, sessions ≥ 3) promotes to LONG at the moment of reinforcement, when its score is freshly re-anchored. Consolidation-cycle promotion stays as a backstop.
- [x] **observe_pattern on LONG re-confirmation** — re-confirming a long-tier lesson feeds the standing-rule pipeline, so hypotheses accrue confirmations and rules accrete.
- [x] **Cross-tier dedup in record_tiered_lesson** — re-learning an already-promoted lesson now reinforces the LONG record (triggering observe_pattern) instead of silently creating a duplicate MEDIUM lesson. This was the gap that would have kept the pipeline dead in production even with the hooks: lessons are recorded via record_tiered_lesson, which only deduped within its own tier. Both dedup loads now use `limit=None` (truncated dedup misses matches).
- [x] Regression tests: promotion-at-reinforcement (incl. the day-old-eligible-lesson race), hypothesis creation + 2nd-confirmation rule promotion, hook failures never break reinforcement, cross-tier dedup, full medium→standing-rule pipeline end-to-end.

## Done (session 40, 2026-06-10 — M3: recovery lessons + dead self-reflection block revived)

- [x] **Post-loop self-reflection was dead for six weeks** — `_finalize_loop`'s Phase 44-45 block referenced `ctx.project` (no `ctx` in scope since the 2026-04-26 extraction): NameError every run, swallowed by its own except. Diagnosis save, lenses, recovery planning, and the diagnosis lesson never ran. Fixed; found while wiring M3 (the new test wouldn't pass — the test was the detector).
- [x] **Recovery insights recorded as typed lessons** (M3 proper, mechanical/no-LLM): stuck run + table plan → `[recovery-plan] <failure_class>: <action>` (confidence 0.5); completed run with recovery_steps > 0 → `[recovery-verified] <kinds> unblocked a run: <first failure>` (confidence 0.7 — completion is the verification). Stable text → dedup reinforcement → eligible for the M2 standing-rule pipeline.
- [x] **Same-class bugs from a pyflakes sweep**: `evolver.rewrite_skill` lost its `verbose` param while both callers pass `verbose=verbose` — TypeError on every call, swallowed by callers' excepts, skill rewriting (circuit-breaker recovery) dead; `llm.py` bare `thinking_budget` in the no-kwarg fallback branch; `agent_loop` terminal handler bare `block_reason`. All fixed.
- [x] **Bug class locked out**: `tests/test_static_undefined_names.py` runs pyflakes' undefined-name check over src/ as part of the suite; evolver's annotation-only `Skill` imports moved under TYPE_CHECKING so the report stays clean.

## Done (session 40, 2026-06-10 — M4: GOAL_BRAIN.md, the compiled-truth anchor)

- [x] **GOAL_BRAIN.md created at repo root** — both the goal-brain artifact definition v0 (defined by example, per the May-18 sequencing step 1) and this project's own instance: Jeremy's invariants quoted verbatim (anti-telephone), compiled truth with verification basis per claim, dated append-only decisions, a Threads section as the manual fan-out defense, and open questions with what they block. Format rules distinguish human-steerable (Intent, Invariants) from system-maintained sections.
- [x] **Wired into CLAUDE.md** as session-checklist step 2, with the precedence rule: when GOAL_BRAIN.md disagrees with any other doc, GOAL_BRAIN.md wins.

## Done (session 40, 2026-06-10 — M5: portability pass + the rc=1 blocker decomposed)

Per the installable-harness invariant ("ideally this is a harness you install, not a single machine setup"). Verified end-to-end: fresh venv, `pip install -e .`, `poe-doctor` from the installed entry point under a foreign `HOME=/tmp/m5-home`.

- [x] **Hardcoded machine paths removed** — `llm._CODEX_BIN` was a literal linuxbrew path; now `_find_codex_bin()` (CODEX_BIN env → PATH → common locations → bare name), mirroring `_find_claude_bin()`. `backtester.py` dropped its `/home/clawd/prototypes` WORKSPACE constant (input must exist at the given path; output + metrics land where `--output` says). `backtest_metrics.py` DEFAULT_INPUT is cwd-relative. `scripts/blind-test-slycrel.sh` uses `$HOME`. The `deploy/systemd/` units keep absolute paths by design — they're documented per-machine templates.
- [x] **doctor.py memory check de-hardcoded** — was `Path(__file__)/../memory` (reported the stale repo-local copy); now uses canonical `orch_items.memory_dir()` resolution, and the skills check reads `<memory>/skills.jsonl` instead of the repo-relative path.
- [x] **The "claude subprocess failed (rc=1)" blocker decomposed into two real defects** (live repro via foreign-HOME doctor + `/tmp/claude_rc1_*.txt` dumps):
  1. **Exit code trusted over payload** — the CLI can print a complete success result JSON and still exit non-zero (e.g. failing to persist session state after responding). `ClaudeSubprocessAdapter` is now payload-first: rc≠0 with a genuine success payload (`type=result`, `subtype=success`, `is_error` falsy, `result` present) is accepted with a warning log. `is_error` is the load-bearing check — the CLI reports *errors* as `subtype:"success"` + `is_error:true` too.
  2. **Error detail buried the actual message** — failures raised with 300 chars of truncated raw JSON, hiding the CLI's human-readable error (it lives in the payload's `result` field, e.g. "Not logged in · Please run /login"). The raise now surfaces that field verbatim. Historical "rc=1" mysteries were very likely real, readable errors nobody could see.
- [x] **Regression tests** — payload-first acceptance (clean + amid JSON/warning noise), error-payload still raises with the readable message, extractor unit tests. test_llm.py: 91 passing.
- [ ] **Deferred**: codex-side payload-first check (JSONL event format differs, no observed repro — revisit if a codex rc≠0-with-output shows up).

M1–M5 complete: the session-40 arc (memory lifecycle → standing rules → recovery lessons → goal-brain → portability) is done.

---

## Correction (session 40, 2026-06-10)

The session-39 "Next Up" queued implementing `src/scope.py`/`ScopeSet` from scratch — but that shipped 2026-04-23 (session 36, plus `ResolvedIntent`), and the session-38 delta-audit explicitly recommended pausing further Phase 65 work. The May 12 autonomous session synthesized from stale sources. Queue replaced with the goal-brain sequencing above. This is an instance of the fan-out failure mode the goal-brain is designed to fix: parallel sessions (May 12 research, May 18 conversation) never reconciled.

---

## Decision recorded (2026-06-10) — navigator visibility of work-LLM output

The open question from the 2026-05-18 conversation ("does the navigator see the work LLM's full output, or only recommendation + summary?") — Jeremy's call: **sometimes, on demand**. Default is recommendation + structured signals; full output is pullable when the navigator judges it needs it — same pattern as skills (not loaded by default, available if needed), "because we need the work to get started before we know if we need it." Criteria for when to pull is a taste/judgment call, deliberately unpinned for now.

---

## Done (session 39, 2026-05-12 — findings-and-design research brief)

Autonomous goal "findings and design" resolved via dev-recall to constraint orchestration (Phase 65) as the primary subject, with intent resolution and adaptive execution as causal prerequisites. Produced a complete synthesis brief covering the three-system causal chain (intent→constraints→execution) and the ScopeSet naming/schema correction.

- [x] **Research brief written** — `docs/research-brief-findings-and-design.md` (242 lines). Covers: §0 question, §1 constraints, §2 research plan, §3 sources, §4 exec summary, §5 key findings (5), §6 counterpoints, §7 risks (6), §8 recommendation, §9 next actions, §10 appendix.
- [x] **Key finding** — the three systems (intent resolution / scope / adaptive execution) form a causal chain but share no explicit handoff protocol; ScopeSet is the missing shared data structure (Phase 65 already has a complete v1 spec in `PHASE_65_IMPLEMENTATION_PLAN.md`; implementation can start now — all prerequisites are done).
- [x] **ScopeSet naming corrected** — brief originally used `ConstraintSet`; audited against `PHASE_65_IMPLEMENTATION_PLAN.md` and corrected to `ScopeSet` throughout. `EvaluationContext` extension confirmed deferred (not v1 scope).
- [x] **Committed** — branch `arch/thread-navigator`, commit `c08006c`.

---

## Done (session 39-main, 2026-05-12 — research synthesis: productive persistence + zoom-out metacognition)

_Merge note (2026-06-10): two independent "session 39"s ran on 2026-05-12 — one on `arch/thread-navigator` (above), one on `main` (below). Neither knew about the other; reconciled in the session-40 merge._

Completed research synthesis on two interconnected design spaces needed for Phase 66–67 (persistence calibration and failure-driven zoom-out):

**Productive Persistence research (`docs/research/productive_persistence.md`):**
- [x] Synthesized grit/persistence literature (Duckworth, Kapur, Dweck, Bjork, Seligman, meta-RL)
- [x] Grounded three highest-value gaps in Poe code: unused `task['attempt']` signal, undetected zoom-out signals (4 of 5), LLM-only failure classification
- [x] Mapped failure taxonomy to retry tiers: informative/confirming/infrastructure/ambiguity
- [x] Defined productive persistence invariant: goal-stable / strategy-flexible (vs. count-based retry)
- [x] Identified optimal persistence zone (~60–85% step-success rate) and calibration surfaces
- [x] Proposed Wave 1 (wiring existing signals), Wave 2 (structural upgrades), Wave 3 (validation-requires) implementation roadmap
- [x] Noted external evidence (Credé meta-analysis r≈0.18) argues *for* signal quality over persistence volume

**Zoom-Out Metacognition research (`docs/research/zoom-metacognition-adaptive-expertise.md`):**
- [x] Synthesized expert learning literature (Hatano & Inagaki, Schwartz & Bransford, Feltovich/Spiro CFT, Ericsson)
- [x] Modeled five zoom-out signals: stuck-streak, near-miss, confidence–accuracy decoupling, meta-ignorance, load-aware threshold degradation
- [x] Mapped reframing mechanics: from local (retry loop) → tactical (strategy pivot) → strategic (decompose) → lateral (skill swap) → hierarchical (goal inversion)
- [x] Connected to existing Poe: introspect.py (stuck detection), inspector.py (friction signals), director.py (strategy pivot), agent_loop.py (multi-tier escalation)
- [x] Identified undetected signals 4/5 and proposed semantic-hash solution for stuck_streak (distinguish identical loops from novel exploration)

**Research Brief (`docs/research-brief-persistence-and-zoom.md` — renamed in the 2026-06-10 merge; the same filename was independently used by the arch-side session 39):**
- [x] Synthesized both research docs into unified findings + design implications
- [x] Flagged theory-to-implementation gaps with priority ranking
- [x] Noted contradiction resolution (retry-as-count vs. retry-as-hypothesis-narrowing) and which model is more defensible

**Knowledge graph updates (`lat.md/core-loop.md`, `lat.md/quality-gates.md`):**
- [x] Added backpointers to new research docs

**Supporting files:**
- [x] `docs/research/productive_persistence_summary.md` — one-page takeaway for quick reference
- [x] `docs/research/zoom-metacognition-adaptive-expertise.md` — full treatment
- [x] `docs/adversarial-verification.md`, `docs/adversarial-verification-report.md`, `docs/md-claims-audit.md` — audit trail from synthesis round-trips

**Next:** Implementation phases (Wave 1–3) depend on design discussion; BACKLOG:DISCUSS (invert the planning stage: 1-shot first) and intent-resolution minimum experiment take priority. These research docs provide the grounding for those decisions.

---

## Done (session 38, 2026-04-26 — decomposition feedback wired forward)

Triggered by the scope A/B 1+1 run on 2026-04-26: both arms hit `decomposition_too_broad` 8/8, treat's step 8 ran 9 minutes, and BACKLOG:316 ("warning fires but nothing acts on it") was still open. The Phase 62 wiring acted on `decomposition_too_broad` only on the *blocked* path; this closes the *post-mortem → next-decompose* path.

- [x] **`LoopDiagnosis.project` field** (introspect.py) — caller threads project slug; persisted on save_diagnosis; load_diagnoses preserves it. Backwards-compat default `""`.
- [x] **`diagnose_loop(loop_id, project="")` signature** — agent_loop.py's end-of-loop introspect block passes `ctx.project`. Mid-loop call sites unchanged.
- [x] **`find_relevant_failure_notes` strengthened** — same-project diagnoses lead the result list ahead of goal-token-overlap matches (a prior decomp warning on this exact project is far more actionable than a vague semantic match). Limit raised 2→3.
- [x] **`decomposition_too_broad` notes carry concrete numbers** — `_format_decomp_too_broad_note` parses evidence ("Step 8 took 534230ms with 277883 tokens") into "Step 8 took 534s with 277K tok" and appends the actionable cap (`≤120s/200K tok per step; split if a step touches >3 files`). The next planner sees specifics, not "decompose further" advice.
- [x] **agent_loop.py call site** — `find_relevant_failure_notes(goal, limit=3, project=project or "")` in `_build_loop_context`.
- [x] **6 new tests** in `tests/test_introspect.py` covering project capture, persistence roundtrip, project-priority retrieval, fallback to token overlap, concrete-number formatting, and no-project-arg backward compat.

Closes BACKLOG:316 for the post-mortem case (still open: in-flight 8/8-strong loops where the warning fires after completion-not-blocking).

**Planner tier lift to MODEL_POWER (opus):**
- [x] **agent_loop._decompose_phase uses `assign_model_by_role("planner")`** — was hardcoded `cheap → mid` lift; now reads the central role→model policy in poe.py (which already maps `planner → MODEL_POWER`). Same surface director.py uses; rule-of-three: third call site, kept the policy in one place. Falls back to MODEL_MID if power unavailable.
- Rationale: planner runs once per loop and biases every subsequent step. Marginal cost of opus (~$0.20, ~30s on a single call) is negligible against what a sloppy plan compounds across 8 step executions. Step execution stays on whatever the loop adapter selected.

**Audit-driven fixes from the 1+1 scope A/B telemetry:**
- [x] **`scope_ab_runner.py --repo`** (commit `6d0f57e`) — runner now passes `--repo` so the closure executes inside the test repo, not Poe's repo. Without this, closure checks ran against the orchestration source tree and gave nonsense verdicts.
- [x] **`CLOSURE_VERDICT` carries `loop_id`** (commit `75dd84f`) — captain's log event now includes `loop_id` so it can be linked to its loop in run-dir slices and lineage chains. `LOOP_CREATED` and `QUALITY_GATE_VERDICT` already had it; closure was the missing third leg.
- [x] **Pre-flight classifier hardened** (commit `2f8ff5b`) — `_classify_precondition` now handles sentinels (`none`, `n/a`, `tbd`, `(none)`, etc.) and Go-style module paths (`gorilla/websocket`) explicitly. Previously a sentinel like `"none"` got classified as a path, `Path("none").exists()` returned False, and the precondition fired as a phantom failure.
- [x] **`decision` field in quality-gate verdict** (commit `599d140`) — disambiguates the prior `verdict=ESCALATE escalate=False` log line. New flow: `escalate=True → decision=ESCALATE`, `verdict=ESCALATE && escalate=False → decision=WEAK_ESCALATE`, otherwise `decision=PASS`. Captain's log event summary now leads with `decision=`.
- [x] **Post-escalate closure** — quality-gate ESCALATE re-runs the loop with a stronger model; the escalated re-run is the version we ship, but the captain's log only carried the *initial* loop's CLOSURE_VERDICT. handle.py now runs a second `verify_goal_completion` after the escalated loop returns, with a fresh `diagnose_loop` for the new loop_id. Wrapped in try/except so closure failures never block delivery. 2 new tests in `tests/test_handle.py::TestPostEscalateClosure`. (commit `3c09a2d`)

**BACKLOG:287 — Step runner long-lived process detection (commit `3c6d901`):**
- [x] **`_is_long_lived_step` in step_exec.py** — phrase set + verb-noun regex catching "start/launch/run/spawn/boot the X server/service/daemon/listener/broker/worker/api". When matched, injects `_LONG_LIVED_PROCESS_EXTRA` into user_msg telling the executor to (a) background-spawn (`run_in_background`/`& disown`/`nohup &`), (b) probe readiness via curl/nc/log-grep, (c) call complete_step on readiness signal — not on exit. Stops the failure mode from the 2026-04-23 audit ("Start server with --headless flag on localhost:8080" hung 10 min until SIGTERM).
- [x] **14 new tests** in `tests/test_step_exec.py::TestIsLongLivedStep` cover the audit case, each long-lived phrase, the verb-noun regex, and false-positive guards.

**BACKLOG:316 leftover — STEP_TOO_BROAD mid-loop signal (commit `3c6d901`):**
- [x] **`STEP_TOO_BROAD` captain's log event** — fires the moment a `done` step exceeds both caps (>120s elapsed AND >200K tokens). Wired in `_write_iteration_artifacts` after march-of-nines. Visible in the per-run `captains_log_slice.jsonl` and as a project decision so the warning is observable mid-loop rather than only at post-mortem (the post-mortem path was closed earlier this session via `find_relevant_failure_notes`; this closes the in-flight visibility gap for the 8/8-strong case).
- [x] **7 new tests** in `tests/test_agent_loop.py` cover the predicate (above caps, below caps, only-one-cap, blocked/skipped/zero-metric guards) and EVENT_TYPES registration.

**Phase 65 delta-audit (2026-04-26):**
- See `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md` "Status as of 2026-04-26" — the minimum experiment described in the 2026-04-16 review **already shipped** as `src/scope.py` (renamed `premise` → `scope` to avoid the `constraint.py` collision), and `ResolvedIntent` extended it with deliverables on 2026-04-23. The unbuilt parts of the original design (constraint lifecycle, violation detection, cross-goal scope retrieval) are now in tension with the 2026-04-26 DISCUSS note at the top of BACKLOG.md (1-shot-first frame) — recommend pausing any further Phase 65 implementation until the frame question is resolved.

---

## Done (session 37, 2026-04-26 — run transparency phase + closure reads deliverables)

Started from the data-loss observation in the 2026-04-25 scope A/B 1+1 (treat made commits, control's setup-reset wiped them). Jeremy framed the gap as systemic transparency, not test-tooling: every paid run should produce a source/build/artifact bundle. Mental model: "source (plan + prompt artifacts) + build folder (interim objects + resources) for compiling a project."

**Run-dir transparency (`src/runs.py` — new module, 4 commits, 25+ tests):**
- [x] **Per-run nickname module** (commit `13a6470`) — deterministic 2-word labels (50 adj × 50 noun = 2500 combos), sha1-hashed handle_id for even distribution. Run-dirs at `~/.poe/workspace/runs/<handle_id>-<nickname>/` with source/build/artifact subtree.
- [x] **Run-dir as the write destination, not a copy target** (commit `8a68e37`) — Jeremy's design correction: writes go to the run-dir from the start. Process-level context var (`set_current_run_dir`) lets agent_loop's PARTIAL.md / scratchpad / step files / plan manifests / loop log JSON consult `runs.artifact_dir()` and land directly in `<run-dir>/build/`. handle.py's scope.md / resolved_intent.md route to `<run-dir>/source/`. Behavior-preserving fallback when no run-dir is active.
- [x] **Per-run captain's log slice** (commit `17fb0e9`) — `record_log_offset()` at run start, `slice_log_for_run()` at finalize → `<run-dir>/build/captains_log_slice.jsonl` covering only this run. Same pattern `scope_ab_runner.py` used externally; centralized so every paid run gets it.
- [x] **Per-run repo bundle** (commit `a99771b`) — `record_repo_base()` + `snapshot_repo_bundle()` capture `repo.bundle` (`git bundle --all`), `git_log.txt`, `branch_diff.patch`, `base_sha.txt` into `<run-dir>/artifact/`. Restorable with `git clone repo.bundle`. Closes the data-loss gap that motivated the audit phase.
- [x] **Smoke-tested end-to-end** — handle.py dry-run on agenda lane produces full source/build tree with all expected files (PARTIAL, scratchpad, plan, log.json, captains_log_slice).

**Captain's log structural events (commit `c644d82`, before this session):**
- [x] **`LOOP_CREATED` event** — every loop-spawn site (initial dispatch, director restart, closure restart, quality-gate escalate) emits cause-effect chain with `reason` + `parent_loop_id`. Threaded through handle.py escalation paths.
- [x] **`QUALITY_GATE_VERDICT` event** — promoted from handle.log into structured captain's log; emitted from `quality_gate.py::run_quality_gate` after pass1 verdict parsing with verdict/confidence/escalate/reason/step_count/loop_id.

**Closure reads deliverables (commit `0921580`):**
- [x] **`verify_goal_completion(resolved_intent=...)`** — when ResolvedIntent has deliverables, they render as "Deliverables committed when planning (verify each was built):" block in the plan call, with name/description/preconditions inline. handle.py threads `_resolved_intent` through. This is the watcher half of `docs/DRIVER_AND_WATCHER.md` #4 — without it, deliverables were advisory planner-prompt text only.
- [x] **3 new tests** in `test_director.py` covering deliverable injection, no-resolved-intent (no header), and empty deliverables list.

**Closure pre-flight for preconditions (commit `3d3d9e6`):**
- [x] **`_classify_precondition()` + `_run_precondition_preflight()`** — command-shaped tokens get `shutil.which`; path-shaped get `Path.exists`; opaque (port numbers, env-var requirements) are skipped. Failed pre-flights are prepended to check_results so the director sees them as gaps; passing pre-flights stay out of the feed. Stops treating "command not found → exit 127" as "check passed" — the silent-failure bug that motivated preconditions in `INTENT_RESOLUTION_DESIGN.md`.
- [x] **8 new tests** in `test_director.py` covering classification, command-present/missing, path-present/missing, opaque-skip, fail-prepended, and pass-suppressed.

---

## Done (session 36, 2026-04-23 — scope A/B analysis + ResolvedIntent v0: plan-creation as its own step)

Two-part session. First: analyze the 2026-04-22 scope A/B chain (6 runs, 3 treat + 3 control). Second: make the "fundamental approach shift" Jeremy asked for — per `docs/INTENT_RESOLUTION_DESIGN.md` and `docs/DRIVER_AND_WATCHER.md` #4, the thread the driver watches is a durable artifact with concrete deliverable commitments, not just a scope bound.

**Analysis (`~/.poe/experiments/scope-ab-2026-04-22/ANALYSIS.md`):**
- Treat (scope injected): 3/3 rc=0, 8-step plans consistently, ~$8 each
- Control (scope not injected): 1/3 clean (rc=0), plans 15/37/40 steps, $8–$41
- **Primary signal: scope injection structurally compresses plan length** (8 vs 15–40). Consistent across all three treat runs.
- Both control failures (run-02 SIGTERM at "start server," run-06 61-min rate-limit cascade + rc=1) are recovery-layer bugs that surface preferentially on long plans — four new backlog entries added.
- Hypothesis on closure quality ("does injection improve verdict?") remains under-tested: too few clean controls.

**ResolvedIntent v0 (the shift):**
- [x] **`src/scope.py`** — new `Deliverable(name, description, preconditions)` dataclass and `ResolvedIntent(scope, deliverables, raw_text)` wrapper. LLM prompt extended from 3 sections to 4 (adds "Deliverables" as a checkable artifact map with inline `[preconditions: X, Y]` annotations). Shared `_split_sections()` helper keeps `_parse_scope_markdown` and `_parse_resolved_intent_markdown` reading the same text.
- [x] **`generate_resolved_intent()`** — wraps existing `generate_scope()` (no extra LLM call); re-parses the scope's `raw_text` for deliverables. `generate_scope()` still works and still returns `ScopeSet` — back-compat preserved for everything that only wants the scope view.
- [x] **`handle.py` integration** — when `scope_generation` is on, calls `generate_resolved_intent` instead of bare `generate_scope`. Writes `resolved_intent.md` alongside the existing `scope.md` (both land in `~/.poe/workspace/projects/<slug>/artifacts/`). Injects the full thread (scope + deliverables) into planner ancestry when `scope_ab_skip` is false. Adds `deliverables_count` to captain's log `SCOPE_GENERATED` context.
- [x] **15 new tests** in `test_scope.py` — Deliverable parsing (full form, no preconditions, bare name, preconditions-only), ResolvedIntent rendering, `generate_resolved_intent` edge cases (missing goal, adapter failure, scope-only response, proxy-resolution carry-through), injection helper, and back-compat guard that `_parse_scope_markdown` still ignores the deliverables section.

**Backlog additions (from A/B analysis):**
- Step runner has no hang protection / no long-lived-process affordance (run-02 SIGTERM)
- Rate-limit recovery has no total-backoff cap; phantom `Step -1` on recovery path (run-06)
- `decomposition_too_broad` miscalibrated post-scope (fires on every 8-step treat run)
- [x] `run-03-treat` CLOSURE_VERDICT missing — fixed 2026-06-11: `_emit_skip()` added to all early-exit paths in `verify_goal_completion` (4 regression tests)

**Explicitly deferred for later steps** (so future session picks them up with shape named):
- Cross-turn agenda-state carryover (godot-replay finding: dormant-but-open items)
- Durable reuse of `resolved_intent.md` across invocations for the same project slug
- `assumed` / `verified` / `unknown-but-accepted` sections
- Closure pre-flight consuming the `preconditions` field (resolves binary via `shutil.which`, downgrades to INCONCLUSIVE)
- Side-quest DAG for unknowns (`INTENT_RESOLUTION_DESIGN.md` says don't build until one's been run by hand)

---

## Done (session 35, 2026-04-22 — heartbeat health-only by default, autonomy explicit)

Live-box behavior exposed an architectural coupling: `poe-heartbeat.service` came back on reboot and `heartbeat_loop()` implicitly owned scheduler drain, task-store drain, mission drain, backlog drain, evolver, inspector, and eval work. That made manual-use deployments surprising and turned heartbeat from "health substrate" into an autonomy daemon by default.

- [x] **Heartbeat loop split by mode** — `heartbeat_loop(..., autonomy=False)` is now health-only by default. Background drains and self-improvement work only run when autonomy is explicitly enabled.
- [x] **CLI switch** — `poe-heartbeat --loop --autonomy` enables the old background behavior intentionally. Plain `poe-heartbeat --loop` now stays in health-check mode.
- [x] **Docs updated** — `README.md` and `skills/arch-platform.md` now describe heartbeat as health-only by default and autonomy as opt-in.
- [x] **Tests added** — `test_heartbeat.py` verifies scheduler drain is skipped in health-only mode and runs when autonomy is enabled.

---

## Done (session 35, 2026-04-22 — deployment path now treats services as optional)

Follow-up to the heartbeat/autonomy split: the repo still nudged fresh installs toward always-on services as if they were the normal path. That was intentional in the original autonomous-host framing, but wrong for manual-use mode and easy to misread as "required for orchestration."

- [x] **Bootstrap wording updated** — `poe-bootstrap install` and `poe-bootstrap services` now describe service files as optional templates, not the default operating mode.
- [x] **Install output softened** — bootstrap now prints commented optional `systemctl` / `launchctl` commands instead of presenting service enablement as the next required step.
- [x] **Systemd unit clarified** — `deploy/systemd/poe-heartbeat.service` now identifies itself as an optional health monitor and explicitly notes that manual runs do not require it.
- [x] **Docs aligned** — `README.md` and `docs/ARCHITECTURE.md` now describe services as optional infrastructure layered on top of self-contained manual runs.

---

## Done (session 35, 2026-04-22 — optional-service framing extended beyond heartbeat)

The heartbeat fix still left adjacent docs reading as if Telegram and inspector should be deployed by default. That blurred the line between manual orchestration and intentionally installed background interfaces.

- [x] **Telegram docs softened** — `README.md` now presents `telegram_listener.py` as an optional interface with manual run instructions first and service enablement second.
- [x] **Optional services section expanded** — `README.md` now groups heartbeat, Telegram, and inspector under explicit opt-in deployment guidance, with manual-run alternatives shown alongside service installs.
- [x] **Architecture docs normalized** — `docs/ARCHITECTURE.md` and `docs/ARCHITECTURE_OVERVIEW.md` now describe heartbeat and Telegram as optional deployment surfaces rather than baseline runtime requirements.
- [x] **Bootstrap metadata clarified** — generated service templates now carry "Optional ..." descriptions so copied units read correctly in `systemctl` and deployment output.

---

## Done (session 34, 2026-04-16 — `synthesize_skill()` 3-gate pre-promotion check)

BACKLOG item (P7/10, Claude Skills quality bar) — wired into `evolver.synthesize_skill()` before persistence. Three gates run in sequence; a skill that fails any gate is discarded with a logged reason. All gates execute at synthesis time with no new infrastructure.

- [x] **Trigger precision gate** — rejects patterns that fire on a fixed 10-goal off-target corpus. `_TRIGGER_PRECISION_MAX_HITS=3`: if any single pattern matches ≥3 off-target goals it's too generic and would steal matches from better skills. `_TRIGGER_MIN_LEN=4` also rejects stubs like "the" / "and" / "do".
- [x] **Output schema gate** — requires a non-empty `expected_outputs` list in the LLM response, forcing the LLM to declare the concrete artifacts the skill produces.
- [x] **Edge case coverage gate** — requires ≥`_MIN_EDGE_CASES=3` distinct non-empty entries in the `edge_cases` list, so the LLM has to articulate adversarial/boundary conditions up front.
- [x] **Prompt updated** — `_SYNTHESIZE_SYSTEM` now asks for `expected_outputs` and `edge_cases` fields with concrete examples and a precision rule on triggers ("NOT generic words that would match unrelated goals").
- [x] **15 new tests** in `test_evolver.py`: 5 tests per gate (each reject path + accept path), 4 end-to-end synthesize_skill rejection tests (one per gate + all-pass reference). Existing `_SynthesisAdapter` fixture extended with the new fields so all prior tests still pass. Full suite green.

Source: @av1dlive / @eng_khairallah1 via the Anthropic engineers' Claude Skills quality bar (80K+ skills most poorly built — these three gates were cited as the highest-leverage filters).

---

## Done (session 34, 2026-04-16 — `correspondence.py` dev-facing retrieval prototype)

Framing: this is explicitly **dev-facing tooling**, not Poe runtime. The name avoids collision with Poe's own `knowledge.py` crystallization dashboard + `knowledge_web.py` tiered memory, both of which serve Poe operating on its own goals. The underlying library is reusable if Poe ever wants self-recall of our correspondence, but v1 is invoked via `dev-recall` CLI (no `poe-` prefix). This distinction — "how we build the system" vs "the system itself" — matters and is now documented explicitly in the module docstring and CLAUDE.md.

- [x] **`src/correspondence.py`** — markdown heading-aware chunker, sqlite-vec storage, OpenAI-compatible embeddings (auto-switches to OpenRouter when only `OPENROUTER_API_KEY` is set), content-hash dedup for idempotent re-ingest, `--since Nd` filter for incremental updates.
- [x] **`tests/test_correspondence.py`** — 14 tests: chunking behavior (heading splits, section chain, size caps, hash stability), ingest/query roundtrip with deterministic fake embeddings, idempotent re-ingest, `since_seconds` filter, status output, graceful failures. sqlite-vec-dependent tests skip cleanly if extension absent.
- [x] **Corpus ingested** — 110 files → 1,181 chunks from `docs/`, `lat.md/`, `MILESTONES.md`, `BACKLOG.md`, `ROADMAP.md`, `CLAUDE.md`, and `~/.claude/.../memory/`. Smoke queries return expected top hits (e.g. "why rename constraint to scope" → `PHASE_65_IMPLEMENTATION_PLAN.md > Rename` at distance 0.83; "don't patch prompts with taxonomies" → the `feedback_inference_not_prompting.md` I wrote earlier in the session as rank 1).
- [x] **Dev guidance in `CLAUDE.md`** — explicit pointer to `dev-recall` as the preferred recall path for prior correspondence, with note that this is dev tooling and not Poe runtime.
- [x] **`pyproject.toml`** — new `correspondence` optional extra (sqlite-vec + requests); NOT added to `[project.scripts]` (no `poe-` prefix, invoked as `python3 -m correspondence` — preserves the dev/system boundary).

Why now: design conversations, reviews, decisions, and conversation logs accumulate across sessions in 4+ locations. MEMORY.md (my auto-memory index) gives me named-file lookup but not query-by-topic. With the corpus growing — especially around the last two sessions (scope orchestration, closure gate, inversion, taste) — blind grep stops scaling. Retrieval first, graph later per Jeremy's preference.

Next steps for this tool (not urgent): BM25+RRF fusion using the existing `hybrid_search.py` if retrieval quality disappoints in practice; nightly re-ingest via heartbeat; conversation-transcript ingestion (JSONL session logs). None of these block using it today.

---

---

## Done (session 34, 2026-04-16 — closure check: verdict gates the loop + behavioral-check prompt)

The review's sharpest point about Phase 65 ("scope alone wouldn't have caught slycrel-go — nobody ran a browser") led to the sibling fix for Phase 63's closure check. Two concrete gaps closed:

- [x] **Closure verdict now drives restart** (`src/handle.py`) — previously `verify_goal_completion` emitted `verification` and `needs_work` events to the channel but the verdict was discarded; `loop_result.status` stayed `done` regardless of gaps found. Now, when the verdict returns `complete=False` with `confidence >= 0.6` and at least one check ran, handle.py injects the gap list as ancestry context (`== Closure gap context ==`) and re-runs the loop. Shares the same `continuation_depth` cap as director-triggered restart (≤3).
- [x] **Closure plan prompt rewritten (inversion-driven, linked to scope)** (`src/director.py`, commit 74cd090) — first attempt (8255b52) encoded a four-category taxonomy in the prompt ("if service goal, demand behavioral check"). Jeremy pushed back: that's prompt-patching, the exact class of fix this project is designed to replace. The foundation is intentionally vague and fuzzy prompting; the payoff is inference, memory, inversion, and perspective rotation — not hardcoded answers in the system prompt. Bitter principle: orchestration harnesses general LLM capability; it doesn't replicate it. Replaced the taxonomy with inversion framing: if scope supplied failure modes, each check probes a named failure mode; if no scope, the LLM does its own inversion first. Each check labels its `failure_mode` so coverage is observable. `verify_goal_completion` now accepts `scope=ScopeSet`; `handle.py` plumbs the scope generated at plan time through to closure — linking Phase 65 (scope narrows planning) and the closure gate (scope's failure modes steer verification) as the two halves of "good judgment."
- [x] **Config flag `closure_restart`** — defaults to `true`; set to `false` for A/B comparison or to disable noisy restarts. Read via `config.get()` matching `scope_generation` / `adaptive_execution` pattern.
- [x] **Tests** — 8 new tests in `test_handle.py` (restart fires on incomplete verdict, gap context injection, depth increment, low-confidence skip, complete-verdict skip, checks_run=0 research skip, config flag disable, depth cap); 1 new source-level prompt guard in `test_director.py` blocking regression to build-only example.

## Done (session 34, 2026-04-16 — Phase 65 MVE: scope generation shipped)

- [x] **Concept renamed to "scope"** (Jeremy's call) — captures both what IS and what IS NOT in the bounded space; complements specs naturally; avoids collision with `src/constraint.py`.
- [x] **`src/scope.py`** — `ScopeSet` dataclass + `generate_scope()` function + `_parse_scope_markdown` parser + `inject_scope_into_context` helper. Single-call inversion via generalist prompt asking for failure modes + derived in-scope/out-of-scope. Non-fatal: returns `None` on any failure.
- [x] **Inversion prompt** — demands goal-specific failure modes (not generic "bug risk" items). Verified against a real adapter: "Build a safe websocket server for a text adventure game" produced 7 concrete failure modes (unauthenticated messages, unbounded size, race conditions, resource leaks, command injection, rate limiting, token leaks) + 5 in-scope + 5 out-of-scope. Output specificity addresses the review's concern that LLM inversion would be generic.
- [x] **`handle.py` integration** — scope generation fires after clarity check, before `run_agent_loop`. Scope markdown injected into `ancestry_context_extra`; artifact written to `~/.poe/workspace/projects/<slug>/artifacts/scope.md`. Verified end-to-end (scope.md landed in the correct project directory with correct format).
- [x] **Config flags** (`~/.poe/config.yml`):
  - `scope_generation: true` — master enable (default false)
  - `scope_ab_skip: true` — A/B paired control: scope is generated and recorded but NOT injected (for comparison runs)
- [x] **`[scope-deferred]` markers** at every punted decision — triad (single generalist used), human gate (no approval UX), enforcement (injected not checked), lifecycle (immutable after set), retrieval (full-block injection), cross-goal memory (recorded but not retrieved), ab-skip path. Searchable via `grep "scope-deferred"` when expanding later.
- [x] **19 tests** in `test_scope.py` covering ScopeSet, parser edge cases (heading variants, asterisk bullets, garbage input), generator non-fatal paths, and deferred-marker emission. Full suite: 4,341 passing (up from 4,322).
- [x] **Config system bug fixed** — `scope_generation` was being read from `user/CONFIG.md` (in-repo) instead of `~/.poe/config.yml` (user-global). Switched to `config.get()` matching `adaptive_execution`'s pattern.
- [x] **Project slug derivation fixed** — when `handle()` is called via CLI with `project=None`, scope artifact path now derives the slug via `_goal_to_slug()` (same as `run_agent_loop`), so scope.md lands in the correct project dir.

## Done (session 34, 2026-04-16 — Phase 65 proposal: constraint/premise orchestration)

- [x] **NEXT.md index bug fix** (`src/agent_loop.py`) — adaptive adjust/replan was rebuilding `remaining_indices` as sequential step counts instead of NEXT.md line numbers, causing `ValueError: item_index N not found` mid-loop. All 4 replacement sites now use `[-1] * len(new_steps)` (same convention as interrupt injection). Exposed by slycrel-go regression run. 2 regression tests added.
- [x] **Phase 64 regression validation** — slycrel-go headless server goal ran through orchestrator, completed cleanly (8 steps, no director intervention), produced real WebSocket+IOProvider implementation pushed to slycrel/slycrel-go#1.
- [x] **Phase 65 design doc** (`docs/CONSTRAINT_ORCHESTRATION_DESIGN.md`) — inversion + constraint-setting before planning; persona perspective rotation (PM/engineer/architect triad); constraint lifecycle (set/inject/detect/revise/except/break).
- [x] **Independent critical review** (`docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`) — identified name collision with `src/constraint.py`, unreachable human gate in autonomous path, scope narrowing (design addresses planning; real defect is in verification — "nobody ran a browser"), shipped word-count gate is wrong, and a substantially smaller minimum experiment.
- [x] **Conversation log preserved** (`docs/conversations/2026-04-16-constraint-orchestration.md`) — full back-and-forth that produced the design.

## Next Up

- **ResolvedIntent v0 validation** — three halves now wired (planner sees deliverables, closure sees deliverables, closure pre-flights preconditions). Re-run the scope A/B (treat=scope+deliverables injected, control=skip) on 2–3 pairs and check: (a) do planners commit to deliverables by name, (b) does closure now converge against the deliverable paths instead of the generic failure-mode checklist, (c) does the precondition pre-flight catch what failed silently in the 2026-04-22 A/B, (d) do control-arm recovery bugs (step hang, rate-limit cascade) need to be fixed before the signal is clean. Budget before launch — prior A/B ran $41 on the slowest control arm.
- **Phase 65 A/B expansion** (hold until v0 signal is in) — run a wider goal spread (~20 total) once the driver/watcher wiring is complete. Measure plan quality, token cost, step count, verification outcome, deliverable convergence. Design: triad / lifecycle / retrieval per full doc only after v0 data lands.
- **Verification with ground-truth feedback — v1 shipped this session** (closure verdict gate + behavioral check mandate). Remaining work: observe v1 behavior on a live service-producing goal (repeat slycrel-go or similar), measure whether behavioral checks get generated and trip correctly, iterate on the prompt taxonomy if the generated checks miss the mark. If the restart actually fires mid-stream, verify the second run is meaningfully different (not just retrying the same thing) — that would be the signal that gap-as-context works as steering signal.
- **Phase 65 expansion triggers** (hold until A/B data is in):
  - Triad (PM/engineer/architect) — ablate against single-generalist to confirm different constraint lines
  - Human gate UX for interactive/channel paths
  - Violation detection at step level (mechanical → structural → semantic)
  - Constraint lifecycle (revise/except/break) hooked into director_evaluate
- **Phase 64D** — memory layer: record approach + outcome per goal type; director uses history to select initial approach. Deferred until Phase A/B/C generate operational data.
- **Closure check unification** (Phase C leftover) — `director_evaluate(trigger="closure")` wraps `verify_goal_completion`; `ClosureVerdict` retired. Low-priority code hygiene.
- **Evolver confidence calibration follow-up** — `scan_suggestion_outcomes` wired; verify calibration is improving. Heartbeat stopped since Apr 7-9 token burn — no new data until restarted.
- **Formal stage contracts (Phase P2)** — Typed output contracts per pipeline phase + hard validation gates. See BACKLOG.

## Done (session 33, 2026-04-15 — Phase 64A/B/C: adaptive execution)

- [x] **EvaluationContext + DirectorDecision dataclasses** (`src/director.py`) — compact serializable snapshot; action/reasoning/revised_steps/new_approach/restart_context/user_question/next_check_in.
- [x] **director_evaluate()** — all 5 actions wired: continue, adjust, replan, restart, escalate. Budget note injected when convergence_budget_remaining=0. Non-fatal — returns `continue` on any exception.
- [x] **LoopContext additions** — `steps_since_last_check`, `director_replan_count`, `director_budget_ceiling`, `channel`.
- [x] **Stuck trigger** (Phase A) — inside `stuck_streak >= 2` before existing advisor; continue resets streak, adjust replaces step tail, replan calls planner with new_approach context, restart breaks loop, escalate calls channel.ask().
- [x] **Verify-failure + step-threshold triggers** (Phase A) — end of each iteration; fires on `session_verify_failures >= 2` or `steps_since_last_check >= 5`; same full action set.
- [x] **Budget enforcement** (Phase B) — `director_replan_count >= director_budget_ceiling` → replan/restart clamped to continue in both trigger sites; budget visible in LLM prompt.
- [x] **replan** (Phase B) — calls `planner.decompose()` with `new_approach + completed steps` as ancestry context; replaces remaining steps + indices; increments `director_replan_count`.
- [x] **restart** (Phase C) — loop breaks with `loop_status="restart"`, `stuck_reason=restart_context`; handle.py detects and re-runs with restart context injected as ancestry, `continuation_depth+1`, capped at depth 3.
- [x] **escalate** (Phase C) — `ctx.channel.ask(user_question)` mid-loop; reply injected as next step context; falls back to logging if no channel.
- [x] **channel param** (`run_agent_loop`) — optional, default None; handle.py passes its channel for main AGENDA path.
- [x] **Gated by `adaptive_execution` config flag** (default off).
- [x] **18 new tests** — 107 total in test_director.py; 4329 total.

## Done (session 32, 2026-04-15 — Phase 63: Director closure check + completion standard)

- [x] **Director closure check** (`src/director.py: verify_goal_completion`) — post-loop gate where the director generates executable verification commands specific to the goal (go build, pytest, curl, etc.), runs them mechanically with real exit codes (no LLM judgment on results), then interprets outcomes and declares whether the goal is genuinely complete. Emits `verification` event (check summary) and `needs_work` event (gap list) to channel. Wired in `handle.py` after `run_agent_loop` returns "done", before `channel.complete()`. Non-fatal — never blocks execution. 8 new tests.
- [x] **Completion standard** (`user/COMPLETION_STANDARD.md`) — "boil the ocean" operating principle injected as ancestry context for every AGENDA run. Editable without code changes. Stacks with persona context.
- [x] **Continue flow** (`/api/continue/<id>`, `channel.restart()`, `prior_context_summary()`) — follow-up input appears on completed/stuck/interrupted threads; new run picks up with full prior context injected. Divider event separates continuation runs in the thread view.
- [x] **Thread persistence across restarts** (`load_channels_from_disk()`) — on service startup, scans `~/.poe/workspace/memory/threads/*.jsonl` and reloads last 7 days of threads. Channels that were mid-run at shutdown get `interrupted` event and status.
- [x] **Goal visibility fixes** — user_goal/user_reply block layout, optimistic goal render on submit, running indicator (pulsing blue dot while loop is active).

## Next Up

- **Evolver confidence calibration follow-up** — `scan_suggestion_outcomes` wired; verify calibration is improving (check live workspace suggestion stats). Heartbeat stopped by design since Apr 7-9 token burn — no new data until restarted.
- **Jeremy's undocumented ideas** — he mentioned having ideas not yet in the backlog. Needs elaboration.
- **Formal stage contracts (Phase P2)** — Typed output contracts per pipeline phase + hard validation gates. See BACKLOG. Medium-term architectural improvement.

## Done (session 31, 2026-04-15 — Phase 62: ConversationChannel + dashboard chat)

- [x] **ConversationChannel abstraction** (`src/conversation.py`) — base interface (`emit`, `ask`, `notify_low_confidence`, `complete`) + `ThreadChannel` implementation. Thread-safe, JSONL-persisted to `~/.poe/workspace/memory/threads/`. Global registry. Dashboard is first peer; Telegram/Slack/openclaw are future peers at same level.
- [x] **Dashboard Goal Chat panel** (`src/observe.py`) — 4 new endpoints: `POST /api/submit`, `GET /api/thread/<id>?since=N`, `POST /api/reply/<id>`, `GET /api/threads`. Chat panel in dashboard UI: goal submission, live polling, color-coded event feed, reply box appears when director is waiting.
- [x] **handle.py channel integration** — `channel=` param; clarity check asks via channel and continues with enriched goal instead of returning early; step events emitted to channel; `channel.complete()` on finish.
- [x] **Director low-confidence notifications** — confidence ≤ 7/10 → `notify_low_confidence` event to channel (non-blocking, purely informational; user knows a ~65% call was made).
- [x] **17 new tests** — `tests/test_conversation.py`; 164 total passing (handle + director + conversation).

## Done (session 30 cont., 2026-04-14 — adversarial fixes, research runs)

- [x] **Adversarial review round 30 — 12 verified findings fixed** — EV-1 CRITICAL: `s.text` → `s.suggestion` in advisor gate (entire 0.6-0.79 confidence band was dead code). AL-1 HIGH: moved `set_loop_running` to after `ctx.project` is assigned (per-project lockfile was never written). IG-1 HIGH: `safe_to_auto_apply` was dead code — switched callsites. CV-1 HIGH: claim verifier fallback was accepting wrong directory prefixes. CV-2: rglob not glob in symbol index. EV-2/3/4: scan_evolver_impact limit fixes + NaN vs 0.0. IG-5: any exfil match → HIGH immediately. Plus earlier session fixes: CG-1/4/5, IG-2/3. All 426 targeted tests pass.
- [x] **18-link research runs complete** — 4 orchestration runs (harness architecture, memory/skills, tooling/market, adversarial). 6 new BACKLOG steal items: proactive memory injection (P8), synthesize_skill 3-gate pre-check (P7), eval holdout discipline (P6), harness hill-climbing loop (P6), associative memory links (P5), dumb loop audit (P5). Research docs committed to docs/research/.
- [x] **PM round 9** — Filed #39-#43 on orchestrator-test-recipes: review HTML UI missing, concurrent edit race (lost update), CSRF protection, photo_url validation, API versioning prefix.

## Done (session 30, 2026-04-14 — housekeeping, link-farm digest)

- [x] **Link-farm Apr 9–11 batch processed** — 18 new entries reviewed. Already-done: advisor pattern, codebase graph, evals flywheel, harness spectrum, event-driven wakeup, harness optimizer, Polymarket dataset, Engramme, MCP Toolbox (all in BACKLOG). 4 net-new steal items added to BACKLOG: Latent Briefing (KV cache compaction), isolated worktree per sub-agent, Claude Skills quality gate for synthesize_skill, Kronos financial model.
- [x] **Docs archive** — Moved 4 stale docs to `docs/archive/`: `LOOP_SCRATCHPAD.md`, `plan-next-phase.md`, `PHASE_AUDIT.md`, `PHASE_AUDIT_2026-04-13.md`. These were research scratch and planning artifacts superseded by current implementation.
- [x] **README + CLAUDE.md updated** — Test count: 3500+ → 4278. `claim_verifier.py` added to source modules table. `tests/` line count updated to 109 files / 4,278 tests.

## Done (session 29, 2026-04-14 — recipe PM/dev rounds 7+8, claim verifier symbol extension)

- [x] **Recipe #5 — duplicate-name form error** — Added `unique=True` to `Recipe.name`; form handlers now catch `IntegrityError` specifically and re-render with "A recipe with that name already exists." form.html got error display block (was silently swallowing errors). 4 new tests (TestHTMLDuplicateName). Closed #5.
- [x] **PM round 7 + dev round 7** — Filed #26 (body size guard 413), #27 (API rate limit 429), #28 (HTML search/pagination tests). Implemented all three: TestRateLimit (4 tests), TestBodySizeGuard (3 tests), TestHTMLSearchAndPagination (6 tests, DB-direct seeding to avoid rate limit collisions). 74 recipe tests passing. Closed #26/#27/#28.
- [x] **Claim verifier symbol extension** — Extended `claim_verifier.py` with Python symbol (function/class/method) existence checking. New: `extract_symbol_claims()`, `_build_symbol_index()` (direct .py scan, <5ms), `verify_symbol_claims()`, `verify_all_claims()`, `SymbolReport`, `CompoundClaimReport`. `annotate_result()` now surfaces `SYMBOL_CLAIMS_NOT_FOUND`. 24 new tests (63 total in test_claim_verifier.py). Closes BACKLOG "claim verifier only catches file paths" item.
- [x] **Test coverage improvements** — captains_log: 7 edge-case tests (empty lines, malformed JSON, since filter, timeline date range). step_exec: 3 tests for `verify_step_with_cross_ref` (cross-ref skip, disputes annotated, exception swallowed). 4278 orchestration tests passing (up from 4242 start of session).

## Done (session 28, 2026-04-14 — PM/dev rounds 5+6)

- [x] **PM round 5+6** — Closed #9 (race false positive), #3 (auto-docs), #15 (photo_url done), #20 (review DELETE), #1 (pagination), #4 (seed data). Commented on #5. 2 issues remain: #5 (partial) + #2 (auth, out of scope).
- [x] **Dev round 5+6** — review DELETE (204/404), photo_url round-trip, form DB error handling, pagination {total/limit/offset/items} envelope, HTML page nav, scripts/seed.py (10 recipes, idempotent, <1s). Rate-limit isolation fixes for TestApiRecipeValidation + TestPagination. 63 recipe tests passing.
- [x] **Evolver suggestion stats dashboard** — `_read_suggestion_stats()` in observe.py: reads suggestions.jsonl, returns total/by_category/by_status/pending/applied. New panel in dashboard shows pending/applied badges + category table. 5 tests (71 total observe tests).
- [x] **FastAPI deprecation warnings** — Eliminated on_event("startup") → asynccontextmanager lifespan; TemplateResponse calls to new API format. Zero warnings in recipe test output.

## Done (session 27, 2026-04-14 — dev round 4 closed #12/#23/#24, first-class project isolation)

- [x] **Dev round 4** — Closed #12 (rate limit test for /api/recipes), #23 (HTML edit blank-name guard), #24 (photo_url/review_text length validators). 9 new tests: rate limit, field validators, HTML blank-name class. All 52 recipe tests pass.
- [x] **First-class project isolation** — `Skill.project` field (""/global or project slug). `find_matching_skills(project=...)` filters to global + project-specific. `set_loop_running(project=...)` writes per-project lock. `get_running_project_loop()` + `is_project_running()` API. 11 new tests. Closes BACKLOG item.

## Done (session 26, 2026-04-14 — codebase graph, injection guard, harness spectrum, PM/dev rounds 3)

- [x] **Codebase Graph** — `src/codebase_graph.py` (39 tests). AST-based Python call graph; 5-pass algorithm (collect → parse → resolve imports → centrality → rank). Basename import resolution (`from llm import ...` → `llm.py`). Centrality = 0.7×in_degree + 0.3×line_coverage. Goal-biased keyword ranking in `format_graph_context()`. Wired into `_build_loop_context()` (fail-open). `poe-codebase-graph` CLI. Verified: `llm.py` tops centrality (54 importers), `agent_loop.py` in top 10.
- [x] **Prompt injection guard** — `src/injection_guard.py` (59 tests). 17 regex patterns across 3 categories (override, tool-call, exfil). `InjectionScanReport` with risk_level + safe_to_auto_apply. Wired into: evolver `apply_suggestion()`, persona `scan_personas_dir()` YAML loading, persona `create_freeform_persona()` goal scanning. Fail-closed (returns False on exceptions).
- [x] **Dev run 2 closed** — #17 (json.loads crash), #18 (test isolation conftest.py), #19 (rating DB constraint) all closed.
- [x] **Harness Architecture Spectrum** — Friction scan wired into inspector heartbeat tick (heuristic, no LLM, runs alongside inspector). Inspector friction summary injected into quality gate Pass 1 user message. Closes BACKLOG P7/10. NOW lane intentionally thin by design; AGENDA has pre-flight + quality gate + post-hoc inspector. Injection guard wired into synthesize_skill() in evolver.
- [x] **user/GOALS.md** — Created user/GOALS.md (north star + active + medium-term goals + values). Wired into planner.py user context injection alongside CONTEXT.md + SIGNALS.md. Director now sees Jeremy's current goals when decomposing missions.
- [x] **Longitudinal evolver impact analysis** — `scan_evolver_impact()`: for each EVOLVER_APPLIED event, compare stuck_rate before vs after. `format_impact_summary()` shows delta. `poe-evolver impact` CLI subcommand. Closes K6 "self-improvement validation" gap. 9 tests.
- [x] **PM round 3 + dev round 3** — PM-3 filed #21-25 (Pydantic validation, rate limit regression, SQL exception handling, field length limits, HTML form blank-name). Dev-3 targeting #22/#21/#25.

## Done (session 25, 2026-04-14 — repo scan, BLE, SIGNALS.md, PM/dev round 2)

- [x] **Repo stack auto-detection** — `src/repo_scan.py` (53 tests). Scans repo for tech stack via 50+ file indicators, deep-scans requirements.txt/package.json for frameworks. Wired into `_build_loop_context()` via project slug heuristic + `--repo` CLI flag. `repo_path` threaded through run_agent_loop → LoopContext → _build_loop_context (fail-open).
- [x] **Bitter Lesson goal rewriter** — `rewrite_imperative_goal()` in intent.py. Strips prescribed execution steps from imperative-heavy goals, rewrites as outcome-focused. Wired into AGENDA path in handle.py before clarity check. `_IMPERATIVE_MARKERS` heuristic avoids LLM cost for already-clean goals. 15 tests.
- [x] **SIGNALS.md → evolver signal alignment** — `_load_user_signals()` reads user/SIGNALS.md. `scan_outcomes_for_signals()` now passes user research priorities as context when proposing sub-missions. Closes the Mode 2→3 loop: factory-mode signal proposals align with user-declared interests. 5 tests.
- [x] **PM round 2** — Filed 5 new issues (#16-20) on orchestrator-test-recipes: N+1, json.loads crash, test isolation, rating constraint, review dedup.
- [x] **Dev run 1** — Closed #16 (N+1 query), #13 (FTS), #11 (timestamps). Plus previous session fixes for #7, #10.

## Done (session 24, 2026-04-14 — K4 write path)

- [x] **K4: Knowledge write path** — `src/knowledge_bridge.py`: `outcome_to_knowledge()` heuristic + LLM extraction of insight/principle/pattern nodes. Dedup via Jaccard ≥0.7 on titles. `upsert_knowledge_from_candidate()` updates confidence on re-validation. `record_skill_evolution()` wired into evolver promote/demote paths. `validate_principle()` for bidirectional validation (validated/contradicted). `reflect_and_record()` in memory.py now calls `outcome_to_knowledge()` as non-blocking hook (fail-open). 27 tests. Closes BACKLOG K4 item + docs/knowledge-layer/README.md updated.

## Done (session 23 continued, 2026-04-14 — input classification, Phase 61 complete, LoopStateMachine, sub_mission enqueue)

## Done (session 23, 2026-04-14 — Phase 61, lesson dedup, phase audit, Stage 3→4, input classification)

- [x] **Memory Stage 3→4 regression tests** — 3 tests: `test_step_outcome_has_result_attribute`, `test_skill_extraction_fires_when_not_dry_run`, `test_skill_extraction_outcome_uses_step_result`. Skill extraction exceptions upgraded debug→warning.
- [x] **Lesson deduplication** — `deduplicate_lessons()` two-pass (exact + Jaccard ≥0.8). `--cleanup-lessons`/`--dry-run` in doctor.py. 5 tests.
- [x] **Phase audit** — Verified phases 44-62: all implementations confirmed real. Phase 45 "never closed" note was stale. No phantom phases found.
- [x] **BACKLOG cleanup** — Closed artifact routing, orch.py tests, HIGH coverage item, inspector threshold asymmetry, Phase 45 note. Updated 6 items.
- [x] **Phase 61 integration depth** — 12 new tests across 4 classes: checkpoint recovery (3), memory injection (2), AGENDA lane heartbeat e2e (3), FailoverAdapter chain (4). Integration total: 31→49.
- [x] **Input classification tag** — `classify_input_type()` in captains_log.py (url/code/structured_data/plain_text). `INPUT_MISMATCH` + `METACOGNITIVE_DECISION` event constants. `update_skill_utility()` logs INPUT_MISMATCH when circuit opens on url-skill-vs-non-url-input domain mismatch. `attribute_failure_to_skills()` threads step_text through. 9 tests. EVENT_TYPES 28→30.
- [x] **LoopStateMachine conversion** — `LoopStateMachine` now inherits `LoopContext`; `set_phase` is an instance method (`ctx.set_phase(X)` replaces `LoopStateMachine.set_phase(ctx, X)`). 6 call sites updated in `run_agent_loop`, 8 test functions updated + 1 new subclass check. `_initialize_loop` creates `LoopStateMachine()` instead of `LoopContext()`. Eliminates two-class pattern; ctx IS the state machine now.
- [x] **sub_mission auto-enqueue** (Mode 2→3 bridge) — `_apply_suggestion_action()` now handles `sub_mission` category. `evolver.auto_enqueue_signals=True` → `enqueue_goal()` on the heartbeat queue. Default: hold for review, record to playbook `Signals` section. Config-gated opt-in. 3 tests.

1. ~~**Memory Stage 3→4 (verify extraction in live runs)**~~ — DONE (session 23). Added 3 regression tests: `test_step_outcome_has_result_attribute` (guards s.result attr), `test_skill_extraction_fires_when_not_dry_run` (verifies extract_skills is called), `test_skill_extraction_outcome_uses_step_result` (verifies step result data flows correctly). Upgraded skill extraction log.debug → log.warning so failures surface. Bug is verified closed.
2. ~~**K2 follow-up: Import links collection**~~ — DONE (session 22). `import_link_farm()` + `poe-knowledge import-links` CLI. 315 nodes already in workspace. +9 tests.
3. ~~**Director persona authoring skill**~~ — DONE (session 22). `record_persona_dispatch()` logs each selection with is_fallback flag. `scan_persona_gaps()` surfaces recurring unmatched roles. Wired into `run_evolver(scan_persona_gaps=True)` as persona_authoring Suggestions. +6 tests.
4. ~~**Evolver confidence calibration**~~ — DONE (session 22). `_record_suggestion_outcomes` + `scan_suggestion_outcomes` wired into run_evolver. 6 tests.
5. ~~**11 unlocked bare-append JSONL paths**~~ — DONE (session 22). Added `locked_append()` to file_lock.py; converted 11 highest-traffic sites (captains_log, memory_ledger×5, metrics, evolver×4, inspector×2). +5 tests.

## Done (session 22, 2026-04-14 — stale hash cleanup, event-driven wakeup, constraint fix, FailoverAdapter)

- [x] **Cross-backend failover on 4xx/5xx (FailoverAdapter)** — `build_adapter("auto")` now returns `FailoverAdapter` when multiple backends are available. Tries each in priority order; 402/401/403/5xx errors trigger failover to next backend. Single-backend case returns adapter directly (no overhead). 14 tests. Closes BACKLOG P2.
- [x] **Constraint false-positive on step descriptions** — Two-part fix: (a) decompose prompt gets STEP DESCRIPTION STYLE section — "describe the task, not shell commands"; (b) `hitl_policy(is_description=True)` downgrades DESTROY tier → WRITE and caps HIGH risk at MEDIUM for step descriptions. Step_exec.py passes `is_description=True`. 3 tests. Closes BACKLOG item.
- [x] **Event-driven subprocess wakeup** — `run_agent_loop` calls `post_heartbeat_event("loop_done", payload=project)` after releasing the loop lock. Heartbeat's `_wakeup_event.wait()` unblocks immediately → next task picked up in near-zero time. 3 tests. Closes MILESTONES item.
- [x] **Stale test skills in workspace** — `poe-doctor --cleanup-skills` now detects skills where `compute_skill_hash(skill) != stored_hash` (test fixtures that leaked in). Ran on live workspace: 15 stale-hash + 2 duplicates removed, 14 real skills remain. `_skill_hash_is_stale()` helper for testability. `skills_path` kwarg for testing. 6 tests. Closes BACKLOG item.

## Done (session 21, 2026-04-14 — budget bump + exception sweep + LoopStateMachine + Stage 2→3 pipeline + skill extraction fix + NOW→Director escalation)

- [x] **Eval flywheel train/test split** — `_train_test_split_patterns()`: oldest 70% → train (suggestion generation), newest 30% → holdout. Skips split when < 4 patterns. `run_eval_flywheel` now has `test_fraction=0.3` param and surfaces `patterns_train`/`patterns_test` in summary. Prevents gaming eval metric by training on the same patterns tested. 5 tests.
- [x] **Eval flywheel pass-rate dashboard** — `_read_eval_trend()` in observe.py; wired into `_snapshot_json()` as `eval_trend` key; new HTML panel "Eval Pass Rate" in `poe-observe serve` dashboard shows last 10 runs with builtin score, gen pass rate, trend direction (improving/declining/stable badge). 4 tests. Closes MILESTONES eval-flywheel hardening.
- [x] **Artifact output routing cleanup** — Per-step artifacts (`loop-{id}-step-*.md`) auto-deleted at loop end by default. Config `keep_artifacts: true` retains them. Permanent files (PARTIAL.md, plan.md, loop log, scratchpad) always kept. 3 tests. Closes MILESTONES artifact-routing item.
- [x] **pytest-via-subprocess timeout fix** — Root cause: 900s wasn't enough for full test suite (pytest ~100-300s on this hardware + Claude response time). Bumped to 1800s default for long-running steps, 3600s for full-suite (`tests/ ` hint). `POE_LONG_RUNNING_TIMEOUT` env var for override. Improved timeout log message identifies `full_suite` vs `long_running`. 5 tests. Closes MILESTONES #2.
- [x] **~35 silent exceptions upgraded (agent_loop.py lines 1000+)** — `except Exception: pass` upgraded to `log.warning` (learning data loss: diagnosis lesson, plan manifest) or `log.debug` (optional context injections, adapter fallbacks, lifecycle telemetry). 4 safety-critical bare-pass sites kept. No behavior change — failures now surface in debug logs. Closes MILESTONES #3.
- [x] **Recovery mid-loop budget bump** — When 75%+ of `max_iterations` consumed, >2 steps remain, and ≥50% of steps done: bumps `max_iterations` by 50% (min +10), fires at most once (`_budget_bumped` guard). Logs `METACOGNITIVE_DECISION` to captain's log. Prevents hard synthesis fallback when good progress is in flight. 5 tests. Closes MILESTONES #6.

## Done (session 21, 2026-04-14 — LoopStateMachine + Stage 2→3 pipeline + skill extraction fix + NOW→Director escalation)

- [x] **Stage 2→3 crystallization pipeline** — `scan_canon_candidates()` in evolver.py surfaces long-tier lessons with 10+ applies across 3+ task types as `crystallization` Suggestions. Wired into `run_evolver(scan_canon=True)` (default on). `apply_suggestion` explicitly holds crystallization for human review (never auto-applies). 7 tests. Closes BACKLOG MODERATE "Memory Stage 2→3."
- [x] **Stage 3→4 skill extraction bug** — `extract_skills()` was silently failing every run due to `s.summary` / `s.step` attribute errors on StepOutcome (which has `.result` and `.text`). Fixed attribute names. `reflect_and_record` outer except upgraded to log.warning. 1 regression test. Skill crystallization now actually fires.
- [x] **NOW → Director escalation** — `_is_complex_directive()` heuristic (>25 words, multi-step language, complex verbs + 8+ words, multiple sentences). Config flag `now_lane.escalate_to_director` (default False, opt-in). When enabled, complex NOW-classified goals reclassify to agenda and get full Director pipeline. 10 tests. Closes MILESTONES #3.

## Done (session 21, 2026-04-14 — LoopStateMachine)

- [x] **LoopPhase state machine** — `LoopStateMachine` class with `_ALLOWED` transitions dict; `set_phase(ctx, phase)` raises `InvalidTransitionError` on invalid transitions. Wired into `run_agent_loop` at all 7 phase boundaries (A→B, B→C, C→D, D→E, E→F, F→G, plus FINALIZE on early exits). `LoopContext.phase` field was present but never set — now set at every transition. 8 tests. Closes BACKLOG CRITICAL finding 3.3.

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
