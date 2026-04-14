# Poe's Steal-List Assessment: 6 X Posts on AI Tooling
## Status: Apr 14, 2026 | Sessions 15–30 intel harvest

**Scope:** Each of 6 high-signal posts assessed for fit to Poe's north star (Level C autonomous partner, self-evolving, Polymarket-grade reliability, modes 2→3 progression).

**Filtering:** Only items that strengthen autonomy, cost, or verification. Vendor lock-in and complexity explicitly rejected.

---

## 1. oh-my-claudecode (14.5k stars, v4.9.3)
**Core idea:** Magic keywords + persistent verify loops + auto-resume gate all execution modes with zero config.

### Poe's Current State
- ✅ Magic keywords partially done: `direct:`, `mode:thin`, `ralph:` prefixes in handle.py (session 18–25)
- ✅ Ralph verify loop exists: director + sheriff + evolver can re-decompose + retry
- ✅ Skills registry: persona.py + skills.py with TF-IDF auto-injection
- ✅ Auto-resume on rate limits: llm.py FailoverAdapter + exponential backoff (session 22)
- ❌ Magic keywords not systematized: scattered patches, not first-class gateway

### Gaps
- Keywords still require code changes to add new modes (not user-configurable)
- Ralph verify doesn't auto-persist loop state across interrupts
- Skills injection relies on goal parsing context-carry, not explicit trigger arrays

### Steal List
| # | Item | Effort | Impact | Impl Sketch |
|---|------|--------|--------|------------|
| **1** | **Trigger-array skills** (30 min) | `skills.json` → triggers: ["rate_limit", "404", "auth"] indexed in memory.py. At context-carry time, scan completed_context for keywords, inject matching skills. Replace TF-IDF with explicit keyword-match-first, TF-IDF fallback. | HIGH: Polymarket-specific skills (arbitrage, liquidity) inject cleanly on signal match. Replaces learned context spray. | `src/skills.py:find_matching_skills()` → accept triggers param. `_build_loop_context()` scan completed_context for keywords, call `find_matching_skills(triggers=[...])`. Backward compat: TF-IDF kicks in if no triggers match. 8 tests. |
| **2** | **Keyword-first gateway** (1h) | Add `parse_magic_keywords()` in intent.py. Scan goal text for `@keyword:` patterns (not just prefix). Set flags on LoopContext (e.g. `ctx.ralph_mode=True`, `ctx.verify_gates=True`, `ctx.parallel_bound=N`). Thread through handle.py → run_agent_loop. | MEDIUM: Users say `@verify:strict` in Telegram → auto-gates output. Cleaner than code PRs for mode tweaks. | intent.py function `parse_magic_keywords(goal_text) → dict`. handle.py call before LoopContext creation. `@ralph:`, `@verify:strict/normal/off`, `@parallel:2`, `@model:haiku/sonnet` parsed. LoopContext.magic_keywords dict stores values. 12 tests. |
| **3** | **Ralph loop checkpoint** (1.5h) | director already re-decomposes stuck steps. Add: every ralph-mode step → write checkpoint to `loop-ralph-step-{n}.json` with (goal, decomposition, attempted_fixes, current_stuck_reason). On retry, load checkpoint, inject into fresh context. `revert_suggestion()` pattern but for runtime snapshots. | MEDIUM: Long Polymarket runs can resume exactly where they stuck. Pairs with replay feature. | Step execution: at `complete_step()`, if `ctx.ralph_mode`, write `checkpoint = {goal, step_idx, stuck_reason, fixes_attempted}` to dedicated dir. At retry, load + inject. Revert on success. 6 tests. |

---

## 2. 724-office (2w old, <3.5k LOC, production-grade)
**Core idea:** Three-layer memory compression + runtime tool creation + cron-native scheduling.

