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
| ~~**Polymarket CLI integration**~~ | polymarket-cli (OSS) | ✅ DONE — `src/polymarket.py`: wraps search/list/market/midpoint/price/history/trades as Python functions with structured JSON output. `polymarket_health_check()` wired into `poe-doctor`. 25 tests. (2026-04-05) | `src/polymarket.py` + `src/doctor.py` | S |
| ~~**verificationAgent as first-class agent**~~ | claw-code | ✅ DONE — `src/verification_agent.py`: `VerificationAgent` class with `verify_step()`, `adversarial_pass()`, `quality_review()`. step_exec.py delegates to it. `poe-verify` CLI. 21 tests. | `src/verification_agent.py` | M |

## NEXT — after stability sprint

| Item | Source | What | Where it lands | Effort |
|------|--------|------|----------------|--------|
| ~~**Hybrid retrieval (BM25 + vector + RRF)**~~ | Mimir | ✅ DONE — `src/hybrid_search.py` with BM25 + RRF. memory.py wired to use `hybrid_rank` (graceful fallback to TF-IDF). Vector deferred (needs embedding API). | `src/hybrid_search.py` + `src/memory.py` | M |
| ~~**Error nodes as queryable memory**~~ | Mimir | ✅ DONE — `find_relevant_failure_notes()` in introspect.py + wired into agent_loop.py `_build_decompose_context()`. Token-overlap matching, zero LLM cost. | `src/introspect.py` + `src/agent_loop.py` | M |
| ~~**Skills trigger arrays**~~ | oh-my-claudecode | ✅ DONE — `trigger_patterns` field already on Skill dataclass; keyword fallback in `find_matching_skills()` does exact substring matching. Functionally identical. | `src/skills.py` | — |
| ~~**Auto-resume on rate limits**~~ | oh-my-claudecode | Detect API rate limit, pause mission, poll, resume automatically. | ✅ DONE — multi-cycle polling retry in `ClaudeSubprocessAdapter.complete()`: 6 retries, exponential backoff 60→1800s cap, stops early on non-rate-limit errors. 5 tests. (2026-04-04) | `src/llm.py` | M→S |
| ~~**SlowUpdateScheduler**~~ | MetaClaw | ✅ DONE — heartbeat_loop() checks `is_drain_running()` before evolver/inspector/eval each tick. Heavy background work deferred when a mission is active. | `src/heartbeat.py` | M |
| ~~**Cron persistence (`jobs.json`)**~~ | 724-office | ✅ DONE — `src/scheduler.py` with JobStore backed by `memory/jobs.json`; once/daily/interval; `drain_due_jobs()` wired into heartbeat_loop(); `poe-schedule` CLI. | `src/scheduler.py` | M |
| ~~**`channels/` pluggable data sources**~~ | Agent-Reach | ✅ DONE — `src/channels.py`: `GitHubChannel` (search repos/code/issues), `RedditChannel` (posts/search), `YouTubeChannel` (transcript + oEmbed fallback). `ChannelResult` dataclass with `to_text()`/`to_json()`. `fetch_channel()` auto-dispatcher. `channels_health_check()` wired into `poe-doctor`. `github_search()`, `reddit_posts()`, `reddit_search()`, `youtube_transcript()` as agent-callable functions. 33 tests. (2026-04-05) | `src/channels.py` | M |
| ~~**`doctor` diagnostic command**~~ | Agent-Reach | ✅ DONE — `poe-doctor` CLI added (src/doctor.py). | `src/doctor.py` | S |

## LATER — when the need is real

