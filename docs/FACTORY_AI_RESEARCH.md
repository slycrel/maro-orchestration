# Factory AI Research — Captured 2026-03-24

Deep dive into Factory AI's orchestration architecture. Source: Factory blog, docs, Latent Space interview, academic papers. Tweet reference: `@factoryai/status/2036184745059688923`.

This document exists to inform Phases 10-13 of the Poe roadmap. Re-read it before designing mission hierarchy, hooks, inspector, or Poe-as-CEO refactors.

---

## 1. Core Architecture: Droids

Factory deliberately avoided calling their agents "agents" — the word was poisoned by AutoGPT-era while-loop systems. "Droids" are specialized, role-assigned agents.

**Five default droid types:** Code, Knowledge, Reliability, Product, Tutorial.

**Custom sub-droids:** Defined in `.factory/droids/` as Markdown files with YAML frontmatter — name, system prompt, model, reasoning effort, tool access scope. Parent delegates to them via a `Task` tool specifying `subagent_type`. Each subagent gets a **fresh context window** — clean slate per delegation. No session holds the entire project in its head.

**Poe parallel:** Our Director/Worker split mirrors this, but we don't yet enforce fresh context per feature. Phase 10 fixes this.

---

## 2. Agent Loop Design (Terminal-Bench Paper)

Three things that actually move the needle — not model selection, but agent design:

### 2a. Three-Tier Hierarchical Prompting ★★★

The most transferable concept. Standard system prompt + messages isn't enough for reliable long-task execution.

1. **Tool Descriptions** — high-level capability specs
2. **System Prompts** — behavioral guidelines and objectives (standard)
3. **System Notifications** — contextual, time-sensitive guidance injected **mid-conversation at the right moment**, not front-loaded

The third tier is the novel one. Notifications let you provide "fine-grained control over model behavior, enabling rapid error recovery and task-specific adjustments without overwhelming the system prompt." The difference between a policy document and a supervisor tapping you on the shoulder at exactly the right moment.

**Poe parallel:** Phase 11 hooks implement this — reviewer/coordinator/reporter hooks inject guidance at the right hierarchy level rather than front-loading everything into the system prompt.

### 2b. Model-Specific Architectures

Each model has "divergent operational patterns" — one prefers FIND_AND_REPLACE, another prefers V4A diff format. Solution: modular architecture sharing core components but allowing per-model adaptation. Droid with Sonnet outperformed Claude Code with Opus on Terminal-Bench (50.5% vs 43.2%).

**Poe parallel:** Our LLM adapter layer already enables this. Phase 13 formalizes model-per-role assignment.

### 2c. Minimalist Tool Design

Tool reliability was the primary bottleneck — not model intelligence. Solution: strictly limit tool count, simplify input schemas to reduce ambiguity. "Multiplicative gains in full-task completion rates" from reducing individual tool error rates.

### 2d. Planning Tool with Live Checkoffs

Agent has a tool to create/update a plan. As steps complete, it calls the planning tool to cross off the completed step while marking the next in-progress. Inserts an explicit reminder of state at each decision point, fighting LLM recency bias in long tasks.

**Poe parallel:** We have NEXT.md checkoffs. The missing piece: making the plan visible *to the executing agent* at each step, not just to humans reading the file.

### 2e. Background Execution Primitive

"A controlled background-execution primitive so the agent can start a process, keep working, and leave it running for tests to hit later." Critical for test-driven long tasks.

**Poe parallel:** Phase 10 adds this explicitly. Currently everything blocks.

### 2f. Runtime Awareness

LLMs are told how long each tool takes so they can "avoid repeating slow operations, choose faster alternatives, and set timeouts more intelligently."

---

## 3. Missions: Multi-Day Orchestration

The big architectural leap. Goal pursuit over multi-day horizons (longest documented: 40 days, median ~2 hours).

### Hierarchy

```
Mission (goal)
  └─ Milestone (validation checkpoint)
      └─ Feature (unit of work)
          └─ Worker Session (fresh context, single feature)
```

Each feature gets a fresh worker session with clean context. No single session holds the entire project.

### Specialized Roles (model-agnostic)

| Role | Job | Model tier |
|------|-----|-----------|
| Orchestrator | Planning, coordination, re-scoping | POWER |
| Feature Workers | Code generation, testing | MID |
| Validators | Regression detection, integration | MID or specialized |
| Research Agents | API exploration, dependency analysis | MID |

The orchestrator is itself an agent you can talk to. "The most effective way to use Missions is to treat yourself as the project manager — monitoring progress, unblocking workers, and redirecting when the plan needs to change."

### Parallelization Strategy

Sequential-first with targeted parallelization: parallelize within features and during validation, sequential across milestones. Git is the coordination primitive — source of truth for handoffs between sessions.

**Why this beats naive parallelism:** Coordination overhead is low within a feature but high across milestones. Sequential milestone execution means errors don't compound across parallel tracks.

### Milestone Validation Phase

Every milestone ends with a validation phase: review accumulated work, run tests, check regressions, verify integration. Validators use native computer use — launch the application, navigate UI flows, check rendering — catching bugs automated tests miss. If validation fails, the orchestrator creates follow-up work before advancing.

### Four Interruption Patterns

Factory explicitly documented how to handle mission failures:

1. **Frozen Missions** — pause, tell orchestrator to reassess and continue with context about last observed state
2. **Stalled Workers** — mark stuck item complete manually, advance to next feature
3. **Blocked Milestones** — tell orchestrator to re-evaluate remaining work, identify blocking dependencies, reorder features
4. **Mid-Mission Pivots** — pause execution, give explicit re-planning instructions

**Poe parallel:** Phase 9 (interrupts) handles the mechanics. Phase 10 adds the milestone structure that makes them meaningful.

