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
| **Verification patterns on rules** | meta_alchemist / oh-my-claudecode | Each rule gets a machine-checkable grep/script that proves it's being followed. Rule only graduates to permanent after 2x verified pass. | Phase 46 (Intervention Graduation) — add `verify_pattern` field to `rules.jsonl` entries | M |
| ~~**Skeptic persona modifier**~~ | Grok advice | ✅ DONE — apply_skeptic_modifier() in persona.py; trigger with "skeptic:" prefix or "--skeptic" in goal. | `src/persona.py`, `src/poe.py` | S |
| ~~**Post-mission notification**~~ | oh-my-claudecode | ✅ DONE — fires at end of _finalize_loop() in agent_loop.py. | `src/agent_loop.py` | S |
| **Polymarket CLI integration** | polymarket-cli (OSS) | Read-only market/position/leaderboard data without wallet. Perfect for research personas. | Researcher persona spec + `src/web_fetch.py` or new `tools/polymarket.py` | S |

## NEXT — after stability sprint

| Item | Source | What | Where it lands | Effort |
|------|--------|------|----------------|--------|
| **Hybrid retrieval (BM25 + vector + RRF)** | Mimir | Combined keyword + semantic search with Reciprocal Rank Fusion reranking. Replaces pure TF-IDF in memory. | `src/memory.py` — add `hybrid_search.py` alongside existing TF-IDF | M |
| **Error nodes as queryable memory** | Mimir | Store failures as first-class nodes so agents self-query "what broke last time on similar goal?" | `src/memory.py` + `memory/diagnoses.jsonl` (Phase 44 data) | M |
| ~~**Skills trigger arrays**~~ | oh-my-claudecode | ✅ DONE — `trigger_patterns` field already on Skill dataclass; keyword fallback in `find_matching_skills()` does exact substring matching. Functionally identical. | `src/skills.py` | — |
| **Auto-resume on rate limits** | oh-my-claudecode | Detect API rate limit, pause mission, poll, resume automatically. | `src/heartbeat.py` or `src/sheriff.py` — extend with rate-limit watch | M |
| **SlowUpdateScheduler** | MetaClaw | IDLE_WAIT -> WINDOW_OPEN -> UPDATING -> PAUSING state machine. Gates heavy background work. | New module, called from heartbeat/cron | M |
| **Cron persistence (`jobs.json`)** | 724-office | Scheduled missions survive restarts. JSON file with timezone-aware one-shot + recurring entries. | Extend heartbeat or new `src/scheduler.py` | M |
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
| Grok (external reviewer) | — | Independent code review of openclaw-orchestration | Skeptic prompting, stability sprint advice, dashboard-as-real-tool |
