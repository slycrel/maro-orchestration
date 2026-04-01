# Sources — Inspiration Log

Canonical log of every external source we've drawn from: repos, papers, people, articles, and feedback sessions. Ordered roughly by when they entered the project.

For each source: what it is, what we took from it, and where it landed in the codebase.

Last updated: 2026-03-31 (expanded from session logs, commit history, Telegram archive)

---

## Repos

### oh-my-claudecode
- **URL:** github.com/Yeachan-Heo/oh-my-claudecode
- **Stars:** ~14.5k
- **What it is:** Claude Code plugin with teams-first orchestration, magic keyword routing, and a "ralph verify" loop.
- **What we took:**
  - Magic keyword prefix pattern (`ralph:`, `pipeline:`, `verify:`) → `effort:`, `mode:thin`, `ultraplan:`, `btw:`, `skeptic:` prefixes in `handle.py`
  - Ralph verify loop (run verifier after step, retry if fails) → `verify_step()` in `step_exec.py`, wired in `agent_loop.py` via `ralph_verify` flag
  - Skill auto-injection → `skills.py` trigger patterns + `find_matching_skills()`
  - Post-mission notification pattern → `_finalize_loop()` in `agent_loop.py`
  - Auto-resume on rate limits → partially addressed with exponential backoff in `llm.py`; full daemon deferred
- **Session:** grok-response-2.txt (2026-03-29)

### 724-office (tom_doerr)
- **URL:** x.com/tom_doerr
- **Stars:** early-stage
- **What it is:** Single-agent tool-use loop with three-layer memory and self-repair.
- **What we took:**
  - Cron persistence (`jobs.json` pattern) → `src/scheduler.py` with `JobStore`; `poe-schedule` CLI
  - FileTaskStore pattern → `src/task_store.py`
  - Three-layer memory compression → deferred (LATER)
  - Runtime tool creation → deferred (needs sandbox hardening)
- **Session:** grok-response-2.txt (2026-03-29)

### Mimir
- **URL:** github.com/orneryd/Mimir
- **Stars:** ~256
- **What it is:** MCP memory server with graph + hybrid retrieval.
- **What we took:**
  - BM25 + RRF reranking → `src/hybrid_search.py`; wired into `memory.py` via `hybrid_rank()`
  - Error nodes as queryable memory → `find_relevant_failure_notes()` in `introspect.py`; wired into `agent_loop.py` `_build_decompose_context()`
  - Multi-hop lesson consolidation → deferred (graph edges with `depends_on`/`supersedes`)
- **Session:** grok-response-2.txt (2026-03-29)

### Agent-Reach
- **URL:** github.com/Panniantong/Agent-Reach
- **Stars:** ~12.7k
- **What it is:** CLI scaffolding for AI agents with internet access.
- **What we took:**
  - `doctor` diagnostic command → `src/doctor.py`; `poe-doctor` CLI
  - `channels/` pluggable data source architecture → deferred (NEXT)
  - Jina Reader integration → already had in `web_fetch.py`
- **Session:** grok-response-2.txt (2026-03-29)

### MetaClaw
- **URL:** github.com/aiming-lab/MetaClaw
- **Stars:** —
- **What it is:** RL-based agent framework. Note: had a hardcoded API key — do not use as reference for secrets handling.
- **What we took:**
  - SlowUpdateScheduler (gate heavy background work to idle windows) → `heartbeat_loop()` checks `is_drain_running()` before evolver/inspector/eval each tick
  - Skill retrieval with stemmer → deferred (LATER)
- **Session:** grok-response-2.txt (2026-03-29)

### ClawTeam
- **URL:** github.com/HKUDS/ClawTeam
- **Stars:** —
- **What it is:** Multi-agent framework with file-based task store.
- **What we took:**
  - FileTaskStore (file-per-task JSON, fcntl locking, DAG dep resolution, stale claim recovery) → `src/task_store.py`
- **Session:** grok-response-2.txt (2026-03-29)

