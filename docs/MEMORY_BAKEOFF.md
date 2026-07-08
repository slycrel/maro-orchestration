---
status: living
---

# Memory Backend Bake-off

Working doc for the memory-module arc (MILESTONES arc -1; decree in
GOAL_BRAIN Decisions 2026-07-07; framing in `MEMORY_DECISION_BRIEF.md`).
Candidates are evaluated as swappable backends behind `src/memory_port.py`
(5 verbs, hierarchical scopes, invalidate-never-delete). The contract suite
`tests/test_memory_port.py` is the entry bar: a candidate joins by adding
one factory to `ADAPTERS` and passing all 24 tests. Becomes a `record` doc
when the verdict lands with Jeremy.

House constraints scoring is done against: local/no-external-API, no
daemons, swipe-over-deps, "decay trust never data", scope hierarchy
(own + ancestors, never siblings), perf on the 2014 Mac Mini (CPU-only),
maintainability over cleverness.

---

## Round 1 — paper screen (2026-07-07)

Method: three parallel research agents, one per candidate, each cloning the
repo and answering from source (not READMEs), mapping APIs against the five
port verbs. Per the verify-before-fix rule, the four decisive claims were
then re-verified by hand against the clones before any conclusion below:

1. **TencentDB local embedding is deliberately unreachable** — VERIFIED,
   `src/config.ts` (~391): `provider:"local"` → disabled; comment:
   "Internal LocalEmbeddingService code is preserved but not reachable from
   config… Please configure a remote embedding provider." As shipped,
   vector search requires a remote API.
2. **TencentDB hard-deletes behind its own pipeline** — VERIFIED,
   `sqlite.ts`: `deleteL1/deleteL1Batch/deleteL1Expired` + dedup merge and
   TTL cleaner physically `DELETE FROM l1_records/l1_vec/l1_fts`.
3. **Graphiti's only in-process backend (Kuzu) is deprecated** — VERIFIED,
   `kuzu_driver.py:148`: constructor emits DeprecationWarning "upstream
   Kuzu project is no longer maintained. Migrate to Neo4j or FalkorDB."
4. **Mem0 `infer=False` stores with zero LLM calls** — VERIFIED,
   `mem0/memory/main.py` `_add_to_vector_store`: `if not infer:` embeds and
   `_create_memory`s directly; only the embedder runs.

### Scorecard

| Criterion | TencentDB Agent Memory | Mem0 (OSS v2.0.11) | Graphiti (v0.29.2) |
|---|---|---|---|
| append | partial (closed 3-type enum, no trust; pipeline later rewrites app appends) | **good** (`add(infer=False)`, metadata free-form) | good (`add_triplet` w/ pre-set timestamps = zero LLM; else LLM dedupe fires) |
| recall | RRF hybrid but **no scope param on any search path** | **good** (filters mini-DSL; hybrid BM25+vector+entity) | **good** (hybrid BM25/vector/BFS + RRF/MMR; BM25-only = zero model calls) |
| get | internal stmt only (raw-SQL shim) | exact | exact |
| link / neighbors | **absent** (no graph at all) | **absent** (v3 deleted graph stores; entity boost is not a read API) | **native** (typed edges, `get_by_node_uuid`, BFS) |
| invalidate-never-delete | **structurally impossible** — 3 independent hard-delete paths | partial hack (`expiration_date`+`show_expired`; sqlite history keeps content) | **native & verified** (bi-temporal `invalid_at`/`expired_at`, set-and-resave; search includes expired by default) |
| scope hierarchy | none (would fork the store) | none native; ~20-line adapter shim via metadata scope path + `in` filter | none native; ~20-line adapter shim via group_id lists (no `/` allowed) |
| local / no-API | **fails as shipped** (vector needs remote embed API; LLM pipeline every cycle) | passes (embedded qdrant local mode + local embedder, `infer=False`) | borderline (BM25-only mode = zero model calls, but backend = deprecated Kuzu or falkordblite redis subprocess) |
| daemons | Node-22 sidecar `:8420`, unauthenticated by default (Hermes mode) | none (pure library) | falkordblite forks a bundled redis-server child (borderline) |
| deps weight | Node ≥22.16 runtime in a Python shop | heavy (torch/spaCy/qdrant/fastembed) but prunable; PostHog telemetry ON by default | moderate; PostHog telemetry ON by default; falkordblite needs py≥3.12 (box: 3.14 ✓) |
| license / health | MIT; 7.2k stars but commits decayed to 0/week, bus factor ≈1, ~210 unmerged PRs | Apache-2.0; 60k stars, very active; heavy platform gravity, v3 broke APIs | Apache-2.0; 28k stars, very active; roadmap follows Zep cloud |
| agent's adapter cost | **L — "a fork, not an adapter"** | M | M |

