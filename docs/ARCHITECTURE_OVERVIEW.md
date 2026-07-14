---
status: living
---

# Architecture Overview

*High-level map of Maro's orchestration system. Read this to understand what exists, what's intended, and where they diverge.*

*For the vision: read `VISION.md`. For crystallization lifecycle: read `docs/KNOWLEDGE_CRYSTALLIZATION.md`. This doc bridges intent and implementation.*

---

## The North Star

Give the system a goal. It plans, executes, reviews, learns, and gets better over time. The user's job is mission definition and exception handling — not step supervision.

Two capabilities, often conflated but distinct:

1. **Maro-as-tool**: Execute tasks autonomously (research, build, analyze). *This works today.*
2. **Maro-as-self-improving-system**: Detect its own friction, change its own behavior, verify the change worked, remember what it learned. *Infrastructure exists; the loop isn't closed.*

**Maturity doctrine: Visibility → Reliability → Replayability.** A useful orchestration system matures in three layers that build on each other: *visibility* (see what it planned, did, spent, and why it failed — without it, debugging is séance), *reliability* (common paths complete consistently, fail legibly, recover sanely, stop repeating mistakes), *replayability* (rerun/replay runs well enough to diagnose decisions and test policy changes against prior traces). Structured logging, traces, and diagnoses are visibility; decomposition quality, recovery, and safer execution are reliability; checkpoints, record-mode capture, and trace replay are replayability. This ordering guides roadmap sequencing.

### Visibility — definition of done (the ladder)

"Visibility" is easy to vibe-claim. It is NOT done until every rung is durably
recorded to the run trace. Established 2026-06-26 after the test-corpus harvest
revealed the runtime record is lossier than we'd been saying — we'd casually
called visibility "closed" while the line was really at ~3.5/6.

| Rung | Visibility of… | Status | Where it lives |
|------|----------------|--------|----------------|
| 1 | Outcome (status, verdict, tokens, timing) | ✅ | loop-log.json, run metadata, captains log |
| 2 | Decisions (scope, diagnosis, quality-gate, claim probes, navigator) | ✅ | captains-log event types |
| 3 | Plan/structure (steps, deps, shared ctx) | ✅ | steps.json, shared.json |
| 4 | Step I/O (full input context + full output) | ⚠️ partial | step text yes; loop-log result still a truncated excerpt; assembled prompt now captured at rung 6 |
| 5 | Agent actions (inner Bash/Write/Read + results) | ✅ forward | tool_events persisted per call in `build/calls/` (record-mode); historical still absent |
| 6 | LLM call (exact prompt + raw response) — the replay tier | ✅ forward | `build/calls/call-NNNNN.json` (record-mode, default ON) |

**Forward record-mode shipped 2026-06-26** — the keystone. `FailoverAdapter`
captures `{prompt, response, tool_events, tokens}` per call to
`<run-dir>/build/calls/call-NNNNN.json` (secret-scrubbed, single seam over every
backend). Default ON; off via `MARO_RECORD=0` or config `record.enabled: false`.
This carries rungs 5–6 forward-only and unlocks the Replayability stage, which is
otherwise unreachable: you cannot replay a call whose prompt you never kept.
Remaining: unify rung-4 loop-log excerpts with the full captured output; no
historical backfill (forward-only by nature).

**Run references.** Handle IDs map directly to deterministic run-directory
names. Loop IDs and pre-resume IDs use hashed leaves in the workspace
`.run-ref-index-v1/`, giving bounded steady-state lookup without capping history.
Existing metadata migrates once under a lock; incomplete migration or index
storage failure retains the legacy scan as an availability fallback. The index
is derived state maintained on metadata publication, workspace import, and
prune; `metadata.json` remains authoritative evidence.

