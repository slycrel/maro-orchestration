# FunSearch / EUREKA / Voyager — Agent Self-Improvement Design

Research via orchestration dogfood run (`funsearch-research`, 2026-04-05). Sources: FunSearch (DeepMind Nature 2023), EUREKA (NVIDIA arxiv), Voyager (MineDojo arxiv).

---

## The Three Systems

### FunSearch (DeepMind, 2023)
Evolves a single critical Python function (`priority(x) -> float`) for combinatorial optimization via LLM + island model.

**Loop:**
1. Seed programs database with hand-written baseline
2. Sample k=2 best-scored programs (score + brevity weighted)
3. LLM generates v_next: "here are v0 (score=4), v1 (score=7) — generate v2"
4. Evaluate v_next against deterministic oracle (e.g. cap set size)
5. Crash/timeout → silent discard; scored candidates enter DB
6. Island model: N independent subpopulations; kill worst half periodically; repopulate from best survivors

**Key results:** First LLM-based genuine mathematical discovery (cap set problem). Favors compact interpretable programs.

### EUREKA (NVIDIA)
Generates RL reward functions for robot control tasks. Outperforms human-designed rewards on 83% of 29 tasks.

**Loop:**
1. Feed full environment source code to GPT-4
2. GPT-4 generates N candidate reward functions zero-shot (Python code)
3. Each candidate runs RL training episode (~2k steps, Isaac Gym)
4. Fitness = ground-truth task metric (NOT reward magnitude — prevents reward hacking)
5. **Reflection step**: LLM receives reward component statistics + training curves → analyzes what worked → generates improved variants
6. Iterate; top survivors enter next generation

### Voyager (MineDojo)
Lifelong learning agent in Minecraft. 3.3x more unique items, 15.3x faster tech-tree progression vs SOTA.

**Loop:**
1. **Curriculum**: GPT-4 proposes next task by novelty-maximization (current inventory + biome + skill library)
2. **Iterative prompting**: attempt → execution errors + env state → refine → retry up to 4x
3. **Self-verification**: separate GPT-4 call judges task completion (decoupled evaluator)
4. On verified success: store skill as named JS function with docstring + embedding
5. **Skill retrieval**: embed task → cosine similarity → inject top-k skills as context for next task

---

## 7 Shared Algorithmic Primitives

### 1. Evaluation Function (Fitness Oracle)
| System | What is scored | Key principle |
|--------|---------------|---------------|
| FunSearch | `evaluate(solution) -> scalar` | Decoupled from generator; measures true objective |
| EUREKA | ground-truth task metric (NOT reward magnitude) | Prevents reward hacking |
| Voyager | self-verification call ("was task completed?") | Separate LLM as judge |

**Rule**: Fitness oracle must be independent of the generator and measure the true objective, not a proxy. **Proxy fitness = degeneration.**

### 2. Mutation Strategy
**Rule**: LLM is the mutation operator. Context = best current solutions + feedback signal. Never random mutation — always informed recombination from ranked-candidate context.

### 3. Selection Pressure
**Rule**: Selection is two-sided — reward fitness AND penalize complexity/bloat. Short/compact preferred over elaborate/verbose.

### 4. Diversity Mechanisms
- FunSearch: Island model (N independent subpopulations)
- EUREKA: Large parallel population (N=16+ candidates per generation)
- Voyager: Novelty curriculum (maximize exploration, prevent stagnation)

**Rule**: Explicit anti-monoculture mechanism is required. Without it, populations converge to local optima within ~10 generations.

### 5. Failure Avoidance / Anti-Degeneration
**Rule**: Every invalid/crashing candidate is silently discarded before scoring — never scored, never stored. The gene pool only contains runnable artifacts.

### 6. Persistence and Memory
**Rule**: Population/history is the memory. Retrieval must match candidates to the current task context with fitness weighting, not just semantic similarity.

### 7. Implementation Checklist (universal)
1. **Sandbox**: isolated execution with time + memory limits per candidate
2. **Fitness oracle**: deterministic, independent of generator, measures true objective
3. **Population store**: persists across iterations (not just latest generation)
4. **Diversity mechanism**: islands, large population, or curriculum
5. **Crash/timeout discard**: before any scoring, silent removal
6. **Reflection loop**: feedback from evaluator back to LLM prompt (training stats, error logs, score trends)
7. **Brevity/compactness penalty**: in scoring or sampling weights

---

## Gap Analysis vs. Poe's Existing Modules

### Primitive 1 — Fitness Oracle: CRITICAL GAP
All scoring in Poe is LLM-mediated (`inspector.py:assess_goal_alignment`, `inspect_session`, `check_alignment`). The same class of model that generates strategies evaluates them. This violates the generator/evaluator separation rule. Inspector can rate degenerate strategies highly if they pattern-match to "good outcomes."

**Required:** A replay-based scorer (leverages Phase 50 thinkback) that scores strategy candidates against actual past outcome records without LLM involvement.

### Primitive 2 — Mutation Context: GAP
`run_evolver` feeds a flat outcome summary to LLM, not a ranked set of competing strategy variants with version tags. FunSearch explicitly shows LLM "here is v0 (score=4), v1 (score=7) — generate v2."

**Required:** When calling LLM to improve a skill or strategy, feed top-K ranked variants (by utility score) as version-tagged context.

### Primitive 3 — Selection Pressure (brevity): GAP
Selection is purely fitness-based (EMA success rate). No brevity/complexity penalty. Long, verbose skills are not disadvantaged vs compact ones with equal success rate.