### Eliminated after round 1: TencentDB Agent Memory

Three of five verbs need engine changes, not shims; the killer is
`invalidate` — its own background pipeline (dedup, TTL cleaner, L2 cleanup)
destructively rewrites and deletes content behind the port's back, so
"retrievable forever" cannot be honored without disabling the consolidation
pipeline that *is* the product. Add: wrong runtime (Node 22), no scope, no
links, README claims that don't survive source reading, and sharply decayed
maintenance. **Also a caution for the OpenClaw side: its npm `postinstall`
runs a script that patches the host OpenClaw installation** — do not
install it into the live OpenClaw without reading that patch first.
Its steal-notes survive (below); the codebase doesn't come with us.

### Convergent findings (all three candidates)

- **Nobody has hierarchical scope.** Own-plus-ancestors visibility lives in
  our adapter no matter what — the port's `visible_at()` and ancestor-list
  computation are permanent our-side code. (Vindicates the port decision.)
- **Only Graphiti treats invalidation the way we do**, and its bi-temporal
  schema is worth stealing verbatim regardless of the verdict.
- **All three phone home or fetch by default** (PostHog ×2, spaCy/HF model
  auto-downloads, hardcoded telemetry keys). Sandboxed venvs + telemetry
  env-vars are mandatory in round 2.
- All three research agents independently landed on some flavor of
  "steal-from, don't adopt" for this box's constraints. Noted — and treated
  as a hypothesis for round 2, not a verdict: paper screens have known
  static-probe bias, so the surviving candidates still get live trials.

## Round 2 — live trials (roster)

Each trial: sandboxed venv in scratchpad, telemetry disabled, adapter
implementing `MemoryStore`, entry added to `ADAPTERS`, full contract suite,
then a retrieval-quality corpus (real goals/lessons from run history; per
query, candidates' top-k judged blind by a persona panel — the reusable
"run this prompt with this persona" recipe), plus append/recall latency on
this box and install footprint.

1. **Mem0** — embedded qdrant (`path=`, in-process) + local embedder
   (fastembed ONNX preferred over torch), `infer=False`, `MEM0_TELEMETRY=False`.
   Known owed shims: scope path in metadata, expiration-based invalidate,
   sidecar edges for link/neighbors.
2. **Graphiti** — conditional on falkordblite wheels installing clean on
   py3.14; BM25-only search config (zero model calls), `add_triplet` with
   pre-set timestamps, `GRAPHITI_TELEMETRY_ENABLED=false`. If the backend
   install fights back, eliminate on environment and record it here.
3. **Adapter-0-grown ("ours")** — `src/memory_jsonl.py` upgraded with the
   stolen ideas below (bi-temporal fields, FTS5 index-as-cache with
   staleness check, RRF fusion). This is the build-our-own candidate,
   competing under the identical suite — it wins on evidence or not at all.

## Consolidated steal-notes (apply to ours regardless of verdict)

From **Graphiti**: 4-field bi-temporal schema (`created_at`/`expired_at`
transaction time, `valid_at`/`invalid_at` event time) — steal verbatim;
invalidate = set-and-resave + filter-at-query-time (no tombstones);
deterministic interval-overlap contradiction math with pluggable candidate
selection; RRF over per-method result lists + named search-recipe objects;
fact-as-sentence on edges (graph degrades to sentence store with links);
exact-match fast path before any model call.

