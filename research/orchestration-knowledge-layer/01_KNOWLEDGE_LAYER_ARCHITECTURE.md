# Knowledge Layer Architecture: Ledger / Web / Lens

**Status:** Design concept — emerged from cross-reference session 2026-04-09
**Relationship to orchestration:** This is the memory/knowledge substrate that sits underneath Poe's agent runtime. The orchestration layer is the harness/OS; this is the long-term intelligence that compounds across sessions.

---

## The Problem

"Memory" is used to mean at least four different cognitive operations: recall (what happened?), association (what's related?), reasoning (what follows?), and recognition (have I seen this pattern before?). These are different operations that happen to share a storage layer. Current memory systems either do temporal well (Zep/Graphiti) or associative well (graph DBs, wiki-links) or contextual well (RAG, injection), but none unify them.

The orchestration project has been building pieces of all three without naming the skeleton:

| What we have | Where it lives | What mode it serves |
|---|---|---|
| outcomes.jsonl, lessons.jsonl | memory module | Temporal (what happened, what we learned) |
| lat.md with wiki-links | lat.md/ | Associative (how concepts relate) |
| standing rules, decision journal | memory module | Active injection (what matters now) |
| three-tier promotion cycle | memory.py | Self-improvement (observations → hypotheses → rules) |
| hybrid_search.py (BM25 + RRF) | hybrid_search.py | Retrieval (finding relevant knowledge) |
| AI links collection | external (Cowork) | Intake/curation (raw knowledge capture) |

These need to become one coherent system with three access modes.

---

## Architecture: Three Views of One Reality

The core insight: the knowledge layer isn't three separate systems that get integrated — it's three views of the same underlying data. Like Cartesian and polar coordinates describing the same plane, each view makes different questions easy.

### 1. The Ledger (Temporal View)

**What it answers:** What happened? When? What did we learn? What changed?

**Character:** Append-only, journaled, git-like. ZFS semantics — every state preserved, snapshots, diffs between any two points, roll back without destroying current state.

**What lives here:**
- Outcome records with timestamps
- Lesson extraction with `learned_at`, `validated_at`, `superseded_by`
- STEAL_LIST progression (TODO → DONE, with dates)
- Links collection entries with enrichment history
- Decision journal entries
- Session transcripts and key conversation captures

**Key design principle:** Bi-temporal modeling (from Zep/Graphiti). Every fact tracks two times: when it was true in the world (`t_valid`) and when we learned it (`t_learned`). The BTC lag claim was "true" (believed) from 2026-04-01 to 2026-04-02 when it was invalidated. That trajectory is knowledge.

**Implementation direction:** Git itself may serve as the temporal backbone — the commit history IS the ledger. Queryable git (structured commit messages, parseable diffs, blame as provenance) rather than a separate temporal store.

### 2. The Web (Associative View)

**What it answers:** How are things related? What's adjacent? What patterns recur?

**Character:** Graph-native, non-temporal, topology-first. The Correspondence sphere from Mage: The Ascension — distance is relational, not spatial. "Harness engineering" and "emergency culture prevention" are adjacent because they share structural patterns, regardless of when they were encountered.

**What lives here:**
- Concept nodes (agent-design, harness-engineering, verification, memory-systems, etc.)
- Principle nodes (extracted insights: "verification is the highest-leverage investment")
- Evidence edges (this link supports this principle)
- Causal edges (this insight led to this implementation)
- Structural similarity edges (these two things solve the same problem differently)
- Author/source clustering (who consistently produces insights in which domains)

**Key design principle:** Wiki-links as the human-readable graph format (from Basic Memory / lat.md). `[[Harness Engineering]]` links are traversable by both humans and agents. The graph should be readable in Obsidian AND queryable programmatically.

**Implementation direction:** Markdown files with wiki-links, backed by a graph index (SQLite or lightweight graph DB). Each concept node is a markdown file. Relationships are wiki-links. Graph queries traverse the link structure. Human-editable, machine-queryable.

### 3. The Lens (Contextual View)

**What it answers:** What matters right now, for this task, through this persona?

**Character:** Not stored — computed at query time. The same knowledge node looks different to the skeptic persona (high scrutiny, low trust) than to the researcher persona (high association, exploratory). This is where temperature, attention weighting, and relevance scoring live.

**What lives here (as configuration, not data):**
- Persona definitions with associated query parameters (temperature, relevance thresholds, association breadth)
- Task-type routing rules (research task → broad association; implementation task → narrow, high-confidence)
- Standing rules (atemporal truths that always inject: "never echo secrets to agent context")
- Active injection templates (how knowledge gets formatted into prompts)

**Key design principle:** The lens is what `inject_standing_rules()` and `inject_decisions()` do primitively. But it should be much richer — a philosopher persona with high temperature sees connections that a low-temperature engineer persona prunes as noise. Both are valid; the lens determines which connections surface.

**Implementation direction:** Extend existing `persona.py` with knowledge query parameters. Each persona definition includes not just voice/tone but retrieval configuration: how broadly to associate, what confidence threshold to surface, how many hops in the graph to traverse.

---

## The Correspondence Principle

> "Everything exists in the same space at the same time, and manipulating reality through that lens is essentially an entire class of magic."
> — Jeremy, referencing Mage: The Ascension's Correspondence sphere

The three views aren't actually separate. They're three coordinate systems over the same knowledge space. A Correspondence-aware system lets you move freely between them:

- Start with a temporal query ("what did we learn last week?") → get results → pivot to associative ("what's related to these findings?") → pivot to contextual ("which of these matter for the current mission?")
- Start with an associative query ("everything related to harness engineering") → filter temporally ("what's new since we last looked?") → apply a lens ("what would the skeptic persona challenge here?")

The system should support these transitions without explicit mode-switching. The query interface is fluid; the underlying storage supports all three views.

---

## Data Flow: Links Collection → Knowledge Layer → Orchestration

```
INTAKE (Links Collection)                KNOWLEDGE LAYER              ORCHESTRATION (Poe)
┌─────────────────────┐          ┌──────────────────────┐          ┌─────────────────┐
│ X/Twitter posts     │          │                      │          │                 │
│ Articles            │──enrich──│  LEDGER (temporal)   │──inject──│ inject_rules()  │
│ Tools/repos         │          │  WEB (associative)   │──query───│ decompose()     │
│ Email captures      │          │  LENS (contextual)   │──filter──│ evolver signals │
│ Conversation notes  │          │                      │          │                 │
└─────────────────────┘          └──────────────────────┘          └─────────────────┘
      ↑                                   ↑                               │
      │                                   │                               │
      └────── human curation ─────────────┴──── outcomes/lessons ─────────┘
```

The links collection remains the **intake layer** — curated by Jeremy, enriched by AI, topic-tagged and prioritized. The knowledge layer transforms raw links into structured knowledge (principles, patterns, evidence, causal chains). The orchestration layer consumes knowledge at reasoning time through the lens appropriate to the current task/persona.

Feedback flows back: outcomes and lessons from the orchestration layer update the knowledge layer (new evidence, validated/invalidated principles, superseded knowledge).

---

## Relationship to Existing Systems

| Existing component | Role in knowledge layer | What changes |
|---|---|---|
| `memory.py` (outcomes, lessons) | Becomes the Ledger's write interface | Add bi-temporal fields, structured provenance |
| `lat.md/` (wiki-link knowledge graph) | Becomes the Web's seed content | Extend with principle nodes, evidence edges, richer relationships |
| `inject_standing_rules()`, `inject_decisions()` | Becomes the Lens's primitive | Parameterize by persona, add confidence-weighted filtering |
| `hybrid_search.py` (BM25 + RRF) | Becomes the retrieval engine across all three views | Add graph traversal, temporal filtering |
| AI links collection (SQLite + JSON) | Stays as intake layer | Add export pipeline to knowledge layer format |
| `skills.py` (skill evolution) | Consumers of knowledge layer | Skills informed by principle nodes, not just outcome patterns |
| `evolver.py` (meta-improvement) | Both consumer and producer | Reads knowledge for improvement signals, writes new observations |

---

## Open Design Questions

1. **Storage format:** Markdown with wiki-links (human-readable, git-friendly) vs. graph DB (powerful queries, opaque)? Can we have both — markdown as source of truth, graph DB as index?

2. **The git question:** Can git commit history literally serve as the Ledger? Structured commits, parseable diffs, blame as provenance? Or is that too clever by half?

3. **Schema for the Web:** What are the node types and edge types? Concepts, principles, evidence, authors, implementations? What relationships matter? `supports`, `contradicts`, `led_to`, `supersedes`, `similar_to`?

4. **Lens parameterization:** What knobs does a persona have for knowledge queries? Temperature (association breadth), confidence threshold, recency weighting, graph traversal depth? How do these compose?

5. **The Correspondence interface:** How do you query across views fluidly? "Show me everything related to harness engineering that changed this month, filtered through the skeptic lens." What does that query language look like?

6. **Scale:** 301 links today. 1000 in six months. 5000 in two years. Plus outcomes, lessons, conversation captures. When does the simple approach stop working?

7. **Migration:** How does the current links collection (enriched, topic-tagged) become knowledge nodes without losing work? What's the transformation pipeline?

---

## Inspirations

| Source | What it contributes |
|---|---|
| Git | Temporal backbone, branching/merging as knowledge evolution |
| Obsidian / Basic Memory | Wiki-link graph format, human-readable AND machine-queryable |
| Zep/Graphiti | Bi-temporal modeling, episode/semantic/community networks |
| Cognee | Self-improving knowledge graph, usage-weighted edges |
| Mage: The Ascension (Correspondence) | Non-spatial adjacency, relational proximity |
| Poe's existing memory module | Lesson extraction, standing rules, hybrid retrieval |
| lat.md | Wiki-link knowledge graph prototype |
