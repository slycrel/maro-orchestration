# Knowledge Layer — Proposed Phases

**Context:** These are practical phase items derived from the 2026-04-09 architecture session. They're written to slot into openclaw-orchestration's ROADMAP.md style (independently shippable, testable, building on each other).

**Prerequisite understanding:** Read `01_KNOWLEDGE_LAYER_ARCHITECTURE.md` for the Ledger/Web/Lens framework these phases build toward.

---

## Phase K0: Foundation — Assess What We Have

Before building anything new, catalog exactly what knowledge infrastructure already exists and what gaps remain.

- [ ] Audit current memory module: what's in outcomes.jsonl, lessons.jsonl, standing rules, decision journal? How much data? What's the schema?
- [ ] Audit lat.md: how many concept nodes? What link density? Is it being maintained or stale?
- [ ] Audit links collection: 301 posts with topics, summaries, content. What's the overlap with orchestration knowledge?
- [ ] Map the cross-reference results (83 heavy, 139 moderate, 79 tangential) to existing knowledge gaps
- [ ] Document: what knowledge does Poe currently access at reasoning time? What's the injection path?
- [ ] Identify the top 5 "knowledge failures" — times Poe made a decision that existing knowledge should have prevented

**Artifact:** `docs/KNOWLEDGE_LAYER_BASELINE.md` — honest snapshot of current state

---

## Phase K1: Seed the Knowledge Base — Choose Foundation

The critical decision: what format/tool becomes the base layer?

**Decision criteria:**
- Human-readable and human-editable (Jeremy needs to curate)
- Machine-queryable (Poe needs to retrieve at reasoning time)
- Git-friendly (version control is the temporal backbone)
- Supports wiki-links or equivalent relationship notation
- Lightweight enough for a Mac Mini
- MCP-compatible or easily bridgeable

**Candidates to evaluate (hands-on, not just research):**
- [ ] Basic Memory: clone, set up, load 20 test posts, evaluate query capability and Obsidian integration
- [ ] Cognee: same — test with real data, evaluate self-improving graph capability
- [ ] Homebrew: markdown + wiki-links + SQLite index (extend lat.md pattern). What does it take to build the index?
- [ ] Hybrid: Basic Memory as human interface + lightweight graph index for queries

**Decision gate:** Pick one and commit. Document why. Don't over-optimize — this can be migrated later.

**Artifact:** Chosen foundation installed, 20 test knowledge nodes loaded, basic query working

---

## Phase K2: Migrate Links Collection → Knowledge Nodes

Transform the 301-post links collection into knowledge layer format.

- [ ] Design the knowledge node schema: what fields? how do links become evidence for principles?
- [ ] Extract principles from enriched posts (LLM-assisted): "harness engineering matters more than model selection" backed by evidence from rohit4verse, systematicls, Meta-Harness paper
- [ ] Create wiki-link relationships: which posts support which principles? which authors cluster together?
- [ ] Preserve provenance: every principle traces back to its source posts
- [ ] Handle temporal metadata: when was this learned? when was it validated? has it been superseded?
- [ ] Import SOURCES.md and STEAL_LIST.md entries as knowledge nodes with implementation status
- [ ] Validate: can Poe query "what do I know about harness engineering?" and get a useful answer?

**Artifact:** Links collection represented as knowledge nodes with relationships, queryable

---

## Phase K3: Bridge to Orchestration — Read Path

Connect the knowledge layer to Poe's reasoning loop (read-only first).

- [ ] Build query interface: knowledge layer → structured results → injectable context
- [ ] Replace or augment `inject_standing_rules()` with knowledge-layer-backed injection
- [ ] Replace or augment `inject_decisions()` with knowledge-layer-backed injection
- [ ] Wire into `_build_decompose_context()`: relevant knowledge surfaces during planning
- [ ] Wire into evolver signal scanning: knowledge layer provides broader context for improvement signals
- [ ] Test: does Poe make measurably better decisions with knowledge layer injection vs. without?

**Artifact:** Poe's decompose and planning steps informed by knowledge layer. A/B comparison possible.

---

## Phase K4: Bridge to Orchestration — Write Path

The orchestration layer writes back to the knowledge layer.

