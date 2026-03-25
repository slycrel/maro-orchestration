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

## Superseded Plans

The original M0-M4 milestones and N1-N4 roadmap items focused on infrastructure plumbing (adapters, scheduling, CI). That work was valuable scaffolding, but it didn't address the core need: making Poe autonomous. This roadmap replaces N1-N4 entirely.