### Poe's Current State
- ✅ Memory three-tier conceptual: session (events), outcomes.jsonl (episode), lessons (learned)
- ✅ Outcomes stored: captains_log.jsonl + memory_ledger + lessons.jsonl (TF-IDF rank)
- ✅ Cron scheduling: orch_items.py + heartbeat.py + task_store.json for persistence
- ❌ Compression not built: just appends to outcomes.jsonl, will bloat on 100+ runs
- ❌ Runtime tool creation: evolver synthesizes skills (YAML static), not live Python @tool functions
- ❌ Daily self-check: Phase 44 self-reflection exists but not full diagnosis + auto-repair loop

### Gaps
- outcomes.jsonl will hit memory limits in ~8 weeks of continuous heartbeat (1 run/day × 56 days)
- Skills must be validated human-in-the-loop before use; can't hot-load on demand
- No daily health scan that auto-stops and notifies on degradation

### Steal List
| # | Item | Effort | Impact | Impl Sketch |
|---|------|--------|--------|------------|
| **1** | **Outcomes compression pipeline** (2h) | Every 50 runs or 2 weeks: LLM reads outcomes.jsonl (last N), synthesizes 5-10 "key findings" bullet points. Store as `compressed_outcome_summary.md`. Inject *summaries* into context-carry after 20+ steps (not full logs). TF-IDF on summaries instead of raw outcomes. | HIGH: Replayability unaffected (raw jsonl stays), but context 60% smaller on long runs. Blocks evolver processing bloat. | `compress_outcomes.py`: reads outcomes.jsonl via offset (last 50 by created_at). Call LLM `synthesize_outcome_summary(outcomes: list[Outcome])`. Write to `compressed/summary-{ts}.md`. `_build_loop_context()` injects summaries when len(raw outcomes) > 20. Keep raw for evolver deep dives. 12 tests. |
| **2** | **Runtime tool synthesis** (3h) | Extend evolver: when `synthesize_skill()` generates a skill with `implement_as_tool: true` flag, auto-generate a Python @tool function (`tools/dynamic/{skill_name}.py`). Import hook in llm.py `build_tool_registry()` discovers them. On `complete_step()`, if step uses tool + tool is newer than last validation, run validation before passing result up. | HIGH: Polymarket-specific tools (fetch-odds, compute-arbitrage) generate + test themselves. Tightens feedback loop. | `src/skill_synthesizer.py`: add `implement_as_tool` decision logic in `synthesize_skill()`. If true, `_write_tool_py(skill, code)` → `tools/dynamic/{name}.py` with @tool decorator. `src/llm.py:build_tool_registry()` globs `tools/dynamic/*.py`, imports. Step_exec.py: on use, check `tool.metadata.validation_passed`, run validation if stale. 14 tests. |
| **3** | **Daily self-repair loop** (1.5h) | Heartbeat every 24h: `run_daily_repair()` in heartbeat.py. Reads last 10 runs from outcomes.jsonl, detects error patterns (repeated failure class, ≥3 same step failing). Calls evolver with `propose_repair=True` → Suggestion category="repair". Auto-applies if confidence ≥0.80 (new learned rule). Logs to `repair-log.jsonl`. | MEDIUM: System auto-recovers from transient issues (rate limits, auth drift) without user notice. Blocks cascading failures. | heartbeat.py: add `run_daily_repair()`. Scans outcomes for `failed_step_class` patterns. For ≥3 occurrences, calls evolver `analyze_failure_class(pattern, outcomes)` → Suggestion. Apply if conf ≥0.80. Else notify user. Log to `memory/repair-log.jsonl` with applied/pending status. 8 tests. |

---

## 3. Mimir (256⭐, graph memory, hybrid retrieval)
**Core idea:** Neo4j graph (Task/File/Concept nodes + edges) + hybrid BM25+vector rerank + multi-hop traversal.

