# Gaps, Blind Spots, and Things to Watch

**Date:** 2026-04-09
**Context:** Things noticed during the cross-reference + architecture session that don't fit neatly into the other docs but shouldn't be lost.

---

## 1. The Recursive Bootstrap Problem

The knowledge layer project needs the knowledge layer it's trying to build. This conversation is a perfect example — we produced insights about knowledge architecture that are currently trapped in a Cowork session transcript. The orchestration project's SOURCES.md already tracks inspiration, but this session's output needs to get *into* the system it's describing.

**Practical implication:** Phase K0-K2 needs to happen fast enough that the design process itself can use the system. Otherwise we'll keep accumulating design knowledge in session transcripts and markdown files that aren't connected.

**Mitigation:** The conversation transcript (00_CONVERSATION_TRANSCRIPT.md) and architecture doc (01_KNOWLEDGE_LAYER_ARCHITECTURE.md) should be committed to the orchestration repo immediately. They're knowledge nodes even before the knowledge layer exists.

---

## 2. The Cross-Reference Quality Gap

The automated cross-reference (83 heavy, 139 moderate, 79 tangential) is a useful first pass, but the relevance classifications are shallow. The agent classified based on keyword overlap and topic tags, not deep understanding of *why* a post matters to the orchestration project.

**Examples of likely misclassifications:**
- Posts classified as "moderate" that are actually heavy but require domain knowledge to see the connection (e.g., management posts about preventing cascading failures → directly relevant to Loop Sheriff design)
- Posts classified as "heavy" based on keyword match ("agent") that are really about a different kind of agent system
- The "relevance_reason" field is often just "Addresses: thin" or "Addresses: feature" — these are placeholder-quality, not insight-quality

**What to do:** The cross-reference should be treated as a triage tool, not a final answer. A human pass through the HEAVY posts (at minimum) would catch misclassifications and add real insight to the relevance reasons. This is also a good Phase K0 activity.

---

## 3. The "Already Tracked" Detection Is Incomplete

We found 32 posts already tracked in SOURCES.md / STEAL_LIST.md by matching handles and URLs. But Jeremy estimated "double the explicitly listed amount." The gap is posts that influenced the project through Jeremy's brain without being formally cited — he read a post, it shaped his thinking, and that thinking showed up in a Telegram message or a design decision without the post being credited.

**This is actually a core knowledge layer problem:** How do you track the provenance of ideas that arrived through indirect channels? The ledger can track explicit citations, but the web of influence is much larger than the citation graph.

**Potential approach:** During the Phase K2 migration, when extracting principles from posts, also check: "does this principle appear in the orchestration project's design even though the post isn't cited?" That reverse-lookup would surface the uncredited influences.

---

## 4. The Orchestration Project Has Outrun Its Documentation

Reading VISION.md, ROADMAP.md, SOURCES.md, STEAL_LIST.md, and BACKLOG.md — the project has moved incredibly fast. 50+ phases completed, hundreds of steal items processed, 2200+ tests. But the docs are a mix of living documents and snapshots at various points in time.

**Specific gaps noticed:**
- SOURCES.md was "last updated 2026-03-31" but significant work has happened since
- Some STEAL_LIST items are marked DONE with dates but the implementation details are spread across multiple files
- The relationship between BACKLOG.md and ROADMAP.md items isn't always clear
- Knowledge that lives in commit messages and Telegram conversations hasn't been captured

**This is the problem the knowledge layer is meant to solve.** But it also means Phase K0 (baseline audit) needs to be thorough — there's more knowledge in the repo than the docs currently expose.

---

## 5. The Team Dimension Is Dormant

The links collection has an `audience` field (me, dev-team, leadership, team) and the orchestration project mentions the dev team and redacted context. But the knowledge layer design is currently single-user (Jeremy + Poe).

**Questions for later:**
- When (not if) the dev team starts consuming knowledge from this system, what changes?
- Does the knowledge layer power the wiki at git.redacted.com? Or is that a separate thing?
- How does team knowledge (what the dev team learns from their own experience) flow back in?

**Not urgent** but worth flagging now so the foundation doesn't paint us into a single-user corner.

---

## 6. Content Decay Is Real and Unmeasured

52 of the original 254 posts were already deleted/unavailable when the links collection was enriched. X/Twitter content is ephemeral. By the time we want to revisit a "heavy" post in 6 months, it may be gone.

**Implication for the knowledge layer:** The extracted *knowledge* (principles, patterns, summaries) is more durable than the source content. The knowledge layer should capture enough from each source that the knowledge survives even if the source disappears. The `content` field in the links collection already does this for text, but screenshots (ARCHITECTURE_PLAN.md Workstream 3) would add another layer of durability.

**Practical step:** Prioritize content extraction and principle capture for the 83 HEAVY posts before they potentially disappear.

---

## 7. The Conversation Pattern Is the Product

This session followed a pattern that's worth recognizing: Jeremy brought a task (cross-reference links), the task surfaced a bigger question (knowledge architecture), the question led to a design framework (ledger/web/lens), and the design framework led to practical phases.

**That pattern — task → question → framework → action — is itself a knowledge process.** The knowledge layer should support it: start with a concrete need, let the system surface connections, let those connections reveal structure, let the structure inform action.

This is different from "search for an answer" (RAG) or "remember what happened" (memory). It's more like... collaborative sensemaking. The knowledge layer is a sensemaking partner, not a database.

---

## 8. Things We Didn't Research That Might Matter

- **Roam Research / LogSeq** — block-based knowledge tools with graph structure. Might have patterns worth stealing for the Web layer.
- **Notion AI / Notion databases** — Not graph-native but widely used for structured knowledge. Integration patterns?
- **Johnny.Decimal** — Organizational system for managing complex knowledge hierarchies. Might inform the schema.
- **Zettelkasten method** — The original "atomic note + link" knowledge system. Well-studied, lots of prior art.
- **Knowledge Graphs in industry** — How do companies like Google (Knowledge Graph), Palantir (Gotham), or Bloomberg manage evolving knowledge at scale? Different problem but potentially transferable patterns.
- **Cognitive science of memory** — How do human brains actually do the ledger/web/lens split? Is there neuroscience that informs the architecture? (The Hindsight project explicitly claims biomimetic design.)

---

## 9. The "Base 7" Intuition

Jeremy raised the possibility that there might be organizational patterns for knowledge that AI can leverage better than narrative/timeline — patterns that humans haven't explored because we're constrained by linear time perception. This is speculative but potentially important.

**Concrete version of the question:** If you had a knowledge graph with 5000 nodes and a dozen relationship types, and you needed to find non-obvious connections, would a graph embedding in hyperbolic space surface different (better?) connections than a traditional graph traversal? Would a different distance metric reveal clusters that Euclidean distance misses?

**This is a research question, not an engineering question.** Worth flagging for a Mode 2 research mission in the orchestration project — have Poe investigate non-Euclidean knowledge representations.

---

## 10. This Document Is Itself a Knowledge Node

Meta-observation: this gaps document, the conversation transcript, the architecture doc, and the phase plan are all knowledge that needs to live in the system they describe. The first test of the knowledge layer will be whether it can ingest its own design documents and make them useful.
