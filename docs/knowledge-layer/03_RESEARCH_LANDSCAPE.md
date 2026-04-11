# Memory System Landscape — Research Summary

**Date:** 2026-04-09
**Purpose:** Evaluate existing persistent memory/knowledge systems for AI agents to inform the knowledge layer foundation choice (Phase K1).

---

## Executive Summary

13 projects evaluated. The field splits into two camps:

**Graph-heavy systems** (Zep, Cognee, Neo4j) — powerful temporal reasoning and relational queries, but opaque. You can't open a folder and read what your AI "knows."

**Human-readable systems** (Basic Memory, Karpathy's Obsidian approach) — transparent and editable, but lack sophisticated retrieval and agent integration.

Nobody has unified them. The orchestration project has more pieces of the unified system than any single external project (hybrid search, temporal memory, associative graph, active injection, self-improvement) — they're just not connected into a coherent knowledge layer yet.

---

## Top Candidates (Ranked by Fit)

### Tier 1: Strong Fit

**Zep / Graphiti** — 9/10 fit, ~15K stars
- Temporal knowledge graph with bi-temporal edges
- Three subgraph networks: episode (conversational), semantic entity (facts), community (themes)
- Best temporal reasoning: 63.8% vs 49% (Mem0) on LongMemEval
- Requires Neo4j. Not human-readable. More infrastructure than a Mac Mini wants.
- **Best for:** The Ledger. Temporal modeling design patterns worth stealing even if we don't adopt the system.

**Hindsight** — 9/10 fit, ~8K stars
- Four parallel memory networks: World (facts), Experience (actions), Opinion (beliefs + confidence), Temporal
- Biomimetic data structures. State-of-the-art accuracy.
- More black-box than explicit graph systems.
- **Best for:** The Lens. Opinion network with confidence scores maps to persona-weighted queries.

**Cognee** — 8/10 fit, 15K stars
- Self-improving knowledge graph. Six-stage extraction pipeline.
- Prunes stale nodes, strengthens frequent connections, reweights by usage.
- Transparent entity extraction. Hybrid 14-retrieval-mode architecture.
- **Best for:** The Web. Self-improvement patterns worth stealing for Phase K8.

**Letta (MemGPT)** — 8/10 fit, 22K stars
- OS-inspired tiered memory: core (always in-context), archival (vector store), recall (history)
- Self-editing: agents actively manage their own memory
- Relational/graph structure is secondary to vector storage.
- **Best for:** Agent self-management of memory. Design influence for how Poe edits its own knowledge.

**SuperLocalMemory** — 8/10 fit, ~6K stars
- Local-only SQLite with mathematical foundations (Fisher-Rao geometry, sheaf cohomology)
- 74.8% LoCoMo accuracy with zero cloud dependency
- Three operating modes: local-only / local LLM / cloud LLM
- **Best for:** Privacy-first deployment. Mathematical rigor for scoring/ranking.

### Tier 2: Partial Fit

**Mem0** — 7/10 fit, 52K stars (most popular)
- Dual-store: vector DB + knowledge graph. 21+ framework integrations.
- 26% accuracy improvement over vector-only. Production-grade.
- Less temporal reasoning than Zep. Not Obsidian-native.
- **Best for:** Drop-in memory if you want something that works today. Less aligned with the Ledger/Web/Lens vision.

**Neo4j Agent Memory** — 7/10 fit, ~3.5K stars
- Graph-native. 16 MCP tools. Multi-framework integration.
- Requires Neo4j operational overhead.
- **Best for:** If you commit to a graph DB, this is the agent interface.

**Supermemory** — 7/10 fit, ~4.5K stars
- Ontology-aware edges. Multi-format ingestion (Notion, Slack, Gmail, S3).
- Less proven at scale than Mem0 or Zep.
- **Best for:** Multi-format ingestion pipeline. Data connector architecture.

**Basic Memory** — 6/10 fit, 2.8K stars
- Obsidian-native. Markdown + YAML frontmatter + wiki-links + SQLite index.
- Human-readable AND machine-queryable. MCP server included.
- No temporal modeling. Not optimized for multi-agent orchestration.
- **Best for:** The Web's human interface. Closest "shape" to what we want. Needs temporal extension.

**Microsoft GraphRAG** — 6/10 fit, ~8.5K stars
- Entity-centric knowledge graph extraction. Community hierarchy.
- Not optimized for session persistence or evolving knowledge.
- **Best for:** Retrieval/grounding layer, not primary memory.

### Tier 3: Limited Fit

**Khoj** — 5/10 fit, ~15K stars
- Personal AI with local document indexing. Obsidian plugin.
- Not graph-native. Better for personal KM than agent orchestration.

**Claude Memory Tool** — 5/10 fit (native)
- File-based memory in /memories directory. Simple but not graph-aware.
- Only relevant for Claude-exclusive builds.

**Memvid** — 3/10 fit, ~2K stars
- Single-file portable memory. BM25 + semantic vectors.
- Too simple for this use case.

---

## Key Patterns Across the Landscape

1. **Temporal modeling is no longer optional.** 15-20 point accuracy gap between systems with and without temporal reasoning on long-horizon benchmarks. The Ledger needs time as first-class data.

2. **Hybrid retrieval is table-stakes.** Every production system combines semantic (embedding) + keyword (BM25) + graph traversal. Single-strategy retrieval is abandoned. We already have BM25+RRF in hybrid_search.py.

3. **Self-improvement differentiates.** Cognee (usage-driven refinement), Letta (agent self-editing), Hindsight (multi-network learning) show knowledge as living structure. Static archives are 2024 thinking.

4. **MCP is the universal agent memory protocol.** Every new system ships an MCP server. This is how the knowledge layer will bridge to the orchestration layer.

5. **Explicit vs. implicit tension is unresolved.** Transparent systems (Cognee, Basic Memory) vs. black-box systems (Hindsight). No clear winner. We lean toward explicit/transparent because Jeremy needs to curate and understand.

6. **Nobody does Ledger + Web + Lens together.** This is the gap. Each project does one or two views well. The unified system we're describing doesn't exist yet.

---

## Recommendation for Phase K1

**Don't adopt a single external system.** None of them match the full vision. Instead:

1. **Start with the markdown + wiki-link format** (Basic Memory's shape, or just plain markdown files with `[[wiki-links]]`). This gives human readability, git friendliness, and Obsidian compatibility.

2. **Build a lightweight index layer** (SQLite, extending what the links collection already has). Indexes the wiki-link graph for programmatic queries.

3. **Steal temporal patterns from Zep/Graphiti** (bi-temporal fields on knowledge nodes). Implement in the schema, not as a dependency.

4. **Steal self-improvement patterns from Cognee** (usage-weighted edges, stale pruning). Wire into evolver cycle.

5. **Expose via MCP** so Poe can query the knowledge layer from the orchestration runtime.

This approach builds on what exists (lat.md pattern, links collection SQLite, memory module) rather than replacing it with someone else's foundation.

---

## Raw Research Data

Full project evaluations with scores, benchmarks, and implementation notes: `memory_landscape_research.json`