**Post-goal curation (adornment).** A run is paid for; we don't discard it.
`run_curation.curate_run` runs at goal-end (through the shared run-lifecycle
close hook). It first builds and atomically persists a side-effect-free card,
then runs an explicit maintenance phase (including skills-lite promotion) and
atomically persists the enriched card. Each phase records completed, failed,
and dependency-skipped actions. The card classifies the outcome
(success / done-not-achieved / done-unverified / partial / failed, done≠achieved
aware) and inventories what's mineable (calls, scripts, artifacts, steps). It's
a phase-aware registry with declared provides/requires dependencies, so a
failed producer skips its dependents without suppressing independent work.
User-visible + prunable via
`python3 -m run_curation list|show|curate|prune`. Off-switch is the same
`record.enabled` (curation still runs but inventories nothing when capture is off).

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
- ~~Personas aren't auto-selected~~ **Stale since 2026-03-27:** `persona_for_goal()` auto-selects (c964d3b), used from conductor.py and handle.py; prefixes remain as manual override.
- The "never off" vision (VISION §9) is not the default operating mode: manual runs work without background services, and always-on behavior must be intentionally enabled.

**Key files:** `handle.py` (~2526 lines), `intent.py`, `director.py`, `workers.py`, `persona.py`

---

## 2. Core Loop

**Intent:** Goal → decompose into steps → execute each step → learn from results. The loop should handle stuck detection, retries, budget limits, parallel execution, and checkpoint/resume — all autonomously.

**What exists:**
- `agent_loop.py` (~5400 lines): Seven-phase pipeline (INIT → DECOMPOSE → PRE_FLIGHT → PARALLEL → PREPARE → EXECUTE → FINALIZE). Monolith decomposition complete — all seven phases (incl. EXECUTE via `_execute_main_loop`) are now extracted as functions taking `LoopContext`; `run_agent_loop()` is the thin orchestrator.
- `planner.py`: Decomposes goals. Routes by scope (narrow/medium/wide/deep). Multi-plan comparison for complex goals.
- `step_exec.py`: Executes individual steps via LLM with tool calling.
- `pre_flight.py`: Cheap plan criticism before execution. Detects scope explosions, hidden assumptions, milestone candidates.
- `LoopContext`: Mutable state bundle. `LoopPhase` constants for each phase.

**Where intent has drifted:**
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
- Captain's log: 11K+ event stream tracking knowledge lifecycle transitions. Full event contract (every type, fields, emitter, when-it-fires) in `docs/CAPTAINS_LOG_EVENTS.md`.

**Where intent has drifted — this is the biggest gap:**
- **Stage 1→2 works:** Lessons get extracted from outcomes and stored in tiered memory.
- **Stage 2→3 doesn't exist:** No automated pathway to promote lessons to identity/canon. The threshold (10+ applies, 3+ task types) is defined in the spec but no code implements it.
- **Stage 3→4 is manual:** Skill extraction from outcomes exists (`extract_skills()`) but isn't reliably triggered in the normal loop.
- **Stage 4→5 is conceptual only:** No code promotes established skills to hardcoded rules.
- **Decay works but reinforcement is weak:** Lessons decay on schedule but only get reinforced when explicitly re-confirmed — the system doesn't proactively validate its own lessons.
- **Captain's log writes but rarely reads:** 11K events accumulated. Read bridge shipped (K3 partial) but injection is coarse — dumps recent events into prompts rather than targeted retrieval.

**Key data stores (all JSONL under `~/.maro/workspace/memory/`):**
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
- **The verify→learn plumbing is closed; the learning input was dead until recently.** Evolver self-changes are verified via `_verify_post_apply()` (pytest after auto-apply, session 17). But the learning *input* — lesson extraction from runs — was silently dead until 2026-06-11: a `safe_list` bug dropped the typed lesson dicts the prompt produces, so verify→learn extracted nothing (fixed, commit `d088ca7`). It is now live-verified but only lightly exercised (~2 typed lessons from one real call). The open question is whether the full medium→long→standing-rule accretion actually fires on organic runtime, not just in tests.
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
- `config.py`: Two-tier YAML (user `~/.maro/config.yml` + workspace). Env var override.
- `heartbeat.py`: Optional health monitor + tiered recovery. Session guard. Diagnosis cooldown.
- `orch_items.py`: Project/item management. NEXT.md parsing. RunRecords.
- `task_store.py`: File-per-task JSON with fcntl locking. DAG deps. Stale claim recovery.
- `metrics.py`: Per-model, per-step-type cost tracking to step-costs.jsonl.

