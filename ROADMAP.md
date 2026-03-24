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

## Superseded Plans

The original M0-M4 milestones and N1-N4 roadmap items focused on infrastructure plumbing (adapters, scheduling, CI). That work was valuable scaffolding, but it didn't address the core need: making Poe autonomous. This roadmap replaces N1-N4 entirely.
