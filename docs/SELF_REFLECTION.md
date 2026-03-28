# Self-Reflection Layer — Embedded Self-Debugging + Policy Memory

*"The orchestrator should not only act — it should continuously watch itself act, and become better at steering based on what it observes."*

## The Problem

Today, when the system fails in a new way, humans diagnose it:

1. Run the system against a real task
2. Observe what went wrong (logs, events, artifacts)
3. Classify the failure (setup? decomposition? execution? policy?)
4. Identify root cause (too-broad step, swallowed exception, false-positive constraint)
5. Apply a fix (prompt change, code fix, parameter tuning)
6. Verify the fix helped
7. Graduate the fix into a rule if it keeps working

Every step in this cycle has a mechanical equivalent in the system. The gap is that nobody wired them together.

## Architecture

Four layers, from sensory to behavioral:

```
Instrumentation     →  hooks / logs / events.jsonl / step artifacts
     ↓
Introspection       →  failure classifier + trace analyzer
     ↓
Policy              →  recovery planner (choose cheapest intervention)
     ↓
Graduation          →  promote repeated human fixes into rules/skills
```

### Layer 1: Instrumentation (mostly done)

What we already have:
- `events.jsonl` — step lifecycle events with timing and tokens
- `outcomes.jsonl` — goal-level success/failure records
- `poe.*` structured logging — per-step token accumulation, blocked reasons, constraint checks
- Step artifacts — `loop-{id}-step-{N}.md` per completed step
- `loop-{id}-PARTIAL.md` — combined partial results
- Inspector friction signals
- Attribution records (which skills contributed to which outcomes)

### Layer 2: Introspection (to build)

**Run Observer:** After each loop completes (or sticks), analyze the execution trace.

Input: events from the loop, step outcomes, timing data
Output: structured diagnosis with failure class + evidence

```python
@dataclass
class LoopDiagnosis:
    loop_id: str
    failure_class: str          # from taxonomy below
    severity: str               # "info" | "warning" | "critical"
    evidence: List[str]         # specific observations
    recommendation: str         # cheapest next action
    token_profile: dict         # per-step token breakdown
    timing_profile: dict        # per-step elapsed breakdown
```

**Failure Taxonomy** (standard buckets):

| Class | Signal | Example |
|-------|--------|---------|
| `setup_failure` | Step 1 blocks with adapter/import error | `make_adapter` bug |
| `adapter_timeout` | tokens=0, elapsed > 60s | Claude subprocess 300s timeout |
| `constraint_false_positive` | tokens=0, blocked by constraint, natural-language step | "remove duplicates" blocked as DESTROY |
| `decomposition_too_broad` | Single step > 200K tokens or > 120s | "read all Python files" |
| `empty_model_output` | tokens > 0 but content < 20 chars, no tool call | Model returns "ok" |
| `retry_churn` | Same step blocked 2+ times, different reasons | Oscillating failures |
| `budget_exhaustion` | max_iterations reached with remaining steps | 18/20 done, no synthesis |
| `token_explosion` | Token growth rate > 3x between consecutive steps | Step N burns 5x step N-1 |
| `artifact_missing` | Loop completes but no readable output artifact | "done" with empty results |
| `integration_drift` | ImportError or AttributeError in try/except | Wrong function name |

### Layer 3: Policy (to build)

**Recovery Planner:** Given a failure class, select the cheapest intervention.

| Failure Class | Recovery Action |
|--------------|----------------|
| `decomposition_too_broad` | Re-decompose with tighter max_steps, add "limit to 3-5 files per step" |
| `constraint_false_positive` | Log pattern, add to allowlist, retry step |
| `adapter_timeout` | Switch to API adapter (not subprocess), reduce step scope |
| `budget_exhaustion` | Increase max_iterations, enable budget-aware landing |
| `empty_model_output` | Retry with explicit "you MUST call a tool" instruction |
| `retry_churn` | Skip step, note as partial, continue to next |
| `token_explosion` | Truncate completed_context to summaries only |
| `setup_failure` | Diagnose import chain, surface real exception |

The planner doesn't need an LLM — it's a decision table indexed by failure class, with a preference for the cheapest action first.

### Layer 4: Graduation (partially exists)

The evolver already proposes improvements and auto-applies high-confidence ones. The missing link is feeding introspection diagnoses into the evolver as structured suggestions.

When the same failure class appears 3+ times across different loops:
1. The observer notices the pattern
2. It creates an evolver Suggestion with `category="execution_pattern"` and high confidence
3. The evolver auto-applies it (existing mechanism)
4. If the fix involves a new constraint rule, it graduates to `rules.jsonl` (Phase 22 Stage 5)

This closes the loop: **observe → classify → fix → verify → graduate**.

## What This Is Not

- Not "AI consciousness" or self-awareness
- Not an LLM reflecting on its own outputs (that's the evolver's job)
- Not a general-purpose debugger

It's **structured pattern matching on execution traces** that produces **actionable diagnoses** and **mechanical recovery actions**. The same thing a human does when watching logs, but codified into heuristics that run automatically.

## Relationship to Existing Components

```
inspector.py    — reviews outcome quality (friction, alignment)
                  → introspection adds: reviews execution quality (timing, tokens, retries)

evolver.py      — proposes improvements from failure patterns
                  → introspection adds: structured diagnoses as high-confidence suggestions

sheriff.py      — monitors health (is anything stuck?)
                  → introspection adds: why is it stuck? what class of failure?

attribution.py  — maps outcomes to skills
                  → introspection adds: maps execution pathology to root causes

rules.py        — zero-cost hardcoded paths
                  → introspection adds: graduated recovery rules
```

## Implementation Plan

See ROADMAP.md Phases 44-46.