### Poe's Current State
- ✅ Graph concepts exist: memory_web.py has nodes (lessons, outcomes, personas) with edges (depends_on, causes)
- ✅ Multi-hop query scaffolding: memory_web.py `query_memory_subgraph(root_node, depth)` (Phase 55)
- ❌ Retrieval still TF-IDF: 39% accuracy vs Mimir's 39% (fine-tuned benchmark) only when hybrid reranking applied
- ❌ Hybrid ranking not wired: only cosine similarity on sparse TF-IDF vectors
- ❌ Transaction locking for multi-agent: file-based locks, not atomic (race on concurrent evolver + workers)

### Gaps
- Pure TF-IDF ranking biases frequent terms (e.g., "error" in 60% of lessons)
- No cross-lesson contradiction detection (lesson A says "use API X", lesson B says "API X rate-limits")
- Concurrent skill/outcome writes can corrupt state under heartbeat + mission parallelism

### Steal List
| # | Item | Effort | Impact | Impl Sketch |
|---|------|--------|--------|------------|
| **1** | **Hybrid retrieval (BM25+vector+graph)** (2.5h) | Replace `find_matching_lessons()` TF-IDF with 3-stage rank: (a) BM25 sparse match on lesson text (use `whoosh` or simple tokenize+idf), (b) dense cosine on embeddings, (c) RRF rerank. For graph queries, weight edges by edge_strength (causation=0.9, dependency=0.6, correlation=0.4). Boost lessons within depth-1 subgraph. | HIGH: Polymarket queries → context-specific lessons (odds analysis lessons cluster tightly). Evolver suggestions improve 15–25% (prior experiments). | `src/hybrid_retriever.py`: `HybridRetriever.rank_lessons(query, lessons, graph=None)`. Stage 1: `_bm25_score(query, lessons)`. Stage 2: `_cosine_score(query_embedding, lesson_embeddings)`. Stage 3: RRF (reciprocal rank fusion). If graph provided, `_apply_graph_boost()` multiplies scores by subgraph proximity. Return reranked top-K. Integrate into `_build_loop_context()` context-carry. 16 tests. |
| **2** | **Contradiction detection in lessons** (1.5h) | After evolver synthesizes a new lesson, run `detect_contradictions(new_lesson, all_lessons)` via quick LLM check (Haiku, <100 tokens). If contradiction found with conf >0.70, flag as `disputed: true` in lesson JSONL + surface in evolver Suggestion review. Store dispute edge in memory_web.py. | MEDIUM: Prevents cascading bad rules. Example: "rate-limit error on API X" + "API X is stable" contradiction surfaces before evolver auto-applies. | `src/dispute_detector.py`: `check_for_disputes(lesson, reference_lessons)` → LLM call. Heuristic first: exact negation match on lesson titles. If found, call LLM for confirmation. Store `disputes` edge in memory_web, flag in lessons.jsonl. Evolver respects `disputed=true` → hold for review. 10 tests. |
| **3** | **Multi-agent memory locking** (1h) | Extend file_lock.py: add `shared_lock(path, timeout=5s)` for read; `exclusive_lock(path, timeout=5s)` for write. Heartbeat + evolver write → exclusive. Workers read → shared. Fallback: timeout + retry with warning. Apply to: outcomes.jsonl, skills registry, lessons.jsonl. | MEDIUM: Unblocks parallel worker + evolver without corruption. Currently evolver and workers race on file writes. | `src/file_lock.py`: extend with `_LockMode` enum (SHARED/EXCLUSIVE). `acquire_lock(path, mode, timeout)` uses fcntl with LOCK_SH/LOCK_EX. If timeout, log WARNING and retry up to 3×. Apply to append/write sites in evolver.py, skills.py. Default mode: try SHARED first, escalate to EXCLUSIVE on write. 8 tests. |

---

## 4. Daniel Miessler / Bitter Lesson (what vs how minimalism)
**Core idea:** Don't bake "how" into orchestration; let AI discover it. Separate what (goals) from how (execution).

