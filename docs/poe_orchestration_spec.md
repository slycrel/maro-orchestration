# Poe Orchestration — v0 Spec
## Synthesized from 5 weeks of design conversations (Feb 5 – Mar 12, 2026)

---

## 1. System Overview

An orchestration layer built on top of OpenClaw that turns a conversational AI bot ("Poe") into an autonomous agent capable of receiving high-level goals and executing them to completion through delegation, sub-agents, and structured task management.

**Core principle**: The orchestration is the product. Projects (Polymarket bot, X scraping, etc.) are test cases that *use* it. It is not a framework that sits under projects — it's the operating system for getting things done.

**Platform**: OpenClaw on a 2014 Mac Mini (Linux Mint 22), residential fiber.

---

## 2. Architecture — Three Layers

### Layer 1: Infrastructure ("The Body")
Schedulers, queues, tool access, sandboxes, cost controls, logging, retries, model selection/routing.

- **Host**: Linux Mint 22, user `clawd` with sudo
- **Gateway**: OpenClaw local gateway, ws://127.0.0.1:18789, systemd service
- **Communication**: Telegram (@edgar_allen_bot / "Poe")
- **Model stack**: gpt-5.4 primary → gpt-5.3-codex-spark → gpt-5.3-codex → gpt-5.2-codex → gpt-5.1-codex-mini (fallbacks). Gemini as last resort.
- **Auth**: Codex CLI OAuth (ChatGPT Plus subscription). Single routing/auth surface via Gateway.
- **Accounts**: agentic.poe@yahoo.com, @AgenticPoe on X, Moltbook profile
- **Browser**: Playwright + computer-use skill, xvfb display :99, Chrome persistent profile, VNC at :5900

### Layer 2: Agent Runtime ("The Process")
Execution context with permissions, memory handles, and toolset.

- **Main agent**: `agent:main:main` — conversational coordinator ("the Handle")
- **Sub-agents**: Spawned via `sessions_spawn` or `openclaw agent` for background work
- **Execution**: `openclaw-agent-locked.sh` wrapper with flock-based concurrency (2 slots)
- **Backoff**: Jitter on rate-limit/cooldown, self-throttle at 85% usage

### Layer 3: Persona/Role ("The Mask")
Goals, voice, decision rules, rubric — portable across runtimes and models.

- **Personas directory**: `prototypes/poe-orchestration/personas/`
- **Shipped personas**: chief-of-staff, last30days-brief, scrapling-adaptive-web-recon, systems-design-architect-coach, research-assistant-deep-synth, loop-validator
- **Persona specs** (JSON): `prototypes/poe-orchestration/persona-specs/` mapping persona → default agent/model
- **Key insight from Jeremy**: Personas are swappable masks, separate from infrastructure. Different models can wear the same persona. The persona is the reusable unit, not the agent instance.

---

## 3. Agent Hierarchy

```
Jeremy (Telegram)
  └── Poe ("The Handle")
        ├── Director sub-agent — plans, reviews, iterates; does NOT execute code
        │     ├── worker-research — info gathering, must produce cited artifacts
        │     ├── worker-build — implementation (model: codex-cli/gpt-5.1-codex-mini)
        │     ├── worker-ops — automation/diagnostics/infrastructure
        │     └── worker-general — catch-all
        └── NOW Lane — 1-shot quick tasks (bypass Director for trivial work)
```

### Director Contract
- Takes a directive, produces SPEC + TICKET artifacts
- `plan_acceptance` modes: `explicit` (for public/irreversible) or `inferred` (low-risk/reversible)
- Gates worker kickoff on acceptance
- Reviews/iterates until acceptance criteria met
- Reports back to Handle for relay to Jeremy
- Checkpoints after each major phase

### Slash Commands
`/director <directive>`, `/research <task>`, `/build <task>`, `/ops <task>`, `/general <task>`

---

## 4. Two Execution Lanes

### NOW Lane (Fast Path)
For tasks completable in ~seconds. 1-shot delegation, no planning overhead.

- **Skill**: `skills/now-lane/SKILL.md`
- **Runner**: `prototypes/poe-orchestrator/scripts/now_run.py` / `now_run.sh`
- **Schema**: UUID job_id + run_id, required fields: job_id, run_id, lane, source, reason, status, attempt, timestamps, artifact_paths
- **Validator**: `prototypes/poe-orchestrator/scripts/validate-latest-now-artifact.py`
- **Artifacts**: `prototypes/poe-orchestrator/artifacts/<job_id>.{md,json,log}`
- **Ledger**: `prototypes/poe-orchestrator/job-ledger.jsonl`