From **Mem0**: append-only history table beside the store (~100 lines of
sqlite = 80% of invalidate-never-delete); UUID→small-int aliasing before an
LLM ever sees ids (anti-hallucination); score fusion with over-fetch +
`explain=True` per-signal score details; portable filter mini-DSL;
`expiration_date`+`show_expired` read-time soft-hide; lemmatize at write
time; graceful degradation when an optional signal is missing.

From **TencentDB**: `embedding_meta` table + auto-reindex on provider/model
mismatch (insurance against silently mixed embedding spaces); append-only
JSONL as source of truth with SQLite as rebuildable index (matches our
staleness lesson from dev-recall); RRF with capability-flag degradation
(hybrid → vector-only → FTS-only without caller changes); FTS5 twin-column
trick (normalized indexed + raw `UNINDEXED`); deferred embedding backfill
(flat append latency on slow CPU); warm-up consolidation trigger (doubling
threshold + idle timeout); scene-index progressive disclosure (inject small
index + paths, agent reads full content on demand); consolidation decisions
as explicit `store|update|merge|skip` contract — but demote, never delete.

---

## Appendix — full dossiers (agent research, claims spot-verified as noted)

# Mem0 Dossier — evaluation as MemoryStore backend
(researched 2026-07-07 from source, mem0ai/mem0 @ v2.0.11, clone in scratchpad/mem0)

## 1. What it actually is
OSS core is a library, not a daemon. `Memory.__init__` (mem0/memory/main.py:445) composes: vector store (26 backends, qdrant default; content lives in vector payload, no separate doc store), embedder (openai default; hf/ollama/fastembed local options), LLM (only for infer=True extraction), SQLite history DB (append-only ADD/UPDATE/DELETE audit per memory_id, ~/.mem0/history.db).
v3 direction change: external graph stores REMOVED from OSS (docs/migration/oss-v2-to-v3.mdx:331; Neo4j/Memgraph/Kuzu deleted ~4,000 lines). Replacement: spaCy heuristic entity linking (no LLM) in a parallel {collection}_entities vector collection, surfaces only as score boost (_compute_entity_boosts main.py:1685).
Retrieval: semantic + BM25 (qdrant sparse via fastembed) + entity boost fused in mem0/utils/scoring.py:score_and_rank.
Platform split explicit: timestamp/reference_date params raise platform-only errors; optional REST server/ + OpenMemory MCP daemons not needed.

## 2. Port-verb mapping
- append → Memory.add(..., infer=False) GOOD; kind/trust/provenance/scope go in metadata (preserved, main.py:1886). infer=False branch skips entity linking.
- recall → Memory.search(query, top_k, filters) GOOD; kinds via filters={"kind":{"in":[...]}}.
- get(id) → Memory.get EXACT.
- link/neighbors → NOTHING PUBLIC. Entity store internal, no typed relations, no neighbors API. Full sidecar shim needed.
- invalidate → PARTIAL: no soft-delete; delete() is hard. Hack: update(id, expiration_date=<past>) hides from search unless show_expired=True; content retained; SQLite history keeps old/new forever. Date-granular + semantic overload.
- SCOPE HIERARCHY: NO. user_id/agent_id/run_id are flat AND-ed tags; child does not see ancestor. Shim (~20 lines, all in our adapter): fixed user_id + metadata scope path + filters {"scope":{"in":[ancestor list]}} — ancestor list computed by our port.

## 3. Local-run reality
- add() without LLM: YES, verified (main.py:831 `if not infer:` → embed + _create_memory only).
- Defaults assume OpenAI; openai + qdrant-client are hard deps. OPENROUTER_API_KEY auto-switch exists.
- Min local config: qdrant embedded local mode (path=..., in-process, no server) or faiss (loses BM25); embedder huggingface sentence-transformers CPU (multi-qa-MiniLM-L6-cos-v1) or fastembed ONNX; infer=False everywhere.
- Phones home by default: PostHog telemetry ON (hardcoded key, us.i.posthog.com) — set MEM0_TELEMETRY=False; spaCy en_core_web_sm auto-downloads at first use (without it entities silently []); first HF model fetch.
- Pure library. Heavy install: torch + sentence-transformers + spaCy + fastembed + qdrant-client — sits badly with swipe-over-deps.

