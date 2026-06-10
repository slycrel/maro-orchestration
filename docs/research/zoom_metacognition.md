# Zoom-Out Metacognition for Autonomous Agents

*Research synthesis — 2026-05-12*
*Source material: docs/research/zoom-metacognition.md (2026-03-27), Phase 62 implementation*

---

## Question

**When should an autonomous agent stop retrying a stuck step and instead re-examine its own plan?**

More precisely: how does an agent distinguish an *execution problem* (wrong action, retry and refine) from a *model problem* (wrong decomposition, step back and reframe)? What signals trigger the switch, what thresholds should govern it, and what mechanism should fire when it does?

This is the zoom-in / zoom-out metacognition problem — a specific instance of the broader question of when to persist vs when to reframe.

---

## Key Findings

### 1. Three Frameworks, One Convergent Insight

Academic research converges on the same structural answer from three independent directions:

| Framework | Zoom-in (persist) | Zoom-out trigger | Zoom-out mechanism | Failure trap |
|-----------|-------------------|------------------|--------------------|--------------|
| **Argyris DLL** | Single-loop: fix error, keep governing variables | Repeated failures on same problem | Question governing variables → revise mental model | Defensive routines block reframing |
| **Boyd OODA** | Fast loop, same Orientation | Observations don't match predictions | Break loop → reorient → re-enter | Stale orientation; fast loops on a bad model |
| **Adaptive Expertise** (Hatano/Bereiter) | Routine schema execution | Schema fitness check fails; partial failures don't converge | Construct new decomposition | Einstellung effect (persevering with wrong schema) |

**Convergent insight:** Persistence is the default. Reframing is the exception. The switch from persist → reframe requires three things: a *signal*, a *threshold*, and a *mechanism*. Without all three, agents default to retry indefinitely.

### 2. The Core Decision

Every stuck situation resolves to one question:

