# Roadmap

Phased build plan for Agentic Poe. Each phase is independently shippable and testable. Phases are sequential for the core path — later phases depend on earlier ones.

See `VISION.md` for the full intent behind this plan.

---

## Phase 0: Foundation Audit *(~1 day)*

Honest baseline. Tag what we actually have before building forward.

- [ ] Audit every file — tag what works, what's scaffolding, what's dead
- [ ] Close stale issues/tasks that reference old M1-M4 plan
- [ ] Add `VISION.md` (intent guide) to repo root
- [ ] Replace `ROADMAP.md` with this phased plan
- [ ] Move source docs (`poe_intent.md`, `poe_orchestration_spec.md`, `poe_miscommunication_patterns.md`) into `docs/`
- [ ] Update `MAINLINE_PLAN.md` to reflect v0.5.0 baseline
- [ ] Tag `v0.5.0` — honest foundation

**Shippable artifact:** Tagged v0.5.0 release with accurate documentation reflecting the real state.

**Cost:** Zero LLM spend. This is file editing.

---

## Phase 1: Autonomous Loop *(~1-2 weeks)*

**THE critical unlock.** Without this, nothing else matters. Poe gets an LLM brain — the ability to receive a goal, reason about it, and take action in a loop.

- [ ] Define LLM adapter interface (model-agnostic: input messages → response + tool calls)
- [ ] Implement adapter for cheapest available model first (minimize cost during development)
- [ ] Build loop runner: goal → plan → act → observe → decide (continue/done/stuck)
- [ ] Wire loop runner to existing task queue (`scripts/task-queue.sh`)
- [ ] Add structured output: each loop iteration produces a log entry (intent, action, result, decision)
- [ ] Implement basic stuck detection (same action repeated 3x = escalate, but this is a heuristic seed — the Loop Sheriff in Phase 4 replaces it)
- [ ] End-to-end test: give a goal via CLI → watch it execute to completion autonomously
- [ ] Verify existing `pytest` and `smoke.sh` still pass

**Shippable artifact:** `poe run "goal description"` — give Poe a goal, watch it work autonomously until done or stuck.

**Cost model:** Cheap model for iteration (codex-mini or equivalent). Expensive model only for complex planning steps. Expect ~$0.01-0.05 per goal during development.

---

## Phase 2: NOW/AGENDA Lanes *(~1 week)*

Route work to the right execution path. Trivial tasks shouldn't go through Director overhead.

- [ ] Build intent classifier: analyze incoming request → NOW or AGENDA
- [ ] NOW lane: 1-shot execution, UUID tracking, artifact output, ledger entry
- [ ] AGENDA lane: multi-step execution using Phase 1 loop runner with checkpointing
- [ ] Response timing scaffolding: immediate ack → status update → substantive response
- [ ] Wire both lanes to a common entry point (`poe handle "message"`)
- [ ] Test: "what time is it" routes to NOW; "research winning polymarket strategies" routes to AGENDA

**Shippable artifact:** Single entry point that auto-routes to fast or deep execution, with response timing that matches the UX contract.

**Cost model:** NOW lane: 1 cheap LLM call. AGENDA lane: Phase 1 costs per iteration.

---

## Phase 3: Director/Worker Hierarchy *(~1-2 weeks)*

Multi-agent delegation. The Director plans and reviews. Workers execute. Nobody does both.

- [ ] Director agent: takes directive → produces SPEC + TICKET → delegates to workers
- [ ] Worker agents: research, build, ops, general — each with a persona and constrained toolset
- [ ] Plan acceptance gates: `explicit` (public/irreversible) vs `inferred` (low-risk/reversible)
- [ ] Review cycle: Director reviews worker output → accepts/iterates/escalates
- [ ] Checkpoint after each major phase of work
- [ ] Handle (Poe) relays Director summaries to Jeremy, not raw worker output
- [ ] Test: give a multi-step directive → verify Director delegates, Workers execute, Director reviews

**Shippable artifact:** `poe director "build a research report on X"` — delegates to workers, reviews output, produces polished result.

**Cost model:** Director uses reasoning model. Workers use cheap models. 1 Director call + N Worker calls per task.

---

