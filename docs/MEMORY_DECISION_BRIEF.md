---
status: dormant-design
---

# Memory Architecture — Decision Brief (2026-07-04)

**For:** Jeremy. **Decision status: OPEN — nothing below is built or scheduled
until you call it.** This is the "filesystem vs 'real' memory / graph theory"
chunk you said you'd been avoiding. Two sub-agents inventoried every runtime
substrate and both dormant design docs against current code; every claim here
carries a file:line and was spot-verified. When you decide, this doc gets a
decision header and moves to `docs/history/`.

---

## 1. The mandate and the constraints

Decree-level anchors (GOAL_BRAIN):

> "my gut says that a real, working memory is the key (meaningful facts,
> pattern matching and fuzzy logic, skills and/or maybe learned lessons and
> so on... all the flavors of persistent working knowledge)." — 2026-06-10

> Orchestration is "recursive — orchestration all the way down" — a memory
> layer must be scoped/hierarchical: a sub-agent reads its own scope PLUS the
> higher orchestration scope, built generically. Pair with CAG-style caching.
> — 2026-06-21

Constraints this brief treats as fixed:

1. **Decay trust, never data** (BACKLOG design constraint) — append-only
   evidence stays perfect; only compiled-truth confidence decays.
2. **Justification boundary** — the memory layer is justified by replacing
   the lossy truncation caps and enabling recursive scope access. NOT by the
   485K token number (proven a cache-blind metric artifact, 2026-06-21).
3. **Steal-list sequencing** — hybrid retrieval starts BM25(+embedding
   later); SQLite adjacency for multi-hop; "not Neo4j until thousands of
   nodes" (we have 479).
4. **Swipe code over 3rd-party deps**; app-not-OS posture.
5. **Capability-form paradigm stays on the table** (Jeremy 2026-06-11):
   prompt-injection-just-in-time "grows with the model over time";
   crystallize-to-code doesn't. Any storage decision must keep language-form
   provenance recoverable (see gap G4).
6. **2026-07-04 lesson (dev-recall ghost-clone incident):** any index needs a
   staleness/provenance check against sources-on-disk or it rots invisibly —
   dev-recall served a deleted clone for 7 weeks and had zero rows from this
   repo.

---

## 2. What actually exists today (verified 2026-07-04)

The surprise: **Maro already has a working memory engine.** The dormant
design docs undersell it — several things they list as "missing" shipped
long ago (prereq sub-goals, graveyard search, Stage-5 graduation, the
crystallization dashboard).

**The live injection spine.** Exactly one memory→prompt path for the core
loop: `loop_planning._build_loop_context` → `recall.recall(goal,
slice="loop")` → fused into the decompose prompt at `planner.py:357`.
`recall()` assembles **eight substrates** (recall.py:353-446): tiered/legacy
lessons, standing rules, decision journal, graveyard resurrection, failure
notes from diagnoses, captain's-log activity, playbook, knowledge nodes.
Second path: NOW-lane banner, capped 1,200 chars (handle.py:1187).

**Crystallization is real.** Tiers with decay 0.85/day, reinforce +0.3,
promote ≥0.9 & 3 sessions, GC <0.2 (knowledge_web.py:55-59 — constants match
MEMORY_ARCHITECTURE.md verbatim). Stage 4 skills with
provisional→established tiers; Stage 5 rules genuinely bypass the LLM at
planning (`find_matching_rule` → skip decompose, loop_planning.py:301-304,
526). Stage 3 (canon→AGENTS.md) is surface-only by design — human-gated,
never auto-writes. Decay-by-invalidation v0 (knowledge_lens.py:477-539)
already implements "trust decays, data never does" for standing rules.

**Retrieval is uniformly primitive.** Every reader is TF-IDF top-K plus a
fixed char cap: lessons 1,200 (memory.py:146), playbook 800, knowledge nodes
600 + description[:200], thread brain 4,000, checkpoint export [:800]
(checkpoint.py:291), attribution [:300/:500/:500]. **Zero embeddings
anywhere in runtime** — router.py's embeddings branch is dead code (every
call hardcodes `feature_method="tfidf"`; SentenceTransformer never
instantiated). **Zero runtime SQLite** — `memory_backends.SQLiteBackend`
exists but is only wired to a benchmark command. The only FTS5 on the box is
dev-recall, which is dev-facing by decree.

