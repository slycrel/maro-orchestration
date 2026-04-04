# Steal List

Patterns worth lifting from other projects, mapped to where they land in openclaw-orchestration.
Compiled from Grok research sessions + prototype experiments (2026-03-29).

---

## NOW — do these next

### From TradingAgents (dogfood research run, 2026-03-30)

| Item | What | Where it lands | Effort |
|------|------|----------------|--------|
| ~~**Commitment-forced verdicts**~~ | ✅ DONE — inspector.py system prompt ends with VERDICT: PROCEED/RETRY/ABORT. | `src/inspector.py` | S |
| ~~**Pre-plan challenger**~~ | ✅ DONE — _challenge_spec() added to director.py, fires after _produce_spec. | `src/director.py` | S |
| ~~**Two-tier model routing**~~ | ✅ DONE — `classify_step_model()` in poe.py, wired into agent_loop.py serial and parallel loops. Tier aliases trigger it; explicit model strings bypass it. | `src/poe.py` + `src/agent_loop.py` | — |

###

| Item | Source | What | Where it lands | Effort |
|------|--------|------|----------------|--------|
| ~~**Verification patterns on rules**~~ | meta_alchemist / oh-my-claudecode | ✅ DONE — `verify_pattern` shell command added to all 8 `_GRADUATION_TEMPLATES` entries. Written into every graduation suggestion. `verify_graduation_rules()` runs all patterns; `poe-graduation --verify` reports pass/fail. | `src/graduation.py` | M |
| ~~**Skeptic persona modifier**~~ | Grok advice | ✅ DONE — apply_skeptic_modifier() in persona.py; trigger with "skeptic:" prefix or "--skeptic" in goal. | `src/persona.py`, `src/poe.py` | S |
| ~~**Post-mission notification**~~ | oh-my-claudecode | ✅ DONE — fires at end of _finalize_loop() in agent_loop.py. | `src/agent_loop.py` | S |
| **Polymarket CLI integration** | polymarket-cli (OSS) | Read-only market/position/leaderboard data without wallet. Perfect for research personas. | Researcher persona spec + `src/web_fetch.py` or new `tools/polymarket.py` | S |
| ~~**verificationAgent as first-class agent**~~ | claw-code | ✅ DONE — `src/verification_agent.py`: `VerificationAgent` class with `verify_step()`, `adversarial_pass()`, `quality_review()`. step_exec.py delegates to it. `poe-verify` CLI. 21 tests. | `src/verification_agent.py` | M |

## NEXT — after stability sprint

| Item | Source | What | Where it lands | Effort |
|------|--------|------|----------------|--------|
| ~~**Hybrid retrieval (BM25 + vector + RRF)**~~ | Mimir | ✅ DONE — `src/hybrid_search.py` with BM25 + RRF. memory.py wired to use `hybrid_rank` (graceful fallback to TF-IDF). Vector deferred (needs embedding API). | `src/hybrid_search.py` + `src/memory.py` | M |
| ~~**Error nodes as queryable memory**~~ | Mimir | ✅ DONE — `find_relevant_failure_notes()` in introspect.py + wired into agent_loop.py `_build_decompose_context()`. Token-overlap matching, zero LLM cost. | `src/introspect.py` + `src/agent_loop.py` | M |
| ~~**Skills trigger arrays**~~ | oh-my-claudecode | ✅ DONE — `trigger_patterns` field already on Skill dataclass; keyword fallback in `find_matching_skills()` does exact substring matching. Functionally identical. | `src/skills.py` | — |
| **Auto-resume on rate limits** | oh-my-claudecode | Detect API rate limit, pause mission, poll, resume automatically. | `src/heartbeat.py` or `src/sheriff.py` — extend with rate-limit watch | M |
| ~~**SlowUpdateScheduler**~~ | MetaClaw | ✅ DONE — heartbeat_loop() checks `is_drain_running()` before evolver/inspector/eval each tick. Heavy background work deferred when a mission is active. | `src/heartbeat.py` | M |
| ~~**Cron persistence (`jobs.json`)**~~ | 724-office | ✅ DONE — `src/scheduler.py` with JobStore backed by `memory/jobs.json`; once/daily/interval; `drain_due_jobs()` wired into heartbeat_loop(); `poe-schedule` CLI. | `src/scheduler.py` | M |
| **`channels/` pluggable data sources** | Agent-Reach | Each platform (X, Reddit, YouTube, GitHub) is a separate module with standard interface. | New `src/channels/` alongside `web_fetch.py` | M |
| ~~**`doctor` diagnostic command**~~ | Agent-Reach | ✅ DONE — `poe-doctor` CLI added (src/doctor.py). | `src/doctor.py` | S |

