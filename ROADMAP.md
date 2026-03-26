# Roadmap

Phased build plan for Agentic Poe. Each phase is independently shippable and testable. Phases are sequential for the core path — later phases depend on earlier ones.

See `VISION.md` for the full intent behind this plan.

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
- [ ] Wire to OpenClaw gateway (ws://127.0.0.1:18789) — deferred (not required for core operation)
- [ ] Interruption handling: new messages additive/corrective — deferred

**Artifact:** `poe-telegram [--loop]`. `src/telegram_listener.py`, `src/llm.py`, `src/ancestry.py`

---

## Phase 7 (§19): Meta-Evolution *(COMPLETE)*

Poe proposes its own improvements based on failure patterns.

- [x] Meta-Evolver: reviews last N outcomes, identifies failure patterns
- [x] Generates structured suggestions (prompt_tweak | new_guardrail | skill_pattern | observation)
- [x] Stores suggestions to `memory/suggestions.jsonl`
- [x] Wired into heartbeat loop (fires every 10 heartbeat cycles)
- [x] Telegram notification of suggestions (optional)
- [ ] Auto-application of suggestions — deferred until confidence in suggestion quality is established

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
- [x] **`/research` Telegram command** routes to researcher persona via `spawn_persona` rather than generic director path.
- [x] **Canon promotion (Phase 16 addition):** `times_applied` tracking wired into `inject_tiered_lessons()` (default on). `_record_canon_hit()` writes to `memory/canon_stats.jsonl`. `get_canon_candidates()` surfaces long-tier lessons eligible for AGENTS.md identity promotion. `poe-memory canon-candidates` CLI. 10 new tests.
- [x] 47 new tests (37 persona + 10 canon); 1008 total passing, 5 skipped

**Artifact:** `src/persona.py`, `personas/` (6 built-in YAML-frontmatter specs), `poe-persona` CLI, `/research` Telegram command, `src/memory.py` (canon tracking appended)

---

## Phase 21: Production Readiness — Bootstrap + Decoupling + macOS

**Make the system installable and self-bootstrapping.** Currently tied to this specific box and OpenClaw directory layout. This phase decouples it, adds a bootstrap skill, and makes it work on macOS.

Deferred intentionally — do this last, after the core system is proven.

- [ ] **Full OpenClaw decoupling:** remove all hardcoded `~/.openclaw/` path assumptions. Config file (`~/.poe/config.json` or env vars) specifies workspace root, gateway URL, credentials. System works without OpenClaw present.
- [ ] **Bootstrap skill:** `poe-bootstrap` installs Claude CLI (`claude` binary via npm/pip), creates workspace directory structure, writes systemd service files, runs first heartbeat. One command from a fresh Ubuntu or macOS box.
- [ ] **macOS compatibility:** replace `systemd` with `launchd` plist generation for service management. `platform.system()` detection throughout. Test suite passes on macOS.
- [ ] **Minimal dependency install:** `pip install poe-orchestration` installs core system. Optional extras: `[telegram]`, `[gateway]`, `[sandbox]`. No hard deps on `websockets`, `sentence-transformers`, etc. — all graceful ImportError fallbacks already exist.
- [ ] **`claude` CLI skill:** bootstrap skill that installs and configures the Claude Code CLI as a worker backend, wires it into `ClaudeSubprocessAdapter`.
- [ ] **Smoke test suite:** `poe-test` command runs a dry-run end-to-end from CLI install → heartbeat → single NOW-lane task → verify output. Green = system ready.

**Artifact:** `src/bootstrap.py`, `deploy/launchd/`, `poe-bootstrap` CLI, macOS CI

---

## Superseded Plans

The original M0-M4 milestones and N1-N4 roadmap items focused on infrastructure plumbing (adapters, scheduling, CI). That work was valuable scaffolding, but it didn't address the core need: making Poe autonomous. This roadmap replaces N1-N4 entirely.