### claw-code
- **URL:** github.com/instructkr/claw-code
- **Stars:** —
- **What it is:** Python skeleton reverse-engineered from Claude Code's leaked TypeScript source. Most code is stubs but the tool/command inventory is a goldmine.
- **What we took:**
  - `verificationAgent` as first-class peer agent → `src/verification_agent.py`; `poe-verify` CLI
  - `effort:` modifier pattern → `effort:low/mid/high` in `handle.py`
  - `ultraplan:` mode → `handle.py`; sets model=power + max_steps=12
  - `bughunter` mode → `src/bughunter.py`; stdlib AST scanner; `poe-bughunter` CLI
  - `btw:` (by-the-way) mode → `handle.py`; non-blocking observation lane
  - TeamCreateTool pattern → deferred (dynamic team creation at runtime)
  - thinkback/replay → deferred (session-level decision replay)
  - `passes` command → deferred (multi-pass review unified concept)
  - `$ralph` mode validated our Ralph verify loop design
- **Session:** BACKLOG research pass (2026-03-31)

### TradingAgents
- **URL:** github.com/TauricResearch/TradingAgents
- **Stars:** —
- **What it is:** Multi-agent Polymarket trading framework.
- **What we took:**
  - Commitment-forced verdicts → Inspector system prompt ends with `VERDICT: PROCEED/RETRY/ABORT`
  - Pre-plan challenger → `_challenge_spec()` in `director.py`; fires after `_produce_spec`
  - Two-tier model routing → `classify_step_model()` in `poe.py`; wired into `agent_loop.py` serial and parallel loops
  - Multi-agent debate pattern (bull/bear + risk manager) → deferred
- **Session:** Dogfood research run (2026-03-30)

### Stanford Agent0
- **URL:** — (Stanford research paper / implementation)
- **What it is:** Self-improvement without supervision: generate problems, solve them, learn from mistakes.
- **What we took:**
  - Problem generation + self-evaluation loop concept → maps to evolver; not yet implemented as standalone
  - Informed Phase 46 Graduation design (repeated failure classes → permanent rules)
- **Session:** Dogfood research run (2026-03-30)

### superpowers
- **URL:** github.com/obra/superpowers
- **Stars:** —
- **What it is:** Claude Code skills framework with SKILL.md frontmatter, subagent-driven-development pattern, and context isolation primitives.
- **What we took:**
  - SKILL.md frontmatter format (`name`/`description`/`category`) → skills YAML convention in `skills/`
  - Subagent-driven-development (Implementer → spec reviewer → code quality reviewer two-stage loop) → maps to ralph verify loop in AGENDA; fresh subagent per task
  - Context isolation (agents don't inherit session context they don't need) → each feature gets a fresh `run_agent_loop` call in `mission.py`
- **Status:** Items in prototype STEAL_LIST; partially implemented

### ClawRouter
- **URL:** github.com/BlockRunAI/ClawRouter
- **Stars:** young
- **What it is:** Model routing layer for Claude Code — cost-aware routing, latency-based selection, capability matching, health-based failover.
- **What we took:** Concept influenced two-tier model routing (`classify_step_model()` in `poe.py`); explicit cost-aware routing design
- **Session:** Telegram (2026-02-06) — Jeremy shared the link while we were discussing model stack

### LangGraph
- **URL:** github.com/langchain-ai/langgraph
- **Stars:** ~10k+
- **What it is:** Graph/state-machine orchestration for LLM agents: nodes, edges, interrupts, human-in-the-loop.
- **What we took:** Design inspiration for stateful loops and durable checkpointing; influenced AGENDA lane's step-state machine
- **Status:** Design inspiration only — we built our own rather than adopting LangGraph as dependency

### AutoGen
- **URL:** github.com/microsoft/autogen
- **Stars:** ~35k+
- **What it is:** Microsoft's multi-agent conversation framework with layered runtime (core message runtime + agentchat + extensions).
- **What we took:** Layered agent runtime concept; GroupChat → Director/Worker hierarchy naming
- **Status:** Design inspiration; referenced in STEAL_THIS_PLAYBOOK (2026-03 prototype docs)

### CrewAI
- **URL:** github.com/crewAIInc/crewAI
- **Stars:** ~25k+
- **What it is:** Multi-agent orchestration with a Crews vs Flows split — collaborative role-play vs production workflows.
- **What we took:** Crews/Flows distinction → Director delegates to named role Workers (researcher, analyst, ops, writer); concept of role-typed agents
- **Status:** Design inspiration; referenced in STEAL_THIS_PLAYBOOK

