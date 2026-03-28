# Poe-Orchestration Backlog Triage
**Date:** 2026-03-27 (updated 2026-03-27)
**Sources:** DeerFlow deep-dive (2026-03-07), Nanoclaws/sub-agent X thread (2026-03-07), AutoHarness arXiv:2603.03329 (2026-02-10)
**Purpose:** Identify what to steal, what's done, what's net new for the poe-orchestration roadmap.
**Roadmap context:** 21 phases complete, 9 partial, 5 planned as of Phase 35 triage. P1 items all shipped March 2026.

---

## Source 1: DeerFlow

**What it is:** Multi-agent research orchestrator with a state machine executor, TF-IDF memory injection, and per-thread context isolation. Coordinator delegates to specialized sub-agents (researcher, coder, reporter). Human-in-the-loop checkpoints before irreversible actions.

### DeerFlow Patterns — Classification

| Idea | Status | Notes |
|------|--------|-------|
| Sub-agent delegation (coordinator → specialized agents) | ✅ Implemented | `claude_agent.py`, `openai_agent.py`, orchestrator prototype |
| State machine executor (step tracking, retry logic) | ✅ Implemented | `orchestrator/runner.py`, heartbeat scripts |
| TF-IDF memory injection (relevance-ranked context) | ⚠️ Partial | `memory_manager.py` exists; no TF-IDF ranking — uses recency only |
| Per-thread context isolation | ⚠️ Partial | Thread IDs tracked; no hard isolation between concurrent sessions |
| Human-in-the-loop gating before destructive steps | ⚠️ Partial | Telegram confirm flow for some actions; not systematic |
| Parallelized sub-agent fan-out | ❌ Not implemented | Sequential execution only in current orchestrator |
| Reporter synthesis agent (structured final output) | ❌ Not implemented | No dedicated synthesis/summary agent role |

### What to steal from DeerFlow
1. **TF-IDF (or embedding-ranked) memory injection** — replace recency-only retrieval with relevance scoring
2. **Parallelized sub-agent fan-out** — run independent research/execution threads concurrently
3. **Reporter synthesis role** — dedicated agent that consolidates multi-agent outputs into structured deliverables

---

## Source 2: Nanoclaws / Sub-Agent Proposal

**What it is:** (Source file missing; reconstructed from TASKS.md.) Tiered orchestration with curated "nanoclaw" sub-agents — small, specialized, cheap-to-run workers instantiated on demand. Proposes a registry of agent capabilities, dynamic selection based on task type, and cost-aware routing (cheaper models for simpler tasks).

### Nanoclaws Patterns — Classification

| Idea | Status | Notes |
|------|--------|-------|
| Agent capability registry | ⚠️ Partial | `AGENTS.md` describes roles; no machine-readable registry |
| Dynamic agent selection by task type | ⚠️ Partial | Manual routing in orchestrator; no automated dispatch |
| Cost-aware model routing (cheap model → complex model escalation) | ❌ Not implemented | All tasks route to same model tier |
| Ephemeral worker instantiation (spawn/teardown per task) | ⚠️ Partial | Claude Code sessions are ephemeral; no programmatic spawn |
| Tiered authority levels (nanoclaw < claw < orchestrator) | ✅ Implemented | `AGENTS.md` authority levels; shell scripts enforce scoping |
| Capability-based task decomposition | ❌ Not implemented | Decomposition is ad-hoc / prompt-driven |

### What to steal from Nanoclaws
1. **Machine-readable agent registry** — YAML/JSON capability manifest enabling automated dispatch
2. **Cost-aware model routing** — haiku/flash for simple steps, opus for synthesis; measurable cost reduction
3. **Structured task decomposition** — formal breakdown before dispatch, not just prompt-level delegation

---

## Source 3: AutoHarness (arXiv:2603.03329)

**What it is:** Auto-synthesizes code harnesses around LLMs using iterative refinement from environment feedback. Prevents illegal/invalid actions (e.g., 78% of Gemini losses in chess came from illegal moves). Extends to full code-as-policy: generate the entire decision policy in code, eliminating LLM calls at inference time. Smaller model + harness outperforms larger model without one.