| Item | Source | What | Where it lands | Effort |
|------|--------|------|----------------|--------|
| **Graph edges for lessons** | Mimir | `depends_on / supersedes / caused_by` relations between lessons. Multi-hop queries. | SQLite adjacency table in memory module | L |
| **Runtime tool creation** | 724-office | Agent writes and hot-loads new `@tool` functions during a mission. Needs sandboxing. | Deep integration — after sandbox hardening (Phase 18) is solid | L |
| ~~**Skill retrieval with stemmer**~~ | MetaClaw | ✅ DONE — `_stem()` in skills.py: suffix-stripping porter-style stemmer (no deps); applied in `_skill_tokens()` which feeds `_tfidf_skill_rank()`. "researching" → "research", "builder" → "build", etc. 8 new tests. (2026-04-05) | `src/skills.py:_stem`, `_skill_tokens` | S |
| ~~**Three-layer memory compression**~~ | 724-office | Session raw → LLM compress → TF-IDF retrieval. Handles memory bloat over long missions. | ✅ DONE — `CompressedBatch` dataclass; `compress_old_outcomes()` (LLM or heuristic fallback); `load_compressed_batches()`; `load_outcomes_with_context()` (3-layer: raw + compressed + TF-IDF rank); 11 tests. (2026-04-05) | `src/memory.py` | M |
| **MCP/plugin hot-reload** | 724-office / Mimir | JSON-RPC over stdio/HTTP with auto-reconnect. Hot-reload persona plugins without restart. | `src/orch_bridges.py` extension | M |
| ~~**Role-specific tool visibility**~~ | systematicls harness article | ✅ DONE — `EXECUTE_TOOLS_WORKER` (all), `EXECUTE_TOOLS_SHORT` (no schedule_run, used in factory_thin), `EXECUTE_TOOLS_INSPECTOR` (flag_stuck only) exported from step_exec.py. factory_thin now uses SHORT. | `src/step_exec.py` | S |
| ~~**Back-pressure lifecycle hooks**~~ | systematicls harness article | ✅ DONE — budget-aware landing now injects `_budget_reminder` into `_next_step_injected_context` at synthesis step so the agent knows it's under budget pressure. | `agent_loop.py` budget-aware landing | S |
| ~~**Subagent context firewall**~~ | systematicls harness article | Sub-loops get only a filtered summary of parent context. DONE in two layers: (1) `_context_firewall()` in handle.py for continuation tasks at depth ≥ 2; (2) `firewall_shared_ctx()` in team.py for TeamCreateTool sub-loops — relevance-ranks shared_ctx entries by task overlap, caps at max_entries/max_chars_per_entry. | ✅ DONE — `firewall_shared_ctx(task, shared_ctx, max_entries, max_chars_per_entry)` in `src/team.py`; wired into `create_team_worker()`; 5 tests. (2026-04-05) | `src/team.py` + `src/handle.py` | M |

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

---

## From X links research batch (2026-04-03)

Sources: @neural_avb (Meta-Harness), @ivanburazin (open-multi-agent), @_overment (dependency graph), @garrytan / @ryancarson (operational philosophy).

### Meta-Harness — Stanford paper `arxiv.org/abs/2603.28052`

"Everything in your AI system that is not the LLM itself is a harness." An agentic proposer (Claude Code) reads source code + execution traces of all prior harness candidates from a filesystem, then iteratively rewrites the harness. Results: +7.7 pts classification with 75% fewer tokens; found environment snapshot optimization unprompted.

| Item | What | Status | Where it lands | Effort |
|------|------|--------|----------------|--------|
| ~~**Proposer reads full execution traces**~~ | Don't pass summary metrics to the improver — give it raw traces, failed candidates, and source code. Much stronger than scalar reward. | ✅ DONE — `record_step_trace()` in memory.py; agent_loop writes traces; `_build_outcomes_summary` enriches stuck outcomes with step-level detail. 13 tests. (2026-04-04) | `src/memory.py`, `src/agent_loop.py`, `src/evolver.py` | M |
| ~~**Harness self-optimization loop**~~ | The scaffold (context assembly, memory formatting, tool selection) is itself code that an agent can rewrite and test. | ✅ DONE — `src/harness_optimizer.py`: reads stuck traces + prompt text, proposes word-level changes as evolver Suggestions; wired into heartbeat_loop every 50 ticks. 25 tests. (2026-04-04) | `src/harness_optimizer.py`, `src/heartbeat.py` | L |
| ~~**Environment snapshot caching**~~ | Cache world-state between steps so heartbeat tasks don't re-read the same files every tick. | ✅ DONE — after each done step, write summary to `_loop_shared_ctx["step:N:text"]`; team workers in later steps inherit snapshots via shared_ctx injection. (2026-04-04) | `src/agent_loop.py` | M→S |
| ~~**Skill text as steering**~~ | The description of what to optimize matters more than the optimizer architecture. Tight skill descriptions → better proposer outputs. | ✅ DONE — `optimization_objective: str = ""` field on Skill; wired into dict serialization, format_skills_for_prompt, compute_skill_hash, export_skill_as_markdown. 8 tests. (2026-04-04) | `src/skills.py`, `src/skill_loader.py` | S |