### AGENDA Lane (Heavy Path)
For multi-step projects requiring planning, delegation, and review cycles.

- **Entrypoint**: `scripts/poe-orch-run.sh` (--once, --loop, --status)
- **Agenda file**: `output/agenda/agenda.json` + `output/agenda/checkpoint.json`
- **Tick**: `scripts/poe-orch-tick.sh` — scans `projects/*/NEXT.md`, picks next unchecked task, marks in-progress
- **Pump**: `scripts/poe-orch-pump.sh` — loops tick → run until stuck or time-bounded
- **Autonomy loop**: `scripts/autonomy/autonomous_loop.py` + `run_loop.sh` — picks next leaf task, plans, executes, verifies, commits, logs

---

## 5. Task Queue System

### Core Engine
- **Script**: `scripts/task-queue.sh` — supports `json:{...}` lines + legacy TSV
- **Operations**: enqueue, list, run, status, archive, prune
- **Task types**: article_capture, x_article_capture, script, project_task, email_triage
- **Intake parser**: `scripts/task-queue-intake.sh` — auto-routes URLs and script paths
- **Migration**: `scripts/task-queue-migrate.sh`

### Atomic Claim/Checkout (from Paperclip pattern)
- flock on `tasks.queue.lock`
- Status transitions: queued → running → done/failed
- `checkout <job_id>` returns 0 (success) or 153 (409-equivalent, walk away)
- Stale claim recovery: `TASK_QUEUE_CLAIM_STALE_MINUTES` (default 30)

### Intent Aliases
- Numbered directive parsing: "Do #1 then #2 and #3" maps to configured aliases
- Config: `config/intent-aliases.tsv` (editable without code changes)
- Heartbeat drains intake every run, consumes queue items (limit 3)

### Queue Archival
- `scripts/task-queue.sh archive` / `prune`
- Supports --keep-last, --min-age-s, --dry-run

---

## 6. Loop Control — The Loop Sheriff

Jeremy was explicit: **validator-based, not count-based**. Don't cap iterations — detect when you're stuck.

- **Validator**: `scripts/poe-orch-validate.sh` / `poe-orch-validate.py`
- **Persona**: `prototypes/poe-orchestration/personas/loop-validator.md`
- Detects repeated selection / no-progress
- Escalates to Handle (Poe) or Jeremy when stuck
- Independent from the execution loop itself

> "You need an independent validator to keep from getting stuck in a loop. This could be a script, agent, or even simple queue. With agents we don't have to know up front." — Jeremy, Mar 3

---

## 7. Heartbeat & Self-Healing

### Heartbeat Loop
- **Script**: `scripts/heartbeat-proactive-loop.sh`
- **Cadence**: Every 5 minutes via cron
- **State**: `memory/heartbeat-state.json`
- **Recovery modes**: same-session, post-compaction, cold-start
- **Prerequisite gate**: validates required bins/scripts + writable dirs before work

### Tiered Recovery
1. Gateway ping (`openclaw status --quiet`)
2. Model ping via `opencode run`
3. Config diagnostics (JSON valid, env vars present, backups exist)
4. Scripted recovery (`auto-recover.sh` — bash-first, deterministic)
5. Agentic recovery (spawn sub-agent for diagnosis)
6. Escalation — Telegram notification to Jeremy

**Key design decision**: Use a different model (Gemini) for heartbeat, so checks work even when primary model is down. Deterministic scripts first, agentic as fallback.

### Codex Usage Guardrail
- Runs `scripts/codex-usage.sh --json`, checks >= 85% usage
- Max 1x/hour, writes to `output/usage/`

### Proactive Discovery
- Discovers and runs `prototypes/*/hooks/heartbeat-proactive.sh` by pattern
- Each prototype can hook into the heartbeat without modifying global scripts

---

## 8. Observability

- **Queue run summaries**: `output/queue-runs/index.jsonl` (append-only)
- **NOW lane ledger**: `prototypes/poe-orchestrator/job-ledger.jsonl`
- **Dashboard**: `scripts/queue-runs-latest.sh` (colorized terminal renderer)
- **Retrieval traces**: `output/qmd/retrieval-traces/`
- **Actionable-only alerts**: Non-actionable failures logged as `status=degraded`, not surfaced to Jeremy

---

## 9. UX Contract

### Response Timing
| Elapsed | Expected behavior |
|---------|-------------------|
| ~1s | Immediate ack or answer |
| 5-15s | Status update if still working |
| 30-40s | Substantive update, even if incomplete |