### AutoHarness Patterns — Classification

| Idea | Status | Notes |
|------|--------|-------|
| Environment feedback loop (action → validate → refine) | ✅ Implemented | Telegram error feedback, heartbeat retry logic |
| Constraint harness (prevent invalid actions) | ❌ Not implemented | No programmatic action validation layer |
| Iterative code refinement from env feedback | ⚠️ Partial | Ad-hoc retry; not a structured refinement loop |
| Code-as-policy (synthesize full decision logic in code) | ❌ Not implemented | No mechanism to codify recurring decisions |
| Self-improvement via harness generalization | ❌ Not implemented | Genuinely net new for Poe |

### What to steal from AutoHarness
1. **Constraint harness layer** — validate agent actions before execution; reject/retry invalid tool calls without LLM re-prompting
2. **Structured iterative refinement loop** — N rounds of env-feedback → patch → re-validate before escalating
3. **Code-as-policy for stable decisions** — codify high-frequency, low-variance decisions (e.g., routing, formatting) as pure code, saving tokens

---

## Summary Table

| # | Idea | Source | Implementation Status | Roadmap Label | Priority |
|---|------|--------|-----------------------|---------------|----------|
| 1 | TF-IDF/embedding memory retrieval | DeerFlow | ✅ Shipped (`_tfidf_rank()` in `memory.py`; `_tfidf_skill_rank()` in `skills.py`) | Phase 35 P1 DONE | — |
| 2 | Constraint harness (action validation) | AutoHarness | ✅ Shipped (`src/constraint.py` — 5 pattern groups, pluggable registry, HIGH/MEDIUM gates) | Phase 35 P1 DONE | — |
| 3 | Parallelized sub-agent fan-out | DeerFlow | ✅ Shipped (`_steps_are_independent()` + `parallel_fan_out()` in `agent_loop.py`) | Phase 35 P1 DONE | — |
| 4 | Cost-aware model routing | Nanoclaws | ✅ Shipped (`classify_step_model()` in `poe.py` — per-step Haiku vs Sonnet, zero token cost) | Phase 35 P1 DONE | — |
| 5 | Machine-readable agent registry | Nanoclaws | ⚠️ Partial (AGENTS.md prose; no YAML manifest) | **on roadmap** (Phase 35 P2) | **P2** |
| 6 | Structured iterative refinement loop | AutoHarness | ⚠️ Partial (ad-hoc retry; no formal N-round contract) | **on roadmap** (Phase 35 P2) | **P2** |
| 7 | Reporter/synthesis agent role | DeerFlow | ⚠️ Partial (reporter hook type exists; no dedicated synthesis agent) | **on roadmap** (Phase 35 P2) | **P2** |
| 8 | Systematic HITL gating | DeerFlow | ⚠️ Partial (Telegram confirm for some actions; not declarative) | **on roadmap** (Phase 35 P2) | **P2** |
| 9 | Code-as-policy for stable decisions | AutoHarness | ⚠️ Partial (Phase 22 crystallization path: Skill→Rule is Stage 5, not yet shipped) | **on roadmap** (Phase 22 Stage 5) | **P3** |
| 10 | Capability-based task decomposition | Nanoclaws | ❌ Not implemented | **net new steal-worthy** | **P3** |

**Already implemented (no action needed):**
| Idea | Source | Where |
|------|--------|-------|
| Sub-agent delegation (coordinator → workers) | DeerFlow | `src/director.py`, `src/workers.py` (Phase 3) |
| State machine executor + retry logic | DeerFlow | `src/agent_loop.py`, `src/orchestrator/` (Phase 1) |
| Tiered authority levels | Nanoclaws | `AGENTS.md`, autonomy tiers (Phase 13) |
| Environment feedback loop | AutoHarness | Telegram error/retry, heartbeat recovery (Phases 4, 6) |
| Heartbeat / keep-alive loop | DeerFlow | `src/heartbeat.py` (Phase 4) |
| Basic model tier routing by role | Nanoclaws | `assign_model_by_role()` in `src/llm.py` (Phase 13) |
| Reporter hook (non-blocking synthesis) | DeerFlow | Hook type `reporter` in `src/hooks.py` (Phase 11) |
| TF-IDF routing for skills | DeerFlow | `src/router.py` (Phase 17) |
| Knowledge crystallization scaffold (Skill→Rule) | AutoHarness | `src/knowledge.py` Stages 1–4 (Phase 22) |

