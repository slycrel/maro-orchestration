# Architecture Overview

*High-level map of Poe's orchestration system. Read this to understand what exists, what's intended, and where they diverge.*

*For the vision: read `VISION.md`. For crystallization lifecycle: read `docs/KNOWLEDGE_CRYSTALLIZATION.md`. This doc bridges intent and implementation.*

---

## The North Star

Give the system a goal. It plans, executes, reviews, learns, and gets better over time. The user's job is mission definition and exception handling — not step supervision.

Two capabilities, often conflated but distinct:

1. **Poe-as-tool**: Execute tasks autonomously (research, build, analyze). *This works today.*
2. **Poe-as-self-improving-system**: Detect its own friction, change its own behavior, verify the change worked, remember what it learned. *Infrastructure exists; the loop isn't closed.*

---

## Five Subsystems

```
┌─────────────────────────────────────────────────────┐
│                   INTERFACE                          │
│  handle.py → intent.py → director.py → workers.py  │
│  How goals enter, get classified, get routed        │
├─────────────────────────────────────────────────────┤
│                   CORE LOOP                          │
│  agent_loop.py → planner.py → step_exec.py          │
│  → pre_flight.py                                     │
│  Decompose → execute → introspect cycle              │
├──────────────────────┬──────────────────────────────┤
│  MEMORY / KNOWLEDGE  │  QUALITY / SELF-IMPROVEMENT  │
│  memory.py           │  inspector.py                 │
│  knowledge_web.py    │  evolver.py                   │
│  knowledge_lens.py   │  graduation.py                │
│  memory_ledger.py    │  introspect.py                │
│  captain's log       │  quality_gate.py              │
│                      │  skills.py                    │
│  How the system      │  constraint.py                │
│  records & retrieves │                               │
│  what it learned     │  How the system validates     │
│                      │  work AND improves itself     │
├──────────────────────┴──────────────────────────────┤
│                    PLATFORM                          │
│  llm.py · config.py · heartbeat.py · orch_items.py  │
│  task_store.py · metrics.py · persona.py             │
│  Operational substrate everything runs on            │
└─────────────────────────────────────────────────────┘
```

---

## 1. Interface & Routing

**Intent:** Goals arrive from any channel (Telegram, Slack, CLI, Python API). The system classifies them and routes to the right execution path without human intervention.

**What exists:**
- `handle.py`: Unified entry point. Classifies intent (NOW vs AGENDA), applies magic prefixes (`direct:`, `verify:`, `garrytan:`, etc.), routes to appropriate lane.
- `intent.py`: LLM-based classification with heuristic fallback. NOW = single-shot; AGENDA = multi-step loop.
- `director.py`: Plans work, delegates to workers, reviews output. Challenger review for risk.
- `workers.py`: Specialized executors (research / build / ops / general) with constrained tool access.
- `persona.py`: Composable agent identities from YAML. Role, model tier, tool access, prompt.

**Where intent has drifted:**
- Director is mostly bypassed (`skip_if_simple=True` for most goals). This is pragmatically correct but means the plan→delegate→review cycle doesn't get exercised.
- Persona system exists but personas aren't auto-selected based on goal type — it's manual via prefixes.
- The "never off" vision (VISION §9) isn't met: no auto-restart, session guard blocks autonomous work.

**Key files:** `handle.py` (1104 lines), `intent.py`, `director.py`, `workers.py`, `persona.py`

---

## 2. Core Loop

**Intent:** Goal → decompose into steps → execute each step → learn from results. The loop should handle stuck detection, retries, budget limits, parallel execution, and checkpoint/resume — all autonomously.

**What exists:**
- `agent_loop.py` (3938 lines): Seven-phase pipeline (INIT → DECOMPOSE → PRE_FLIGHT → PARALLEL → PREPARE → EXECUTE → FINALIZE). Recently extracted from monolith into sub-methods.
- `planner.py`: Decomposes goals. Routes by scope (narrow/medium/wide/deep). Multi-plan comparison for complex goals.
- `step_exec.py`: Executes individual steps via LLM with tool calling.
- `pre_flight.py`: Cheap plan criticism before execution. Detects scope explosions, hidden assumptions, milestone candidates.
- `LoopContext`: Mutable state bundle. `LoopPhase` constants for each phase.

**Where intent has drifted:**
- The monolith extraction is incomplete — Phase F (main execute loop) is still inline in `run_agent_loop()`.
- Checkpoint/resume exists but isn't automatically triggered on crash recovery.
- Budget ceiling creates continuation tasks but doesn't autonomously re-queue them.
- Parallel fan-out is conservative (heuristic independence check).

**Key data structures:** `LoopContext`, `LoopResult`, `StepOutcome`, `LoopPhase`

---

## 3. Memory & Knowledge

**Intent:** The system's intelligence should compound over time. Every LLM call that answers a question Poe has answered 50 times before is waste. Knowledge crystallizes: Fluid → Lesson → Identity → Skill → Rule (see KNOWLEDGE_CRYSTALLIZATION.md).

**What exists:**
- `memory.py`: Outcome recording, lesson extraction via LLM, TF-IDF injection.
- `memory_ledger.py`: Task-level execution traces.
- `knowledge_web.py`: Cross-linked concept nodes (lat.md graph).
- `knowledge_lens.py`: Focused analysis lenses for memory data.
- Tiered memory: MEDIUM (decays 15%/day) → LONG (promoted at 0.9+ score, 3+ sessions). Standing rules (zero-cost, always active).
- Captain's log: 11K+ event stream tracking knowledge lifecycle transitions.