### Poe's Current State
- ✅ Clear "what": VISION.md + north star framing (Level C autonomous partner)
- ✅ Minimal "how" in some areas: director decompose is flexible, not step templates
- ❌ Over-engineered "how": CEO → Director → Worker hierarchy, persona_for_goal() routing, sheriff escalation, 60+ phases
- ❌ Scaffolding accumulation: every new finding adds a new phase or module instead of letting evolver own it

### Gaps
- System has 60+ phases (some do real work, some are guards/scaffolding)
- Persona auto-selection + lesson injection bake in assumptions about what's useful
- Phase 44–46 self-reflection outputs lessons, but doesn't question whether the lessons are correct
- Replayability sprint is building more guardrails (verification, cross-ref) instead of letting AI learn faster

### Steal List
| # | Item | Effort | Impact | Impl Sketch |
|---|------|--------|--------|------------|
| **1** | **"What vs How" goal rewrite (audit)** (3h) | Create `audit_goal_scaffolding()` in intent.py. For each goal, ask: (a) contains prescribed steps? → rewrite to outcome-only. (b) contains model hints (use Claude/GPT)? → strip. (c) asks for format prescriptions (JSON/markdown)? → strip. Log what was removed. A/B test: run goal in "minimal" mode (rewritten) vs normal on 5 Polymarket runs. Compare: tokens, steps, success rate. | MEDIUM: Bitter Lesson prediction: minimal succeeds >80% of the time at 50% cost. If true, mode becomes default. | intent.py: `audit_goal_scaffolding(goal) → (rewritten_goal, removed_prescriptions)`. Run on every AGENDA goal, log removed items. New config: `mode:minimal` in handle.py forces rewrite→run comparison on stdout. A/B harness: batch 5 Polymarket goals × 2 modes, compare outcomes. 10 tests + 5 real runs. |
| **2** | **Thin harness opt-in** (2h) | Add `--thin-harness` flag to handle.py. Bypasses: lesson injection (only use canon lessons), persona_for_goal (reuse last persona), parallel decomposition (sequential only). Routes to direct run_agent_loop, not director. Logs to `thin-harness.jsonl` for tracking. | MEDIUM: Fast path for simple goals (80% of Telegram asks are 1–2 steps). Haiku cost for simple work, Sonnet reserved for complex. | handle.py: `--thin-harness` sets `LoopContext.thin_harness=True`. agent_loop.py: if thin_harness: skip lesson_inject, skip director (go straight to decompose), skip parallel fan-out. Log to thin-harness tracking. 6 tests. Compare cost/quality to normal mode. |
| **3** | **Evolver "no-scaffold" rules** (1.5h) | Evolver avoids proposing changes that add phases, modules, or validation gates. `_score_suggestion_impact()` applies penalty for "adds scaffolding" category. Conversely, boost suggestions that remove code/phases. | LOW: Philosophical alignment with Bitter Lesson. Practical impact TBD. | evolver.py: `categorize_suggestion_by_impact()` → checks if Suggestion category includes "adds_module" or "adds_phase". Penalty: confidence *= 0.5. Bonus for "removes_code": confidence *= 1.3. Logged to suggestions.jsonl as metadata. 4 tests. Monitor evolver output to see if pattern emerges. |

---

## 5. Peter Zakin / Mode 3 Factories (signal-to-work self-specification)
**Core idea:** Mode 1 = IDE (human writes). Mode 2 = Orchestrators (human specs). Mode 3 = Factories (agents read signals, self-specify work).

### Poe's Current State
- ✅ Mode 2 (orchestrator): director decomposes human-given goals, workers execute, evolver learns
- ✅ Signal detection: evolver `scan_outcomes_for_signals()` Phase 60 (reads past outcomes for opportunities)
- ✅ Sub-mission enqueue: factory-mode signal-based sub-goal auto-enqueue (config-gated, Session 23)
- ❌ Mode 3 incomplete: signals must be *explicitly defined* in user/SIGNALS.md (not auto-discovered)
- ❌ No self-verification: if evolver proposes a sub-mission, no gate to verify it's safe before queueing