Legend: ✅ Done · ⚠️ Partial · ❌ Not implemented

---

## Prioritized Recommendations

### P1 — All shipped (March 2026)

**1. TF-IDF/embedding memory retrieval** ✅
`_tfidf_rank()` in `memory.py` (smooth IDF, cosine similarity, stdlib only). `_tfidf_skill_rank()` in `skills.py` as middle retrieval tier between trained router and raw keyword matching.

**2. Constraint harness layer** ✅
`src/constraint.py`: 5 pattern groups (destructive, secret, path_escape, unsafe_network, unsafe_exec). HIGH blocks, MEDIUM warns. Pluggable `CONSTRAINT_REGISTRY`. Fires in `agent_loop.py` before LLM call.

**3. Parallelized sub-agent fan-out** ✅
`_steps_are_independent(steps)` (word-boundary regex scan for inter-step refs) + `parallel_fan_out()` (ThreadPoolExecutor). Wired into `run_agent_loop()` when all steps are independent.

**4. Cost-aware model routing** ✅
`classify_step_model(step_text)` in `poe.py`. `_CHEAP_STEP_KEYWORDS` (retrieval/classify/format/verify) → Haiku; `_FORCE_MID_KEYWORDS` (synthesis/analysis/implement) → Sonnet. Per-step, zero token cost.

### P2 — Next sprint (infrastructure improvements)

**5. Machine-readable agent registry**
Convert `AGENTS.md` to a structured YAML manifest with capability tags, model preferences, cost tier, and authority level. Enables automated dispatch and audit trails.

**6. Structured iterative refinement loop**
Formalize the retry/feedback cycle: action → env response → patch → re-validate, up to N rounds before escalation. Currently ad-hoc; making it explicit reduces wasted Telegram round-trips.

**7. Reporter/synthesis agent**
Add a dedicated synthesis step at goal completion: agent reads all sub-agent outputs and produces a structured deliverable. Prevents final output being raw tool dumps.

**8. Systematic HITL gating**
Define a consistent taxonomy of action risk (read/write/destroy/external) and gate destructively-classified actions behind Telegram confirm. Make the policy declarative, not implicit.

### P3 — Future / research track

**9. Code-as-policy for stable decisions**
For high-frequency, low-variance decisions (e.g., task routing, priority scoring), synthesize a code function that encodes the learned policy. Eliminates LLM calls at inference time for those paths. Requires enough repetition to justify synthesis cost.

**10. Capability-based task decomposition**
Before dispatching a goal, run a structured decomposition step that maps subtasks to registered agent capabilities. More principled than current prompt-level delegation.

---

## Already Implemented (no action needed)

- Sub-agent delegation architecture (`claude_agent.py`, `openai_agent.py`)
- State machine executor with retry logic (`orchestrator/runner.py`)
- Heartbeat / keep-alive loop (80+ shell scripts in `~/.openclaw/workspace/scripts/`)
- Tiered authority levels (`AGENTS.md`)
- Basic environment feedback loop (Telegram error/retry flow)
- Thread ID tracking for session context

---

## Net New (not in current codebase at all)

1. Constraint harness / action validation layer
2. Code-as-policy synthesis
3. Self-improvement via harness generalization (AutoHarness §5 extension)
4. Parallelized sub-agent fan-out
5. Cost-aware model tier routing

These five represent the clearest gaps between current Poe capability and state-of-the-art agent architectures as of early 2026.