### open-multi-agent — `github.com/JackChen-me/open-multi-agent`

Claude Code architecture reverse-engineered into a standalone in-process multi-agent framework. Key: coordinator agent decomposes goal into dependency DAG at runtime; semaphore-gated AgentPool runs independent tasks in parallel; SharedMemory is team-level, not per-agent.

| Item | What | Status | Where it lands | Effort |
|------|------|--------|----------------|--------|
| ~~**Coordinator-as-agent for DAG decomposition**~~ | LLM agent that decomposes goals into a dependency task graph at runtime — not hardcoded plans. | ✅ DONE — `decompose_to_dag()` in planner.py: calls decompose(), runs parse_dependencies + build_execution_levels, returns (clean_steps, deps, levels, parallel_levels) ready for _run_steps_dag. (2026-04-04) | `src/planner.py` | M |
| ~~**Semaphore-based parallel pool with auto-unblock**~~ | Independent tasks run concurrently; tasks with unmet deps block and auto-unblock when deps complete. | ✅ DONE — `_run_steps_dag()` in agent_loop.py: dep-aware pool, tasks start as deps complete, dep results passed as completed_context. 12 tests. (2026-04-04) | `src/agent_loop.py` | M |
| ~~**Team-level SharedMemory**~~ | Persistent state store accessible to all agents in a team — eliminates redundant re-fetches. | ✅ DONE — `_loop_shared_ctx` dict initialized before fan-out; threaded through `_run_steps_parallel` → `execute_step` → `create_team_worker`; written back after each worker completes. 8 tests. (2026-04-04) | `src/agent_loop.py`, `src/step_exec.py`, `src/team.py` | S |
| ~~**Three execution modes**~~ | `runAgent` (one-shot) / `runTeam` (auto-plan + DAG) / `runTasks` (explicit pipeline). | ✅ DONE — `team:` prefix (parallel_fan_out=4 → _run_steps_dag), `pipeline:` prefix (preset_steps bypass decompose), `direct:` already done. 19 tests. (2026-04-04) | `src/handle.py`, `src/agent_loop.py` | S |
| **In-process execution vs. subprocess spawning** | No PTY/subprocess overhead; can deploy serverless or in Docker without shell access. | LATER | Architecture shift — deferred until Poe needs containerized deployment | L |

### @_overment — Dynamic dependency graph + triadic heartbeat

776 bookmarks, 90K views. Architecture: dynamic (mutable) dependency graph + heartbeat involving LLM + code + events. The graph mutates during execution; events are first-class nodes that can unblock tasks or inject new branches.

| Item | What | Status | Where it lands | Effort |
|------|------|--------|----------------|--------|
| ~~**Mutable task graph**~~ | Static DAGs break when reality diverges from plan. Tasks can spawn new tasks and update deps mid-execution. | ✅ DONE — `inject_steps` array field on `complete_step` tool; serial and parallel agent_loop both prepend injected steps to remaining_steps. (2026-04-04) | `src/step_exec.py`, `src/agent_loop.py` | M |
| ~~**Event-reactive heartbeat (LLM + code + events)**~~ | Heartbeat isn't just a timer — external events (Telegram, file change, API response) trigger the reasoning cycle. | ✅ DONE — `post_heartbeat_event()` in heartbeat.py; `_wakeup_event` replaces `time.sleep(interval)`; `InterruptQueue.post()` calls it automatically after posting. 5 tests. (2026-04-05) | `src/heartbeat.py`, `src/interrupt.py` | M |
| ~~**Events as first-class graph nodes**~~ | An incoming message is an event node that can unblock a waiting task or inject a branch — not just interrupt the loop. | ✅ DONE — `TypedEvent` dataclass + `EventRouter` singleton (`get_event_router()`, `post_typed_event()`, `wait_for(kind, timeout)`); `await:<kind>[timeout=Ns]` step shorthand intercepted in `execute_step` before LLM call — blocks DAG step until matching event arrives, returns payload as step result; `post_heartbeat_event()` now also fires typed events so Telegram/Slack/API signals unblock waiting steps; 26 tests. (2026-04-05) | `src/interrupt.py` + `src/step_exec.py` + `src/heartbeat.py` | L |