## Phase 4: Loop Sheriff + Heartbeat *(~1 week)*

Independent progress validation and self-healing. Replace the Phase 1 stuck-detection heuristic with a proper validator.

- [ ] Loop Sheriff: independent process that monitors all running loops for progress
- [ ] Progress detection: diff-based (are outputs changing?) + semantic (is the goal getting closer?)
- [ ] Escalation chain: Sheriff → Handle → Jeremy (only when truly stuck)
- [ ] Heartbeat loop: periodic health check (gateway, model, config, disk)
- [ ] Tiered recovery: scripted fixes first (deterministic), agentic diagnosis second, escalation last
- [ ] Use different model for heartbeat (works even when primary is down)
- [ ] Test: intentionally create a stuck loop → verify Sheriff detects and escalates

**Shippable artifact:** Loops that self-correct or escalate cleanly. Heartbeat that keeps the system alive without human intervention.

**Cost model:** Sheriff uses cheap model for progress checks. Heartbeat uses alternative model (Gemini or equivalent). Negligible per-check cost.

---

## Phase 5: Memory + Learning *(~1-2 weeks)*

Poe remembers across sessions, learns from outcomes, and improves over time.

- [ ] Session bootstrap: every new session loads full state from persisted files (never starts blank)
- [ ] Outcome tracking: for each completed goal, record what worked/failed and why
- [ ] Skill extraction: identify repeated patterns → generate reusable scripts to reduce future token usage
- [ ] Feedback loop: Jeremy's corrections get encoded into policy (not just session context)
- [ ] Audit trail separate from actionable memory (log everything, surface only what matters)
- [ ] "Also-After" hooks: post-goal tasks that capture memory/follow-ups/index artifacts
- [ ] Test: complete a goal → restart session → verify Poe remembers the outcome and applies learnings

**Shippable artifact:** Poe that gets better over time. Each session starts where the last one left off, with accumulated skills and knowledge.

**Cost model:** Memory reads are free (file reads). Skill extraction is a periodic batch job (cheap model).

---

## Phase 6: OpenClaw + Telegram Integration *(~1 week)*

The gateway integration. Poe talks to Jeremy through Telegram with proper UX.

- [ ] Wire Poe's Handle to OpenClaw gateway (ws://127.0.0.1:18789)
- [ ] Telegram message → intent classification → lane routing → execution → response
- [ ] Response timing enforcement: ack in ~1s, status in 5-15s, substantive in 30-40s
- [ ] Slash commands: `/director`, `/research`, `/build`, `/ops`, `/status`
- [ ] Interruption handling: new messages are additive/corrective unless explicit stop
- [ ] Actionable-only alerts: non-actionable status logged silently
- [ ] Test: send Telegram message → verify full pipeline (classify → route → execute → respond)

**Shippable artifact:** Poe on Telegram, responding to natural English with autonomous execution. The interface Jeremy described from the start.

**Cost model:** Gateway is free (local). Telegram API is free. LLM costs are Phase 1-5 costs.

---

## Phase 7: Scaling + Evaluation *(ongoing)*

Concurrent projects, crew composition, quality tracking.

- [ ] Concurrent project support: multiple AGENDA lanes running in parallel
- [ ] Crew composition: dynamic worker pool sizing based on task complexity
- [ ] Quality tracking: per-goal success rate, time-to-completion, cost-per-goal
- [ ] Evaluation suite: benchmark goals with known-good outcomes
- [ ] Cost optimization: track token usage per goal, identify expensive patterns, generate cheaper alternatives
- [ ] Self-improvement: Poe proposes its own roadmap items based on failure patterns

**Shippable artifact:** Poe handling multiple projects simultaneously with measurable quality and cost metrics.

**Cost model:** Scales with concurrency. Target: demonstrably cheaper per-goal over time as skills accumulate.

---

## Superseded Plans

The original M0-M4 milestones and N1-N4 roadmap items focused on infrastructure plumbing (adapters, scheduling, CI). That work was valuable scaffolding, but it didn't address the core need: making Poe autonomous. This roadmap replaces N1-N4 entirely. Infrastructure items from the old roadmap will be absorbed into the relevant phases above as needed.
