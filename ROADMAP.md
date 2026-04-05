# Roadmap

Phased build plan for autonomous agent orchestration. Each phase is independently shippable and testable.

**North star**: give the system a top-level mission; it breaks it into milestones, executes them autonomously over days/weeks, learns from what works, and reports progress without hand-holding. The user's job is mission definition and exception handling — not step supervision.

See `VISION.md` for the full intent.

## Guiding systems model: Visibility → Reliability → Replayability

A recurring systems principle for orchestration:

1. **Visibility** — see what the system planned, did, spent, produced, and why it failed.
2. **Reliability** — make the common path complete consistently, fail legibly, and recover sanely.
3. **Replayability** — preserve enough trace/checkpoint fidelity to replay failures, compare policy changes, and evaluate alternate interventions.

These stages build on each other. Visibility without reliability is just a clearer view of dysfunction. Reliability without replayability limits the system's ability to learn from past runs.

Where each phase sits:

| Stage | Phases | What they deliver |
|-------|--------|-------------------|
| **Visibility** | 23 (observe), 36 (dashboard), 43 (structured logging), 44 (failure classifier + lenses) | See what happened, why, and where tokens/time went |
| **Reliability** | 22 (rules), 32 (skills auto-promotion), 33 (token budgets), 35 (constraints + HITL), 44-45 (diagnosis + recovery planner) | Common path completes, failures are legible, recovery is mechanical |
| **Replayability** | 42 (nightly eval), 46 (intervention graduation), 40 (SQLite for queryable history) | Replay failures, compare policy changes, harden from past runs |

We're currently at the **visibility→reliability boundary**. The introspection and lens work is converting visibility into reliability — structured diagnosis that changes behavior, not just reports.

A short version: **stop debugging by seance, then stop failing the same way twice, then make past runs reusable for future improvement.**

---

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

### Phase 23: Observability — Execution Visualization *(PARTIAL)*

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

### Phase 27: Prerequisite Knowledge Sub-Goals and Graveyard Query *(PARTIAL)*

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

### Phase 29: Human Psychology / Neurology / Philosophy Research Track *(PARTIAL)*

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

**Still pending (research queue):**
- Tacit vs. explicit knowledge in expertise research (crystallization Stage 4→5)
- Enneagram 6w5 + INFJ communication failures (companion persona Phase 28)

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

**Still pending:**
- **Per-model cost breakdown in `poe-metrics`**: report should show actual cost by model across outcomes. Needs `model` field recorded in `outcomes.jsonl`.
- **Haiku routing for simple sub-tasks**: `assign_model_by_role()` currently maps `worker → MID`. For classifier, summarizer, routing decisions, downgrade to `CHEAP`. Add a `classifier` role tier.
- **Budget alerting**: configurable per-session token budget with Telegram alert when crossed.
- **Sub-agent token tracking**: `StepOutcome.tokens_in/out` already populated — feed per-step costs into evolver so the system can detect high-burn step patterns and propose cheaper alternatives.

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

### Phase 48: Conversation Mining — Idea Archaeology *(TODO)*

*"Revisiting ideas with current maturity yields perspectives we missed the first time."*

Research pass through all Poe/Jeremy conversation history:
- Telegram bot messages (`@edgar_allen_bot` history)
- Claude Code session logs (`~/.claude/projects/` JSONL files)
- OpenClaw workspace MEMORY.md, TASKS.md history
- Git commit messages and PR discussions

Extract orchestration-related ideas, patterns, deferred concepts, and "what if" musings. Run them through the system as research goals. Cross-reference against current BACKLOG.md and STEAL_LIST.md for items that were noted but never pursued, or ideas whose time has come now that the foundation is stronger.

**Why this matters:** Early conversations contain raw intuitions that were too ambitious at the time but may now be achievable. The system's improved self-improvement loop means these ideas get evaluated by a smarter planner than when they were first discussed.

---

## Superseded Plans

The original M0-M4 milestones and N1-N4 roadmap items focused on infrastructure plumbing (adapters, scheduling, CI). That work was valuable scaffolding, but it didn't address the core need: making Poe autonomous. This roadmap replaces N1-N4 entirely.