**Required:** Add a compactness weight: `adjusted_score = utility_score / log(1 + token_count)`. Apply at sampling time for mutation context.

### Primitive 4 — Diversity: LARGEST / CRITICAL GAP
Single skills pool, single evolver pass. No anti-monoculture enforcement anywhere. Without it, skill pool will converge to a local optimum within ~10 evolver cycles.

**Required (minimum viable):** Partition `skills.py` skill pool into 2–3 named islands (by task_type tag). Run evolver passes per-island. Periodically kill bottom-half of each island and repopulate by cloning top survivors. ~50 lines using existing `load_skills()` / `save_skill()` infrastructure.

### Primitive 5 — Pre-scoring discard: PARTIAL
Test gate (`_run_skill_test_gate`) runs after LLM scoring, not before. Invalid/crashing candidates are evaluated before discard.

**Required:** Pre-scoring discard gate — any candidate that fails a deterministic sanity check must be discarded before reaching the LLM evaluator.

### Primitive 6 — Score-weighted retrieval: MINOR GAP
`find_matching_skills` retrieves by task-similarity but doesn't weight by fitness when building mutation context for the evolver.

**Required:** In `evolver.py:rewrite_skill` and `synthesize_skill`, fetch top-K candidates by `utility_score * similarity_weight`.

### Checklist Status

| Requirement | Status | Where |
|-------------|--------|-------|
| Sandbox (isolated candidate execution) | MISSING | No sandboxing of candidate strategies |
| Independent fitness oracle | MISSING | Inspector is LLM-based |
| Population store (persists across iterations) | PRESENT | `skills.py` + `memory.py` |
| Diversity mechanism | MISSING | No islands, no curriculum |
| Crash/invalid discard (pre-scoring) | PARTIAL | Test gate exists but post-scoring |
| Reflection loop (feedback → LLM) | PRESENT | `evolver._llm_analyze`, `scan_outcomes_for_signals` |
| Brevity/compactness penalty | MISSING | No token-count weighting in scoring |

---

## Concrete Design: Agent Self-Improvement via FunSearch Loop

### What Poe Would Evolve
Not weights — **strategy functions**: Python/JSON callables that control how Poe handles recurring task patterns.

Examples:
- `route_goal(goal_text) -> lane` (NOW vs AGENDA vs RESEARCH)
- `decompose_task(goal) -> [steps]` (how tasks get broken down)
- `evaluate_step_quality(output) -> score` (Inspector)
- `select_worker(task_type) -> worker_config`

### The Loop
```
1. SEED: current best strategy function(s) from skills.py / standing_rules
2. SAMPLE: pull k=2 top-scored variants from StrategyDB (score + recency)
3. GENERATE: LLM prompt → "here are two strategies that performed well;
   generate a new variant that improves on them" → new Python function
4. EVALUATE: run against held-out task replay set (thinkback/replay infra)
   → score = task_success_rate + avg_turns_to_complete - error_rate
5. FILTER: discard crash/timeout/invalid; store scored variants in StrategyDB
6. PROMOTE: top variants after N=20 evals → merge into skills.py via evolver
7. ISLAND MODEL: 3-5 strategy lineages evolve independently; merge best every K cycles
```

### Evaluation Harness (Critical)
- Use `src/thinkback.py` (Phase 50) as replay sandbox
- Score on real past task outcomes from `memory/outcomes.jsonl`
- Metric: composite of success_rate, step_count, cost_per_task (already in metrics.py)
- **Never evaluate on live runs** — replay only until promoted

### Anti-Degeneration Mechanisms
- Island model (FunSearch): 4 independent lineages, kill worst half periodically
- Score includes brevity penalty (favor compact strategies)
- Human override gate for strategies touching core routing
- Crash/exception discard before scoring
- Reflection step (EUREKA): after each eval batch, LLM analyzes which strategy components correlated with high vs low scores → feeds next generation prompt

### Implementation Path
1. `src/strategy_db.py` — store/retrieve/score strategy variants (extend task_store.py pattern)
2. `src/strategy_evolver.py` — outer loop (extends evolver.py)
3. `src/strategy_evaluator.py` — runs strategy in replay sandbox, returns composite score
4. Wire into `evolver.py` cycle every ~20 heartbeats (separate from skill rewriting)
5. Promoted strategies flow to `skills.py` via existing auto-promote pipeline

### How This Differs from Current Evolver
Current `evolver.py`: meta-improvement by analyzing outcomes and writing lessons (Reflexion-style text reflection).

This system: **generates and tests executable strategy code** before any lessons propagate (FunSearch-style verifiable mutation). The key addition is the sandboxed replay evaluator as the fitness oracle. Current system mixes generation and evaluation — this separates them as a hard architectural invariant.

---

## Gap Priority Ranking

1. **Generator/evaluator separation** — highest risk; degeneration is silent and self-reinforcing
2. **Diversity mechanism** — monoculture is irreversible once set; island model is the fix
3. **Brevity penalty** — prevents prompt bloat and complexity drift
4. **Score-weighted mutation context** — improves mutation quality without structural changes
5. **Pre-scoring discard gate** — reduces wasted LLM calls; straightforward to add
6. **Fitness oracle independence** — requires Phase 50 thinkback as replay scorer

---

*Research: dogfood run `funsearch-research`, 2026-04-05, 5 steps, 453k tokens*
