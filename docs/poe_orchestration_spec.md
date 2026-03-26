# Poe Orchestration — Design Spec
## Synthesized from 7 weeks of design conversations (Feb 5 – Mar 23, 2026)

---

## 1. System Overview

An orchestration layer that turns a conversational AI bot ("Poe") into an autonomous agent capable of receiving high-level goals and executing them to completion through delegation, sub-agents, and structured task management.

**Core principle**: The orchestration is the product. Projects (Polymarket bot, X scraping, etc.) are test cases that *use* it. It is not a framework that sits under projects — it's the operating system for getting things done.

**Platform**: 2014 Mac Mini (Ubuntu Linux headless), user `clawd`, residential fiber.

---

## 2. Architecture — Three Layers

### Layer 1: Infrastructure ("The Body")
Schedulers, queues, tool access, sandboxes, cost controls, logging, retries, model selection/routing.

- **Host**: Ubuntu Linux headless, user `clawd` with sudo, 2014 Mac Mini
- **Gateway**: OpenClaw local gateway, ws://127.0.0.1:18789, systemd service
- **Communication**: Telegram (@edgar_allen_bot / "Poe"), Slack (Claude Code bridge)
- **Model stack**: Claude Sonnet/Opus (primary) via Anthropic API or subprocess → OpenRouter (fallback)
- **Auth**: Anthropic API key or OpenRouter key; `gh` CLI for GitHub (authenticated as `slycrel`)

### Layer 2: Agent Runtime ("The Process")
Execution context with permissions, memory handles, and toolset.

- **Main agent**: conversational coordinator ("the Handle")
- **Sub-agents**: Spawned via Director/Worker hierarchy or `run_mission()` features
- **Backoff**: Jitter on rate-limit/cooldown

### Layer 3: Persona/Role ("The Mask")
Goals, voice, decision rules, rubric — portable across runtimes and models.

- **Personas directory**: `personas/`
- **Key insight**: Personas are swappable masks, separate from infrastructure. Different models can wear the same persona.

---

## 3. Agent Hierarchy

```
Jeremy (Telegram)
  └── Poe ("The Handle")
        ├── Director sub-agent — plans, reviews, iterates; does NOT execute code
        │     ├── worker-research — info gathering, must produce cited artifacts
        │     ├── worker-build — implementation
        │     ├── worker-ops — automation/diagnostics/infrastructure
        │     └── worker-general — catch-all
        └── NOW Lane — 1-shot quick tasks (bypass Director for trivial work)
```

### Director Contract
- Takes a directive, produces SPEC + TICKET artifacts
- `plan_acceptance` modes: `explicit` (for public/irreversible) or `inferred` (low-risk/reversible)
- Gates worker kickoff on acceptance
- Reviews/iterates until acceptance criteria met

### Slash Commands
`/director <directive>`, `/research <task>`, `/build <task>`, `/ops <task>`, `/general <task>`

---

## 4. Two Execution Lanes

### NOW Lane (Fast Path)
For tasks completable in ~seconds. 1-shot delegation, no planning overhead. `handle.py` classifies intent → NOW or AGENDA.

### AGENDA Lane (Heavy Path)
For multi-step projects. `agent_loop.py` decomposes goal → executes steps → done|stuck. Per-project `projects/<slug>/NEXT.md` tracks checklist.

---

## 5. Loop Control

Jeremy was explicit: **validator-based, not count-based**. Don't cap iterations — detect when you're stuck.

`sheriff.py` checks:
- Repetition: same TODO selected 3+ times with no state change
- Artifact freshness: have output files changed recently?
- Decision log freshness: are new decisions being added?

The "March of Nines" problem: a 10-step chain at 90%/step = 35% failure. Stage gating (milestone validation) and checkpoint-resume are the fix — not retry logic.

---

## 6. Heartbeat & Self-Healing

Three-tier recovery:
1. Scripted: deterministic bash checks and recovery (`sheriff.py`)
2. LLM diagnosis: stuck projects analyzed by LLM
3. Telegram escalation: critical alerts only

**Key design**: Deterministic scripts first, agentic as fallback. Non-actionable failures are logged silently — not surfaced to Jeremy.

---

## 7. Observability

- `memory/outcomes.jsonl` — per-run outcomes
- `memory/heartbeat-log.jsonl` — heartbeat history
- `memory/sandbox-audit.jsonl` — sandboxed skill execution audit
- `projects/<slug>/DECISIONS.md` — per-project decision log
- `memory/YYYY-MM-DD.md` — daily narrative log

---

## 8. UX Contract

### Response Timing
| Elapsed | Expected behavior |
|---------|-------------------|
| ~1s | Immediate ack or answer |
| 5-15s | Status update if still working |
| 30-40s | Substantive update, even if incomplete |

Three response modes: instant answer / "just a sec" / "I've got that started" (backgrounded).

Default: async when uncertain. Better to pleasantly surprise than leave hanging.

---

## 9. Autonomy Policy

### Authority Level: C (Aggressive)
> "As long as we're able to roll back with git and/or are working forwards compatible I think that's going to be just fine." — Jeremy

### Decision Gates (MUST ask Jeremy)
- Money / real trades
- Credentials / auth / external posting as Jeremy
- Destructive deletions
- Major direction changes / scope pivots
- Exposing private data outside the box

### Everything Else: Act First
> "Prefer forgiveness rather than permission."

---

## 10. External Patterns Incorporated

| Source | What was taken |
|--------|---------------|
| **Paperclip** | Atomic checkout, run_id propagation, goal ancestry |
| **Reflexion** | Agent self-reflection on mistakes, stored lessons improve subsequent runs |
| **Factory AI** | Three-tier prompting, milestone-gated validation, GAN-style Generator/Evaluator split |
| **Memento-Skills** | Skill library evolution, failure attribution, unit-test gate on mutations |
| **Anthropic Harness** | Sprint contracts, boot protocol, `pass^k` regression gates |
| **LangGraph** | Durable state graphs, checkpointed self-improvement loops |
| **DSPy** | Prompt-as-module auto-optimization from success/failure metrics |

---

## 11. Memory Strategy

Three tiers + identity:
- `short`: in-process session only
- `medium`: decays 0.85×/day, GC at <0.2
- `long`: promoted from medium at score ≥ 0.9 AND sessions ≥ 3
- `AGENTS.md/SOUL.md`: behavioral identity (human-gated writes only)

See `docs/MEMORY_ARCHITECTURE.md` for the full graduation path and `docs/KNOWLEDGE_CRYSTALLIZATION.md` for the crystallization pipeline.

---

## 12. Goal Ancestry

Every task carries a reverse-linked chain back to the top-level mission. Prevents drift. Each agent's prompt includes:

```
Goal Ancestry (stay aligned with this chain):
1. Mission: Build self-leveling autonomous assistant
2. Objective: Add autonomous bug-fix & evolution loop
→ Current Task: Implement WebSocket handler
```

Ancestry stored in `projects/<slug>/ancestry.json`.

---

## 13. Self-Leveling

The north star: an assistant that gets measurably better over time without manual prompt tuning.

```
Execute task → Log outcome + artifacts
    → Reflect (per-task lesson, memory.py)
    → Aggregate (meta-evolver reviews N runs, evolver.py)
    → Optimize (prompt tweaks, new skills, guardrail updates)
    → Deploy changes → Execute next task (improved)
```

Start simple: file-based lesson storage, manual review of meta-evolver suggestions. Graduate to auto-application once confidence is established.
