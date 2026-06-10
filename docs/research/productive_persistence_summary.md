# Productive Persistence — Executive Summary

**Definition:** Continuing effort while varying approach, anchored by evidence that each failure narrows the hypothesis space — not sunk cost.

---

## Productive Persistence vs. Stubbornness

| Aspect | Productive Persistence | Stubbornness |
|--------|------------------------|--------------|
| **Approach** | Adjusted strategy informed by failure | Identical attempt, hoping for different result |
| **Goal attachment** | Goal stable; strategy flexible | Goal unstable OR strategy rigid |
| **Quit trigger** | When hypothesis space is exhausted | When emotionally exhausted / never |
| **Sunk cost** | Low — willingness to quit is a feature | High — stopping feels like failure |
| **Failure attribution** | "This approach doesn't work" → pivot | "I failed" → try harder |
| **Psychology** | Growth mindset; schema-adaptive | Fixed mindset; schema-rigid |

**Key insight:** Productive persistence is *not* working harder — it's working smarter. Each attempt should narrow the hypothesis space. If you're repeating the same failure with no new information, you're being stubborn, not persistent.

---

## 6 Core Dimensions

### 1. Hypothesis-Narrowing Criterion

**The core test:** Is each failure teaching us something new?

- **Persist** if failure eliminates a specific hypothesis (confirms a "no" that wasn't established)
- **Pivot** if consecutive failures produce no new information (same error, same cause)
- **Quit branch** if the hypothesis space is exhausted (no remaining untried approaches)

**When to apply:** After each failure, ask: "What did I learn?" If the answer is "the same thing as last time," pivot.

---

### 2. Tiered Retry Budget

**Question:** What level of the system should absorb this failure?

| Tier | Scope | Budget | When exhausted |
|------|-------|--------|-----------------|
| **Tactic** | Single step | 2–3 retries | Escalate to strategy tier |
| **Strategy** | Plan / decomposition | 2 replans | Escalate to goal tier |
| **Goal** | Mission | No fixed count | Human judgment |

A tactic that fails 3× has produced all learnable information. The budget is a signal gate, not a performance constraint.

**When to apply:** Count failures at each level. At tactic tier: after 3 step failures, replan the strategy. At strategy tier: after 2 replans, restart the goal interpretation.

---

### 3. Failure Signal Taxonomy

**Question:** What does this failure tell us?

| Signal | Prescribed action |
|--------|-------------------|
| **Informative failure** | Narrows hypothesis; adjust strategy; persist; log hypothesis eliminated |
| **Confirming failure** | Confirms "this won't work"; try orthogonal approach or escalate tier |
| **Infrastructure failure** | Fix environment; DO NOT count against retry budget |
| **Ambiguity failure** | Goal underspecified; escalate to goal-level clarification |

**When to apply:** Before retrying, classify the failure. Infrastructure failures don't count. Ambiguity failures require reframing, not retrying.

---

### 4. Recovery Mechanisms — Vary Approach, Not Effort

**Escalation ladder (each level varies approach at increasing scope):**

```
continue   → same step, minor parameter adjustment
adjust     → same plan, step-level modification
replan     → discard decomposition, re-decompose from goal
restart    → reframe intent; may change goal interpretation
escalate   → human in the loop
```

**When to apply:** At each tier exhaustion, move one level up. Do not increase effort at the same level; increase scope.

---

### 5. Schema Fitness Check (Zoom-Out Signal)

**Question:** When should the agent reframe (zoom out) vs. retry (zoom in)?

**Core distinction (Hatano & Inagaki 1986):**
- *Routine expertise*: fail → retry without schema check → silent wrong-answer loop.
- *Adaptive expertise*: outcome check → reframe if schema mismatch, before hypothesis space exhausts.

**Five zoom-out signals (cross-source synthesis):**

| Signal | Source | Poe Analog | Currently Wired? |
|--------|--------|------------|-----------------|
| Procedure applied + outcome unexpected | Hatano 1986 | Step completes; result diverges from plan expectation | Partial — `stuck_streak` count proxy |
| Near-miss / unexpected-path success | Schwartz 2005 | Step "succeeds" but parent goal not advanced | **No** |
| Confidence–accuracy decoupling | Fleming 2012 | Pre-step confidence high + outcome poor | **No** (requires pre-step confidence logging) |
| Meta-ignorance: confident + wrong | Fleming 2010 | Skill match score high; outcome diverges; no self-detection | **No** (structural comparison required) |
| Schema fitness check fails | Hatano / Argyris | `stuck_streak ≥ N` without hypothesis change | Yes (count-based proxy) |

**Meta-ignorance catastrophe**: the most dangerous failure mode. High confidence + wrong output does **not** self-trigger zoom-out. Detection requires structural comparison: if skill match score is high but outcome diverges, flag for structural review regardless of stated confidence.

**Cognitive load degrades zoom-out (Fleming et al.):** Under high load (long context, deep nesting, many active tasks), metacognitive accuracy degrades before task performance. The agent continues to act but loses the ability to detect schema failure. → Zoom-out threshold should *decrease* under high load — fire earlier, not later.

**Zoom-out type is domain-dependent (Feltovich, Spiro & Coulson 1993 — CFT):**
- **Well-structured tasks**: zoom-out = hierarchical escalation (strategy-tier re-decompose)
- **Ill-structured tasks**: zoom-out = *lateral case-traversal first* ("what prior cases share structure?"), *then* hierarchical re-decompose

Skipping lateral traversal on ill-structured tasks produces CFT reductive biases: single-cause attribution, context-stripping, schema-reduction. Ill-structured tasks include goals admitting multiple valid decompositions, underspecified success criteria, and domains with prior low skill-match.

**Argyris & Schön (1978) — persistence default, reframing exception:**
- *Single-loop* (default): adjust action within existing frame. Correct for most retries.
- *Double-loop* (zoom-out): detect that the governing frame itself is causing the error → surface and change the frame. Expensive; fires only when single-loop has demonstrably failed.

**Orientation hygiene (Boyd / Argyris & Schön):** Before retrying a stuck step:
1. Are step inputs still valid?
2. Does the success criterion still serve the parent goal?
3. Has the environment shifted since decomposition?

Any "no" → re-decompose immediately (double-loop), regardless of remaining `step_retries` budget.

**Verbalization as zoom-out circuit builder (Hatano 1986; Ericsson 1993):** Director narration IS the computational verbalization analog. Explaining reasoning forces structural re-encoding. Stripping narration under token pressure removes the schema-check mechanism — narration is not cosmetic.

**CFT anti-nots:**
- Do NOT promote skills on use-count alone (frequency → automaticity → tacit lock-in)
- Do NOT strip case metadata at crystallization (context-stripping = CFT reductive bias)
- Do NOT skip director narration under token pressure (verbalization IS the schema-check circuit)
- Do NOT use identical zoom-out path for all task types (well-structured vs. ill-structured differ)
- Do NOT rely on explicit failure alone as zoom-out trigger (near-miss, meta-ignorance, and load are invisible to explicit-failure-only detection)

**When to apply:** After each step, check for near-miss (success without goal advancement), meta-ignorance signals (high confidence + diverging outcome), and load spikes. If any fire, zoom out before retrying.

---

### 6. Durable Artifact Strategy

**Question:** What outlasts the run?

Design principle: *Expect pivots. Favor designs where prior artifacts still apply. Persistent workspaces, resolved-intent artifacts, captain's log events — these outlast the specific run they were written for.* (`CODING_NOTES.md` §8)

**Poe artifact inventory:**

| Artifact | Location | What it preserves |
|---|---|---|
| Run checkpoint | `agent_loop.py` — `write_checkpoint()` / `load_checkpoint()` | Step-execution state; crash-resume without re-executing completed steps |
| Task JSON | `task_store.py` — one file per task; `mkstemp+rename` atomic writes | Task state across restarts; no partial writes |
| Task archive | `task_store.py` — done/failed → archive dir | Completed/failed task history; write-once |
| `recover_stale_claims()` | `task_store.py` | Dead-PID claim reset to `queued` on startup/janitor |
| `task['attempt']` | `task_store.py` | Durable per-task retry count across restarts |
| `fcntl` advisory locking | `task_store.py` — per-task `.lock` sidecar | Concurrent-safe writes across multiple workers |

**Checkpoint lifecycle:** After every completed step — `STEP_EXEC → CHECKPOINT → SKILL_UP → STEP_LOOP`. Written on step completion; deleted on clean finish; resumed on crash. See `lat.md/checkpointing.md`.

**Gaps:**
- Checkpoint deleted on success — no post-run audit trail of step sequence for retro analysis
- Archive is write-once; no mechanism to re-examine failure chains or compute per-task failure rates
- `task['attempt']` is the most durable retry signal but **not yet plumbed into director budget decisions** (see §2 Tiered Retry Budget gap)
- `fcntl` advisory locking is cooperative-only; won't survive multi-host expansion

**When to apply:** On any crash or unexpected stop, resume is automatic (checkpoint + `recover_stale_claims()`). For retro analysis, archived tasks are queryable from `task_store.py`. The missing link is surfacing `task['attempt']` to the director.

---

## Decision Tree

```
After failure or unexpected outcome:

1. ORIENTATION CHECK
   ├─ Are step inputs still valid? → if no → re-decompose (free)
   ├─ Does success criterion still serve goal? → if no → re-decompose (free)
   └─ Has environment shifted? → if no → re-decompose (free)

2. CLASSIFY SIGNAL
   ├─ INFRASTRUCTURE → Fix; reset budget; retry
   ├─ AMBIGUITY → Escalate goal-level clarification
   ├─ NEAR-MISS → Schema fitness check
   ├─ CONFIRMING → Go to [CONFIRMING BRANCH]
   └─ INFORMATIVE → Adjust strategy; persist

3. [CONFIRMING BRANCH] — Tier escalation
   ├─ Tactic tier: budget exhausted? → escalate to strategy
   ├─ Strategy tier: budget exhausted? → escalate to goal
   └─ Goal tier: reframe or escalate to human

4. At every tier transition: require validator confirmation, not just count
```

---

## Poe Implementation Status

| Component | Status | Gap |
|-----------|--------|-----|
| Retry budgets (tactic/strategy) | Implemented | `task['attempt']` durable history unused in decisions |
| Checkpoint + resume | Implemented | No post-run audit trail (checkpoint deleted on success) |
| Failure classification | LLM-based | No structured signal tagging; infrastructure/ambiguity detection weak |
| Zoom-out signals | Partial | Near-miss detection incomplete; confidence–accuracy decoupling undetected |
| Schema fitness check | Partial | `_is_converging` is implemented in `agent_loop.py` and used in retry decisions; broader zoom-out signals are still incomplete |

**Highest-value fixes (priority order):**
1. Wire `task['attempt']` into director decisions — durable retry history is collected but unused
2. Implement structured failure classification (informative vs. confirming) — largest semantic gap
3. Add near-miss detection for validator rejections on "completed" steps
4. Extend beyond `_is_converging` with confidence-gap and meta-ignorance triggers so zoom-out is not purely failure-centric

---

## Psychological Grounding

- **Duckworth (2016):** Grit is commonly framed here as goal-stable / strategy-flexible, but that remains an engineering interpretation rather than a direct source claim.
- **Kapur (2016):** Productive failure — early exploratory errors that expose constraints outperform hyper-guarded first passes.
- **Hatano & Inagaki (1986):** Routine expertise retries identically; adaptive expertise reframes before hypothesis space exhausts.
- **Bjork (1994):** Optimal challenge zone is 60–85% success rate — sweet spot for learning without demoralization.

---

## See Also

- `docs/research/productive_persistence.md` — Full research synthesis with all gaps and open questions
- `docs/research/zoom-metacognition-adaptive-expertise.md` — Adaptive expertise survey (schema checks, CFT)
- `skills/arch-core-loop.md` — Implementation gaps in retry/recovery