### Three Response Modes
1. **Instant answer** — known fact, quick lookup
2. **"Just a sec"** — working on it, will update shortly
3. **"I've got that started"** — backgrounded, will notify on completion

### Default: async when uncertain. Better to pleasantly surprise than leave hanging.

### Message Intent Contract
- **Informational**: no action verb → treat as FYI
- **Directive**: explicit verb + scope + success condition → execute
- **Tie-breaker**: if ambiguous but low-risk and reversible → take action

### Interruption Policy
New messages during a run are additive/corrective by default. Only stop on explicit stop/wait/pivot. Continue background work and integrate new information.

---

## 10. Autonomy Policy

### Authority Level: C (Aggressive)
> "As long as we're able to roll back with git and/or are working forwards compatible (additive + backwards compatible) I think that's going to be just fine." — Jeremy

### Decision Gates (MUST ask Jeremy)
- Money / real trades
- Credentials / auth / external posting as Jeremy
- Destructive deletions
- Major direction changes / scope pivots
- Representing Jeremy externally
- Exposing private data outside the box
- Locking out critical access

### Everything Else: Act First
> "Prefer forgiveness rather than permission." — Jeremy, Feb 8
> "Tie goes to taking action." — Jeremy, Feb 12
> "Default to action and best judgement, document uncertainty > 30% to revisit at the end." — Jeremy, Mar 6

---

## 11. Concurrency Model

- Queue workers: 3
- One nested level: up to +3 each
- Effective cap: ~10 total including Poe (tunable)
- Persistent queue, variable over time
- Global concurrency throttle: `scripts/openclaw-agent-locked.sh` (flock, 2 Codex slots)

---

## 12. Workspace Isolation

Each prototype is fully self-contained:
```
prototypes/
  poe-orchestration/
    docs/           # ORCHESTRATOR_SPEC_v0.md, CONVENTIONS.md, etc.
    personas/       # Swappable persona definitions
    persona-specs/  # JSON mappings (persona → agent/model)
    projects/       # Per-project workspaces
      <project>/
        NEXT.md     # Task checklist
        RISKS.md
        DECISIONS.md
    src/            # orch.py — core module
    hooks/          # heartbeat-proactive.sh
    memory/
    output/
  poe-orchestrator/
    scripts/        # now_run.py, validate-latest-now-artifact.py
    artifacts/      # Per-job artifacts
    job-ledger.jsonl
  polymarket-research-bot/  # Separate project, uses orchestration
```

Global ops (gateway, tools, email, calendar) live at `system/` level. No cross-prototype coupling in global scripts.

---

## 13. Memory Strategy

- File-based memory: daily files at `memory/YYYY-MM-DD.md`, plus `MEMORY.md` index
- Separate audit trail vs. actionable memory
- "Also-After" hooks: post-goal tasks that capture audit/memory/follow-ups/index artifacts

> "An audit log isn't the same as actionable memory, so let's not forget, but also not mainline everything either." — Jeremy, Feb 15

---

## 14. External Patterns Evaluated ("Steal List")

| Source | What was taken |
|--------|---------------|
| **Paperclip** | Atomic checkout/409, run_id propagation, redaction, secret_ref |
| **Deer-Flow** | LangGraph ideas, middleware, memory, eval hooks |
| **Souls CLI** | Workspace fixer, curated persona bundles |
| **AutoHarness** | Auto-test generation concepts |
| **LangGraph** | Durable state graphs |
| **AutoGen** | Layered multi-agent patterns |
| **CrewAI** | Crews + Flows control plane |
| **Temporal** | Durable workflow ideas |

---

## 15. Failure Taxonomy

- **Type A**: Technical/runtime limits → retry, backoff, escalate
- **Type B**: Goal drift / exploration waste ("ralph-wiggum exploration") → agenda correction + checkpoint

---

## 16. Published Artifacts

- **GitHub**: `slycrel/openclaw-orchestration` (tagged v0.1.0)
- **Infrastructure backup**: `slycrel/openclaw-setup` (public, sanitized)
- **Roadmap**: M1-M4 implemented, then replaced with N1-N4

---

## 17. Rollout Phases

| Phase | Focus |
|-------|-------|
| 0 | Sanity & audit — validate existing scaffolding |
| 1 | Instant-ack + queue instrumentation |
| 2 | Durable graph flows & control plane |
| 3 | Scaling & crew personas |
| 4 | Evaluation + continuous learning |