## 4. License + health
Apache-2.0. 60,330 stars / 7,002 forks / 497 open issues (2026-07-07). Multiple commits/day; v2.0.11 released 2026-07-01. v3 rewrite broke APIs (graph deleted; top_k 100→20, threshold None→0.1, rerank True→False). Heavy platform pull: OSS↔platform converging, decay/temporal land platform-first, telemetry "notices" nudge OSS users.

## 5. Steal-notes
1. UUID→small-int aliasing before LLM sees ids (anti-hallucination, main.py ~885).
2. Entity side-collection instead of graph DB ({collection}_entities + linked_memory_ids); hub-damping memory_count_weight = 1/(1+0.001*(n-1)^2) (main.py:1755).
3. Score fusion with over-fetch (max(4k,60) candidates) + explain=True per-signal score_details — debuggable rankings.
4. Append-only history table alongside store (old/new/event/actor/timestamps) — 80% of invalidate-without-delete in ~100 lines of sqlite.
5. expiration_date + show_expired: soft-hide as payload attribute checked at read time.
6. Portable filter mini-DSL (eq/ne/in/nin/gt/gte/lt/lte/contains + AND/OR/NOT) translated per-backend — the shape our port wants.
7. text_lemmatized stored at write time; graceful degradation throughout (no spaCy → no entities → search still works).

## 6. Adapter cost: M
append/recall/get near-direct; invalidate semi-clean shim; link/neighbors zero support → sidecar = we build the hardest fifth ourselves anyway.
Biggest risk: platform gravity + churn (v3 deleted whole graph subsystem in one release; OSS reads as funnel to api.mem0.ai). Bottom line: steal-from, don't adopt, unless we specifically want their hybrid-retrieval quality off the shelf.

# Graphiti Dossier (v0.29.2 @ 62ff03ac, researched 2026-07-07, clone in scratchpad/graphiti)

## 1. What it is
Python lib (graphiti_core): episodes → temporal knowledge graph via LLM extraction, external graph DB behind GraphDriver (Neo4j/FalkorDB/Kuzu/Neptune). Semantic payload on EntityEdge (edges.py:263): natural-language `fact` + embedding.
BI-TEMPORAL VERIFIED (edges.py:271-281): created_at/expired_at = transaction time; valid_at/invalid_at = event time. Invalidation non-destructive: resolve_edge_contradictions (utils/maintenance/edge_operations.py:536-573) sets invalid_at + expired_at and RE-SAVES, never deletes. Search does NOT filter expired by default — exclusion opt-in via SearchFilters.expired_at (search/search_filters.py:27-66). Caveat: contradiction CANDIDATE selection is an LLM prompt; date math deterministic.
Search: true hybrid — cosine | bm25 (DB fulltext) | breadth_first_search, fused by rrf/mmr/node_distance/episode_mentions/cross_encoder; recipes in search_config_recipes.py. BM25-only config = zero model calls. Episodes are BM25-only.

## 2. Port-verb mapping
- append → add_episode (LLM extraction) or add_triplet (pre-structured). trust: NO native field → attributes dict (not a ranking input). provenance → episodes list.
- recall → search()/search_() + group_ids + SearchFilters. kinds → edge_types/node_labels. Default recall adds expired_at IS NULL; include_invalid = omit filter.
- get → EntityEdge.get_by_uuid / EpisodicNode.get_by_uuid. Direct.
- link/neighbors → add_triplet; get_by_node_uuid (edges.py:543) / get_between_nodes (edges.py:410); rel filter = small Cypher shim.
- invalidate → NO public API but shim is easy+idiomatic: get_by_uuid → set expired_at/invalid_at → save. reason → attributes['invalidation_reason'].
- SCOPE: group_id is FLAT partition, regex ^[a-zA-Z0-9_-]+$ (NO slashes; must encode thread--x--run--y). Hierarchy entirely in our adapter (resolve scope → [self+ancestors] list per read, ~20 lines); nothing enforces it. Cross-partition edges don't exist — thread-scoped fact can't link to global entity node (entity duplication across scopes is the documented pattern).