### Cost Formula

`total runs ≈ #features + 2 * #milestones`

Token consumption: 12x higher at median than standard sessions. Per-message complexity: 11K → 19K tokens. Worth it for mission-length work.

### Skill-Based Learning

"When the orchestrator analyzes a new task, it identifies patterns that can be captured as reusable skills. Workers refine and extend the skill library as they work." Skills persist across missions, enabling compound improvement.

**Poe parallel:** Phase 10 includes skill library extraction from completed goal chains.

---

## 4. Signals: Closed-Loop Self-Improvement ★★★

The self-improvement mechanism. Most directly applicable to Poe's evolver + inspector.

### The Core Loop

1. LLM-as-judge processes every session in 24-hour batches (minimum 30 agentic steps)
2. Extracts **abstracted friction/delight signals** without reading raw user content (privacy-safe)
3. Clusters signals into patterns using semantic embeddings
4. When friction crosses threshold → automatically files a structured ticket → self-assigns to Droid → Droid implements fix → PR opened → **human approves**
5. Result: **73% of issues auto-resolved within 4 hours average**

### Seven Friction Indicators

| Signal | What it means |
|--------|--------------|
| Error Events | Model errors, tool failures, timeouts |
| Repeated Rephrasing | 3+ consecutive restatements of the same ask |
| Escalation Tone | "broken," "why isn't," "frustrating" |
| Platform Confusion | Questions about features that exist |
| Abandoned Tool Flow | Rejected/cancelled tool calls |
| Backtracking | "undo," "revert," code deletion patterns |
| Context Churn | Repeated add/remove of same files or context |

**Key empirical discovery:** Context churn (not errors) emerged as the **leading frustration indicator**. After 3 rephrase attempts: 40% chance of another. After 5: significant drop in task completion.

**Key empirical discovery 2:** Error recovery generates more user trust than error prevention. Resilience > perfection.

**Key empirical discovery 3:** Sessions where the agent explained its reasoning generated disproportionately positive signals compared to sessions that just executed silently.

### Privacy Architecture

Multi-layer abstraction: LLM extracts patterns while omitting specific content → individual results feed aggregate stats → patterns only surface across enough distinct sessions. "Privacy without blindness."

### Facet Schema Evolution

Sessions decompose into structured metadata (language, intent, completion status, frameworks). The facet schema itself evolves — system generates embeddings, clusters sessions, proposes new dimensions when clusters don't map to existing facets. Self-describing observability.

**Poe parallel:** Phase 12 (Inspector) implements the full Signals loop. Phase 7's evolver gets upgraded with the 7-signal friction detection.

---

## 5. Context & Retrieval Systems

### HyperCode

Proprietary multi-resolution codebase representation:
- Explicit graph relationships (cross-file dependencies, AST)
- Implicit latent-space similarity relationships
- Insights at multiple abstraction levels

Handles "figuring out what I actually need" rather than flooding context.

### ByteRank

Specialized RAG on top of HyperCode. Retrieves contextually relevant code at multiple abstraction levels for a given task.

**Poe relevance:** Less applicable for Poe's general-purpose domain, but the multi-resolution principle (don't dump everything, retrieve at the right abstraction level) applies to how we inject memory/lessons context.

---

## 6. Agent Readiness Framework

Novel meta-concept: before agents work well, the **codebase** needs to be ready. Factory's `/readiness-report` scores repos across 8 pillars and 5 maturity levels (Functional → Autonomous). Target is Level 3 (Standardized = "production-ready for agents").

Key: they found LLM non-determinism caused 7% evaluation variance. Fixed by grounding each assessment against the previous report — reduced inconsistency to 0.6%.

**Poe relevance:** Lower priority. Interesting for software-focused Poe work but not core orchestration.

---

## 7. Delegator-as-Non-Coder Principle

"If the Delegator writes code, the system has already failed."

More broadly: if the orchestrator layer is executing steps directly, the architecture has failed. Clean separation: orchestrator plans and reviews, workers execute. This is the core of Phase 13 (Poe as CEO).

---

## 8. Prioritized Hit List for Poe

| Priority | Concept | Phase |
|----------|---------|-------|
| ★★★ | Three-tier prompting (System Notifications) | 11 |
| ★★★ | Signals friction detection + closed improvement loop | 12 |
| ★★★ | Milestone-gated validation with fresh context per unit | 10 |
| ★★ | Background execution primitive | 10 |
| ★★ | Skill library accumulation | 10 |
| ★★ | Delegator-as-non-coder enforcement | 13 |
| ★★ | Autonomy tier system | 13 |
| ★★ | Model assignment per role | 13 |
| ★ | Runtime awareness (tool timing hints) | 11 |
| ★ | Readiness framework | future |
| ✗ | HyperCode/ByteRank | not applicable (code-specific) |

---

## Sources

- [Terminal-Bench Technical Report](https://factory.ai/news/terminal-bench)
- [Code Droid Technical Report](https://factory.ai/news/code-droid-technical-report)
- [Introducing Missions](https://factory.ai/news/missions)
- [Signals: Toward a Self-Improving Agent](https://factory.ai/news/factory-signals)
- [Introducing Agent Readiness](https://factory.ai/news/agent-readiness)
- [Missions Documentation](https://docs.factory.ai/cli/features/missions)
- [Custom Droids](https://docs.factory.ai/cli/configuration/custom-droids)
- [How to Talk to a Droid](https://docs.factory.ai/cli/getting-started/how-to-talk-to-a-droid)
- [Factory.ai: The A-SWE Droid Army — Latent Space](https://www.latent.space/p/factory)
- Factory.ai $50M Series B announcement (SiliconANGLE, Sep 2025)