### @garrytan / @ryancarson — Operational philosophy (not architecture)

Garry Tan quoting Ryan Carson's "How to turn your OpenClaw into the world's best assistant." Both are using Claude Code as an autonomous EA/chief-of-staff — proactive, memory-backed, runs without hand-holding. Garry also previewed "GStack for Openclaw" (upcoming integration layer). No code to steal, but the framing matters:

> Poe is already building toward this pattern. The north star posture — "a chief of staff that wakes up with tasks and executes without prompting" — validates the architecture direction. Worth reading the full Ryan Carson article when available.

---

## From GStack research (2026-04-04)

Source: Garry Tan (@garrytan) — "GStack for OpenClaw" integration layer preview. Tier 1 items are framing/judgment-quality improvements, not new capabilities.

| Item | What | Status | Where it lands | Effort |
|------|------|--------|----------------|--------|
| ~~**Mechanical/Taste/User Challenge taxonomy**~~ | Decision classifier for escalation: Mechanical (clear rules) → automate; Taste (preferences/aesthetics) → surface; User Challenge (challenges user agency) → always surface | ✅ DONE — `EscalationDecision.decision_class`; `user_challenge` forces surface regardless of LLM action. (2026-04-04) | `src/director.py` | S |
| ~~**Confidence-gated escalation (1–10)**~~ | LLM rates its own decision confidence; low confidence → force surface with `[Low confidence]` prefix; medium → caveat prepended | ✅ DONE — `EscalationDecision.confidence`; thresholds at 5 and 7. (2026-04-04) | `src/director.py` | S |
| ~~**Anti-sycophancy rules**~~ | Explicit instructions in escalation system prompt to resist agreeing with user momentum; commit to taxonomy-based classification | ✅ DONE — injected into `_ESCALATION_SYSTEM`. (2026-04-04) | `src/director.py` | XS |
| ~~**Calibration logging**~~ | Append decision_class + confidence + action + reasoning to `memory/calibration.jsonl` for retrospective calibration review | ✅ DONE — written on every `handle_escalation()` call (non-dry-run). (2026-04-04) | `src/director.py` → `memory/calibration.jsonl` | XS |
| ~~**Tier 2: Calibration review loop**~~ | Periodic scan of calibration.jsonl to detect systematic over/under-confidence by decision class; suggest prompt adjustments | ✅ DONE — `scan_calibration_log()` in evolver.py; wired into `run_evolver(scan_calibration=True)`. 10 tests. (2026-04-04) | `src/evolver.py` | S |

---

## From FunSearch/EUREKA/Voyager research (2026-04-05)

Source: DeepMind FunSearch (Nature 2023), NVIDIA EUREKA, MineDojo Voyager. Full synthesis: `docs/research/funsearch-agent-design.md`.