> Is this an **execution problem** (I know what to do, I'm doing it wrong) or a **model problem** (my understanding of the goal/environment is wrong)?

- Execution problem → **retry / zoom-in**: adjust action, keep same frame
- Model problem → **re-decompose / zoom-out**: revise the decomposition, discard stale assumptions

### 3. Retry Signals (zoom in when)

- Step has failed ≤ 3 times
- Each failure produces a *different* error or partial progress — problem space is narrowing
- Error is clearly transient (rate limit, network timeout, resource unavailable)
- Failure matches a known pattern with a known fix
- Preconditions are still valid (parent goal unchanged, context stable)

### 4. Re-decompose Signals (zoom out when)

- Step fails ≥ 3 times with *no convergence* — identical error, identical failure point
- Multiple sibling steps under the same parent fail independently (>50% sibling failure rate → decomposition is wrong)
- A retry succeeds but produces output that doesn't advance the parent goal (wrong step, not wrong execution)
- New information invalidates a planning assumption
- Time/cost budget for this path is exhausted without progress

### 5. Convergence Detection

The clearest zoom-out signal is *non-convergence*: retries produce identical failures. Proxy signals for convergence (progress being made):

- Error message changes between retries (new error = progress through the problem space)
- Partial output increases (more work done before failure each time)
- Resource consumption pattern shifts

Non-convergence after N retries = strong signal the schema is wrong, not the execution.

### 6. Orientation Hygiene (Boyd)

Before retrying any stuck step, validate three things:

1. Are the step's *inputs* still valid? (Context may have changed since planning)
2. Does the step's *success criterion* still serve the parent goal?
3. Has the environment changed in a way that makes this step obsolete?

If any answer is "no" — re-decompose immediately; do not retry.

### 7. The Einstellung Trap

Luchins (1942): subjects who learned an efficient solution to a problem type became *less able* to solve a simpler variant requiring a different approach. They kept applying the cached schema even when it was counterproductive.

For autonomous agents: cached plans, stale context, and prior-success bias all create Einstellung pressure. The defense is *a priori* fitness criteria — set a retry budget at planning time, not after failure accumulates.

### 8. Decision Algorithm

```
on_step_failure(step, context):
  error_type = classify_error(step.last_error)

  if error_type == TRANSIENT:
    return RETRY(backoff=exponential)

  failure_count = context.failure_count(step)
  convergence   = context.is_converging(step)   # errors narrowing?

  if failure_count < RETRY_THRESHOLD and convergence:
    return RETRY

  if failure_count >= RETRY_THRESHOLD or not convergence:
    sibling_failures = context.sibling_step_failure_rate()
    if sibling_failures > SIBLING_THRESHOLD:
      return REDECOMPOSE_PARENT_GOAL
    else:
      return REDECOMPOSE_THIS_STEP

  if redecompose_attempts >= REDECOMPOSE_THRESHOLD:
    return FLAG_STUCK  # escalate to human or goal reframing
```

**Suggested thresholds (empirically tunable):**

| Threshold | Default | Meaning |
|-----------|---------|---------|
| `RETRY_THRESHOLD` | 3 | Max retries before forced zoom-out evaluation |
| `SIBLING_THRESHOLD` | >50% | Sibling failure rate that implies wrong decomposition |
| `REDECOMPOSE_THRESHOLD` | 2 | Re-decompositions without progress → escalate |

---

## Implications for Poe

### 1. Track error fingerprints, not just failure counts

A step that fails 5 times with changing errors is making progress. A step that fails 5 times with identical errors is stuck in Einstellung. Store a fingerprint (error class + failure point) per retry; compare across retries to compute convergence.

*Implementation:* Phase 62 delivered convergence tracking in `agent_loop.py`.

### 2. Aggregate sibling failure rates by parent goal

When >50% of siblings under the same parent goal fail, the decomposition is wrong — not individual execution. Track failure rates grouped by parent goal ID. When threshold is crossed, promote the re-decompose action to parent scope.

*Implementation:* Phase 62 delivered sibling failure correlation.

### 3. Orientation checkpoint before retry N+1

After N failures, before retry N+1, run a mini-reorientation: re-validate preconditions, re-check that the parent goal is unchanged, re-confirm that the step's success criterion still serves the parent. This is the OODA reorientation micro-step applied to agent execution.

*Implementation:* Phase 62 delivered orientation hygiene checks.

### 4. Budget-aware zoom-out at planning time

Adaptive expertise research: experts set schema-fitness criteria *before* starting, not after failure accumulates. Poe should set retry budgets at decomposition time. When budget exhausts → trigger re-decompose, not one-more-retry.

*Gap:* A priori budget setting is not yet fully wired into the planner; post-hoc threshold checking is the current mechanism.

### 5. Discard subtree assumptions on re-decompose, not just steps

Double-loop learning is blocked by "defensive routines" — in agent terms: cached context, stale assumptions, prior-plan bias. When re-decomposing a failed subtree, discard its *assumptions* (inputs, environment state, success criteria) alongside its steps. A re-decomposition that inherits wrong assumptions will fail the same way.

*Gap:* Current re-decomposition logic discards steps but may carry forward stale context. Verify in `agent_loop.py`.

### 6. Log retry-vs-redecompose rationale for deutero-learning

Deutero-learning (Argyris) = learning how to learn. Poe should log not just what it did, but *why it chose retry vs redecompose* for each stuck step. This audit trail enables later analysis: were zoom-out decisions made at the right threshold? Were they made too late? This is the input for self-improving the thresholds over time.

*Implementation:* Phase 62 delivered metacognitive logging. Full deutero-loop (using logs to tune thresholds) not yet closed.

### 7. Three-tier escalation, not binary

Don't collapse stuck handling to retry/give-up. The correct hierarchy is:
1. **Retry** (execution problem, convergence still possible)
2. **Re-decompose this step** (model problem, local scope)
3. **Re-decompose parent goal** (decomposition problem, sibling failures confirm)
4. **Flag stuck / reframe goal** (goal itself is wrong or unachievable)

Each tier should be explicit in the agent loop, not implicit in retry-count logic.

---

## Implementation Status (Phase 62)

Phase 62 is **DONE**. Delivered:

| Deliverable | Status |
|-------------|--------|
| Convergence tracking (error fingerprint per retry) | Done |
| Mid-loop re-decomposition | Done |
| Sibling failure correlation by parent goal | Done |
| Orientation hygiene (pre-retry precondition check) | Done |
| Metacognitive logging (retry-vs-redecompose rationale) | Done |
| Three-tier escalation (retry → redecompose-step → redecompose-parent → stuck) | Done |
| Budget-aware zoom-out | Partial (post-hoc threshold; a priori planning not wired) |
| Deutero-loop (threshold self-tuning from logs) | Not yet started |

---

## Cross-Links

| Related module / doc | Connection |
|---------------------|------------|
| `src/knowledge_lens.py` | Knowledge lenses surface *what the agent knows* about a domain; zoom-out metacognition determines *when to question the frame* applied to that knowledge. Stale orientation (Boyd) maps directly to stale lens selection. |
| `src/introspect.py` | Phase 44–46 failure classifier, lenses, recovery planner. Zoom-out metacognition is the *trigger*; introspect provides the *mechanism* (lens selection, recovery planning, intervention graduation). The decision algorithm above should feed into `introspect.py`'s recovery planner. |
| `docs/research/self_reflection.md` | Self-reflection research covers the broader question of when an agent updates its own beliefs. Zoom-out metacognition is the action-loop-scoped version: when to update the current *plan*, not just beliefs. |
| `src/agent_loop.py` | Primary implementation site. Phase 62 changes live here. Convergence tracking, sibling failure aggregation, orientation checkpoints, and the three-tier escalation all execute inside the core loop. |
| `src/inspector.py` | Inspector detects friction and quality gate failures. Zoom-out metacognition determines *what to do* when Inspector fires: retry the step or re-decompose the goal? The integration point is the `on_step_failure` handler. |
| `src/evolver.py` | Long-run self-improvement. Deutero-learning (the unimplemented tier) is evolver territory: aggregate zoom-out decisions across sessions, detect patterns, tune thresholds. Current evolver confidence calibration work (see MILESTONES.md) is a prerequisite. |
| `docs/ADAPTIVE_EXECUTION_DESIGN.md` | Adaptive execution design. Zoom-out metacognition is one mechanism for adaptation; this doc covers the broader adaptive execution framework that contextualizes it. |

---

## Sources

- Argyris, C. & Schön, D. (1978). *Organizational Learning: A Theory of Action Perspective.*
- Argyris, C. (1991). "Teaching Smart People How to Learn." *Harvard Business Review.*
- Boyd, J. (1987). *A Discourse on Winning and Losing* (OODA loop briefings).
- Hatano, G. & Inagaki, K. (1986). "Two courses of expertise." In *Child Development and Education in Japan.*
- Bereiter, C. & Scardamalia, M. (1993). *Surpassing Ourselves: An Inquiry into the Nature and Implications of Expertise.*
- Luchins, A.S. (1942). "Mechanization in problem solving: The effect of Einstellung." *Psychological Monographs.*
- Hammond, K.J. (1990). "Case-based planning: A framework for planning from experience." *Cognitive Science.*

---

*Thresholds (RETRY=3, SIBLING=50%, REDECOMPOSE=2) are empirically tuned starting points, not fixed values. Phase 62 logging creates the data to refine them.*