### DeerFlow (ByteDance)
- **URL:** github.com/bytedance/deer-flow
- **Stars:** —
- **What it is:** Multi-agent orchestration framework from ByteDance with Director/Worker split and structured research pipelines.
- **What we took:** Director/Worker architecture and the specific naming convention we use; deep research pipeline structure
- **Status:** Core design inspiration — named in CLAUDE.md as one of three primary reference architectures

### polymarket-cli
- **URL:** github.com/Polymarket/polymarket-cli
- **Stars:** —
- **What it is:** Official Polymarket CLI — read-only market/position/leaderboard data without a wallet.
- **What we took:** Nothing yet — pending steal (S effort). Target: Researcher persona + `src/web_fetch.py` or new `tools/polymarket.py` for read-only market data in research goals.
- **Status:** In STEAL_LIST.md NOW section

### Personal AI Infrastructure (PAI)
- **URL:** github.com/danielmiessler/Personal_AI_Infrastructure
- **Stars:** ~10.7k
- **What it is:** TELOS files, hooks system, personal AI infrastructure patterns.
- **What we took:** Nothing yet — hook patterns worth a deeper look
- **Status:** Deferred (BACKLOG)

---

## Papers

### AutoHarness
- **URL:** arxiv.org/abs/2603.03329
- **What it is:** Paper on automated harness synthesis for agent evaluation — generating test harnesses automatically from agent specs.
- **What we took:** Nothing yet — referenced in `read-later.md` with a steal list note; context reinforces adversarial verification direction
- **Status:** Deferred (captured in openclaw workspace, not yet processed into orchestration system)

### FunSearch (DeepMind)
- **URL:** — (garybasin link; DeepMind FunSearch paper)
- **What it is:** LLM + genetic programming: iterative optimization where LLM generates and refines solutions.
- **What we took:** Nothing yet — Mode 3 territory
- **Status:** Deferred (BACKLOG — read the actual papers)

### EUREKA / Voyager
- **URL:** — (garybasin link)
- **What it is:** LLM-driven reward/environment design and open-ended agent exploration.
- **What we took:** Nothing yet
- **Status:** Deferred (BACKLOG)

---

## Articles / Tweets / Social

### rohit4verse — Harness Engineering article
- **URL:** x.com/rohit4verse/status/2033945654377283643 (article); x.com/rohit4verse/status/2036845273117581676 (thread)
- **What it is:** Rohit's 8,000-word harness engineering article — "The best AI teams are not winning on models, they are winning on harness engineering." 52k views, 7.4k bookmarks. The primary source for the harness patterns we implemented. (Note: in BACKLOG this was attributed to @systematicls; rohit4verse is the original author, systematicls may have amplified it.)
- **What we took:**
  - Instruction fade-out → goal+constraints re-injected every 5 steps AND on every retry in `agent_loop.py`
  - Verification is the highest-leverage investment → adversarial verification + VerificationAgent
  - Back-pressure lifecycle hooks → `agent_loop.py` budget-aware landing injects `BUDGET PRESSURE` reminder
  - Role-specific tool visibility → `EXECUTE_TOOLS_SHORT`/`EXECUTE_TOOLS_INSPECTOR` in `step_exec.py`
  - Dual-memory (episodic + working) validated → we already had both
  - Subagent context firewall → deferred (LATER)
- **Captured:** 2026-03-26 in `~/.openclaw/workspace/output/x/`

### JayScambler — autocontext
- **URL:** x.com/JayScambler/status/2032508829959868690
- **What it is:** "Introducing autocontext: a recursive self-improving harness designed to help your agents (and future iterations of those agents) succeed on any task." 295k views, 3.1k bookmarks. Built referencing Karpathy's autoresearch thread.
- **What we took:** Concept of a recursive self-improving harness → validates Phase 44/45/46 self-reflection pipeline direction; nothing directly ported yet
- **Captured:** 2026-03-26 in `~/.openclaw/workspace/output/x/`

### Karpathy — vibe coding / IKEA DevOps
- **URL:** x.com/karpathy/status/2037200624450936940
- **What it is:** Karpathy on the real difficulty of shipping: not the code, but assembling the IKEA furniture of services (auth, payments, database, security, DNS). 1M views, 2.2k bookmarks.
- **What we took:** Context for why autonomous agent infra matters — reinforced the "ship it, the hard part is ops" framing; nothing directly ported
- **Captured:** 2026-03-26 in `~/.openclaw/workspace/output/x/`