| Item | What | Status | Where it lands | Effort |
|------|------|--------|----------------|--------|
| ~~**Island model diversity**~~ | Partition skill pool into 4 named islands (research/build/analysis/general) by keyword scoring. Cull bottom half of open-circuit skills per-island on every evolver run. Prevents monoculture convergence. | ✅ DONE — `assign_island()`, `get_skills_by_island()`, `cull_island_bottom_half()`, `run_island_cycle()` in `src/skills.py`; wired into `run_evolver()`. 11 tests. (2026-04-05) | `src/skills.py` + `src/evolver.py` | M |
| ~~**Replay-based fitness oracle**~~ | Replace LLM-mediated inspector scoring with a deterministic replay scorer that runs candidate strategies against past outcomes from `outcomes.jsonl` via Phase 50 thinkback — no LLM in the eval path. | ✅ DONE — `src/strategy_evaluator.py`: `evaluate_strategy()` (TF-IDF cosine over outcomes, similarity-weighted pass-rate), `evaluate_skill()`, `evaluate_suggestion()` wrappers; wired into `run_skill_maintenance()` frontier rewrite loop (PASS verdict skips unnecessary rewrite); 35 tests. (2026-04-05) | `src/strategy_evaluator.py` + `src/evolver.py` | L |
| ~~**Failure-chain lesson recording**~~ | Agent0 steal: store full failure→diagnosis→recovery chain on every Outcome. Every retry is a training signal. `Outcome.failure_chain: List[str]` + `Outcome.recovery_steps: int`. | ✅ DONE — `record_outcome()` + `reflect_and_record()` accept `failure_chain`/`recovery_steps`; `run_agent_loop()` accumulates chain on retry/split/terminal. (2026-04-05) | `src/memory.py` + `src/agent_loop.py` | S |
| ~~**Majority-vote pseudo-labels**~~ | Agent0 steal: multi-sample lesson extraction where only lessons agreed on by majority of k samples are promoted. Eliminates false lessons from noisy single-attempt outcomes. | ✅ DONE — `majority_vote_lessons(all_samples, threshold)` + `extract_lessons_via_llm(k_samples=N)`; `_jaccard_similarity()` for lesson matching; 7 tests. (2026-04-05) | `src/memory.py` | M |
| ~~**Frontier task targeting**~~ | Agent0 steal: `frontier_skills()` returns skills in 40–70% utility zone (challenging but not circuit-broken). Evolver preferentially rewrites these (max 2/cycle) alongside open-circuit skills. | ✅ DONE — `frontier_skills(skills, min_uses)` + `FRONTIER_LOW/HIGH` constants in `src/skills.py`; wired into `run_evolver_with_friction()` after open-circuit scan. 5 tests. (2026-04-05) | `src/skills.py` + `src/evolver.py` | M |
| ~~**Skill validation harness**~~ | Agent0/Voyager steal: LLM quality gate on promotion + up to N repair-rewrite cycles. Only promote skills that pass; skills that fail max attempts stay provisional. | ✅ DONE — `validate_skill_for_promotion()` (fail-open LLM gate); `maybe_auto_promote_skills(adapter, max_repair_attempts)` with repair loop via `evolver.rewrite_skill`; 8 tests. (2026-04-05) | `src/skills.py` | M |
| ~~**Score-weighted mutation context**~~ | When prompting LLM to improve a skill, feed top-K ranked variants as version-tagged context ("v0 score=4, v1 score=7 — generate v2"), not flat outcome summary. | ✅ DONE — `_top_peer_skills()` + peer_context block in `rewrite_skill()` prompt. Filters open-circuit skills, sorts by compactness-adjusted score. (2026-04-05) | `src/evolver.py` | S |
| ~~**Brevity penalty in skill scoring**~~ | `adjusted_score = utility_score / log(1 + char_count/200)` at sampling time. Prevents prompt bloat and complexity drift in evolved skills. | ✅ DONE — `_compactness_adjusted_score()` used by `_top_peer_skills()` for peer selection. `utility_score` EMA unchanged. (2026-04-05) | `src/evolver.py` | S |
| ~~**Pre-scoring discard gate**~~ | Any candidate strategy that fails a deterministic sanity check must be silently discarded before reaching LLM evaluator. | ✅ DONE — `rewrite_skill()` now validates before saving: empty steps/desc → discard; desc >400 chars → discard; >10 steps → discard; empty triggers → inherit. Silent discard (no LLM call wasted scoring bad outputs). (2026-04-05) | `src/evolver.py:rewrite_skill` | S |
| ~~**Rule A/B variants**~~ | Agent0 steal: generate competing skill variants; route tasks via hash; retire loser after eval window. Prevents evolver from blindly replacing working skills with unvalidated rewrites. | ✅ DONE — `create_skill_variant()`, `get_skill_variants()`, `select_variant_for_task()` (hash-based 50/50 routing), `record_variant_outcome()`, `retire_losing_variants()` in `src/skills.py`; frontier rewrites now create challengers instead of immediate replacements; `retire_losing_variants()` wired into `run_skill_maintenance()`; `Skill.variant_of/wins/losses` fields; 25 tests. (2026-04-05) | `src/skills.py` + `src/evolver.py` | H→M |

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
| [@garrytan (GStack)](https://x.com/garrytan) | — | GStack for OpenClaw — judgment-quality Tier 1 improvements | Mechanical/Taste/User Challenge taxonomy, confidence gates, anti-sycophancy, calibration logging |
| [@tom_doerr (724-office)](https://x.com/tom_doerr) | — | Single-agent tool-use loop with persistent memory | (previously noted; confirmed via this batch) |
| [@teknium](https://x.com/teknium) | — | LLM research insights | (ingested; no immediate steal candidate) |
