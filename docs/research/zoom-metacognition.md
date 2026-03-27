# Zoom In / Zoom Out Metacognition for Autonomous Agents

**Research question:** What do researchers know about zoom-in/zoom-out metacognition — double-loop learning (Argyris), OODA loops, adaptive expertise — and what does it mean for an autonomous agent deciding when to re-decompose a goal vs retry a stuck step?

*Written: 2026-03-27*

---

## 1. Framework Summaries

### 1.1 Double-Loop Learning (Argyris & Schön)

**Core idea:** Single-loop learning fixes errors within existing rules ("zoom in" — retry). Double-loop learning questions the rules themselves ("zoom out" — reframe).

Key distinctions:
- **Single-loop:** Detect error → adjust action → continue. Governing variables (goals, assumptions) unchanged.
- **Double-loop:** Detect error → question governing variables → revise mental model → re-plan.
- **Deutero-learning:** Learning *how* to learn — recognizing which loop to use and when.

Mechanisms that trigger double-loop:
- Repeated single-loop failures on the same problem
- Surfacing "defensive routines" — implicit assumptions that block reframing
- Organizational/agent reflection on *why* the current strategy keeps failing

Empirical finding: Most agents (human and organizational) default to single-loop even when double-loop is needed; switching requires deliberate interruption of the action cycle.

---

### 1.2 OODA Loop (Boyd)

**Core idea:** Decision cycle — Observe → Orient → Decide → Act. Agility comes from cycling faster than the environment changes, and from rich, updated mental models.

Key distinctions:
- **Zoom-in (fast OODA):** Tight loop, same orientation, rapid action. Useful when model is valid and environment is stable.
- **Zoom-out (orientation update):** Break the loop, update the mental model (Orientation phase), re-enter with new framing. Required when observations conflict with predictions.
- **Orientation** is the pivot: culture, prior experiences, mental models, and analysis all feed it. A corrupted or stale orientation causes all subsequent decisions to fail.

Failure signal: When Actions consistently fail to produce expected Observations, the Orientation is wrong — stop acting, reorient.

Boyd's insight: Destroying the enemy's OODA loop (causing disorientation) is more decisive than superior firepower. For agents: getting stuck in a bad orientation is the primary failure mode.

---

### 1.3 Adaptive Expertise (Hatano & Inagaki; Bereiter & Scardamalia)