**Where intent has drifted — this is the biggest gap:**
- **Stage 1→2 works:** Lessons get extracted from outcomes and stored in tiered memory.
- **Stage 2→3 doesn't exist:** No automated pathway to promote lessons to identity/canon. The threshold (10+ applies, 3+ task types) is defined in the spec but no code implements it.
- **Stage 3→4 is manual:** Skill extraction from outcomes exists (`extract_skills()`) but isn't reliably triggered in the normal loop.
- **Stage 4→5 is conceptual only:** No code promotes established skills to hardcoded rules.
- **Decay works but reinforcement is weak:** Lessons decay on schedule but only get reinforced when explicitly re-confirmed — the system doesn't proactively validate its own lessons.
- **Captain's log writes but rarely reads:** 11K events accumulated. Read bridge shipped (K3 partial) but injection is coarse — dumps recent events into prompts rather than targeted retrieval.

**Key data stores (all JSONL under `~/.poe/workspace/memory/`):**
- `outcomes.jsonl`, `lessons.jsonl`, `medium/lessons.jsonl`, `long/lessons.jsonl`
- `standing_rules.jsonl`, `hypotheses.jsonl`, `decisions.jsonl`
- `captains_log.jsonl`, `task_ledger.jsonl`, `step_traces.jsonl`

---

## 4. Quality & Self-Improvement

**Intent:** Two zoom levels of the same thing: (a) "did this run work?" and (b) "how do we get better over time?" The system should autonomously detect friction, propose changes, apply safe ones, verify they worked, and remember what it learned.

**What exists:**
- `inspector.py`: Post-hoc friction detection (7 signal types). Configurable thresholds.
- `evolver.py`: Proposes improvements (prompt tweaks, guardrails, skills, observations). Auto-applies low-risk changes (lessons, observations). Holds guardrails for human review.
- `graduation.py`: Promotes repeated failure-class diagnoses to permanent fixes. Has templates with verify_patterns.
- `introspect.py`: Failure classification (11 classes), lenses, recovery planning.
- `quality_gate.py`: Multi-pass review (verdict, adversarial claims, cross-ref, council, debate).
- `skills.py`: Discovery, scoring, promotion/demotion, circuit breaker. Auto-promote at 5+ uses / 70%+ success.
- `constraint.py`: Pre-execution enforcement. Tiered gates (READ/WRITE/DESTROY/EXTERNAL).

**Where intent has drifted:**
- **The self-improvement loop isn't closed.** Evolver proposes → changes are applied (low-risk) or held (high-risk) → but nobody verifies that applied changes actually fixed the problem. The verify→learn→apply cycle is broken at the verify step.
- **Inspector and evolver share almost no data structures.** Inspector produces friction signals; evolver reads outcomes. They should feed each other directly.
- **Graduation templates exist but verification isn't automated.** `verify_graduation_rules()` exists but isn't called in the heartbeat loop.
- **Quality gate is comprehensive but expensive.** 5 passes × LLM calls. In practice, most runs skip the expensive passes. The gate degrades gracefully but this means the system runs mostly unreviewed.
- **Skills circuit breaker works but skill creation doesn't.** Auto-promote/demote for *existing* skills works. But new skill discovery from successful outcomes is rare in practice.

**The honest assessment:** This is sophisticated *infrastructure for* self-improvement. Low-risk auto-application works (lessons, provisional skills). But the full autonomous loop (detect → propose → apply → verify → learn) has gaps at the verify and learn stages.

---

## 5. Platform

**Intent:** Operational substrate that everything runs on. Model-agnostic, cost-aware, resilient.

**What exists:**
- `llm.py`: Adapter hierarchy (Anthropic → OpenRouter → OpenAI → subprocess). Model abstraction (CHEAP/MID/POWER). Retry with exponential backoff. Advisor pattern (`advisor_call()`).
- `config.py`: Two-tier YAML (user `~/.poe/config.yml` + workspace). Env var override.
- `heartbeat.py`: Periodic health check + tiered recovery. Session guard. Diagnosis cooldown.
- `orch_items.py`: Project/item management. NEXT.md parsing. RunRecords.
- `task_store.py`: File-per-task JSON with fcntl locking. DAG deps. Stale claim recovery.
- `metrics.py`: Per-model, per-step-type cost tracking to step-costs.jsonl.

**Where intent has drifted:**
- **"Never off" not implemented.** Heartbeat has 4h auto-stop. No auto-restart mechanism in production (systemd unit exists but isn't installed).
- **Cost awareness is after-the-fact.** `metrics.py` records costs. `tool_cost_report.py` summarizes them. But there's no real-time budget enforcement that says "stop, you've spent $5 on this goal."
- **Workspace routing is split.** `output_root()` and `projects_root()` still point to repo, not `~/.poe/workspace/`. Captain's log and memory are in workspace, but projects and output aren't.

---

## Cross-Cutting Concerns

### Correspondence Layer (Not Yet Built)
The system lacks a shared mental model between sessions. CLAUDE.md + MILESTONES.md + BACKLOG.md serve as the bridge, but they're prose documents that require full reading. The architecture skills (see `skills/arch-*.md`) are the first step toward modular, loadable context.

### Phase Transition Contracts (Not Yet Built)
Boundaries between subsystems are implicit. There's no explicit contract saying "the core loop promises to call reflect_and_record() after every run" or "the evolver promises to check inspector friction before proposing." These contracts should be documented and tested.

### Conway's Law
The system's architecture mirrors its development process: each subsystem was built in a focused session, then wired together. The result is good individual subsystems with loose coupling — but the *interfaces between them* are the weakest points.

---

## Reading Order for New Sessions

1. `VISION.md` — what Poe is and isn't
2. `CLAUDE.md` — current state, how to run things
3. `MILESTONES.md` — what to do next
4. This document — how the pieces fit together
5. Relevant `skills/arch-*.md` — deep dive on the subsystem you're working on