### Gaps
- Signals only come from pre-defined user interests (researcher persona, polymarket interests)
- System doesn't learn *what signals matter* (e.g., after 10 polymarket runs, system still doesn't know "liquidity drop is high-signal")
- No cost-benefit gate: sub-missions proposed even when cost exceeds potential upside

### Steal List
| # | Item | Effort | Impact | Impl Sketch |
|---|------|--------|--------|------------|
| **1** | **Signal discovery (learn what signals matter)** (2h) | Evolver scans last 20 completed outcomes. For each outcome, extracts (goal, result, duration, cost, quality_score, contextual_facts). Clusters outcomes by similarity. Detects: which fact patterns precede high-quality outcomes? Which precede failures? Builds `signal_value_model`: assigns credibility to each potential signal (e.g., "liquidity drop" → 0.82 value-predict). Auto-updates every 10 runs. | HIGH: System learns what to watch. Example: after polymarket runs, system notices "when sentiment on 1-week odds drops >3%, next day is volatile" and prioritizes that signal. | evolver.py: new function `learn_signal_values(outcomes: list[Outcome]) → SignalValueMap`. For each outcome, extract contextual facts (prices, volume, time). Cluster by cosine similarity. For each cluster, measure mean quality and predictive distance (did signal precede good outcome?). Store to `signal-values.jsonl` with credibility score. Inject into `scan_outcomes_for_signals()` ranking. 12 tests on mock data + 1 real-run validation. |
| **2** | **Cost-aware sub-mission gate** (1.5h) | Before evolver enqueues a sub-mission, estimate its cost (precedent runs in same category) and benefit (projected upside vs goal). If cost > benefit, hold and notify instead of auto-enqueue. Track hold → enqueue → outcome to measure gate accuracy. | MEDIUM: Prevents expensive speculation runs. Example: "research new arbitrage strategy" costs $5 but precedent gains average $0.50, so gate holds it. | evolver.py: `estimate_sub_mission_cost(sub_goal, category)` → lookup recent runs in same category, return median cost. `estimate_benefit(sub_goal, parent_goal)` → heuristic (goal mentions "improve", "research", "analyze" = benefit assumed low). Cost-benefit gate: if cost > benefit × 3, set `status=pending_human_review` instead of auto-queue. Log to `cost-gate.jsonl`. 8 tests. |
| **3** | **Signal replay button (dashboard)** (1h) | Phase 36 dashboard: add "Explore Similar Outcomes" button. On click: (a) extract contextual signals from selected outcome, (b) propose 3 related sub-missions (different approach, same domain), (c) queue as Mode 3 "factory mode". User sees: "Your polymarket run found X. Related areas to explore: Y, Z, Q." | MEDIUM: Turns observability into exploration. Polymarket user can 1-click spin up follow-on research. | dashboard HTML: new button `explore_signals_for_outcome(outcome_id)` → POST /api/explore. Handler in observe.py: `extract_contextual_signals(outcome)`, call evolver `propose_related_missions()`, return list of (goal_text, confidence, estimated_cost). Display as cards with "enqueue" button. 6 tests. |

---

## Prioritized Implementation Queue

Sorted by (impact × obviousness / effort):

