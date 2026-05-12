# Productive Persistence — Poe Implementation Guide

*Synthesis of ML/psychology research + Poe codebase implementation analysis*
*Updated: 2026-05-12*

---

**Question:** When should Poe persist vs. quit or escalate, and how should it vary strategy while maintaining goal integrity?

**Key findings:**
- Goal-stable / strategy-flexible is the productive persistence invariant — retry budget applies to tactics and strategies, never the core goal (Duckworth 2016, goal hierarchy model)
- `task['attempt']` — the most informative durable persistence signal — is collected in `task_store.py:69,216` but never read by the director; wiring it is the single highest-value Wave 1 action
- Four failure types require distinct responses: informative (persist + log hypothesis eliminated), confirming (pivot strategy), infrastructure (fix env, do NOT charge retry budget), ambiguity (escalate to goal-level clarification)
- 4 of 5 zoom-out signals are undetected; `stuck_streak` is a count-based proxy that cannot distinguish identical-repeated failures from genuine novel exploration (semantic hash needed)
- Optimal persistence zone is ~60–85% step-success rate (Bjork 1994); below ~15% sustained → goal-level quit signal; budget counts operationalize this under limited information

**Implications:**
- Wave 1 (wire what's built): connect `task['attempt']` to director context; add rule-based infra failure classifier to `inspector.py` (pattern-match timeout/429/connection-refused); protect director narration from token-pressure stripping
- Wave 2 (structural signal upgrades): semantic error-signature hash for `stuck_streak`; near-miss signal in inspector; make `_REDECOMPOSE_THRESHOLD` a config key
- Wave 3 (requires validation): LLM confidence calibration audit → confidence-gap Inspector trigger; ill-structured task classifier → lateral zoom-out path before hierarchical re-decompose

**Sources:** Duckworth (2016), Kapur (2016), Dweck (2006), Hatano & Inagaki (1986), Bjork (1994), Credé et al. (2017), Argyris & Schön (1978), Seligman (1972), Feltovich et al. (1993); Poe codebase: `task_store.py`, `agent_loop.py`, `inspector.py`, `director.py`

---

## Executive Summary

**Productive persistence = continuing effort while varying approach, anchored by evidence that each failure narrows the hypothesis space.** The key distinction from stubbornness: goal-stable, strategy-flexible. Quit when hypothesis space is exhausted, not when emotionally depleted. The Ralph Wiggum model (Jeremy's framing): positive, mid-IQ faith — vary approach, low sunk-cost, know when to quit.

Poe's retry architecture has strong structural bones: tiered retry budget (tactic/strategy/goal), escalation ladder (continue→adjust→replan→restart→escalate), durable task state (`task_store.py`), and checkpoint/resume. The critical weakness is **signal quality**: every tier decision is count-based, not signal-based.

**Three highest-value gaps:**
1. `task['attempt']` (`task_store.py:69,216`) — durable, cross-restart retry counter — is collected but never read by the director. The most informative persistence signal goes unused.
2. Four of five zoom-out signals are undetected: only `stuck_streak` (schema fitness check proxy) fires. Near-miss, confidence–accuracy decoupling, meta-ignorance, and load-aware threshold degradation are invisible.
3. Failure type classification is LLM judgment, not rule-based. Infrastructure failures can silently consume tactic budget, eventually producing a learned-helplessness analog across sessions.

**External dissent is structurally supportive of the design direction:** Credé's meta-analysis shows grit's incremental validity over conscientiousness is modest (r≈0.18). Counting retry volume does not predict good outcomes — signal quality matters more than persistence effort. This strengthens the case for the failure signal taxonomy, not against it.

See `lat.md/core-loop.md` for the retry/escalation implementation map; `lat.md/quality-gates.md` for the inspector friction detection layer; `lat.md/quality-gates.md` for how failure classification feeds the quality gate pipeline.

---

## 1. Definition

**Productive persistence** is continuing effort while varying approach, anchored by evidence that each failure narrows the hypothesis space — not sunk cost.

The critical distinction:

| Characteristic | Productive Persistence | Stubbornness |
|---|---|---|
| Retry stance | Adjusted strategy informed by failure signal | Identical attempt, hoping for different result |
| Goal attachment | Goal stable; strategy flexible | Goal unstable OR strategy rigid |
| Quit trigger | When hypothesis space is exhausted | When emotionally exhausted / never |
| Sunk cost | Low — willingness to quit is a feature | High — stopping feels like failure |
| Failure attribution | "This approach doesn't work" → pivot | "I failed" → try harder |

The **Ralph Wiggum model** (Jeremy's framing): positive, mid-IQ faith. Vary approach. Low sunk-cost. Know when to quit. Not paralyzed by first obstacle. Not bulldozed by hundred-and-first.

Grounding in external research:
- **Duckworth (2007) — engineering interpretation:** Grit = passion × perseverance for long-term goals (Grit Scale, JPSP 92(6):1087; 6 studies). Duckworth's operationalization is goal-level consistency of interest + effort persistence — not strategy rigidity. *Design principle derived (not Duckworth's vocabulary):* for autonomous agents, the productive analog is **goal-stable / strategy-flexible** — goal attachment is high; individual strategy attachment is low. This is an engineering restatement of Duckworth's finding, not a direct quote.
- **Kapur (2016):** Productive failure — early exploratory errors that expose constraint boundaries outperform hyper-guarded first passes.
- **RL literature (UCB, Gittins):** Persistence cost is opportunity cost. Quit when another path's index exceeds the current one.

---

## 2. Core Dimensions

### 2.1 Hypothesis-Narrowing Criterion

**Question:** Is each failure teaching us something new?

| Decision | When |
|---|---|
| **Persist** | Failure eliminates a specific hypothesis — confirms a "no" that hadn't been established |
| **Pivot** | Consecutive failures produce no new information (same error, same cause, no state change) |
| **Quit branch** | Hypothesis space exhausted — no remaining untried approaches |

The operationally useful test: track a hash of `(error_type × last_action × context_signature)`. If the hash repeats, the agent is looping. If it changes, it's exploring.

**Poe touchpoints:**
- `stuck_streak` (`agent_loop.py:233`, LoopContext field) — primary gate; consecutive steps without forward progress; triggers inspector review and possible escalation at threshold (reset logic: `agent_loop.py:4220–4358`)
- `verify_failure_count` (`EvaluationContext`) — validator-based signal; more honest than self-reported retry counts; independent of executor
- `_is_converging()` (`agent_loop.py:2718` def, `:3344` call) — `failure_count + direction signal`; distinguishes informative from confirming failures; **fully implemented and used in retry decisions**

**Gaps:**
- `stuck_streak` is count-based, not signal-based: same failure repeated 3× reads as "confirming" only statistically, not semantically. No error-signature deduplication.
- `verify_failure_count` resets on new session; historical pattern across restarts is invisible.

---

### 2.2 Tiered Retry Budget

**Question:** What level of the system should absorb this failure?

| Tier | Scope | Budget | Exhaustion action |
|---|---|---|---|
| **Tactic** | Single step | 2–3 retries | Escalate to strategy tier |
| **Strategy** | Plan / decomposition | 2 replans (`_REDECOMPOSE_THRESHOLD`) | Escalate to goal tier |
| **Goal** | Mission | No fixed count | Human judgment or restart |

Tactic budget is intentionally small — a single tactic that fails 3× has almost certainly already produced all the information it can. The budget is not a performance constraint; it is a signal gate.

**RL grounding (UCB/Gittins):** Budget exhaustion is an opportunity-cost calculation, not just a count. A tactic should be abandoned when another path's expected return exceeds the current path's upper confidence bound. The 2–3 budget operationalizes this threshold under limited information.

**Meta-RL implication (Wang/Duan 2016):** Persistence thresholds are themselves learnable from outcome history — agents trained on variable-difficulty problems develop richer stop/continue criteria. Poe's lesson extraction pipeline (`memory.py`) is the plausible site for adaptive budget calibration; not yet implemented.

**Poe touchpoints:**
- `step_retries` dict (`agent_loop.py:241`, LoopContext field; used at `:562` in execution flow) — per-step tactic-level counter; configurable cap
- `director_replan_count` (`agent_loop.py:267`, LoopContext field) — strategy-tier counter; hard-capped by `_REDECOMPOSE_THRESHOLD = 2` (`agent_loop.py:2747`)
- `task['attempt']` (`task_store.py:69` init, `:216` increment) — durable cross-restart retry history; incremented each claim

**Gaps:**
- `step_retries` budget exhaustion is count-only — no validator gate at tactic tier.
- `_REDECOMPOSE_THRESHOLD = 2` (`agent_loop.py:2747`) is a fixed constant; harder or more novel goals may warrant higher budget with no mechanism to raise it. `skills/arch-core-loop.md` notes this as a known gap ("Budget ceiling creates continuation tasks but doesn't auto-enqueue them").
- `task['attempt']` is durable retry history but is **not yet read by the director** — the most valuable cross-restart signal goes unused in budget decisions.
- `step_retries` (in-memory, session-scoped) and `task['attempt']` (durable, cross-restart) track the same concept at different layers without synchronization.

---

### 2.3 Failure Signal Taxonomy

**Question:** What does this failure tell us?

| Signal type | Description | Prescribed action |
|---|---|---|
| **Informative failure** | Narrows hypothesis space; approach should change | Adjust strategy, persist, log hypothesis eliminated |
| **Confirming failure** | Confirms "this won't work"; no new information | Quit this branch; try orthogonal approach or escalate tier |
| **Infrastructure failure** | Environment/tooling failure, not logic failure | Fix environment; do NOT count against retry budget |
| **Ambiguity failure** | Goal underspecified; more retries won't help | Escalate to goal-level clarification |

**Psychological grounding:**

- **Dweck (2006) — Attribution theory:** Growth mindset operationalized as failure classification. Classifying failure as *confirming* and pivoting = growth mindset behavior. Retrying identically after failure = fixed mindset / strategy-rigid behavior. The informative/confirming taxonomy is a computational instantiation of this distinction: the agent's failure attribution determines its next action, not an arbitrary retry count.

- **Seligman (1972) — Learned helplessness risk:** Repeated uncontrollable failure produces passive giving-up, even when control becomes available. **Infrastructure failures must not count against the hypothesis-narrowing budget** — an agent accumulating infrastructure failures against its retry count learns "I cannot affect outcomes" for problems it could actually solve. Cross-run failure clustering (many consecutive `flag_stuck` across sessions) may produce a session-level helplessness analog that is invisible to current in-memory signals.

**Poe touchpoints:**
- Director action space (`agent_loop.py` / `director.py`): `continue | adjust | replan | restart | escalate` — execution-layer expression of the taxonomy
- `stuck_streak` threshold branch — proxy for confirming failure: same failure N× = confirming
- `verify_failure_count` — infrastructure/ambiguity failures can accumulate here without exhausting `step_retries`

**Gaps:**
- Classification into informative/confirming/infrastructure/ambiguity is LLM judgment, not rule-based. No structured signal tagging.
- `verify_failure_count` cannot distinguish why the validator rejected — infrastructure vs. ambiguity look identical.
- `restart` action may reframe intent but does not guarantee a new hypothesis; `_is_converging()` is the check that catches non-novel restart paths (implemented at `agent_loop.py:2718`).
- Cross-run failure clustering (learned helplessness accumulation) is invisible — no inter-session failure-pattern detection.

---

### 2.4 Recovery Mechanisms — Vary Approach, Not Effort

**Question:** How do we change, not just retry?

The escalation ladder (each level varies approach at increasing scope):

```
continue   → same step, minor parameter adjustment
adjust     → same plan, step-level modification
replan     → discard current decomposition, re-decompose from goal
restart    → back to goal interpretation; may reframe intent
escalate   → human in the loop
```

Key principle from Kapur: early-phase errors should not short-circuit learning. A naive first-pass followed by corrective second pass outperforms a hyper-guarded first pass. Apply most aggressively on novel or underspecified tasks.

**Poe touchpoints:**
- Director action space (`agent_loop.py` / `director.py`) — the escalation ladder is implemented
- `recovery_step_count` (`agent_loop.py:246`) — costed but not hard-capped; tracks recovery cost without blocking it
- `EvaluationContext` (compact serializable snapshot) — carries context forward through recovery actions without loading full `LoopContext`; enables stateless recovery workers

**Gaps:**
- `recovery_step_count` has no per-tier breakdown — a single large value could be all-tactic or all-strategy; can't diagnose recovery pattern.
- `restart` doesn't guarantee new hypothesis without `is_converging`.
- `verify_failure_count` resets on first pass by design — recovery doesn't inherit session penalties (intentional), but prior run patterns are invisible.

---

### 2.5 Zoom-Out Signal Model

**Question:** When should the agent reframe (zoom out) vs. retry (zoom in)?

**The core distinction (Hatano & Inagaki 1986):**
- *Routine expertise*: pattern match → execute → fail → retry with no schema check. Failure mode: applies wrong schema and doesn't notice — silent wrong-answer loop.
- *Adaptive expertise*: schema check → execute → outcome check → reframe if schema mismatch. Failure mode: schema mismatch detection triggers reframe before hypothesis space is exhausted.
- **Primary trigger**: procedure applied + outcome unexpected. All other zoom-out signals build on this.

**Five zoom-out signals (cross-source synthesis):**

| Signal | Source | Poe Analog | Currently Wired? |
|--------|--------|------------|-----------------|
| Procedure applied + outcome unexpected | Hatano 1986 | Step completes; result diverges from plan expectation | Partial — `stuck_streak` count proxy |
| Near-miss / unexpected-path success | Schwartz 2005 | Step "succeeds" but parent goal not advanced | **No** |
| Confidence–accuracy decoupling | Fleming 2012 | Pre-step confidence high + outcome poor | **No** (requires pre-step confidence logging) |
| Meta-ignorance: confident + wrong | Fleming 2010 | Skill match score high; outcome diverges; no self-detection | **No** (structural comparison required) |
| Schema fitness check fails | Hatano / Argyris | `stuck_streak ≥ N` without hypothesis change | Yes (count-based proxy) |

**Meta-ignorance catastrophe**: the most dangerous failure mode. High confidence + wrong output does **not** self-trigger zoom-out. The agent is wrong and doesn't know it. Detection requires structural comparison: if skill match score is high but outcome diverges, flag for structural review regardless of stated confidence.

**Cognitive load degrades zoom-out capacity (Fleming et al.):** Under high cognitive load (long context, deep nesting, many active tasks), metacognitive accuracy degrades before task performance. The agent continues to act but loses the ability to detect when its schema is failing. Under high-load conditions, zoom-out threshold should decrease — fire zoom-out earlier, not later.

**Zoom-out type is domain-dependent (Feltovich, Spiro & Coulson 1993 — Cognitive Flexibility Theory):**
- **Well-structured tasks**: zoom-out = hierarchical escalation (strategy-tier re-decompose)
- **Ill-structured tasks**: zoom-out = *lateral case-traversal first* ("what is this really a case of? what prior cases share structure?"), *then* hierarchical re-decompose

Skipping lateral traversal on ill-structured tasks produces CFT reductive biases: single-cause attribution, context-stripping, schema-reduction — applying a known template when structural reframing is required. Ill-structured tasks include: goals admitting multiple valid decompositions, domains with prior low skill-match, underspecified success criteria.

**Argyris & Schön (1978) — persistence default, reframing exception:**
- *Single-loop learning* (persistence default): detect error → adjust action within the existing governing frame. This is the correct mode for most retries — the goal and strategy are sound; the execution failed.
- *Double-loop learning* (reframing exception): detect that the governing frame itself is causing the error → surface and change the frame. This is zoom-out. It is expensive and should fire only when single-loop has demonstrably failed.

The design implication: **default to persistence; reframe only when the zoom-out signals below fire.** Triggering double-loop on every failure is overthinking; never triggering it is learned helplessness. The five zoom-out signals below operationalize the threshold.

**Orientation hygiene check (Boyd 1987 / Argyris & Schön 1978):** Before retrying a stuck step, verify:
1. Are step inputs still valid?
2. Does the success criterion still serve the parent goal?
3. Has the environment shifted since decomposition?

Any "no" → re-decompose immediately (double-loop), regardless of remaining `step_retries` budget. Defensive routines (cached plans, stale context, confirmed-schema bias) block reframing even when signals are present.

**Verbalization as zoom-out circuit builder (Hatano 1986; Ericsson 1993):** Explaining reasoning to others forces structural re-encoding of tacit knowledge. Director narration IS the computational verbalization analog. Stripping it under token pressure removes this mechanism. Narration is not a cosmetic output — it builds the schema-check capacity that zoom-out depends on.

**CFT anti-nots (from `docs/research/zoom-metacognition-adaptive-expertise.md`):**
- Do NOT promote skills on use-count alone (frequency → automaticity → tacit lock-in)
- Do NOT strip case metadata at crystallization (context-stripping = CFT reductive bias)
- Do NOT skip director narration under token pressure (verbalization IS the schema-check circuit)
- Do NOT use identical zoom-out path for all task types (well-structured vs. ill-structured require different zoom-out)
- Do NOT rely on explicit failure alone as zoom-out trigger (near-miss, meta-ignorance, and load are invisible to explicit-failure-only detection)

**Poe touchpoints:**
- `inspector.py` (`lat.md/quality-gates.md`) — friction detection; fires on explicit failure; does not yet detect confidence–accuracy decoupling, near-miss, or meta-ignorance signals
- `_is_converging()` (`agent_loop.py:2718`) — schema fitness check equivalent; **fully implemented**
- `stuck_streak` (`agent_loop.py:233`) — proxy schema-fitness-check signal (count-based, not signal-based)
- Director narration (`director.py`) — verbalization mechanism; must be protected under token pressure

**Gaps:**
- Inspector fires only on explicit failure — near-miss and unexpected-path successes are invisible
- Confidence–accuracy decoupling undetected (no pre-step confidence tracking)
- Meta-ignorance (high skill match + diverging outcome) not structurally detected
- Task structure classification (well-structured vs ill-structured) does not exist; lateral zoom-out path unimplemented
- Director narration at risk of token-pressure stripping; no protection mechanism
- Load-aware zoom-out sensitivity not implemented

---

### 2.6 Durable Artifact Strategy

**Question:** What outlasts the run?

Coding principle 8 (`CODING_NOTES.md`): *"Expect pivots. Favor designs where prior artifacts still apply. Persistent workspaces, resolved-intent artifacts, captain's log events — these outlast the specific run they were written for."*

**Poe touchpoints:**

| Artifact | Location | What it preserves |
|---|---|---|
| Run checkpoint | `agent_loop.py:880` (write via `write_checkpoint()`), `:1776` (delete on success), `:1976` (resume via `load_checkpoint()`) | Step-execution state; enables crash-resume without re-executing completed steps |
| Task JSON | `task_store.py` — one file per task; `mkstemp+rename` atomic writes | Task state across restarts; no partial writes |
| Task archive | `task_store.py` — done/failed → archive dir | Completed/failed task history; write-once |
| `recover_stale_claims()` | `task_store.py` | Dead-PID claim reset to `queued` on startup/janitor; no work lost from crashes |
| `task['attempt']` | `task_store.py` | Durable per-task retry count across restarts |
| `fcntl` advisory locking | `task_store.py` — per-task `.lock` sidecar | Concurrent-safe writes across multiple workers |

Checkpoint placement: after every completed step — `STEP_EXEC → CHECKPOINT → SKILL_UP → STEP_LOOP`. The checkpoint is deleted on clean finish; the sequence is: write on completion, delete on success, resume on crash. See `lat.md/checkpointing.md` for the checkpoint lifecycle design.

**Gaps:**
- Checkpoint deleted on success — no post-run audit trail of step sequence for retro analysis.
- Archive is write-once; no mechanism to re-examine archived failure chains or compute per-task failure rates.
- `task['attempt']` is the most durable retry signal but is not yet plumbed into director budget decisions (see §2.2 gap).
- `fcntl` advisory locking is cooperative-only; acceptable for current single-host design but won't survive multi-host expansion.

---

## 3. Signals-to-Pivot Decision Tree

```
After each step failure OR unexpected outcome:
│
├─ ORIENTATION CHECK (runs before signal classification)
│   ├─ Are step inputs still valid? → if no → re-decompose (do not consume tactic budget)
│   ├─ Does success criterion still serve parent goal? → if no → re-decompose
│   └─ Has environment shifted? → if no → re-decompose
│
├─ Classify signal type (failure AND unexpected-path outcomes)
│   ├─ INFRASTRUCTURE → Fix environment; reset retry budget; retry from same step
│   ├─ AMBIGUITY      → Escalate to goal-level clarification (retries won't help)
│   ├─ NEAR-MISS      → Step "succeeded" but goal not advanced → schema fitness check
│   │                    → is schema still fitting this problem? → if no → zoom-out
│   ├─ CONFIRMING     → Go to [CONFIRMING BRANCH]
│   └─ INFORMATIVE    → Adjust approach; persist; log hypothesis eliminated
│
[ZOOM-OUT CHECK — runs before CONFIRMING BRANCH for all non-infrastructure signals]
│
├─ Is task well-structured or ill-structured?
│   ├─ WELL-STRUCTURED → proceed to CONFIRMING BRANCH (hierarchical re-decompose)
│   └─ ILL-STRUCTURED  → lateral case-traversal first ("what is this really a case of?")
│                         → then proceed to CONFIRMING BRANCH
│
[CONFIRMING BRANCH]
│
├─ At TACTIC tier?
│   ├─ step_retries[step_id] < budget (2-3)?
│   │   └─ Try orthogonal tactic variant (different tool / parameter set)
│   └─ Budget exhausted → escalate to STRATEGY tier
│
├─ At STRATEGY tier?
│   ├─ replan_count < _REDECOMPOSE_THRESHOLD (2)?
│   │   └─ Discard decomposition; replan from goal; increment replan_count
│   └─ Threshold hit → escalate to GOAL tier
│
└─ At GOAL tier?
    ├─ Is goal itself invalid? → restart or flag_stuck
    └─ Is goal reinterpretable? → reframe intent; reset strategy/tactic budgets

[At every tier transition: require validator-based confirmation before escalating,
 not just count exhaustion]
```

**Key invariants:**
1. Classify failure type first. Infrastructure failures must not exhaust retry budget. Ambiguity failures must not trigger replanning.
2. Run orientation check before any retry — stale inputs or success criteria invalidate all retry budget consumed.
3. For ill-structured tasks, lateral traversal precedes hierarchical re-decompose.
4. Near-miss signals (step succeeded but goal not advanced) are zoom-out triggers, not success signals.

---

## 4. Current State in Poe

### 4.1 Checkpointing

Checkpoint lifecycle is fully implemented. Write at `agent_loop.py:880` after every completed step; delete on clean finish at `:1776`; resume at `:1976`. Crash-resume works. Gap: no post-run audit trail (checkpoint is deleted on success). See `lat.md/checkpointing.md`.

### 4.2 Task Durability

`task_store.py` provides:
- Atomic writes (mkstemp+rename) — no partial state
- Per-task fcntl locking — multi-worker safe
- `recover_stale_claims()` — crash recovery at startup
- `task['attempt']` — durable cross-restart retry counter
- Archive tier — task history is preserved, not deleted

Gap: `task['attempt']` is not read by the director. Durable retry history is collected but not acted on.

### 4.3 Retry Limits

See `skills/arch-core-loop.md § Retry & Recovery` for the execution-layer view of these signals.

| Signal | Location | Current value | Type |
|---|---|---|---|
| `step_retries` | `agent_loop.py:241` (field), `:562` (usage) | Configurable; ~2-3 | In-memory, per-step, tactic tier |
| `director_replan_count` / `_REDECOMPOSE_THRESHOLD` | `agent_loop.py:267` / `:2747` | Hard cap = 2 | In-memory, per-session, strategy tier |
| `stuck_streak` | `agent_loop.py:233` (field), `:4220` (update) | Threshold triggers inspector | In-memory, consecutive steps |
| `verify_failure_count` | `EvaluationContext` | Resets per session | In-memory, validator-based |
| `task['attempt']` | `task_store.py:69` (init), `:216` (increment) | Unbounded (recorded only) | **Durable, cross-restart** |
| `recovery_step_count` | `agent_loop.py:246` | Tracked but not capped | In-memory, per-session |

The critical structural gap: every retry signal except `task['attempt']` is in-memory and session-scoped. A box crash mid-task resets tactic and strategy budgets. The durable signal (`task['attempt']`) is unused in decisions.

### 4.4 Signal Implementation Status

| Signal | Code location | Status | Wave |
|--------|--------------|--------|------|
| `stuck_streak` | `agent_loop.py:233` | Wired (count-based) | Improve to semantic hash (Wave 2) |
| `step_retries` | `agent_loop.py:241` | Wired | OK |
| `director_replan_count` | `agent_loop.py:267` | Wired | Make configurable (Wave 2) |
| `task['attempt']` | `task_store.py:69,216` | Collected; **not read by director** | **Wire (Wave 1)** |
| `verify_failure_count` | `EvaluationContext` | Wired; session-scoped | OK for now |
| `_is_converging()` | `agent_loop.py:2718` (def), `:3344` (call) | **Wired; fully implemented** | No action needed |
| near-miss signal | Not implemented | Missing | **Wave 2** |
| confidence-accuracy | Not implemented | Missing | Wave 3 (prereq: calibration audit) |
| meta-ignorance | Not implemented | Missing | Wave 3 |
| load-aware threshold | Not implemented | Missing | Wave 3 |

---

## 5. Recommended Thresholds

Grounded in RL literature (N ≈ 3–5 before strategy switch) and current implementation:

| Tier | Recommended budget | Rationale |
|---|---|---|
| Tactic (`step_retries`) | **2–3** | Single tactic that fails 3× has produced all learnable information; matches human switching rhythm |
| Strategy (`replan_count`) | **2–3** | Current `_REDECOMPOSE_THRESHOLD = 2` is adequate; consider 3 for complex/novel goals |
| Goal-level escalation | **1–2** before `flag_stuck` | Goal reinterpretation is expensive; limit to 2 before human escalation |
| `stuck_streak` trigger | **2–3** | Trigger inspector review at 2; force strategy escalation at 3 |
| `verify_failure_count` gate | **2** before budget escalation | Independent validator rejection is stronger signal than self-reported retries; 2 rejections should escalate tier |

**Adaptive budget rule (optional):** tie exploration width to remaining step budget. `exploration_factor = remaining_budget / initial_budget`. Broader tolerance early; tighten to highest-confidence path as budget narrows. Not yet implemented.

**Optimal challenge zone (Bjork/Kapur):** ~60–85% success rate across attempts is the productive persistence zone. Below 15% sustained → goal-level quit signal. Poe does not currently track per-step success rate across runs for threshold calibration.

---

## 6. Open Questions

### Implementation-level

1. **How to detect informative vs. confirming failure automatically?** Semantic similarity of failure messages across consecutive retries is the candidate mechanism (error-signature hash). Not implemented. Would close the largest single gap in the taxonomy.

2. **Should `task['attempt']` feed the director's tier budget?** Durable retry history persisting across crashes is more honest than in-memory `step_retries`. The straightforward fix: load `task['attempt']` in the director's context before deciding next action; subtract from tier budget. Low implementation cost; high value.

3. **Should `_REDECOMPOSE_THRESHOLD` be dynamic?** Hard cap of 2 may be too tight for novel or multi-phase goals. Options: (a) configurable per-task type, (b) elevated by director on first `replan` if new hypothesis is clearly different, (c) remain fixed for predictability. Current fixed value is safe but potentially over-constraining.

4. **How do `stuck_streak` and tiered budget interact?** Are they additive signals (both must fire) or independent gates (either can escalate)? Not documented in code. Current behavior: `stuck_streak` triggers inspector + director separately from `step_retries` exhaustion — they can both fire on the same step.

5. **Should checkpoints be archived on success?** Delete-on-success is clean but loses post-run step sequence for retro analysis. A lightweight post-run checkpoint archive (compressed JSON, retained for N days) would enable failure-pattern analysis across sessions.

6. **LLM confidence calibration for zoom-out triggers (pre-implementation requirement).** Fleming's meta-d framework (confidence–accuracy decoupling, §2.5) requires a well-calibrated confidence reporter. LLM self-reported confidence is known to be systematically miscalibrated. Before implementing confidence-gap Inspector triggers, run a calibration audit: sample N=100 step outcomes, compare pre-step stated confidence to outcome correctness, establish baseline meta-d analog. Without this, the confidence-gap signal fires on noise rather than signal.

7. **Ill-structured vs well-structured task classification.** The lateral-before-hierarchical zoom-out path (§2.5, CFT) requires classifying tasks as ill-structured before applying it. No validated heuristic exists. Candidate: task is ill-structured if (a) goal admits multiple decompositions without clear dominance, (b) prior skill match is low across all candidate skills, or (c) domain is flagged ill-structured in knowledge_web. Needs empirical validation against historical task corpus before deployment.

### Research-level

8. **Does the 60–85% success rate zone generalize from human learning to LLM agents?** Bjork/Kapur findings are cognitive science. Transfer to agent architectures is assumed but not validated for Poe's task distribution.

9. **Can persistence calibration be learned from experience?** Meta-RL (Wang/Duan 2016) demonstrates that persistence thresholds are learnable — agents trained on variable-difficulty problems develop richer stop/continue criteria as a meta-learned capability. Poe's lesson extraction pipeline (`memory.py`) is the plausible site for threshold-updating from outcome history. Not yet implemented. The adaptive budget rule (§5) is the downstream artifact; the lesson extraction mechanism is the upstream requirement.

10. **Passion analogue for agents:** Duckworth's "consistency of interest" is a key predictor of grit in humans. A goal-salience weighting that resists distraction from side-quests may be the functional equivalent. Poe's intent resolution design (`docs/INTENT_RESOLUTION_DESIGN.md`) is the relevant design space.

---

## 7. Counterpoints / Dissent

- **Credé, Tynan & Harms (2017) — Grit's incremental validity is small.** Meta-analysis of 88 studies found grit adds r≈0.18 over conscientiousness in predicting performance. The "just persist" model does not reliably produce better outcomes. **Implication for Poe:** counting retry volume is insufficient. The design's emphasis on signal quality (informative vs. confirming) over raw persistence is directly supported by this dissent. Grit counts don't predict outcomes; signal-responsive pivoting does.

- **Fixed-threshold skepticism.** `_REDECOMPOSE_THRESHOLD = 2` and tactic budget of 2–3 are empirically grounded but may be too conservative for novel or ill-structured goals. Fixed thresholds optimize for efficiency in well-structured domains at the cost of exploration depth in novel ones. **Counter:** The adaptive budget rule (§5) addresses this but is not yet implemented — the fixed values are not wrong, they are potentially over-constraining for a specific task type.

- **Productive failure generalizability (Kapur).** Kapur's findings are from structured educational settings with clear success criteria and a human instructor. Transfer to open-ended LLM task execution with ambiguous success criteria is assumed, not validated. **Counter:** The orientation hygiene check (are step inputs still valid? does success criterion still serve parent goal?) is the mechanism to surface ambiguous success criteria — it must run before any retry.

- **Human oversight as counterforce.** Maximizing autonomous persistence at goal tier conflicts with Poe's design contract — Jeremy's job is "exception handling." The system should escalate to human at goal tier, not self-solve indefinitely. Current design has human escalation as goal-tier action; the risk is `replan_count` and `restart` looping at goal tier without mandatory escalation after N attempts.

- **LLM metacognition validity.** Fleming's meta-d' framework assumes well-calibrated confidence. LLM self-reported confidence is systematically miscalibrated (training distribution artifact). The confidence–accuracy decoupling signal (§2.5) requires a calibration audit before deployment — implementing it on uncalibrated confidence produces noise, not signal. **This is a prerequisite, not a design flaw.**

---

## 8. Recommendation

Three ordered waves. Each wave is gated on the previous.

**Wave 1 — Wire what's already built (low cost, high value):**
1. Wire `task['attempt']` into director context before tier budget decisions
2. Add rule-based infrastructure failure classifier to `inspector.py` (pattern-match: timeout, 429, connection refused, permission denied) — tag as `failure_type: infrastructure` before LLM judgment consumes it
3. Protect director narration from token-pressure stripping — add budget guard in director prompt assembly

**Wave 2 — Structural signal improvements (medium cost):**
4. Add near-miss signal to inspector: step completes + downstream verify fails within same plan → zoom-out trigger, not success signal
5. Implement error-signature hash in `stuck_streak` update logic (`agent_loop.py:4220`): hash `(error_type, last_action, context_sig)` across retries; if hash repeats → mark confirming, not informative
6. Make `_REDECOMPOSE_THRESHOLD` a config key (default=2); allow director to raise on first replan if new hypothesis is semantically distinct from prior

**Wave 3 — Requires prerequisite validation (higher complexity):**
7. LLM confidence calibration audit (N=100 step sample) → then implement confidence-gap Inspector trigger
8. Ill-structured task classifier (manual annotation N=50, test heuristics) → then implement lateral zoom-out path
9. Adaptive budget rule (`exploration_factor = remaining_budget / initial_budget`)

**Confidence level:**
- **High** for Wave 1: direct code changes at verified gap sites, low blast radius
- **Medium** for Wave 2: design validated but implementation requires careful wiring; error-signature hash needs empirical tuning
- **Low-Medium** for Wave 3: conceptually sound; prerequisites not yet met; do not implement without calibration audit

---

## 9. Next Actions (Concrete)

1. **[WAVE 1 — IMMEDIATE]** Grep `agent_loop.py` for `task['attempt']` usage — confirm it is not already being read in director context; then add `task_attempt` to director context dict in the director call site.
2. **[WAVE 1 — IMMEDIATE]** Add infrastructure failure classifier to `inspector.py`: pattern-match known env-error signatures (timeout, HTTP 429, connection refused, permission denied, ENOENT/EPERM) and tag as `failure_type: infrastructure` before LLM failure analysis.
3. **[WAVE 1 — IMMEDIATE]** Audit director narration in `director.py` prompt assembly: identify any token-pressure truncation path; add a protection guard that preserves narration block even under budget pressure.
4. **[WAVE 2]** Add near-miss signal: in step completion handler, if step status=success but `verify_failure_count` increments on the same plan step → fire zoom-out signal (call existing inspector friction path with `near_miss=True`).
5. **[WAVE 2]** Implement error-signature hash in `stuck_streak` update at `agent_loop.py:4220`: compute `hash(error_type + last_action + context_key)` per retry; if hash repeats across consecutive retries, annotate as confirming failure.
6. **[WAVE 2]** Make `_REDECOMPOSE_THRESHOLD` a config key: `config.get("core_loop.redecompose_threshold", 2)`; document in `~/.poe/workspace/config.yml`.
7. **[WAVE 2]** Archive checkpoints on success (compressed JSON, TTL=7 days) in a `~/.poe/workspace/checkpoints/archive/` path — enable post-run step-sequence analysis without impacting clean-finish behavior.
8. **[PREREQ for Wave 3]** Calibration audit: instrument 100 step pre/post pairs (pre-step LLM confidence → actual outcome); compute Pearson r; if r < 0.50, defer confidence-gap inspector indefinitely.
9. **[PREREQ for Wave 3]** Validate ill-structured task heuristic on N=50 historical tasks: test three classifiers (multi-decomposition ambiguity, low skill match across candidates, underspecified success criteria); require >70% precision before deployment.

---

## 10. Source Reconciliation

*This section documents how sources were integrated, conflicts resolved, and recall findings reconciled.*

### Prior draft integration (`docs/research/productive-persistence.md`, 2026-03-27)

The prior draft provided the foundational ML/psychology survey. Three areas required reconciliation:

| Source | Prior draft claim | Reconciliation | Disposition |
|--------|-----------------|----------------|-------------|
| **Meta-RL** (Wang/Duan 2016) | Persistence-as-policy: agents learn when to persist from reward structure | Now in §2.2 and §6 Q9 | Integrated; `memory.py` identified as plausible calibration site |
| **Dweck (2006)** | Attribution theory: growth mindset = failure attributed to effort/strategy, not ability | Now in §2.3; distinct from Duckworth (grit) | Integrated; correctly separated from grit framing |
| **Kapur (2016)** | "Productive failure" — exploratory errors outperform guarded first passes | Retained in §1, §2.4; counterpoint added in §7 (generalizability to LLM agents) | Integrated with caveat |

### Adversarial verification corrections (from `docs/adversarial-verification.md`)

| Claim | Issue found | Correction applied |
|-------|------------|-------------------|
| CLAIM-07 | `reframe_intent` code citation fabricated; `agent_loop.py:4326–4331` contains `_ae_restart_ctx` | Removed fabricated citation; grit framing rewritten as engineering design principle with explicit "not Duckworth's vocabulary" caveat (§1) |
| CLAIM-13 | `_is_converging()` described as "design intent only — not yet wired" | Corrected to "fully implemented" at `agent_loop.py:2718` in §2.1, §2.5, §4.4 |
| CLAIM-09 | `hash(error_type × last_action × context_signature)` formula fabricated; `context_signature` DNE | §2.1 no longer presents this as implemented code; §8/§9 describe it as a Wave 2 recommendation |
| CLAIM-06 | `stuck_streak` threshold described as "strong signal" | Described as "heuristic threshold, empirically uncalibrated" throughout |
| CLAIM-14 | Inspector described as "primary quality gate" | Corrected: inspector is background analytics; real execution chain is `pre_flight.py → step_exec.py → _post_step_checks` |

### Argyris & Schön framing (from `docs/research/zoom-metacognition-adaptive-expertise.md`)

Zoom-metacognition doc introduced a tighter frame: *single-loop = persistence default, double-loop = reframing exception*. This is now the organizing principle for §2.5 — persistence is the correct default; zoom-out fires only when the five zoom-out signals trigger. This prevents the anti-pattern of over-triggering reframing (analysis paralysis) or never triggering it (learned helplessness).

### Duckworth year reconciliation

Prior draft used 2016 (book); summary used 2007 (JPSP paper). This doc cites **Duckworth (2007)** for the primary grit empirics (JPSP 92(6):1087) and Ericsson & Pool (2016) for *Peak* separately. Both are in Sources.

---

## Sources

**External research:**
- Duckworth, A.L., Peterson, C., Matthews, M.D. & Kelly, D.R. (2007). Grit: perseverance and passion for long-term goals. *Journal of Personality and Social Psychology*, 92(6), 1087–1101. [Primary grit empirics; ~8,000 citations]
- Credé, M., Tynan, M.C. & Harms, P.D. (2017). Much ado about grit: A meta-analytic synthesis of the grit literature. *JPSP*, 113(3), 492–511. [Dissent: grit r≈0.18 incremental over conscientiousness; 88 studies]
- Kapur, M. (2016). Examining productive failure, productive success, unproductive failure, and unproductive success in learning. *Educational Psychologist*, 51(2), 289–299.
- Ericsson, K.A., Krampe, R.T. & Tesch-Römer, C. (1993). The role of deliberate practice in the acquisition of expert performance. *Psychological Review*, 100(3), 363–406.
- Ericsson, K.A. & Pool, R. (2016). *Peak: Secrets from the New Science of Expertise*. Houghton Mifflin Harcourt.
- Dweck, C.S. (2006). *Mindset: The New Psychology of Success*. Random House.
- Seligman, M.E.P. (1972). Learned helplessness. *Annual Review of Medicine*, 23(1), 407–412.
- Csikszentmihalyi, M. (1990). *Flow: The Psychology of Optimal Experience*. Harper & Row.
- Bjork, R.A. (1994). Memory and metamemory considerations in the training of human beings. In Metcalfe & Shimamura (Eds.), *Metacognition: Knowing about Knowing* (pp. 185–205). MIT Press.
- Hatano, G. & Inagaki, K. (1986). Two courses of expertise. In Stevenson, Azuma & Hakuta (Eds.), *Child Development and Education in Japan* (pp. 262–272). Freeman.
- Schwartz, D.L. & Bransford, J.D. (1999). A time for telling. *Cognition and Instruction*, 16(4), 475–522.
- Schwartz, D.L., Bransford, J.D. & Sears, D. (2005). Efficiency and innovation in transfer. In Mestre (Ed.), *Transfer of Learning from a Modern Multidisciplinary Perspective* (pp. 1–51). Information Age Publishing.
- Fleming, S.M. & Dolan, R.J. (2012). The neural basis of metacognitive ability. *Philosophical Transactions of the Royal Society B*, 367, 1338–1349.
- Feltovich, P.J., Spiro, R.J. & Coulson, R.L. (1993). Learning, teaching, and testing for complex conceptual understanding. In Frederiksen, Mislevy & Bejar (Eds.), *Test Theory for a New Generation of Tests*. Erlbaum.
- Argyris, C. & Schön, D. (1978). *Organizational Learning: A Theory of Action Perspective*. Addison-Wesley.
- Boyd, J.R. (1987). *A Discourse on Winning and Losing* (OODA loop briefings). Air University Library.
- Cleeremans, A. (2002). Levels of representation in implicit learning. In French & Cleeremans (Eds.), *Implicit Learning and Consciousness*. Psychology Press.
- Wang, J.X. et al. (2016). Learning to reinforcement learn. *arXiv:1611.05763*.
- Duan, Y. et al. (2016). RL²: Fast reinforcement learning via slow reinforcement learning. *arXiv:1611.02779*.
- Baker et al. (2019). Emergent tool use from multi-agent autocurricula. *arXiv:1909.07528*.
- Auer, P., Cesa-Bianchi, N. & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning*, 47(2–3).
- Gittins, J. (1979). Bandit processes and dynamic allocation indices. *Journal of the Royal Statistical Society B*, 41(2), 148–177.
- Sutton, R.S. & Barto, A.G. (2018). *Reinforcement Learning: An Introduction* (2nd ed.). MIT Press.
- Chase, W.G. & Simon, H.A. (1973). Perception in chess. *Cognitive Psychology*, 4, 55–81.

**Poe codebase:**
- `src/agent_loop.py` — `stuck_streak` field `:233`, update `:4220`; `step_retries` field `:241`, usage `:562`; `director_replan_count` `:267`; `_REDECOMPOSE_THRESHOLD = 2` `:2747`; `recovery_step_count` `:246`; checkpoint write `:880`, delete `:1776`, resume `:1976`
- `src/task_store.py` — file-per-task JSON, fcntl locking, `recover_stale_claims()` `:320`, `task['attempt']` init `:69`, increment `:216`
- `docs/ARCHITECTURE_OVERVIEW.md` — Core Loop intent: "stuck detection, retries, budget limits, parallel execution, and checkpoint/resume" `:76`
- `skills/arch-core-loop.md` — Retry & Recovery section; Known Gaps: checkpoint auto-resume, budget ceiling
- `docs/CODING_NOTES.md` — Principle 8 (durable artifacts)
- `docs/ADAPTIVE_EXECUTION_DESIGN.md` — EvaluationContext, director action space
- Dev-recall: Jeremy's Ralph Wiggum model, validator-based stuck detection, Loop Sheriff concept

---

*See also:*
- `lat.md/core-loop.md` — retry/escalation execution map; [[checkpointing]] lifecycle; stuck detection signals
- `lat.md/quality-gates.md` — inspector friction detection; how failure taxonomy feeds the quality gate pipeline
- `lat.md/checkpointing.md` — checkpoint write/delete/resume lifecycle; durable artifact strategy
- `lat.md/self-improvement.md` — evolver and thinkback use loop outcomes (including failure patterns) as input
- `lat.md/memory-system.md` — lesson extraction pipeline; plausible site for adaptive budget calibration (meta-RL implication)
- `docs/research/productive-persistence.md` — foundational ML/psychology survey (2026-03-27); theoretical depth on Duckworth, Kapur, RL framing
- `docs/research/zoom-metacognition-adaptive-expertise.md` — adaptive expertise survey; zoom-out signals, crystallization failure mechanisms, 15 Poe implementation recommendations
- `docs/research/productive_persistence_summary.md` — executive summary of this document
- `docs/ADAPTIVE_EXECUTION_DESIGN.md` — EvaluationContext design; director action space
- `docs/ARCHITECTURE_OVERVIEW.md` — subsystem map; Core Loop entry covers stuck detection, retries, checkpoint/resume
- `skills/arch-core-loop.md` — implementation gaps: checkpoint auto-resume not wired, budget ceiling doesn't auto-enqueue continuations
- `docs/INTENT_RESOLUTION_DESIGN.md` — goal-salience weighting; passion-analogue design space