- [ ] Outcomes produce new observations in the knowledge layer
- [ ] Validated/invalidated principles get temporal updates (superseded_by, validated_at)
- [ ] New causal edges: "we implemented X because of principle Y, and it worked/failed"
- [ ] Lesson extraction feeds principle refinement
- [ ] Skill evolution events become knowledge (which skills work for which task patterns?)

**Artifact:** Bidirectional flow: knowledge informs orchestration, orchestration updates knowledge

---

## Phase K5: The Lens — Persona-Aware Queries

Different personas query the same knowledge differently.

- [ ] Define query parameters per persona: association breadth (temperature-like), confidence threshold, recency weighting, graph traversal depth
- [ ] Implement: skeptic persona queries with high confidence threshold, narrow association
- [ ] Implement: researcher persona queries with low confidence threshold, broad association
- [ ] Implement: philosopher persona with high temperature, cross-domain connections
- [ ] Test: same query ("what's relevant to this goal?") through different lenses produces meaningfully different results
- [ ] Wire into persona selection: when persona is chosen, query parameters are set

**Artifact:** Persona-parameterized knowledge queries, demonstrably different results by lens

---

## Phase K6: Temporal Intelligence — The Ledger

Make time a first-class dimension.

- [ ] Add bi-temporal fields to knowledge nodes: `t_valid` (when true in world), `t_learned` (when we learned it), `t_superseded` (when replaced)
- [ ] Implement temporal queries: "what did we learn last week?", "what's changed since March?", "what was our understanding of X at time T?"
- [ ] Explore git-as-ledger: can commit history serve as the temporal backbone? Structured commits, parseable diffs?
- [ ] Track knowledge evolution: how has our understanding of "harness engineering" changed over 6 months?
- [ ] Implement knowledge decay: old, unreinforced knowledge gets lower confidence scores

**Artifact:** Temporal queries working, knowledge evolution visible

---

## Phase K7: The Correspondence Interface — Cross-View Queries

Fluid movement between temporal, associative, and contextual views.

- [ ] Design query language that spans views: "everything related to X that changed this month, through the skeptic lens"
- [ ] Implement view pivoting: start temporal → pivot associative → filter contextual
- [ ] Test with real scenarios: "I'm planning a new verification approach — what do we know, what's changed recently, what would the skeptic challenge?"
- [ ] Performance: cross-view queries need to be fast enough for reasoning-time injection

**Artifact:** Cross-view query interface, tested with real orchestration scenarios

---

## Phase K8: Self-Improvement — Knowledge Layer Evolves Itself

The knowledge layer should improve its own structure over time.

- [ ] Prune stale connections (Cognee-inspired): edges that are never traversed decay
- [ ] Strengthen frequent connections: edges traversed often get higher weight
- [ ] Detect missing connections: when two concepts keep appearing in the same contexts but aren't linked, suggest a link
- [ ] Principle consolidation: when multiple principles say the same thing, merge with combined evidence
- [ ] Knowledge health metrics: coverage (what topics have thin evidence?), freshness (what's stale?), connectivity (what's isolated?)

**Artifact:** Self-improving knowledge graph with health dashboard

---

## Dependencies and Ordering

```
K0 (baseline) → K1 (choose foundation) → K2 (migrate links)
                                              ↓
                                         K3 (read bridge) → K4 (write bridge)
                                              ↓
                                         K5 (lens/persona) → K7 (cross-view)
                                              ↓
                                         K6 (temporal) → K7 (cross-view)
                                              ↓
                                         K8 (self-improvement)
```

K0-K3 are the critical path. K5, K6, K7 can be worked in parallel after K3. K8 is the long game.

---

## What This Doesn't Cover (Yet)

- **Multi-user knowledge:** What happens when the dev team contributes knowledge? Currently Jeremy-only.
- **External knowledge ingestion beyond links:** Papers, codebases, documentation, conversation transcripts.
- **Knowledge sharing/export:** How does curated knowledge get shared with the team? (Currently via HTML viewer.)
- **Real-time knowledge:** Can the knowledge layer update in response to events (new X post, new commit, new Slack message)?
- **The "shared mind" across AI instances:** Multiple Poe sessions accessing the same knowledge simultaneously.

These are Phase K9+ territory.