**Scale check:** the largest live substrate is the captain's log at 1.4MB
active / 2.4K lines. Everything the loop actually reads fits in single-digit
MB of JSONL.

---

## 3. The five gaps (each verified, none speculative)

**G1 — Memory stops at the top of the loop.** Workers get NOTHING: a
dispatched worker's prompt is `ticket + director-supplied context + sibling
outputs` (workers.py:247-248, director.py:410-412). No recall(), no lessons,
no standing rules, no parent scope. The per-thread `goal_brain.md` that
would carry lineage into a child is read **only by the shadow-only
navigator** (thread_brain.py → navigator_shadow.py). The recursion Jeremy
wants has no memory below level one. Related live gap: "no cross-run memory
at dispatch" (GOAL_BRAIN) — the same goal re-runs blind.

**G2 — We write a graph and never read it.** `memory/knowledge_edges.jsonl`
has **2,124 edges (316KB)**; `load_knowledge_edges` (knowledge_web.py:1234)
has zero callers. Node injection uses nodes only. The wiki-link neighbor
machinery (knowledge_web.py:1356-1370) exists too. "Graph memory" is not a
greenfield project here — it's a read-side wiring project.

**G3 — Write-only graveyards, ~8MB and growing.**
`handle_inputs.jsonl` 6.4MB (no reader), `task_ledger.jsonl` 1MB
(load_task_ledger: zero live callers), `mission-log.jsonl` (no reader),
`compressed_outcomes.jsonl` (the 3-layer compression is never invoked — the
file doesn't even exist; GC just deletes old outcomes instead). And
`outcomes.jsonl` itself — the main ledger, 1,373 rows — is **not injected
into the loop prompt** (only the CLI banner + analytics read it).

**G4 — The decay-trust constraint has two unbuilt halves.**
(a) Rule auto-demote is unwired: `record_rule_wrong_answer` (rules.py:293)
has zero callers — only manual CLI demote is reachable. (b) Demotion goes
Stage 5→4 only; nothing recovers language form (Stage 2/3). If a model
upgrade or world-change invalidates a compiled rule, we can deactivate it
but can't re-fight from the evidence that produced it.

**G5 — lat.md is a stub pretending to be a graph.** 9 nodes / 19.7K chars,
but injection is ≤2 nodes × ≤400 chars ≈ 200 tokens of flat text, TF-IDF
matched, `[[links]]` never traversed, 2 callers (decompose extras + director
spec). Two nodes are fabricated vs code: poe-identity.md cites
`src/poe_self.py` + `user/POE_IDENTITY.md` (don't exist); quality-gates.md
cites `src/passes.py` + a `poe-passes` CLI (don't exist).

---

## 4. The question, reframed

"Filesystem vs 'real' memory" turns out to be the wrong axis. The evidence
says the **data layer is fine** — append-only JSONL at single-digit MB, with
a decay/promotion engine that works. What's missing is the **access layer**:
who can ask (only the top-of-loop today), how they ask (TF-IDF + char caps),
and what can be traversed (nothing — the graph is write-only). A storage
migration (SQLite/graph DB) would rewrite ~30 readers/writers and buy none
of G1–G5 by itself.

## 5. Options

**A — Storage-first ("real" memory store).** Migrate substrates into SQLite
(nodes + adjacency + FTS), readers/writers ported, decay as columns.
*Buys:* real queries, provenance edges, staleness enforcement in one place.
*Costs:* rewrites every reader/writer before any behavior improves; marries a
schema before usage patterns are known; contradicts the sqlite-indexer
BACKLOG posture ("defer until a concrete query we keep wanting").
**Not recommended now.**

**B — Access-first (scoped recall + retrieval upgrade over unchanged
files).** Keep JSONL. Make `recall()` the generic scoped interface Jeremy
described: a scope parameter (run → thread → goal-family → global), workers
get a bounded slice (parent goal_brain summary + own scope + top-K lessons),
prompts ordered CAG-friendly (stable summaries first). Upgrade retrieval
under the same seam: swipe the FTS5/BM25 pattern from correspondence.py into
an index over lessons/nodes/decisions — index as cache, sources stay
canonical, with the staleness check the ghost-clone incident taught us.
Wire the graph read-side: 1-hop edge expansion in `inject_knowledge_for_goal`.
*Buys:* G1, G2, G5, retrieval quality — without touching the data layer.
*Costs:* worker-context pollution risk (needs the experiment, §7); index
maintenance (mitigated by staleness check).

**C — Summary+handle contract (the RLM/Agentic-RAG shape).** Don't just
push memory *at* agents — every scope keeps a small always-loaded summary
plus **handles** the agent can pull on demand (read slice / query). The
"skill vs CLAUDE.md line": cheap pointer always in context, expensive
content fetched when the work needs it. `goal_brain.md` is already this
shape for threads; generalize it.
*Buys:* bounded prompts at any depth; caps become backstops, not the
mechanism; grows with the model (better models use handles better —
paradigm constraint #5 satisfied).
*Costs:* agents must actually use the handles — needs worker-tool wiring
plus measurement.

**A-lite (fold into B):** keep the SQLite *index* (not migration) from B;
revisit full storage only if a concrete multi-hop query recurs that the
index can't serve.

## 6. Recommendation

**B + C together, phased; A deferred.** They compose: C is the context
contract (summary + handle), B is the query mechanism underneath the
handles. Both stand on the approved justifications (caps replacement,
recursive scope) and stay inside the deps/app-not-OS posture.

- **Phase 0 — hygiene (no design risk, do regardless of the rest):**
  retention-cap or give readers to the G3 graveyards; wire
  `record_rule_wrong_answer` to the inspector (G4a); fix or retire the two
  fabricated lat.md nodes (G5); delete the dead embeddings branch in
  router.py or mark it intentional.
- **Phase 1 — scoped recall seam (G1):** scope parameter on `recall()`;
  worker slice = parent goal_brain summary + own scope + top-K lessons,
  capped and CAG-ordered. **Gated by the experiment in §7.**
- **Phase 2 — retrieval + graph read-side (G2):** BM25/FTS5 index-as-cache
  over lessons/nodes/decisions with sources-on-disk staleness check; 1-hop
  edge expansion in knowledge injection. lat.md's real content folds into
  goal-brains/arch-skills or becomes seed knowledge nodes (your call, §8).
- **Phase 3 — language-form demotion (G4b):** compiled artifacts (rules,
  skills) carry pointers to their source lessons/outcomes so demotion can
  re-fight from evidence. This is cheap NOW (a provenance field at
  promotion time) and impossible later (provenance not recorded is gone) —
  which is why it's in the recommendation despite being furthest out.

## 7. The experiment gate (before Phase 1 lands anywhere)

Lesson from intent-resolution: we shipped past our own experiment and never
measured. Not again. Minimum A/B for worker memory: same director mission,
workers with vs without the scoped slice; measure closure verdict,
adversarial-review findings, and worker token delta (cache-aware). If the
slice doesn't move outcomes, G1 stays open and we saved the build.

## 8. Decision points for you

1. **Direction:** access-layer (B+C) over storage-first (A) — agree?
2. **Worker slice contents/size** — how much memory does a worker get by
   default? (pollution vs blindness tradeoff; the experiment informs but
   the default is a taste call.)
3. **lat.md fate** — retire into arch-skills/goal-brains, or become seed
   knowledge nodes? (Its two fabricated nodes get fixed either way.)
4. **Graveyard policy** — handle_inputs/task_ledger/mission-log: reader,
   retention cap, or delete the write?
5. **Dormant-doc refresh** — MEMORY_ARCHITECTURE.md and
   KNOWLEDGE_CRYSTALLIZATION.md both claim shipped things are missing;
   fold their still-true intent into this direction and retire them to
   history, or keep them as-is?
6. **Sequencing** — this chunk vs the Hermes adapter / organic cutover
   re-verify already queued in MILESTONES.

---

*Sources: two sub-agent inventories (runtime substrates; design docs vs
code), 2026-07-04, all claims file:line-cited and spot-verified; GOAL_BRAIN
decree quotes; memory notes `project_recursive_orchestration_memory`,
`project_retrieval_graph_memory_direction`, `project_research_steal_list`.*