## LATER — when the need is real

| Item | Source | What | Where it lands | Effort |
|------|--------|------|----------------|--------|
| **Graph edges for lessons** | Mimir | `depends_on / supersedes / caused_by` relations between lessons. Multi-hop queries. | SQLite adjacency table in memory module | L |
| **Runtime tool creation** | 724-office | Agent writes and hot-loads new `@tool` functions during a mission. Needs sandboxing. | Deep integration — after sandbox hardening (Phase 18) is solid | L |
| **Skill retrieval with stemmer** | MetaClaw | Lightweight skill matching without embeddings. | Skills layer when needed | S |
| **Three-layer memory compression** | 724-office | Session -> LLM compress -> vector retrieval. Handles memory bloat over long missions. | `src/memory.py` — compress on eviction or every N heartbeats | M |
| **MCP/plugin hot-reload** | 724-office / Mimir | JSON-RPC over stdio/HTTP with auto-reconnect. Hot-reload persona plugins without restart. | `src/orch_bridges.py` extension | M |
| ~~**Role-specific tool visibility**~~ | systematicls harness article | ✅ DONE — `EXECUTE_TOOLS_WORKER` (all), `EXECUTE_TOOLS_SHORT` (no schedule_run, used in factory_thin), `EXECUTE_TOOLS_INSPECTOR` (flag_stuck only) exported from step_exec.py. factory_thin now uses SHORT. | `src/step_exec.py` | S |
| ~~**Back-pressure lifecycle hooks**~~ | systematicls harness article | ✅ DONE — budget-aware landing now injects `_budget_reminder` into `_next_step_injected_context` at synthesis step so the agent knows it's under budget pressure. | `agent_loop.py` budget-aware landing | S |
| **Subagent context firewall** | systematicls harness article | Sub-loops get only a filtered summary of parent context — prevents context contamination and reduces token cost on deep delegations. Complements TeamCreateTool. | New `context_firewall.py` or adapter wrapper | M |

---

## From X links research batch (2026-04-01)

Ingested via orchestration loop from @tom_doerr, @teknium, @slash1sol, @pawelhuryn, @k1rallik threads.

| Item | Source | What | Status | Where it lands | Effort |
|------|--------|------|--------|----------------|--------|
| ~~**lat.md knowledge graph**~~ | @k1rallik | ✅ DONE — 9 concept nodes in `lat.md/`, `[[wiki links]]`, `lat check` CI. Replaces flat AGENTS.md for orchestration concepts. | DONE | `lat.md/` | M |
| ~~**Three-tier promotion cycle**~~ | @pawelhuryn | ✅ DONE — observation → hypothesis (2+ confirmations) → standing rule. `observe_pattern()`, `contradict_pattern()`, `inject_standing_rules()` in memory.py. Standing rules injected into every decompose call. | DONE | `src/memory.py` | M |
| ~~**Decision journal**~~ | @pawelhuryn | ✅ DONE — `record_decision()`, `search_decisions()`, `inject_decisions()` in memory.py. TF-IDF search of prior decisions before new planning. | DONE | `src/memory.py` | S |
| **Tool registry + progressive disclosure** | @k1rallik | Gate tools at prompt composition time; progressive skill disclosure (stub in prompt, full on invoke); `PermissionContext` deny patterns. Phase 41. Design complete (`research/PHASE41_TOOL_REGISTRY_DESIGN.md`). | DESIGN COMPLETE | `src/step_exec.py` → `ToolRegistry`, `PermissionContext`, `SkillLoader` | L |
| **Polymarket BTC lag claim** | @slash1sol | UNCONFIRMED — structural analysis: binary YES/NO contracts, 4% round-trip fees vs 0.3% claimed edge = negative EV. See `research/POLYMARKET_BTC_LAG_VALIDATION.md`. | INVALIDATED | — | — |

---

## From Pi coding agent research (2026-04-03)