**Where intent has drifted:**
- **Always-on mode is explicitly optional.** Manual orchestration is self-contained; service installation is a deployment choice rather than a baseline requirement.
- ~~Cost awareness is after-the-fact.~~ **RESOLVED 2026-07-01:** real-time budget gates exist — per-run `budget.per_run_usd` (loop hard-stops at budget+20% slush), cross-run `budget.daily_usd` via `metrics.spend_today()`, refusal emits an escalation notify.
- ~~Workspace routing is split.~~ **RESOLVED 2026-07-03:** BACKLOG #-1 workspace-pin unification — all roots (`output_root()`, `projects_root()`, memory) resolve through `config.workspace_root()`; `MARO_WORKSPACE=x` means the workspace IS x.

---

## Cross-Cutting Concerns

### Correspondence Layer (Not Yet Built)
The system lacks a shared mental model between sessions. CLAUDE.md + MILESTONES.md + BACKLOG.md serve as the bridge, but they're prose documents that require full reading. The architecture skills (see `skills/arch-*.md`) are the first step toward modular, loadable context.

### Phase Transition Contracts (Not Yet Built)
Boundaries between subsystems are implicit. There's no explicit contract saying "the core loop promises to call reflect_and_record() after every run" or "the evolver promises to check inspector friction before proposing." These contracts should be documented and tested.

### Conway's Law
The system's architecture mirrors its development process: each subsystem was built in a focused session, then wired together. The result is good individual subsystems with loose coupling — but the *interfaces between them* are the weakest points.

### Goal Lineage (Ancestry)

Two invariants Jeremy holds here: (a) a human must be able to **see** a goal's
lineage (what it descended from, what it spawned), and (b) later work must be
able to **pull in context from prior related work** downstream, not just
display it. Four mechanisms currently exist and are not fully unified:

| Mechanism | Granularity | What it tracks | Status |
|---|---|---|---|
| `ancestry.py` (spec §12) | per-project | Full multi-hop chain (root mission → ... → immediate parent) in `<project>/ancestry.json` | Live — the only source with real chain depth |
| `goal_map.py` | per-project | Reads the same `ancestry.json` files across all projects, adds sibling/conflict detection (competing active missions sharing a parent) | Live — used by `conductor.py`, `telegram_listener.py`, `maro map` |
| `thread_brain.py` | per-thread (run-dir) | One-hop origin only (`parent_handle_id`/`parent_goal`) in a narrative `goal_brain.md` | Part of the in-progress Thread Architecture (`docs/THREAD_ARCHITECTURE.md`), shadow-only |
| `recall.py`'s `_resolve_thread` | per-run | A *separate* parent-chain walk over run metadata (`origin`), independent of `ancestry.json` | Live — currently the one actually injected into `agent_loop`'s recall slice |

**Known gap:** `agent_loop.py` injects ancestry twice per loop from two
disagreeing sources (`ancestry.py`'s `ancestry.json` chain and `recall.py`'s
run-metadata walk) — see BACKLOG.md for the consolidation item. The intended
end state is one source of truth: `ancestry.py`'s project-level chain,
consulted by `recall.py` instead of independently re-derived, with
`thread_brain.py`'s per-thread origin feeding the same `ancestry.json` at
thread-fork time as the Thread Architecture work lands.

**Visibility today:** `maro ancestry <project> [--set-parent] [--format]`
(CLI, text or JSON) is the durable surface. The `observe.py` dashboard also
rendered an ancestry tree panel, but that dashboard was an archived
proof-of-concept (see `archive/observe_dashboard.py` and BACKLOG_DONE.md) —
the CLI command is what to build on, not the dashboard.

**Downstream reference today:** partial. `ancestry.py`'s chain is real but
requires manual `--set-parent` wiring; nothing yet auto-walks it to inject
"what did the parent goal conclude" into a child run's context. This is the
open thread — not a new subsystem to build, but wiring the pieces above into
one path.

---

## Reading Order for New Sessions

1. `VISION.md` — what Poe is and isn't
2. `CLAUDE.md` — current state, how to run things
3. `MILESTONES.md` — what to do next
4. This document — how the pieces fit together
5. Relevant `skills/arch-*.md` — deep dive on the subsystem you're working on
