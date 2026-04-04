# Pi Coding Agent — Synthesis for Poe Orchestration

**Source:** Talk by Mario Zechner (@badlogicgames) — "I Hated Every Coding Agent, So I Built My Own"
**Flagged by:** @dexhorthy (2026-04-03) as "very relevant for coding agents and tasteful software"
**Mario:** Creator of libGDX, 17-year OSS contributor. Pi became the foundation for OpenClaw (160K+ GitHub stars).

---

## Why He Hated Existing Agents

1. **Hidden complexity** — Tools "inject stuff behind your back that isn't surfaced in the UI." System prompts and tool definitions change between releases, breaking workflows.
2. **Zero sub-agent visibility** — When agents spawn sub-agents, "you have zero visibility into what that sub-agent does." (Sound familiar? This is why Poe has inspector + thinkback.)
3. **Feature bloat** — Claude Code became "a spaceship with 80% of functionality I have no use for."
4. **Self-hosted incompatibility** — Vercel AI SDK "doesn't play nice with self-hosted models" for tool calling.
5. **Flickering UI** — Cosmetic but signals lack of care.

---

## Pi's Core Philosophy

**YAGNI: "If I don't need it, it won't be built."**

The four essential tools:
- **read** — files, images, directories, globs, line ranges
- **write** — file creation with auto parent dirs
- **edit** — precise string search/replace
- **bash** — synchronous shell execution

Everything else — ripgrep, GitHub CLI, file searching, web fetching — goes through bash. Frontier models know how to use CLI tools.

**System prompt under 1,000 tokens** (combined tools). Claude Code uses 10,000+.

---

## Design Decisions Poe Should Steal

### 1. Radical context efficiency

Pi fits entire system prompt + tools in <1k tokens. Poe's EXECUTE_SYSTEM is already well-structured but could be audited against this standard. Key principle: every token in the system prompt competes with context for the actual work.

**Steal:** Run a token audit on EXECUTE_SYSTEM + DECOMPOSE_SYSTEM. Any sentence that doesn't change model behavior should go.

### 2. Explicit omissions as features

Pi deliberately documents what it DOESN'T do: no MCP, no sub-agents at launch, no permission popups, no plan mode. This is the Bitter Lesson applied to tooling.

**Steal:** Add an "Architecture non-goals" section to docs. Helps Jeremy say no to scope creep.

### 3. Self-extending agents

Rather than downloading extensions, you ask the agent to extend itself. This is recursive self-improvement applied to capability acquisition.

**Steal:** Poe's `evolver.py` does this for prompt/memory improvement. Not yet for tool extension. The path: `step_exec.py` generates a new tool definition + registers it in `tool_registry.py` at runtime. Viable given Phase 41 infrastructure.

### 4. File-based transparent state

Sessions in Pi serialize to AGENTS.md, PLAN.md — plain text visible to the user, enabling alternative UIs and post-processing.

**Steal:** Poe already has `memory/` JSONL. The gap is a human-readable session export. `poe-checkpoint` shows the structure; needs a `poe export --human` format. Low priority but good for debuggability.

### 5. Session trees for branching

Sessions form trees — side quests don't consume main context. Avoids the "one long context" trap.

**Steal:** Poe's `checkpoint.py` + `loop_id` ancestry is the foundation. The missing piece is branching vs. always resuming a single thread. Would enable "try this approach without committing" patterns. Medium priority.

### 6. Model-agnostic design

Pi sessions serialize fully across LLM providers. Poe's `llm.py` already has multi-provider support but doesn't guarantee session portability.

**Not immediately actionable** — Poe's `llm.py` already handles this reasonably well.

---

## Steal Candidates (Ordered by Value)

| Item | What | Effort | Priority |
|------|------|--------|----------|
| Token audit on system prompts | Measure + reduce token overhead in EXECUTE_SYSTEM/DECOMPOSE_SYSTEM. Target: 20%+ reduction. | Low | NOW |
| Architecture non-goals doc | Document what Poe deliberately doesn't do. Prevents scope creep. | Very low | NOW |
| Runtime tool extension | Agent generates + registers new ToolDefinition at runtime via Phase 41 infrastructure. | Medium | NEXT |
| Human-readable session export | `poe export` produces markdown summary of a completed loop (steps, results, context). | Low | NEXT |
| Session branching | Checkpoint creates branch instead of overwriting — enables experimental paths. | High | LATER |

---

## What NOT to Steal

- **Four-tool minimalism** — Poe deliberately has more tools (schedule_run, create_team_worker, flag_stuck). These are correct for an autonomous multi-day orchestrator, not a coding assistant. Pi's minimal set fits its use case; Poe's set fits its use case.
- **TypeScript monorepo structure** — Irrelevant for Python.
- **Flicker-free terminal UI** — Poe is headless; not applicable.

---

## Verdict

Pi validates Poe's core architecture choices:
- Transparent state (memory/ JSONL) ✓
- Multi-provider LLM abstraction ✓
- Structured self-improvement (evolver) ✓
- Tool composition over feature bundling ✓

The novel additions are **token efficiency focus** and **runtime tool extension**. The former is immediately actionable; the latter builds naturally on Phase 41.

Mario's key insight: "Very freeing to build your own coding agent. Whatever I need is just a bit of coding away and works exactly how I want it to work without compromise." This is exactly where Poe's custom orchestration beats generic tools.

**Date researched:** 2026-04-03