Source: Mario Zechner (@badlogicgames) — "I Hated Every Coding Agent, So I Built My Own"
Flagged by @dexhorthy. Full synthesis: `research/PI_CODING_AGENT_SYNTHESIS.md`.

| Item | What | Status | Where it lands | Effort |
|------|------|--------|----------------|--------|
| ~~**System prompt token audit**~~ | Measure + cut EXECUTE_SYSTEM + DECOMPOSE_SYSTEM token overhead. Pi: <1k combined. | ✅ DONE — 1892→936 tokens (-51%). `src/step_exec.py` + `src/planner.py`. | `src/step_exec.py`, `src/planner.py` | S |
| ~~**Architecture non-goals doc**~~ | Document what Poe deliberately doesn't do — prevents scope creep. | ✅ DONE — `docs/ARCHITECTURE_NON_GOALS.md`, 8 non-goals with rationale and revisit conditions. | `docs/ARCHITECTURE_NON_GOALS.md` | XS |
| ~~**Runtime tool extension**~~ | Agent generates + registers new ToolDefinition at runtime. Self-extending system. | ✅ DONE — `src/runtime_tools.py`; `register_tool` in EXECUTE_TOOLS (WORKER only); `_resolve_tools()` per-step in agent_loop. (2026-04-03) | `src/runtime_tools.py`, `src/step_exec.py`, `src/agent_loop.py` | M |
| ~~**Human-readable session export**~~ | `poe export` produces markdown summary of a completed loop (steps, results, context). | ✅ DONE — `export_human()` in checkpoint.py; `poe-checkpoint export <loop_id>`. (2026-04-03) | `src/checkpoint.py` | S |
| ~~**Session branching**~~ | Checkpoint creates branch instead of overwriting — enables experimental paths without consuming main context. | ✅ DONE — `branch_checkpoint()` in checkpoint.py; `parent_loop_id` field; `poe-checkpoint branch <loop_id>`. (2026-04-03) | `src/checkpoint.py` | M |

**What NOT to steal:** 4-tool minimalism (wrong use case — Poe is an orchestrator, not a coding REPL), TypeScript monorepo, flicker-free terminal UI.

---

## Sources

| Repo | Stars | What it is | Key insight |
|------|-------|-----------|-------------|
| [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) | 14.5k | Claude Code plugin — teams-first orchestration | Magic keywords, ralph verify loop, skill auto-injection |
| [724-office](https://x.com/tom_doerr) | young | Single-agent tool-use loop — memory + self-repair | Three-layer memory, runtime tool creation, cron persistence |
| [Mimir](https://github.com/orneryd/Mimir) | 256 | MCP memory server — graph + hybrid retrieval | BM25+RRF reranking, multi-hop fact consolidation, error nodes |
| [Agent-Reach](https://github.com/Panniantong/Agent-Reach) | 12.7k | CLI scaffolding for AI agent internet access | `channels/` architecture, `doctor` command, Jina Reader |
| [MetaClaw](https://github.com/aiming-lab/MetaClaw) | — | RL-based agent framework | SlowUpdateScheduler, skill retrieval (WARNING: hardcoded API key) |
| [ClawTeam](https://github.com/HKUDS/ClawTeam) | — | Multi-agent framework | FileTaskStore (DONE — ported as `src/task_store.py`) |
| meta_alchemist | — | Self-evolving Claude Code framework | Verification-based rule promotion, session hooks |
| [systematicls harness article](x.com/systematicls) | — | Agent harness engineering patterns from LangChain agents/evals | Role-specific tool visibility, back-pressure hooks, subagent context firewall, dual-memory validation |
| Grok (external reviewer) | — | Independent code review of openclaw-orchestration | Skeptic prompting, stability sprint advice, dashboard-as-real-tool |
| [@k1rallik](https://x.com/k1rallik) | — | Claude Code architecture reverse-engineering — lat.md knowledge graph + tool registry design | Tool registry at prompt-composition time, progressive skill disclosure, lat.md cross-links |
| [@pawelhuryn](https://x.com/pawelhuryn) | — | Self-improving agent memory patterns | Three-tier promotion cycle, decision journal, contradict/confirm dynamics |
| [@tom_doerr (724-office)](https://x.com/tom_doerr) | — | Single-agent tool-use loop with persistent memory | (previously noted; confirmed via this batch) |
| [@teknium](https://x.com/teknium) | — | LLM research insights | (ingested; no immediate steal candidate) |