## 3. Local-run reality
Backends: Neo4j (JVM daemon — out); FalkorDB (Docker daemon — out); FalkorDB Lite (embedded but actually forks bundled redis-server child over unix socket; Python ≥3.12, redis<9; borderline under no-daemons); Kuzu — only true in-process AND formally DEPRECATED in constructor ("upstream no longer maintained"; Kuzu Inc. shut down 2025).
LLM: ingestion cannot fully skip LLM. add_triplet skips extraction but resolve_extracted_edge still LLM-calls for dedupe when similar edges exist; _extract_edge_timestamps LLM-calls unless valid_at/invalid_at pre-set (early-return, edge_operations.py:587). Zero-LLM only for triplets-with-timestamps into empty neighborhoods. No disable flag. GLiNER2Client (local CPU NER) requires a delegate llm_client anyway.
Embedders in-core: OpenAI/Azure/Gemini/Voyage ONLY — no local; custom EmbedderClient is a small interface, or BM25-only. Local reranker exists (bge-reranker-v2-m3, slow CPU). Telemetry: PostHog on by default (GRAPHITI_TELEMETRY_ENABLED=false).
Net for this box: falkordblite subprocess + Ollama daemon + custom embedder, slow CPU extraction — two house rules bent before any adapter code.

## 4. License + health
Apache-2.0, CLA required. 28,480 stars / 413 open issues; last push 2026-07-06; 49 commits in June. Genuine engine of Zep's hosted product; roadmap follows Zep commercial needs (Kuzu deprecated toward Neo4j/FalkorDB).

## 5. Steal-notes
1. 4-field bi-temporal schema (created/expired = transaction; valid/invalid = event) — steal verbatim into MemoryStore item schema.
2. Invalidate = set-and-resave, filter at query time (default filter expired_at IS NULL; include_invalid drops filter). No tombstones, no second store.
3. Deterministic overlap math + pluggable candidate selection (edge_operations.py:546-571 pure interval logic; swap LLM picker for same-key/threshold/caller-supplied).
4. RRF over per-method result lists (BM25 FTS5 + vector separately, fuse; MMR for diversity); recipe-objects as named search policies.
5. Fact-as-sentence on the edge + embed the fact — graph degrades gracefully to sentence store with links; our link() can carry a fact string.
6. Provenance as episode list pointing to raw stored content; remove_episode walks provenance backwards.
7. Exact-match fast path before any model call (normalized fact+endpoint hash-match short-circuit, edge_operations.py:686-697).

## 6. Adapter cost: M
All 5 verbs have landing spots + ~20-line scope shim, BUT owe: custom local embedder (or BM25-only), group_id encoding, trust bolted into attributes with post-hoc rerank, pinning LLM out of add_triplet.
Biggest risk: local-backend ground moving — only in-process driver deprecated; replacement is 3.12-only embedded subprocess pinned against redis-py 9. Honest conclusion: worth more as SCHEMA/ALGORITHM DONOR (steal-notes 1-4 ≈ 400 lines over SQLite+FTS5) than as a dependency on this box.

# TencentDB-Agent-Memory Dossier (v0.3.6 @ 4339e63, researched 2026-07-07)