### pzakin / Peter Zakin — Mode 1/2/3 taxonomy
- **URL:** x.com/pzakin/status/2038378114351608214
- **What it is:** Peter Zakin's clean taxonomy of where agentic development is headed: Mode 1 (manual IDE), Mode 2 (orchestrators with human specs), Mode 3 ("Factories" — agents that self-specify work from raw signals). Explicitly called Mode 3 the "wide-open blue ocean."
- **What we took:** The Mode 1/2/3 / Factory framing we use throughout the project; openclaw-orchestration is Mode 2 with Mode 3 as north star; informed factory_minimal/factory_thin naming
- **Session:** Shared by Jeremy with Grok (grok-response-3.txt) night of 2026-03-30

### bc1beat — Karpathy coding rant as system prompt
- **URL:** x.com/bc1beat/status/2019614947253317875
- **What it is:** System prompt based on Andrej Karpathy's AI coding rant — opinion-first, direct, no hedging. The "coding sub-agent should have a strong voice" concept.
- **What we took:** Influenced Poe's direct tone in SOUL.md and step executor prompting ("Do not hedge or defer — just do the work")
- **Session:** Telegram (2026-02-06) — Jeremy asked Poe to investigate

### dair_ai — agentic RAG
- **URL:** x.com/dair_ai/status/2019061395342622947
- **What it is:** DAIR.AI post on agentic RAG patterns. Jeremy noted "more about agentic RAG than what we're looking for."
- **What we took:** Nothing directly — flagged as future work if we move to vector-based memory retrieval; hybrid_search.py is the foothold
- **Session:** Telegram (2026-02-05) — Jeremy shared it, Poe deferred

### Deno Sandbox secrets isolation pattern
- **URL:** — (shared by Jeremy via Telegram 2026-02-05, no direct source URL)
- **What it is:** Deno Sandbox pattern where secrets never enter the environment as plaintext — code sees only a placeholder, and the real value materializes only when the sandbox makes an authorized outbound request. Prevents prompt-injected code from exfiltrating credentials.
- **What we took:** Security inspiration for future constraint.py hardening; validated the "never echo secrets to agent context" rule already in practice
- **Status:** Not yet implemented as a formal subsystem; noted for security sprint

### LLM sycophancy (Karpathy / rohanpaul)
- **URL:** — (x.com/karpathy, x.com/rohanpaul)
- **What it is:** Models mirror prompts not truth — sycophancy as a systemic problem.
- **What we took:**
  - Adversarial verification step → `factory_thin.py` (post-execute) and `quality_gate.py` (second pass on Mode 2 runs)
  - LLM Council / multi-angle critique → deferred (spawn N sub-agents with distinct critical framings)
- **Session:** BACKLOG item (2026-03-31)

### Hesamation tweet (LLM Council)
- **URL:** x.com/hesamation (Karpathy LLM Council pattern)
- **What it is:** Karpathy's LLM Council ported to Claude Code skill: N sub-agents with distinct critical framings (devil's advocate, domain skeptic, implementation critic) that critique before synthesis.
- **What we took:** Nothing yet — deferred
- **Status:** Deferred (BACKLOG)

---

## Feedback Sessions

### Grok — response 2 (grok-response-2.txt)
- **Date:** 2026-03-29
- **What it covered:** oh-my-claudecode, 724-office, Mimir steal list; autonomous agent patterns
- **What we took:** Full steal list above — majority of STEAL_LIST.md sourced here
- **File:** `/home/clawd/claude/grok-response-2.txt`

### Grok — response 3 (grok-response-3.txt)
- **Date:** 2026-03-29
- **What it covered:** Bitter Lesson Engineering; Mode 1/2/3 taxonomy (reactive / planned / self-generating)
- **What we took:**
  - Outcome-first decomposition → decompose prompt now targets outcomes not actions
  - Mode 1/2/3 framework → language we use for factory_minimal/factory_thin/Mode 2
  - User context injection → `user/` folder + `USER.md` injected into goal context
- **File:** `/home/clawd/claude/grok-response-3.txt`

### Grok — ongoing review feedback
- **What it covered:** Skeptic prompting, stability sprint advice, dashboard-as-real-tool, Zakin/evolver signal scanning feedback
- **What we took:**
  - Skeptic persona → `apply_skeptic_modifier()` in `persona.py`; `skeptic:` prefix
  - Evolver signal scanning → deferred (Mode 2 → Mode 3 bridge)
  - Dashboard-as-real-tool → deferred (Phase 36 dashboard still a prop)