**Core idea:** Experts have two modes — routine expertise (efficient, pattern-match and execute) and adaptive expertise (know when patterns don't fit, invent new approaches).

Key distinctions:
- **Routine expertise = zoom in:** Apply known schema, refine execution, optimize within the current frame.
- **Adaptive expertise = zoom out:** Recognize schema mismatch, decompose the problem differently, construct new approach.
- **Metacognitive monitoring** is the switch mechanism: adaptive experts continuously check "is my current schema actually working for this problem?"

Empirical findings:
- Novices often zoom in too long (persevere with wrong schema) — "Einstellung effect"
- Overspecialized experts also zoom in too long — optimized for known problems, blind to novel ones
- Adaptive experts set explicit "schema fitness" criteria before starting; they know when to abandon

Trigger for switching: When the current decomposition produces repeated partial failures that don't converge — the schema itself is wrong, not just execution.

---

## 2. Cross-Framework Synthesis

| Dimension | Argyris DLL | Boyd OODA | Adaptive Expertise |
|-----------|------------|-----------|-------------------|
| **Zoom-in mode** | Single-loop correction | Fast OODA, same orientation | Routine schema execution |
| **Zoom-out trigger** | Repeated failures, surfaced assumptions | Observation/prediction mismatch | Schema fitness check fails |
| **Zoom-out mechanism** | Question governing variables | Reorient (update mental model) | Construct new decomposition |
| **Meta-level** | Deutero-learning | Strategic OODA (meta-loop) | Metacognitive monitoring |
| **Failure trap** | Defensive routines block reframing | Stale orientation, fast loops on bad model | Einstellung effect |
| **Key signal** | "Same problem, different day" | Observations don't match predictions | Partial failures that don't converge |

### Convergent insight across all three:

**Persistence is the default; reframing is the exception that must be triggered deliberately.**

All three frameworks agree:
1. Agents have a strong bias toward continuing current strategy (cognitive, organizational, evolutionary pressure toward efficiency)
2. This bias is adaptive in stable environments and catastrophic in novel/shifted ones
3. The switch from persist → reframe requires a *signal* + a *threshold* + a *mechanism*
4. The mechanism (double-loop reflection, reorientation, schema revision) is cognitively expensive and should not fire constantly

### The zoom-in/zoom-out decision is essentially:

> "Is this a *execution problem* (I know what to do, I'm doing it wrong) or a *model problem* (my understanding of the goal/environment is wrong)?"

Single-loop / zoom-in = execution problem → retry, refine, optimize
Double-loop / zoom-out = model problem → re-decompose, reframe, re-plan

---

## 3. Heuristics for Autonomous Agents: Retry vs Re-decompose

### 3.1 Retry (zoom in) when:

- The step has failed ≤ N times (suggested N=2–3 for most steps)
- Each failure produces a *different* error or partial progress — the problem space is narrowing
- The error is clearly environmental/transient (rate limit, network timeout, resource unavailable)
- The failure mode matches a known pattern with a known fix
- The step's preconditions are still valid (parent goal unchanged, context stable)

### 3.2 Re-decompose (zoom out) when:

- The step has failed ≥ N times with *no convergence* — same error, same point of failure
- Multiple steps in the same sub-goal are failing independently → the sub-goal decomposition is wrong
- A retry succeeds but produces output that doesn't advance the parent goal → the step was the wrong step
- New information has arrived that invalidates a planning assumption
- The step is succeeding but the parent goal is drifting or has changed
- Time/cost budget for this path is exhausted without progress

### 3.3 Escalate to full goal reframing when:

- Re-decomposition has been attempted and the new decomposition also fails
- The goal itself is ambiguous, contradictory, or unachievable given current constraints
- The environment has shifted in a way that makes the goal irrelevant or harmful

### 3.4 Concrete decision algorithm for an agent

```
on_step_failure(step, context):
  failure_count = context.failure_count(step)
  convergence = context.is_converging(step)   # errors narrowing?
  error_type = classify_error(step.last_error)

  if error_type == TRANSIENT:
    return RETRY(backoff=exponential)

  if failure_count < RETRY_THRESHOLD and convergence:
    return RETRY

  if failure_count >= RETRY_THRESHOLD or not convergence:
    sibling_failures = context.sibling_step_failure_rate()
    if sibling_failures > SIBLING_THRESHOLD:
      return REDECOMPOSE_PARENT_GOAL
    else:
      return REDECOMPOSE_THIS_STEP

  if redecompose_attempts >= REDECOMPOSE_THRESHOLD:
    return FLAG_STUCK_TO_HUMAN  # or escalate to goal reframing
```

Suggested thresholds (tunable):
- `RETRY_THRESHOLD`: 3 retries
- `SIBLING_THRESHOLD`: >50% of sibling steps failing
- `REDECOMPOSE_THRESHOLD`: 2 re-decompositions without progress

### 3.5 Convergence detection (key sub-problem)

A step is *converging* if each retry produces output closer to the target. Proxy signals:
- Error message changes (new error = progress through the problem)
- Partial output increases (more work done before failure)
- Resource consumption pattern shifts

A step is *not converging* if retries produce identical failures. This is the clearest signal for zoom-out.

### 3.6 The orientation hygiene principle (from Boyd)

Before retrying any stuck step, check:
- Are the step's *inputs* still valid? (Context may have changed since planning)
- Does the step's *success criterion* still serve the parent goal?
- Has the environment changed in a way that makes this step obsolete?

If any answer is "no" — re-decompose immediately, don't retry.

---

## 4. Implications for Poe / OpenClaw Architecture

1. **Track failure convergence, not just count.** A step that fails 5 times with improving partial output is different from one that fails 5 times identically. Store error fingerprints per retry.

2. **Sibling failure correlation.** If multiple steps under the same parent goal fail, this is a strong signal the decomposition is wrong. Aggregate failure rates by parent goal.

3. **Orientation checkpoints before retries.** After N failures, before retry N+1, re-validate that the step's preconditions and parent goal are still coherent. This is the OODA reorientation micro-step.

4. **Budget-aware zoom-out.** Adaptive expertise research shows experts set *a priori* fitness criteria. Poe should set a retry budget at planning time and trigger re-decompose when budget is exhausted, not after-the-fact.

5. **Reframing as a first-class operation.** Double-loop learning is blocked by "defensive routines" — in agent terms, this means cached plans and stale context. When re-decomposing, discard the failed subtree's assumptions, not just its steps.

6. **Metacognitive logging.** The agent should log not just what it did, but *why it chose retry vs redecompose*. This creates the audit trail needed for deutero-learning: the agent (or operator) can later ask "did we make the right zoom-out decisions?"

---

## 5. Key Sources

- Argyris, C. & Schön, D. (1978). *Organizational Learning: A Theory of Action Perspective.*
- Argyris, C. (1991). "Teaching Smart People How to Learn." *Harvard Business Review.*
- Boyd, J. (1987). *A Discourse on Winning and Losing* (OODA loop briefings).
- Hatano, G. & Inagaki, K. (1986). "Two courses of expertise." In *Child Development and Education in Japan.*
- Bereiter, C. & Scardamalia, M. (1993). *Surpassing Ourselves: An Inquiry into the Nature and Implications of Expertise.*
- Luchins, A.S. (1942). "Mechanization in problem solving: The effect of Einstellung." *Psychological Monographs.*
- Hammond, K.J. (1990). "Case-based planning: A framework for planning from experience." *Cognitive Science.*

---

*This document synthesizes academic research for application to autonomous agent design. Thresholds and algorithms are starting points for empirical tuning, not fixed values.*