## 1. What it is
~15k lines of TypeScript, Node ≥22.16 (uses node:sqlite DatabaseSync). OpenClaw plugin first; "Hermes adapter" = thin Python supervisor that Popen()s a Node HTTP sidecar on :8420 (unauthenticated by default). L0–L3 pyramid real but split across substrates: L0 raw turns → JSONL shards + l0_conversations/l0_vec(sqlite-vec)/l0_fts(FTS5); L1 atomic facts via LLM extraction (3 fixed types persona|episodic|instruction, priority 0–100, source_message_ids) → append-only JSONL ("source of truth") + SQLite index, with LLM batch dedup (store|update|merge|skip) that PHYSICALLY DELETES losing rows; L2 scenario = markdown files curated by a sandboxed tool-using LLM agent + LLM-maintained heat counter; L3 = single persona.md regenerated. Scheduling: in-process timers (L1 600s idle w/ doubling warm-up; L2 900–3600s). Retrieval: FTS5(BM25, jieba) + vector cosine, RRF k=60, threshold 0.3; injects top-k L1 + persona + scene paths; registers search tools whose usage guide is written in Chinese.

## 2. Port-verb mapping
- append: partial — closed 3-type enum, no trust, provenance only source_message_ids; app-driven appends get rewritten/deleted later by its own dedup.
- recall: ranking good (RRF hybrid) but NO scope parameter on any search path — search is global across the DB; sessionKey exists only on non-ranked queryL1Records. Hierarchical scope: nowhere.
- get(id): no public API (internal prepared stmt; raw-SQL shim trivial).
- link/neighbors: ABSENT. No graph, no rel types. Only implicit string links (L1→L0 ids, L1→L2 scene_name).
- invalidate: ABSENT — hard-delete only (deleteL1/Batch/Expired, dedup merge deletes, TTL LocalMemoryCleaner prunes L0/L1 and the JSONL; even L2 soft-delete markers get physically removed). include_invalid retrieval structurally impossible without forking the store layer.

## 3. Local-run reality
Node ≥22.16 + native deps (sqlite-vec alpha, @node-rs/jieba). Default embedding provider "none" → vector search OFF out of the box; LocalEmbeddingService (embeddinggemma-300m GGUF) exists but is deliberately unreachable from config (config.ts:391-398 maps provider:"local" → disabled: "Please configure a remote embedding provider"). L1/L2/L3 pipelines need a chat LLM every cycle. "Zero external API dependencies" claim: false as shipped unless self-hosting both LLM+embedding servers (ruled out on this box's CPU). No phone-home by default, BUT npm postinstall runs a script that patches the host OpenClaw installation (scripts/openclaw-after-tool-call-messages.patch.sh). Hermes mode = auto-spawned unauthenticated HTTP sidecar.

## 4. License + health
MIT (Tencent-header variant; GitHub shows NOASSERTION). 7,220 stars / 680 forks in 3 months — heavily hyped. 98 commits total; weekly cadence last 8 weeks: 22,12,6,4,9,1,4,0 — sharp decay. Bus factor ≈1 (top contributor authored last commit). ~210 open PRs unmerged. Docs/prompts Chinese-first; BM25 defaults language:"zh".

## 5. Steal-notes
1. embedding_meta table + auto-reindex on provider/model mismatch (sqlite.ts:487-555) — steal verbatim.
2. Append-only JSONL source of truth + SQLite as rebuildable index (l1-writer.ts header) — exactly our invalidate-never-deletes substrate shape.
3. RRF hybrid with StoreCapabilities degradation flags (hybrid → vector-only → FTS-only, no caller changes) — ~50-line Python port.
4. FTS5 twin-column trick: normalized indexed content + content_original UNINDEXED (sqlite.ts:756-785).
5. Deferred embedding (supportsDeferredEmbedding): metadata-only at capture, backfill vectors async — flat append latency on slow CPU.
6. Warm-up extraction trigger (doubling threshold + idle timeout).
7. Scene-index progressive disclosure (small index + heat + paths injected; agent reads full scene on demand); dedup as explicit store|update|merge|skip contract — but demote, never delete.

## 6. Adapter cost: L — "a fork, not an adapter"
Three of five verbs need schema/engine changes. Biggest risk: invalidate contract cannot be honored — its own background pipeline destructively rewrites content behind the port's back. Net: don't adapt; swipe items 1–5 into a ~500-line Python MemoryStore on stdlib sqlite3 (+optional sqlite-vec loadable extension — no daemon, no Node).