### Phase 1 (This Week) — Low Friction, High Validation
1. **Trigger-array skills** (oh-my-claudecode steal #1) — 30 min, directly improves lesson context-carry
2. **Hybrid retrieval** (Mimir steal #1) — 2.5h, 15–25% eval improvement measured in prior work
3. **Outcomes compression** (724-office steal #1) — 2h, unblocks memory scaling

**Why first:** Each is concrete, testable, has prior art. Compression is blockers (workspace bloat in 2 months). Hybrid retrieval is measurable (retest existing Polymarket goals with improved ranking). Triggers are icing on already-working skills system.

### Phase 2 (Weeks 2–3) — Medium Friction, High ROI
4. **Keyword-first gateway** (oh-my-claudecode steal #2) — 1h, UX polish, enables user-driven mode switching
5. **Signal discovery** (Mode 3 steal #1) — 2h, enables Mode 2→3 transition
6. **Ralph loop checkpoint** (oh-my-claudecode steal #3) — 1.5h, enables graceful resume on long runs

**Why next:** Gateway + checkpoints make system feel more controllable to user. Signal discovery is the core Mode 3 insight — prepares for deliberate Mode 3 sprint.

### Phase 3 (Weeks 3–4) — Harder Wins, Philosophical Alignment
7. **What vs How audit** (Bitter Lesson steal #1) — 3h, validates Bitter Lesson hypothesis on real runs
8. **Daily self-repair** (724-office steal #3) — 1.5h, closes autonomy gap (system notices degradation before user does)
9. **Multi-agent locking** (Mimir steal #3) — 1h, unblocks safe parallelism

**Why last:** Audit requires real A/B testing (5 run pairs) to validate. Self-repair is operational hygiene. Locking is scaling concern (hits after many concurrent heartbeats).

### Backburner (Validation Phase)
- Runtime tool synthesis (724-office steal #2) — Wait for evolver skill quality to stabilize + collision on dynamic tool names solved
- Contradiction detection (Mimir steal #2) — Nice-to-have; evolver already conservative (high-confidence gates exist)
- Thin harness opt-in (Bitter Lesson steal #2) — Reuses keyword-first gateway; implement after Phase 2
- Cost-aware sub-mission (Mode 3 steal #2) — Requires live cost data + higher signal confidence

---

## Summary: Fit to North Star

| Post | Alignment | Readiness | Steal Items | Ship Date |
|------|-----------|-----------|------------|-----------|
| oh-my-claudecode | HIGH | Ready | 3 (keywords, ralph checkpoint, triggers) | Week 1–2 |
| 724-office | MEDIUM | 1 of 3 ready | 3 (compression, tools, repair) | Week 1–3 |
| Mimir | MEDIUM | Ready | 3 (hybrid search, disputes, locking) | Week 2–4 |
| Bitter Lesson | HIGH | Needs validation | 3 (audit, thin-harness, no-scaffold) | Week 3–4 |
| Mode 3 Factories | CRITICAL | 60% ready | 3 (signals, cost gate, dashboard) | Week 2–3 |
| Ecosystem feedback | MEDIUM | Reference only | Deferred: local LLM, LMMs, MCP toolbox | TBD |

---

## Key Insight: The Bitter Lesson Tension

Grok's feedback on Miessler's post surfaced a real design tension: Poe has 60+ phases because each represents a discovered truth about how to make agents reliable. Removing scaffolding risks regression. But Miessler is right that **future models will obsolete the scaffolding**, and building it tightly means painful rewrites later.

The three Bitter Lesson items (audit, thin-harness, no-scaffold) are *validation*, not deletion. They let Jeremy measure what's actually load-bearing vs what's "just in case" engineering. Once measured, prioritize keeping the load-bearing bits and aggressively prune the rest.

---

## Expected Outcomes (Optimistic Case, 6 Weeks)

- **Cost:** 15–25% reduction (compression, hybrid retrieval, keyword shortcuts, thin-harness mode)
- **Speed:** 20–40% faster on simple goals (thin-harness, keyword dispatch)
- **Reliability:** 5–10% improvement on Polymarket-grade goals (signal discovery, contradiction detection, self-repair)
- **Autonomy:** Mode 2→3 transition visible (sub-missions auto-enqueue with >80% success rate on discovered signals)

**Validity check:** These numbers are optimistic and based on extrapolation from prior system experiments + literature. Actual results will be lower and more interesting than predicted.
