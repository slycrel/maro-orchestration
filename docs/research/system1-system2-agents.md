# System 1 / System 2 Framework for Autonomous Agents

*Research date: 2026-03-27*

---

## 1. The Framework

Daniel Kahneman's dual-process theory distinguishes two cognitive modes:

| Dimension | System 1 (Fast) | System 2 (Slow) |
|-----------|-----------------|-----------------|
| Speed | Immediate | Deliberate |
| Effort | Low | High |
| Mechanism | Heuristic, pattern-match | Logical, step-by-step |
| Accuracy | High in familiar domains | High in novel/complex domains |
| Failure mode | Bias, overconfidence | Overthinking, paralysis |
| Trigger | Automatic | Demand-driven |

Key Kahneman insight: System 2 is lazy — humans (and agents) default to System 1 whenever plausible. Errors arise from System 1 firing in situations that actually require System 2.

---

## 2. Mapping to Autonomous Agents

### System 1 ↔ Execute (fast heuristic action)
- Executing a known tool call in a familiar context
- Applying a learned pattern (e.g., "file not found → check path")
- Routine sub-tasks with high prior success rate
- Time-constrained responses (heartbeat, streaming)

### System 2 ↔ Decompose (slow deliberate planning)
- Breaking a novel or ambiguous goal into sub-steps
- Evaluating multiple strategies before committing
- Resolving conflicts between evidence or instructions
- Calibrating confidence before irreversible actions

The decompose/execute split in agent_loop already embodies this architecture implicitly — but without explicit switching logic.

---

## 3. Switching Signals

### Trigger System 2 (slow down, deliberate) when:

**Novelty**
- Goal type not seen in recent N runs
- Tool combination never used before
- External context changed significantly since last run

**Stakes**
- Action is irreversible (file delete, send message, deploy)
- Downstream steps depend critically on this output
- Error recovery cost is high

**Confidence**
- Model confidence score below threshold (e.g., < 0.7)
- Multiple plausible interpretations of the goal
- Prior similar runs had high variance in outcomes

**Conflict**
- Tool output contradicts prior reasoning
- Instructions conflict with memory or SOUL constraints
- Unexpected state observed mid-execution

**Error signal**
- Recent retry or failure in this loop
- Exception raised by a tool
- Output validation failed

**Resource**
- Token budget is sufficient (not near limit)
- Time budget allows deliberation
- Not in a streaming/real-time context

### Stay in System 1 (fast execution) when:
- High prior success rate on this exact task type
- Action is reversible or low-stakes
- Confidence is high and context is familiar
- Token/time budget is constrained
- In a heartbeat or keep-alive context

---

## 4. Implications for agent_loop Architecture

### Current state
- `decompose` = implicit System 2 (but always called, regardless of task novelty)
- `execute` = implicit System 1 (but no confidence gating)
- No explicit signal routing between modes
- No feedback loop from execute results back into decompose

### Gaps identified
1. **No confidence gate on decompose.** Simple, familiar tasks still pay full decompose cost.
2. **No mid-execute escalation path.** When execute hits an unexpected state, there's no clean way to re-enter decompose without restarting the loop.
3. **No calibration feedback.** Outcomes don't update heuristic confidence for future runs (no "this worked" signal stored).
4. **Switching is implicit, not instrumented.** Hard to tune or debug which mode fired.

### Recommended additions

#### a) Novelty/confidence pre-check before decompose
```python
if task_is_familiar(goal) and confidence > THRESHOLD:
    skip_decompose()  # go straight to execute with cached plan
else:
    run_decompose(goal)
```

#### b) Mid-execute escalation hook
```python
result = execute_step(step)
if result.confidence < THRESHOLD or result.unexpected_state:
    re_decompose(remaining_steps, context=result)
```

#### c) Outcome feedback to heuristic store
```python
on_loop_complete(outcome):
    update_task_confidence(goal_type, outcome.success, outcome.steps_taken)
```

#### d) Explicit mode logging
- Tag each loop run with `mode: system1 | system2`
- Log switching events with reason
- Surface in TASKS.md or agent dashboard for review

### Priority order for implementation
1. Mid-execute escalation (highest leverage, catches live failures)
2. Novelty pre-check (saves tokens on routine tasks)
3. Outcome feedback (compounds over time, lower immediate ROI)
4. Mode logging (observability, low effort)

---

## 5. Key Takeaways

- The decompose/execute split is architecturally sound — it maps cleanly to System 2/System 1.
- The missing piece is **dynamic routing**: the agent should choose mode based on signals, not always run both.
- Irreversibility and novelty are the strongest switching signals — prioritize these in any gate logic.
- Metacognition (knowing when you don't know) is the hardest part; confidence calibration is the core unsolved problem.
- System 1 failure mode for agents = executing a cached plan in a context that has silently changed. Guard against this with lightweight state-diff checks before execute.

---

## 6. References & Inspiration

- Kahneman, D. *Thinking, Fast and Slow* (2011)
- Yao et al., ReAct (2022) — interleaved reasoning/action as lightweight System 2
- Shinn et al., Reflexion (2023) — post-hoc verbal feedback as System 2 error correction
- CoALA framework (Sumers et al., 2023) — memory + action space decomposition
- Robotics dual-architecture literature (reactive + deliberative layers)
- Anthropic Constitutional AI — value-alignment as a System 2 override mechanism
