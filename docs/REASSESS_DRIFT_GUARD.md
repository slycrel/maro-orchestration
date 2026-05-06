# Re-assess Drift Guard (Pseudo-Adversarial)

**Purpose:** Guard the re-assess against a familiar failure mode: gradually drifting from **"make LLMs useful, automated, and able to stay on target over ordered time"** into either decorative agent theater or an implicit AGI project.

This is not a spec. It is a pressure-test lens.

---

## Primary stance

Assume the system is always a little bit guilty.

Not malicious. Not doomed. Just persistently tempted to:
- rename confusion as intelligence
- rename verbosity as reasoning
- rename persistence as autonomy
- rename clever structure as progress
- rename self-description as self-knowledge

The guard exists to keep us honest.

---

## Core question

> Does this mechanism measurably improve usefulness, automation, or staying-on-target across time?

If not, it is probably architecture cosplay.

---

## The guard questions

### 1) Self-model: does the system know what state it is actually in?
A self-model is only useful if it predicts behavior and failure.

Pressure test:
- When the system says `blocked`, `done`, `waiting`, `high confidence`, or `urgent`, do those labels correspond to a stable, repeatable operational state?
- Does the label change what the system does next in a reliable way?
- Can we show that the self-description predicts likely next outcomes better than not having it?

Failure smell:
- the system narrates itself fluently, but the narration does not bind control flow

### 2) Layer discipline: are we diagnosing the failure at the correct layer?
Many failures get misattributed to “the model” when the defect is actually in thread shape, tool policy, memory use, or review structure.

Pressure test:
- Is the problem at the token/tool layer, task-strategy layer, or identity/policy layer?
- Are we introducing a higher-layer abstraction to compensate for a lower-layer bug?
- Are we blaming the model for orchestration mistakes?

Failure smell:
- one grand mechanism allegedly fixes everything

### 3) Semantic accountability: do the system’s words cash out?
This is adjacent to correspondence, but stricter. A label is not good because it sounds right; it is good because it reliably maps to action, prediction, and outcome.

Pressure test:
- Do internal terms like `blocked`, `ready`, `done`, `needs_review`, `delegate`, `stale`, or `high_confidence` produce the right next action with high consistency?
- Can we audit when a label was applied and whether reality later validated it?
- Are there labels whose meaning has drifted from operational reality?

Failure smell:
- labels become decorative and stop constraining behavior

### 4) Analogy transfer: can the system detect the same failure class in different clothing?
A useful orchestrator should notice structural similarity, not just keyword similarity.

Pressure test:
- Can a fix learned in one workflow transfer to another because the underlying failure pattern is the same?
- Can the review layer identify recurrent drift classes across different tasks and tools?
- Are we storing lessons in a form that preserves structure rather than anecdote?

Failure smell:
- each new failure is treated as novel because the surface details changed

### 5) Identity: is the “self” acting as a stabilizing fiction, not a metaphysical distraction?
Persistent identity is useful if it helps maintain continuity of intent across time. It is harmful if it becomes an excuse for anthropomorphic indulgence.

Pressure test:
- Does identity help preserve goals, standards, and commitments across sessions?
- Does it improve calibration, recovery, or consistency?
- Are we quietly designing for a dramatic self instead of a reliable one?

Failure smell:
- identity work grows faster than reliability work

### 6) Anti-cleverness: is this actually doing work, or just being intricate?
The system should earn complexity.

Pressure test:
- What concrete failure does this mechanism reduce?
- What evidence would show the mechanism is unnecessary?
- Is there a simpler feedback loop that gets 80% of the benefit?
- If the mechanism disappeared tomorrow, what would actually get worse?

Failure smell:
- recursive elegance with no measurable control benefit

### 7) Target retention: does the system stay aimed at the user’s actual intent over ordered time?
This is the practical center of the whole exercise.

Pressure test:
- After delays, delegation, retries, and context shifts, is the system still optimizing the original success condition?
- Does it preserve user intent better than baseline chat + reminders?
- When it drifts, does it notice and recover on its own?

Failure smell:
- local competence paired with mission drift

---

## Non-goal reminder

We are **not** trying to prove machine consciousness, build AGI, or win a philosophy seminar.

We are trying to build a system that:
- is useful without constant supervision
- automates real work reliably
- preserves intent across time and sequence
- recovers from drift before the drift becomes the task

Any idea that does not serve those ends should be treated with suspicion, especially if it is very clever and very beautiful.

---

## Related context

- `docs/REASSESS_LINEAGE.md` — how the project moved from judgment drift → scope/constraints → verification sibling → 1-shot-first step-back
- `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md` — the planning-vs-verification correction

## Suggested use in the re-assess

Use this as a review overlay, not as the main structure.

For any proposed mechanism, ask:
1. Which drift class is this meant to prevent?
2. Which guard question does it satisfy?
3. What observable would prove it helped?
4. What simpler alternative did we reject, and why?
5. How could this itself become a source of drift?

That last one matters. Every guard can become theater.
