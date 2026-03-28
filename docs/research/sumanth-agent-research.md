# Memento-Skills: Self-Evolving Agent Framework

**Source:** [@Sumanth_077 on X](https://x.com/sumanth_077/status/2036807382135943244)
**Date:** March 25, 2026
**Views:** 50.1K | Reposts: 117 | Likes: 474 | Replies: 29
**Repo:** Linked in tweet comments (not fetched — check @Sumanth_077 replies)

---

## Summary

Sumanth_077 introduces **Memento-Skills**, an open-source agent framework where skills are not static artifacts but living code that the agent rewrites through experience. The core thesis: *most frameworks treat skills as write-once, fail-forever*. When a skill breaks, a human debugs it. Memento-Skills closes that loop — the agent reflects on failures, rewrites the broken skill, and persists the improved version.

The system runs a continuous **Read → Execute → Reflect → Write** loop:

- **Read:** Retrieve relevant skills from a local library (not dump everything into context)
- **Execute:** Run skills in a sandboxed environment with real tool calling (files, web, scripts, external APIs)
- **Reflect:** On failure, record what broke, update the skill's utility score, attribute failure to specific skills
- **Write:** Rewrite broken skills, strengthen weak ones, or synthesize new skills when none exist

Benchmarked on **HLE** (Humanity's Last Exam) and **GAIA** (General AI Assistants). Performance improved across learning rounds as the library evolved from atomic primitives into richer learned capabilities.

Built for open-source LLMs: Kimi, MiniMax, GLM, any OpenAI-compatible endpoint.
Ships with 9 built-in skills: filesystem, web-search, PDF, docx, xlsx, pptx, image analysis, skill-creator, dependency install.

---

## Extracted Architectural Ideas

### 1. Self-Modifying Skill Library
Skills are versioned, rewritable code objects — not static prompts or frozen functions. The library is the agent's accumulated competence. High relevance to Poe: current skill/tool definitions are static.

### 2. Read → Execute → Reflect → Write Loop
A tight 4-phase agentic loop that is both execution and learning. The "Reflect" and "Write" phases are the novel addition over standard ReAct/tool-use patterns. Maps naturally onto Poe's heartbeat loop.

### 3. Failure Attribution
When execution fails, the system attributes the failure to specific skills rather than the task or the LLM. This enables targeted rewrites instead of blanket retries. Analogous to root-cause isolation in engineering.

### 4. Utility Scoring
Each skill carries a utility score updated on success/failure. Low-utility skills get deprioritized or rewritten. High-utility skills get retrieved preferentially. Simple but powerful signal for skill lifecycle management.

### 5. Selective Context Loading
Skills are retrieved (RAG-style) rather than loaded wholesale into context. Addresses the "context stuffing" anti-pattern. Poe's current approach of loading all available tools is a liability at scale.

### 6. Sandboxed Execution
Skills run in an isolated local sandbox with actual tool invocations. Prevents skill failures from contaminating the agent state. Important safety property for autonomous operation.

### 7. Emergent Skill Synthesis
When no existing skill covers a task, the agent creates a new one. The skill-creator is itself a built-in skill — meta-programming the skill library. This is the "Let Agents Design Agents" headline claim.

### 8. Benchmark-Driven Evaluation
Uses HLE and GAIA as objective improvement signals across learning rounds. Provides a principled way to measure whether the skill library is actually getting better. Poe currently lacks systematic self-evaluation.

### 9. Open-Source LLM Compatibility
Works with any OpenAI-compatible endpoint. Not locked to GPT-4. Directly relevant given Poe's OpenRouter routing layer.

---

## Relevance to OpenClaw / Poe

| Idea | Poe Status | Gap / Opportunity |
|------|-----------|-------------------|
| Skill self-rewriting | ✅ Phase 32: `rewrite_skill()` in `evolver.py` — circuit-breaker gated | Circuit breaker: 3+ consecutive failures → OPEN → LLM rewrites skill body → HALF_OPEN (probationary) |
| Failure attribution | ✅ Phase 32: `attribute_failure_to_skills()` in `skills.py` | Wired into `agent_loop.py` on step blocked |
| Utility scoring | ✅ Phase 32: EMA utility_score (alpha=0.3) in `skills.py` | Updated on success ↑ / failure ↓; gates promotion/demotion/rewrite |
| Selective skill retrieval | ✅ Phase 32: `_tfidf_skill_rank()` in `skills.py` | Middle tier between trained router and keyword fallback; smooth IDF, cosine sim |
| Skill synthesis | ❌ Phase 32 pending | skill-creator skill could bootstrap Poe's own capabilities |
| Sandboxed execution | ✅ Phase 15+18: `src/sandbox.py` hardened | Resource limits, network isolation, audit log, static analysis |
| Benchmark loop | ⚠️ Partial: eval harness + pass@k / pass^k in `metrics.py` | No continuous HLE/GAIA benchmark; eval is goal-specific not library-level |

**Priority steal (remaining):** The **Skill synthesis** (creating new skills when none exist) is the only unshipped Memento-Skills idea. The circuit-breaker + rewrite cycle addresses the Reflect+Write phases. The read-only gap: continuous benchmark evaluation of the skill library's improving capability over time.

---

## Open Questions

1. **How does Memento-Skills handle skill versioning?** Is there a rollback mechanism if a rewrite degrades performance?
2. **What is the skill representation format?** Python functions, LLM-generated code strings, structured JSON descriptions?
3. **How is the utility score computed?** Binary success/fail, weighted by task difficulty, or something else?
4. **Does skill synthesis create runnable code or prompt templates?** The distinction matters for sandboxed execution safety.
5. **Is there a skill pruning mechanism?** Libraries that only grow become retrieval problems — does low utility eventually trigger deletion?
6. **How does it handle skills with external dependencies?** The dependency-install built-in suggests dynamic package installs — what are the security boundaries?
7. **Benchmark results specifics:** What was the baseline vs. peak HLE/GAIA score, and over how many rounds?

---

## Next Actions

- [x] Implement skill self-rewriting with circuit breaker (Phase 32 — `rewrite_skill()`, `run_skill_maintenance()`)
- [x] Implement failure attribution (Phase 32 — `attribute_failure_to_skills()`, wired into agent_loop)
- [x] Implement utility scoring (Phase 32 — EMA utility_score)
- [x] Implement selective skill retrieval (Phase 32 — `_tfidf_skill_rank()`)
- [ ] Fetch the repo URL from @Sumanth_077's reply thread — verify skill representation format
- [ ] Implement skill synthesis (Phase 32 pending — create provisional skill when no match found)
- [ ] Define library-level benchmark evaluation (continuous HLE/GAIA-style eval over skill library rounds)
