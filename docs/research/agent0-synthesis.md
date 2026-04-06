# Agent0 + Self-Improvement Papers — Research Synthesis

**Source:** dogfood run `agent0-research` (loop `ee4d5e86`, 2026-04-05, 8 steps, $2.49)
**Papers covered:** Agent0 (arxiv 2511.16043), FunSearch (Nature), EUREKA (arxiv 2310.12931), Voyager (arxiv 2305.16291)

---

## The 3 Laws (Cross-Paper Convergence)

1. **State-aware difficulty** — all 4 systems track current capability and target the frontier; none uses a fixed curriculum
2. **Actor/evaluator separation** — never self-grade with the same model pass; error traces always in training context
3. **Preserve partial wins** — externalize knowledge as reusable artifacts (weights, programs, reward code, skill library); maintain diversity

---

## Agent0 Core Mechanism

**Two co-evolving agents:**
- **Curriculum Agent (π_θ)**: generates task batches from its own policy — no seed dataset, no human problems
  - Reward: `R_C = R_unc + R_tool`
  - `R_unc = 1 - 2|p̂ - 0.5|`: peaks at p̂=0.5 (frontier tasks), zeros at trivial/impossible
  - `R_tool` = code-interpreter call count → drives tool-use curriculum
  - Optimizer: GRPO (no critic)
- **Executor Agent (π_φ)**: solves tasks
  - Reward: binary (answer == majority-vote pseudo-label ỹ)
  - Gradient scaled by `f(p̂)`: harder tasks get higher gradient weight
  - Training: ADPO on multi-turn rollouts with sandbox execution

**Virtuous cycle:**
1. Executor improves → existing tasks become easy (p̂→1, exit δ-band)
2. Curriculum updates to harder frontier tasks (R_unc rewards p̂≈0.5)
3. Executor trains on harder set
4. Error traces in trajectory context → model learns error→recover→succeed
5. Repeat for T iterations → confirmed: executor pass-rate DECREASES per iteration (harder tasks), tool-call count INCREASES

**Results:** +18% math, +24% general reasoning on Qwen3-8B-Base across 10 benchmarks

---

## FunSearch (DeepMind)

- Evolutionary program search: LLM generates programs → formal fitness function selects → best programs become in-context exemplars
- Island model prevents homogenization (we've implemented this for skills)
- Key: search over program space, not answer space — correctness is provably verifiable

## EUREKA (RL reward function generation)

- Generates the *reward function itself* (as Python code), not just solutions
- GPT-4 mutates top-k rewards based on actual RL training curve feedback
- Most meta-level pattern — applicable when "what is good" is uncertain
- 83% win rate vs human-designed reward functions (ICLR 2024)

## Voyager (Embodied lifelong learning)

- Explicit external skill library (executable JS code) — retrieved by embedding similarity
- Auto-curriculum proposes next task based on current skills + game state
- GPT-4 critic reads error messages and iterates up to 3x per task
- Knowledge compounds combinatorially — early skills unlock mid-tier tasks, mid-tier unlock complex
- Result: 15.3x faster tech tree than baseline

---

## Poe Architecture Gap Analysis

| Paper pattern | Poe module | Gap |
|---|---|---|
| EUREKA: mutate rules based on fitness | evolver.py | Already mutates rules, but lacks fitness measurement over rule quality |
| Agent0 ADPO: records failure→recovery chains | memory.py | Records outcomes, but not failure→recovery chains |
| Voyager: test-on-promotion + repair loop | skills.py | Library exists, no test-on-promotion or repair loop |
| FunSearch: island model for rule evolution | constraint.py | Applied uniformly, no variant testing or fitness selection |
| Agent0: majority-vote pseudo-labels | memory.py | Promotes lessons without agreement voting |

---

## Top 5 Recommendations (Priority Order)

### 1. Failure-Chain Lesson Recording ✅ DONE (2026-04-05)
**Where:** `memory.py` | **Effort:** Low

Record full `failure → diagnosis → recovery → success` chain, not just final outcome.
Every retry becomes a training signal. New fields: `failure_chain`, `recovery_steps`.

### 2. Majority-Vote Pseudo-Labels
**Where:** `memory.py` | **Effort:** Low-Medium

Run k=3 samples for verifiable tasks; only promote lessons where majority of attempts agree.
Eliminates false lessons from noisy single-attempt outcomes.
Implementation: `record_outcome()` accepts optional `k_samples` list; `extract_lessons_via_llm()` checks agreement before promoting.

### 3. Frontier Task Targeting
**Where:** `skills.py` + `evolver.py` | **Effort:** Medium

Track per-skill pass-rate; evolver targets the 40–60% zone (neither trivially easy nor impossible).
Outcome-based: only trigger skill rewrite when pass-rate exits the frontier band.

### 4. Skill Validation Harness
**Where:** `skills.py` | **Effort:** Medium

Test case on promotion + 3-attempt repair loop before skill enters the library.
Direct mirror of Voyager's mechanism. Prevents bad skills from entering the gene pool.

### 5. Rule A/B Variants
**Where:** `constraint.py` + `evolver.py` | **Effort:** High (defer)

Generate 3 variants per rule proposal, route by task_id hash, retire losers after eval window.
Enables compound rule quality gains. Architectural risk — defer until evolver is stable.

**Recommended start:** Ship #1 now (memory.py only, zero architectural risk).