- **Session:** Multiple sessions

---

## Dogfood Runs (Poe researching her own roadmap)

### agent0-research project
- **Goal:** "Research Stanford Agent0 approach to self-improvement without supervision"
- **Key output:** Problem generation + self-evaluation loop design; maps to evolver Phase 46+
- **Location:** `projects/agent0-research/`

### factory comparison
- **Goal:** Compare factory_minimal vs factory_thin vs Mode 2 on real goals
- **Key output:** `factory-comparison.md` — thin+adv matches Mode 2 quality at ~2x lower cost; Haiku token explosion on complex research erases cost advantage
- **Location:** `/tmp/factory-comparison.md` (ephemeral)

---

## Design Inspirations (no direct steal)

| Name | What | Influence |
|------|------|-----------|
| Paperclip (LangGraph) | Goal ancestry, stateful loops, heartbeats | Mission → Milestone → Feature hierarchy; heartbeat_loop() |
| DSPy / Reflexion | Self-improvement via outcome reflection | Phase 44/45 self-reflection pipeline; lessons.jsonl |
| DeerFlow | Multi-agent orchestration | Director/Worker architecture |
| Altered Carbon (the show) | Poe's name and personality | SOUL.md |

---

## Tangential References (ambient / domain-specific / not yet processed)

Collected from session logs, Telegram archive, and workspace `read-later.md`. Not directly stolen from, but in the orbit of the project. Captured here so we don't lose the thread.

### SDKs & Tooling

| Repo | What |
|------|------|
| github.com/anthropics/claude-agent-sdk-python | Official Claude Agent SDK (Python) — agent dev patterns |
| github.com/anthropics/claude-agent-sdk-typescript | Official Claude Agent SDK (TypeScript) — TS counterpart |
| github.com/anthropics/claude-plugins-official | Official Claude plugins incl. Discord + Telegram integrations |
| github.com/microsoft/playwright-mcp | Playwright via MCP — browser automation for agents |
| github.com/modelcontextprotocol/servers | MCP server collection — various protocol integrations |
| github.com/jxnl/instructor | Structured LLM output library — schema-enforced responses |
| github.com/guidance-ai/guidance | Prompt engineering / constrained generation framework |
| github.com/run-llama/llama_index | Data indexing + retrieval (LlamaHub) — RAG patterns |
| github.com/ggml-org/llama.cpp | Local LLM inference — offline model option |

### Evaluation / Quality

| Repo | What |
|------|------|
| github.com/confident-ai/deepeval | LLM evaluation framework — unit-test-style evals |
| github.com/explodinggradients/ragas | RAG evaluation — retrieval + generation scoring |
| github.com/microsoft/semantic-kernel | Agent plugin framework — MS approach to skill composition |

### Multi-Agent Frameworks (additional)

| Repo | What |
|------|------|
| github.com/Factory-AI/factory | Agent factory pattern framework |
| github.com/agentscope-ai/agentscope | Multi-agent framework with observability focus |
| github.com/ComposioHQ/agent-orchestrator | Tool integration layer for agents |
| github.com/openai/multi-agent-emergence-environments | OpenAI multi-agent research environment |

### Research & Self-Improvement Patterns

| Repo | What |
|------|------|
| github.com/karpathy/autoresearch | Karpathy's research automation approach (referenced by JayScambler) |
| github.com/greyhaven-ai/autocontext | Recursive self-improving harness (the autocontext JayScambler tweeted) |
| github.com/CharlesQ9/Self-Evolving-Agents | Agent meta-improvement patterns |
| github.com/EvoAgentX/Awesome-Self-Evolving-Agents | Curated self-evolving agent approaches |
| github.com/Shichun-Liu/Agent-Memory-Paper-List | Research papers on agent memory systems |
| github.com/TsinghuaC3I/Awesome-Memory-for-Agents | Memory patterns for agent systems |
| github.com/MineDojo/Voyager | Open-ended agent learning (the Voyager in "EUREKA/Voyager") |
| github.com/virattt/ai-hedge-fund | Trading/financial agent example — domain reference |

### Curated Lists (low signal, captured for completeness)

| Repo | What |
|------|------|
| github.com/e2b-dev/awesome-ai-agents | e2b curated agents list |
| github.com/kyrolabs/awesome-agents | KyroLabs curated agents list |
| github.com/alvinunreal/awesome-autoresearch | Autonomous research agent patterns |
| github.com/CodeCrafters-io/build-your-own-x | DIY implementation tutorials (general system design ref) |

### X / Social — Orchestration-Adjacent

Accounts that appeared in session context or Telegram archives in an orchestration-relevant capacity (not the crypto/trading accounts, which are Polymarket domain-specific):

| URL | Why it appeared |
|-----|----------------|
| x.com/vtrivedy10/status/2038346865775874285 | Viv (LangChain) — harness patterns, evaluation frameworks |
| x.com/ihtesham2005/status/2027701751630205073 | Architecture of Thought (AoT) discussion — referenced in PRINCIPLES.md |
| x.com/NousResearch/status/2026758996107898954 | Model + training discussion (model selection context) |
| x.com/godofprompt/status/2030434516397891732 | Prompt engineering / system prompt patterns |
| x.com/_avichawla/status/2026907616337883612 | Agent engineering discussion |
| x.com/miolini/status/2030402705374728218 | Agent patterns discussion |
| x.com/karpathy/status/2030371219518931079 | Karpathy on agent/coding workflow (separate from the IKEA DevOps post) |
| x.com/gregisenberg/status/2030680849486668229 | AI tools and automation |
| x.com/githubprojects/status/2030346933009821801 | GitHub-adjacent project/tool reference |
| x.com/livingdevops/status/2033845127244825041 | DevOps automation patterns |
| x.com/BrianRoemmele/status/2027268778266943964 | Prototype research discussion |
| x.com/youjiaxuan/status/2020395463266980008 | Agent orchestration research |
| x.com/xbenjamminx/status/2021332945491927503 | Camoufox browser plugin for OpenClaw — anti-bot fingerprint spoofing |
| x.com/tom_doerr/status/2027323833477116360 | Tom Doerr (724-office) — additional agent pattern post |
| x.com/roundtablespace/status/2030837224871272645 | Agent/automation discussion |
| x.com/thejayden/status/2026362787413237853 | Agent patterns |
| x.com/sillydarket/status/2023232371038757328 | Sidenote captured for later |
| x.com/pradeep24/status/2021319785947316490 | Trading/automation patterns |
| x.com/VittoStack/status/2018326274440073499 | Agent infrastructure |

### X / Social — Polymarket / Trading Domain

Domain-specific accounts from Jeremy's Polymarket research track. Not orchestration inspiration — captured because they appear in workspace files alongside orchestration-relevant content.

| URL | Domain |
|-----|--------|
| x.com/0xRicker/status/2023354636380340657 | Polymarket trading patterns |
| x.com/noisyb0y1/status/2024131165490434286 | Polymarket mispricing signals |
| x.com/k1rallik/status/2024191701599080828 | Latency regime monitoring |
| x.com/L2WTrades/status/2024978068255695085 | Ruin-risk sizing |
| x.com/kanikabk/status/2023359515597623694 | Parameter sweep strategies |
| x.com/polydao/status/2024170664840601797 | Polymarket governance |
| x.com/polydao/status/2025510883007332733 | Polymarket governance (second post) |
| x.com/lorden_eth/status/2026948169926373794 | Crypto automation |
| x.com/0x_kaize/status/2026392724945953118 | Trading automation |
| x.com/lunarresearcher/status/2025520980605473174 | Research patterns |
| x.com/moondevonyt/status/2025240062267719912 | Dev patterns / algo trading |
| x.com/recogard/status/2024620826146869331 | Trading bot validation claims |
| x.com/Shelpid_WI3M/status/2024580148587049396 | Trading claims falsification |
| x.com/bl888m/status/2027059173578404346 | Signals discussion |
| x.com/dunik_7/status/2026961543531778303 | Agent patterns |
| x.com/helicerat0x/status/2027060602279993369 | Crypto automation |
| x.com/0xMovez/status/2027801807897145578 | Crypto automation |
| x.com/bored2boar/status/2024846407576801783 | Trading discussion |
| x.com/bored2boar/status/2027133363086127492 | Trading discussion (second post) |
| x.com/chiefofautism/status/2026057517881499662 | Trading patterns |
| x.com/DenisKursakov/status/2027756385283322352 | Claude bot + Polymarket liquidity rewards |
| x.com/whizz_ai/status/2026949902073491591 | AI automation / trading |
